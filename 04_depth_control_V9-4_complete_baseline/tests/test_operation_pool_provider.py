from .common import *

import tempfile
from pathlib import Path

from fsys.external_kv import ExternalCandidateKV
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.operation_pool import OperationPoolProvider
from fsys.preference import UsageRegistry
from fsys.serialization import digest
from fsys.verifier import VerificationRecord


def _goal():
    inputs=({'name':'value','type':'float'},)
    return TaskSpec('operation-pool-probe',inputs,{'type':'float'},(),
                    digest({'inputs':inputs,'output':'float'}))


def _lifecycle_engine():
    domain=NumericDomain();library=domain.bootstrap()
    library.record_equivalence('f_double','f_increment',{'test_only':'lifecycle routing fixture'})
    usage=UsageRegistry(library)
    receipt=VerificationRecord(True,'operation-pool test verifier','passed fixture receipt')
    usage.record_successful_path(('f_double',)*3,receipt)
    return fs.Engine(domain,library,usage_registry=usage)


class _UsageBoundary:
    def __init__(self,library):
        self.library=library
    def active_ids(self):return frozenset(('F_PUBLIC_PROBE',))
    def retired_ids(self):return frozenset()
    def __getattr__(self,name):
        if name in {'_counts','_current','count','counts','status','snapshot'}:
            raise AssertionError('Composer reached UsageRegistry internals')
        raise AttributeError(name)


class _PublicProbe:
    identity='F_PUBLIC_PROBE';operation_id='probe.numeric'
    ports=(fs.Port('value_in','input','float'),fs.Port('value_out','output','float'))
    alpha_schema=fs.AlphaSchema(('input_value',))
    metadata={}
    def __getattr__(self,name):
        if name in {'parents','kernel','formation_provenance','provenance','formation_recipe'}:
            raise AssertionError('Composer reached an operation implementation field')
        raise AttributeError(name)


class _PublicProbeLibrary:
    def __iter__(self):return iter((_PublicProbe(),))


class TestOperationPoolProvider(unittest.TestCase):
    def test_active_full_and_retired_views(self):
        engine=_lifecycle_engine();provider=engine.operation_pool;goal=_goal()
        self.assertEqual({op.identity for op in provider.get_operations(goal,lifecycle_view='ACTIVE')},
                         {'f_double'})
        self.assertEqual({op.identity for op in provider.get_operations(goal,lifecycle_view='FULL')},
                         {'f_double','f_increment'})
        self.assertEqual({op.identity for op in provider.get_operations(goal,lifecycle_view='RETIRED')},
                         {'f_increment'})

    def test_composer_default_exposure_uses_active_public_pool(self):
        engine=_lifecycle_engine();goal=_goal()
        composer=GoalDrivenComposer(engine.library,ExternalCandidateKV(),
            operation_pool=engine.operation_pool)
        self.assertEqual({op.identity for op in composer.retrieve_operations(goal)},
                         {'f_double'})
        self.assertEqual({op.identity for op in composer.retrieve_operations(
            goal,lifecycle_view='FULL')},{'f_double','f_increment'})

    def test_retired_fallback_remains_available_in_engine_and_planner(self):
        engine=_lifecycle_engine();problem=NumericProblem(2.0,3.0)
        direct=engine.solve(problem)
        self.assertEqual(direct.status,'FULL_HIT')
        self.assertEqual(direct.structure_id,'f_increment')
        self.assertTrue(direct.metrics['preference']['retired_fallback_used'])

        context=engine.context(problem)
        planner=engine.planner;stages=[];original=planner._plan_stage
        def capture_stage(ctx,anchors,allowed_ids):
            stages.append(set(allowed_ids))
            return original(ctx,anchors,allowed_ids)
        planner._plan_stage=capture_stage
        plan=engine.planner.plan(context)
        self.assertTrue(plan.success)
        self.assertEqual(tuple(action.structure_id for action in plan.actions),('f_increment',))
        self.assertTrue(plan.metrics['retired_fallback_used'])
        active_ids={item.identity for item in engine.search_exposure('ACTIVE')}
        retired_ids={item.identity for item in engine.search_exposure('RETIRED')}
        self.assertEqual(stages,[active_ids,retired_ids])
        self.assertEqual(set(engine.planner.operation_pool_ids(context,'ACTIVE')),
                         {s.identity for s in engine.search_exposure('ACTIVE')})

    def test_all_search_entrypoints_share_provider_exposure(self):
        engine=_lifecycle_engine();goal=_goal();problem=NumericProblem(2.0,3.0)
        provider_ids={op.identity for op in engine.operation_pool.get_operations(goal,lifecycle_view='ACTIVE')}
        composer=GoalDrivenComposer(engine.library,ExternalCandidateKV(),operation_pool=engine.operation_pool)
        self.assertEqual({op.identity for op in composer.retrieve_operations(goal)},provider_ids)
        result=composer.search_operations(goal,engine,problem,lifecycle_view='ACTIVE')
        self.assertEqual({op.identity for op in result['public_pool']},provider_ids)
        self.assertEqual(set(engine.planner.operation_pool_ids(engine.context(problem),'ACTIVE')),
                         {s.identity for s in engine.search_exposure('ACTIVE')})
        self.assertEqual({s.identity for s in engine.retrieval_search_exposure('ACTIVE')},
                         {s.identity for s in engine.search_exposure('ACTIVE')})

    def test_composer_only_receives_public_contract_and_not_lifecycle_or_graph_fields(self):
        library=_PublicProbeLibrary();usage=_UsageBoundary(library)
        provider=OperationPoolProvider(library,usage)
        composer=GoalDrivenComposer(library,ExternalCandidateKV(),operation_pool=provider)
        operations=composer.retrieve_operations(_goal())
        self.assertEqual([item.identity for item in operations],['F_PUBLIC_PROBE'])
        edge,=composer.candidates(_goal())
        self.assertEqual(edge['candidate_kind'],'operation_edge')
        self.assertNotIn('kernel',edge['public_operation'])
        self.assertNotIn('parents',edge['public_operation'])

    def test_retired_f_remains_loadable_executable_and_cold_reloadable(self):
        engine=_lifecycle_engine();retired_id='f_increment';before=engine.library[retired_id].formation_provenance
        self.assertIn(retired_id,engine.usage.retired_ids())
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'library.json';engine.save_knowledge(path)
            loaded=fs.Library.load(path);restored=fs.Engine(NumericDomain(),loaded)
            self.assertTrue(restored.load_usage_sidecar(path))
            self.assertEqual(restored.usage.status(retired_id),'RETIRED')
            self.assertEqual(loaded[retired_id].formation_provenance,before)
            artifact=restored.execute(loaded[retired_id],NumericProblem(2.0,3.0))
            self.assertAlmostEqual(artifact.value,3.0)
            self.assertTrue(restored.verifier.verify(
                artifact,restored.context(NumericProblem(2.0,3.0))).passed)

    def test_missing_sidecar_rebuilds_the_provider_with_the_new_registry(self):
        engine=_lifecycle_engine();old_provider=engine.operation_pool
        with tempfile.TemporaryDirectory() as folder:
            self.assertFalse(engine.load_usage_sidecar(Path(folder)/'missing.json'))
        self.assertIsNot(engine.operation_pool,old_provider)
        self.assertIs(engine.operation_pool.usage_registry,engine.usage)
