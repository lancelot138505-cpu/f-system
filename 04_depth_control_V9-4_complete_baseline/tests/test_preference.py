from tempfile import TemporaryDirectory
from pathlib import Path

from .common import *
from fsys.domains.numeric import PORTS
from fsys.provenance import FormationProvenance
from fsys.verifier import VerificationRecord
from fsys.alpha import DirectStructuralSolver
from fsys.retrieval import RetrievalResult
from fsys.domain import Action
from fsys.search import PlanResult

PASS_RECEIPT=VerificationRecord(True,'synthetic successful-path verifier','test success receipt')

def record_path(engine,ids):return engine.usage.record_successful_path(ids,PASS_RECEIPT)


def equivalent_numeric_engine(n=2,edges=None):
    e=numeric_engine();parents=('f_double','f_increment');ids=[]
    for i in range(n):
        sid=f'F_EQ_{i:05d}'
        s=fs.Composite(sid,'numeric.sequence',PORTS,fs.AlphaSchema(('input_value',)),parents,1,
                       FormationProvenance('explicit test equivalence',parents,'test receipt','same verified numeric sequence'),
                       {'reference':'numeric.sequence'},{'call_plan':parents,'variant':i})
        e.library.commit(s,VerificationRecord(True,'test receipt','same verified numeric sequence'))
        ids.append(sid)
    edges=edges if edges is not None else tuple(zip(range(n-1),range(1,n)))
    for a,b in edges:e.library.record_equivalence(ids[a],ids[b],{'explicit':'same numeric sequence'})
    return e,ids


class RejectOneAlpha:
    def __init__(self,domain,structure_id):self.delegate=DirectStructuralSolver(domain);self.structure_id=structure_id
    def solve(self,structure,X,context=None):
        if structure.identity==self.structure_id:raise ValueError('active candidate binding rejected for this instance')
        return self.delegate.solve(structure,X,context)


class FailVerifier(fs.Verifier):
    def verify(self,artifact,context=None):return VerificationRecord(False,'intentional test failure','failed')

class MissAllRetrieval(fs.RetrievalStrategy):
    def retrieve(self,context):return RetrievalResult('TRUE_LIBRARY_MISS',metrics={'method':'test_forced_miss','before':0,'after':0,'wall_seconds':0.})

class PassOnSecondVerifier(fs.Verifier):
    def __init__(self):self.calls=0
    def verify(self,artifact,context=None):
        self.calls+=1
        return VerificationRecord(self.calls==2,'scripted verification','pass on fallback' if self.calls==2 else 'forced preferred-stage miss')

class ScriptedPlanner(fs.DepthPlanner):
    def __init__(self,preferred_id,retired_id):super().__init__();self.preferred_id=preferred_id;self.retired_id=retired_id
    @staticmethod
    def _result(sid,stage):
        metrics={'expansions':1,'legal_actions':1,'repeated_states':0,'dead_end_expansions':0,
                 'candidate_probes':1,'structural_checks':1,'cache_hits':0,'whole_F_calls':1,'primitive_calls':0,
                 'candidate_generation_seconds':0.,'ranking_seconds':0.,'wall_seconds':0.,
                 'wall_seconds_per_expansion':0.,'index_enabled':True,'cache_enabled':True,'alpha_updates_in_depth':0,
                 'preferred_candidates':1,'retired_candidates_deferred':1,'retired_fallback_used':False,
                 'equivalence_classes_seen':1,'active_switches':0,'preference_stage':stage}
        return PlanResult(True,(Action(sid,()),),'SUCCESS',metrics)
    def plan(self,context,anchors=()):return self._result(self.preferred_id,'preferred')
    def plan_fallback(self,context,anchors=()):return self._result(self.retired_id,'retired_fallback')


