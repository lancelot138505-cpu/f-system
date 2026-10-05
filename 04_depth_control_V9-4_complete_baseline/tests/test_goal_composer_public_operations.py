from .common import *

from dataclasses import replace
import tempfile
from fsys.external_kv import ExternalCandidateKV
from fsys.goal_composer import GoalDrivenComposer,TaskSpec
from fsys.formation import canonical_compose
from fsys.provenance import FormationProvenance
from fsys.public_operations import normalize_operation,is_first_class_operation
from fsys.serialization import digest
from fsys.verifier import VerificationRecord


def numeric_goal():
    inputs=({'name':'input_value','type':'float'},)
    return TaskSpec('affine-goal',inputs,{'type':'float'},
        ({'input':[2.0],'output':5.0},{'input':[3.0],'output':7.0}),
        digest({'inputs':inputs,'output':'float'}))


def numeric_engine_with_f():
    domain=NumericDomain();library=domain.bootstrap()
    children=('f_double','f_increment')
    topology={'kind':'ordered_numeric_pipeline','order':'left_to_right'}
    definition=domain.composition_definition_from_children(children,topology)
    structure=canonical_compose(children,topology,definition,
        {item.identity:item.level for item in library},source='autonomous discovery',
        evidence={'test':'recurrent composition'})
    library.commit(structure,VerificationRecord(True,'test verifier',digest(structure.kernel)))
    return fs.Engine(domain,library),structure


class PublicOnlyProbe:
    identity='F_PUBLIC_PROBE'
    operation_id='probe.affine'
    ports=(fs.Port('input','input','float'),fs.Port('output','output','float'))
    alpha_schema=fs.AlphaSchema(('input_value',))
    metadata={}
    def __getattr__(self,name):
        if name in {'parents','kernel','formation_provenance','provenance','formation_recipe'}:
            raise AssertionError(f'Composer read forbidden internal field {name}')
        raise AttributeError(name)


class PublicMetadataProbe:
    """A metadata view that fails if Composer enumerates unrelated internals."""
    def get(self,key,default=None):
        if key!='public_operation_interface':
            raise AssertionError(f'Composer probed non-public metadata key {key}')
        return default
    def __iter__(self):
        raise AssertionError('Composer enumerated metadata containing formation internals')


class PublicOnlyLibrary:
    def __iter__(self):return iter((PublicOnlyProbe(),))


class TestGoalComposerPublicOperations(unittest.TestCase):
    def test_retrieval_compatibility_and_binding_are_public_only(self):
        PublicOnlyProbe.metadata=PublicMetadataProbe()
        composer=GoalDrivenComposer(PublicOnlyLibrary(),ExternalCandidateKV())
        goal=numeric_goal()
        operations=composer.retrieve_operations(goal)
        self.assertEqual([item.identity for item in operations],['F_PUBLIC_PROBE'])
        self.assertTrue(is_first_class_operation(operations[0]))
        self.assertEqual(composer.bind_operation(operations[0],goal),
                         {'input_value':{'goal_input':'input_value'}})
        edge,=composer.candidates(goal)
        self.assertEqual(edge['candidate_kind'],'operation_edge')
        self.assertNotIn('parents',edge['public_operation'])
        self.assertNotIn('kernel',edge['public_operation'])
        self.assertNotIn('provenance',edge['public_operation'])

    def test_search_counts_real_composition_child_dispatches(self):
        engine,target=numeric_engine_with_f()
        composer=GoalDrivenComposer(engine.library,ExternalCandidateKV())
        result=composer.search_operations(goal=numeric_goal(),engine=engine,
            X=NumericProblem(2.0,5.0,((3.0,7.0),)))
        self.assertEqual([action.structure_id for action in result['plan'].actions],[target.identity])
        self.assertGreater(result['search_metrics']['composition_expansions'],0)

    def test_autonomous_f_uses_a_true_first_class_search_edge(self):
        engine,target=numeric_engine_with_f()
        library_hash_before=engine.library.persistent_hash
        target_hash_before=target.persistent_hash
        composer=GoalDrivenComposer(engine.library,ExternalCandidateKV())
        result=composer.search_operations(goal=numeric_goal(),engine=engine,
            X=NumericProblem(2.0,5.0,((3.0,7.0),)))
        self.assertTrue(result['plan'].success)
        self.assertTrue(result['verification'].passed)
        self.assertEqual([action.structure_id for action in result['plan'].actions],[target.identity])
        self.assertEqual(engine.library.persistent_hash,library_hash_before)
        self.assertEqual(target.persistent_hash,target_hash_before)
        events=[event['event'] for event in composer.search_trace
            if event.get('structure_id')==target.identity]
        for required in ('RETRIEVE_OPERATION','SELECT_OPERATION','BIND_PUBLIC_SLOT',
                         'EXPAND_OPERATION_EDGE'):
            self.assertIn(required,events)
        self.assertLess(events.index('RETRIEVE_OPERATION'),events.index('SELECT_OPERATION'))
        self.assertLess(events.index('SELECT_OPERATION'),events.index('BIND_PUBLIC_SLOT'))
        self.assertLess(events.index('BIND_PUBLIC_SLOT'),events.index('EXPAND_OPERATION_EDGE'))
        for structure_id in ('f_double','f_increment'):
            operation_events=[event['event'] for event in composer.search_trace
                if event.get('structure_id')==structure_id]
            for required in ('RETRIEVE_OPERATION','SELECT_OPERATION','BIND_PUBLIC_SLOT',
                             'EXPAND_OPERATION_EDGE'):
                self.assertIn(required,operation_events)

    def test_formation_origin_does_not_change_composer_path(self):
        engine,autonomous=numeric_engine_with_f()
        ordinary=replace(autonomous,formation_provenance=FormationProvenance(
            'ordinary composition',autonomous.parents))
        ordinary_view=normalize_operation(ordinary)
        autonomous_view=normalize_operation(autonomous)
        self.assertEqual(ordinary_view,autonomous_view)
        self.assertTrue(is_first_class_operation(ordinary_view))
        goal=numeric_goal()
        self.assertEqual(composer_eligibility(engine.library,ordinary_view,goal),
                         composer_eligibility(engine.library,autonomous_view,goal))
        composer=GoalDrivenComposer(engine.library,ExternalCandidateKV())
        self.assertEqual(composer.bind_operation(ordinary_view,goal),
                         composer.bind_operation(autonomous_view,goal))
        ordinary_library=fs.Library()
        for structure in engine.library:
            candidate=ordinary if structure.identity==autonomous.identity else structure
            ordinary_library.commit(candidate,VerificationRecord(True,'test verifier',digest(candidate.kernel)))
        ordinary_engine=fs.Engine(NumericDomain(),ordinary_library)
        ordinary_composer=GoalDrivenComposer(ordinary_library,ExternalCandidateKV())
        X=NumericProblem(2.0,5.0,((3.0,7.0),))
        ordinary_result=ordinary_composer.search_operations(goal,ordinary_engine,X)
        autonomous_result=composer.search_operations(goal,engine,X)
        self.assertEqual(ordinary_result['plan'].actions,autonomous_result['plan'].actions)
        self.assertEqual(ordinary_result['verification'].passed,autonomous_result['verification'].passed)
        ordinary_events=[event for event in ordinary_composer.search_trace
                         if event.get('structure_id')==ordinary.identity]
        autonomous_events=[event for event in composer.search_trace
                           if event.get('structure_id')==autonomous.identity]
        self.assertEqual(ordinary_events,autonomous_events)

    def test_library_and_kv_cold_restart_keeps_kv_operation_first_class(self):
        engine,target=numeric_engine_with_f()
        base=fs.Library()
        for structure in engine.library:
            if structure.identity==target.identity:continue
            base.commit(structure,VerificationRecord(True,'test verifier',digest(structure.kernel)))
        with tempfile.TemporaryDirectory() as temp:
            library_path=f'{temp}/library.json'
            kv_path=f'{temp}/candidate-kv.json'
            base.save(library_path)
            kv=ExternalCandidateKV(kv_path)
            kv.add('candidate-f',target,operation_signature=target.operation_id,
                   candidate_graph={},x_leakage_status='ZERO')
            for status in ('VALIDATED','PROMOTABLE','PERSISTENT'):
                kv.transition('candidate-f',status)
            kv.save()
            cold_library=fs.Library.load(library_path)
            cold_kv=ExternalCandidateKV.load(kv_path)
            cold_engine=fs.Engine(NumericDomain(),cold_library)
            composer=GoalDrivenComposer(cold_library,cold_kv)
            result=composer.search_operations(numeric_goal(),cold_engine,
                NumericProblem(2.0,5.0,((3.0,7.0),)))
            self.assertTrue(result['verification'].passed)
            self.assertEqual([a.structure_id for a in result['plan'].actions],[target.identity])
            self.assertIn(target.identity,[op.identity for op in result['public_pool']])
            events=[event for event in composer.search_trace
                    if event.get('structure_id')==target.identity]
            names=[event['event'] for event in events]
            self.assertLess(names.index('RETRIEVE_OPERATION'),names.index('SELECT_OPERATION'))
            self.assertLess(names.index('SELECT_OPERATION'),names.index('BIND_PUBLIC_SLOT'))
            self.assertLess(names.index('BIND_PUBLIC_SLOT'),names.index('MATERIALIZE_SELECTED_KV_OPERATION'))
            self.assertLess(names.index('MATERIALIZE_SELECTED_KV_OPERATION'),names.index('EXPAND_OPERATION_EDGE'))


def composer_eligibility(library,operation,goal):
    composer=GoalDrivenComposer(library,ExternalCandidateKV())
    return [item.identity for item in composer.compatible_operations([operation],goal)]