class TestPreference(unittest.TestCase):
    def test_a_equivalence_closure(self):
        e,ids=equivalent_numeric_engine(3)
        self.assertEqual(e.library.equivalence_class(ids[0]),tuple(ids))
        self.assertTrue(e.library.are_equivalent(ids[0],ids[2]))
        self.assertEqual(e.library.equivalence_classes(),tuple(sorted((tuple(ids),)+tuple((s.identity,) for s in e.library if s.identity not in ids),key=lambda c:c[0])))

    def test_b_usage_maximum_is_active(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]]*7+[ids[1]]*3)
        self.assertEqual(e.usage.status(ids[0]),'ACTIVE');self.assertEqual(e.usage.status(ids[1]),'RETIRED')

    def test_c_active_switches_after_successful_traversals(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]]*7+[ids[1]]*3)
        event=record_path(e,[ids[1]]*5)
        self.assertEqual(event['active_switches'],1);self.assertEqual(e.usage.status(ids[1]),'ACTIVE')

    def test_d_failed_searches_and_verification_failures_do_not_count(self):
        e,ids=equivalent_numeric_engine();before=e.usage.counts()
        e.solve(NumericProblem(3,99999.))
        self.assertEqual(e.usage.counts(),before)
        with self.assertRaisesRegex(ValueError,'passed verification receipt'):
            e.usage.record_successful_path([ids[0]],VerificationRecord(False,'failed test receipt',''))
        failing=fs.Engine(e.domain,e.library,verifier=FailVerifier())
        result=failing.solve(NumericProblem(3,7.))
        self.assertEqual(result.status,'VERIFY_FAIL');self.assertEqual(failing.usage.counts(),before)

    def test_e_repeated_node_counts_each_visit(self):
        e,ids=equivalent_numeric_engine();event=record_path(e,[ids[0],ids[1],ids[0]])
        self.assertEqual(event['increments'],{ids[0]:2,ids[1]:1})
        self.assertEqual(e.usage.count(ids[0]),2);self.assertEqual(e.usage.count(ids[1]),1)

    def test_f_active_candidate_is_searched_first(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]]*5+[ids[1]])
        result=e.solve(NumericProblem(3,7.))
        self.assertEqual(result.status,'FULL_HIT');self.assertEqual(result.structure_id,ids[0])
        full_hit_usage=next(row for row in result.context.trace if row.get('phase')=='usage_update')
        self.assertEqual(full_hit_usage['visited'],[ids[0]])
        self.assertEqual(result.metrics['preference']['preferred_candidates'],len([s for s in e.domain.retrieval_candidates(e.library) if e.usage.status(s.identity)=='ACTIVE']))
        self.assertEqual(result.metrics['preference']['retired_fallback_used'],False)
        e.retrieval=MissAllRetrieval();planned=e.solve(NumericProblem(3,7.))
        self.assertEqual(planned.status,'COMPLETION_VERIFIED')
        self.assertEqual(planned.metrics['depth']['preference_stage'],'preferred')
        usage=next(row for row in planned.context.trace if row.get('phase')=='usage_update')
        self.assertEqual(usage['visited'],[ids[0]])
        e.verifier=PassOnSecondVerifier();e.planner=ScriptedPlanner(ids[0],ids[1])
        verified_fallback=e.solve(NumericProblem(3,7.))
        self.assertEqual(verified_fallback.status,'COMPLETION_VERIFIED')
        self.assertTrue(verified_fallback.metrics['depth']['retired_fallback_used'])
        self.assertEqual(next(row for row in verified_fallback.context.trace if row.get('phase')=='usage_update')['visited'],[ids[1]])

    def test_g_retired_fallback_after_active_alpha_rejects(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]]*4)
        e.alpha_solver=RejectOneAlpha(e.domain,ids[0])
        result=e.solve(NumericProblem(3,7.))
        self.assertEqual(result.status,'FULL_HIT');self.assertEqual(result.structure_id,ids[1])
        self.assertTrue(result.metrics['preference']['retired_fallback_used'])
        self.assertEqual(e.usage.count(ids[1]),1)
        self.assertTrue(any(row.get('phase')=='usage_update' for row in result.context.trace))
        e.retrieval=MissAllRetrieval();e.verifier=PassOnSecondVerifier();e.planner=ScriptedPlanner(ids[0],ids[1])
        formed=e.learn(NumericProblem(3,7.))
        self.assertEqual(formed.status,'FORMED');self.assertEqual(formed.parents,(ids[1],))
        self.assertTrue(formed.metrics['depth']['retired_fallback_used'])
        self.assertEqual(e.usage.count(ids[0]),4);self.assertEqual(e.usage.count(ids[1]),2)

    def test_h_retired_structure_direct_execution_remains_valid(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]]*2)
        record_path(e,[ids[1]]*3)
        self.assertEqual(e.usage.status(ids[0]),'RETIRED')
        before=e.usage.counts()
        artifact=e.execute(e.library[ids[0]],NumericProblem(3,7.))
        self.assertAlmostEqual(artifact.value,7.)
        self.assertEqual(e.usage.counts(),before)  # direct execute has no verifier receipt

    def test_i_retired_parent_descendant_executes_verifies_and_reloads(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[1]]*3)
        parents=(ids[0],'f_increment')
        child=fs.Composite('F_CHILD_RETIRED_PARENT','numeric.sequence',PORTS,fs.AlphaSchema(('input_value',)),parents,2,
                           FormationProvenance('descendant test',parents,'test receipt','child certificate'),
                           {'reference':'numeric.sequence'},{'call_plan':parents})
        e.library.commit(child,VerificationRecord(True,'test receipt','child certificate'))
        self.assertEqual(e.usage.status(ids[0]),'RETIRED')
        with TemporaryDirectory() as td:
            path=Path(td)/'knowledge.json';e.save_knowledge(path)
            lib=fs.Library.load(path);loaded=fs.Engine(NumericDomain(),lib)
            self.assertTrue(loaded.load_usage_sidecar(path));self.assertEqual(loaded.usage.status(ids[0]),'RETIRED')
            artifact=loaded.execute(lib[child.identity],NumericProblem(3,8.))
            self.assertAlmostEqual(artifact.value,8.)
            self.assertTrue(loaded.verifier.verify(artifact,loaded.context(NumericProblem(3,8.))).passed)

    def test_j_usage_does_not_change_structure_hash_or_provenance(self):
        e,ids=equivalent_numeric_engine();s=e.library[ids[0]];hash_before=s.persistent_hash;prov_before=s.formation_provenance
        library_hash_before=e.library.persistent_hash;version_before=e.library.version
        record_path(e,[ids[1]]*3);record_path(e,[ids[0]]*5)
        self.assertEqual(s.persistent_hash,hash_before);self.assertEqual(s.formation_provenance,prov_before)
        self.assertEqual(e.library[ids[0]].persistent_hash,hash_before)
        self.assertEqual(e.library.persistent_hash,library_hash_before);self.assertEqual(e.library.version,version_before)

    def test_k_missing_sidecar_keeps_knowledge_equivalence_and_execution(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[1]]*3)
        with TemporaryDirectory() as td:
            path=Path(td)/'knowledge.json';sidecar=e.save_knowledge(path);sidecar.unlink()
            lib=fs.Library.load(path);self.assertEqual(len(lib),len(e.library))
            self.assertEqual(lib.equivalence_class(ids[0]),tuple(ids));self.assertEqual(lib[ids[0]].formation_provenance,e.library[ids[0]].formation_provenance)
            loaded=fs.Engine(NumericDomain(),lib);self.assertFalse(loaded.load_usage_sidecar(path))
            artifact=loaded.execute(lib[ids[0]],NumericProblem(3,7.))
            self.assertTrue(loaded.verifier.verify(artifact,loaded.context(NumericProblem(3,7.))).passed)
            self.assertEqual(loaded.usage.count(ids[1]),0)

    def test_l_usage_sidecar_restores_counts_and_status(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]]*7+[ids[1]]*3)
        with TemporaryDirectory() as td:
            path=Path(td)/'knowledge.json';sidecar=e.save_knowledge(path)
            self.assertEqual(path.name,'knowledge.json')
            self.assertEqual(sidecar.name,'knowledge.usage.json')
            lib=fs.Library.load(path);loaded=fs.Engine(NumericDomain(),lib);self.assertTrue(loaded.load_usage_sidecar(path))
            self.assertEqual(loaded.usage.counts(),e.usage.counts())
            self.assertEqual(loaded.usage.status(ids[0]),e.usage.status(ids[0]))
            self.assertEqual(loaded.usage.status(ids[1]),e.usage.status(ids[1]))

    def test_m_library_hash_mismatch_rejects_usage_sidecar(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[0]])
        with TemporaryDirectory() as td:
            path=Path(td)/'usage.json';e.usage.save(path)
            other=numeric_engine()
            with self.assertRaisesRegex(ValueError,'library hash mismatch'):fs.UsageRegistry.load(path,other.library)

    def test_n_equivalence_class_merge_preserves_counts_and_active(self):
        e,ids=equivalent_numeric_engine(4,edges=((0,1),(2,3)))
        record_path(e,[ids[0]]*7+[ids[1]]*2+[ids[2]]*3+[ids[3]]*9)
        before={sid:e.usage.count(sid) for sid in ids}
        e.library.record_equivalence(ids[1],ids[2],{'proof':'bridge'})
        self.assertEqual(e.library.equivalence_class(ids[0]),tuple(ids))
        self.assertEqual({sid:e.usage.count(sid) for sid in ids},before)
        self.assertEqual(e.usage.active_member(tuple(ids)),ids[3])

    def test_o_preference_switch_reuses_structure_index(self):
        e,ids=equivalent_numeric_engine();rebuilds=[0];original=e.index.rebuild
        def counted_rebuild():rebuilds.__setitem__(0,rebuilds[0]+1);return original()
        e.index.rebuild=counted_rebuild;e.index.query(None)
        version=e.index.version;buckets=e.index.buckets;build_count=rebuilds[0]
        record_path(e,[ids[0]]*5+[ids[1]]*3)
        record_path(e,[ids[1]]*3)
        e.index.query(None)
        self.assertEqual(e.index.version,version);self.assertIs(e.index.buckets,buckets);self.assertEqual(rebuilds[0],build_count)
        self.assertEqual(e.usage.status(ids[1]),'ACTIVE')

    def test_t_tie_keeps_current_active_then_uses_lexical_on_cold_start(self):
        e,ids=equivalent_numeric_engine();record_path(e,[ids[1]])
        record_path(e,[ids[0]])
        self.assertEqual(e.usage.active_member(tuple(ids)),ids[1])
        cold=fs.UsageRegistry(e.library);self.assertEqual(cold.active_member(tuple(ids)),min(ids))
