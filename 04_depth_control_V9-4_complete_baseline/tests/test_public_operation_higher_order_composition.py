import json
import unittest
from pathlib import Path

from fsys.external_kv import ExternalCandidateKV
from fsys.admission import audit_formation_parents
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.library import Library
from fsys.public_operations import is_first_class_operation,normalize_operation
from fsys.token_goal_domain import publish_recursive_operation_contracts


ROOT=Path(__file__).resolve().parents[1]
LIBRARY_PATH=ROOT/'artifacts/clean_strict_library/library_final.json'
SUMMARY_PATH=ROOT/'artifacts/clean_strict_library/summary.json'
GOALS_PATH=ROOT/'data/higher_order_goals.json'


class TestPublicOperationHigherOrderComposition(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.goals={row['id']:row for row in json.loads(GOALS_PATH.read_text(encoding='utf-8'))['later_goals']}
        cls.root_map=json.loads(SUMMARY_PATH.read_text(encoding='utf-8'))['goal_root_map']

    def _goal(self,record):
        ns={};exec(record['source'],ns,ns);fn=ns[record['later_function']]
        examples=record['examples']
        signature='sequence-int-to-int'
        goal=TaskSpec(record['id'],({'name':'nums','type':'sequence<int>'},),{'type':'int'},
            tuple({'input':[xs],'output':fn(xs)} for xs in examples),signature)
        return goal,fn,examples

    def test_persisted_f_is_a_real_public_child_and_kv_candidates_are_excluded(self):
        record=self.goals['HG01'];goal,fn,examples=self._goal(record)
        library=Library.load(LIBRARY_PATH);kv=ExternalCandidateKV()
        composer=GoalDrivenComposer(library,kv,public_contracts=publish_recursive_operation_contracts(library))
        selected=None
        for candidate in composer.candidates(goal):
            try:
                values=[composer.execute_candidate(candidate,goal,[xs],runtime_context={
                    'STATE':{},'INDEX':None,'BOOLEAN':None}) for xs in examples]
            except (ValueError,KeyError,TypeError,IndexError,LookupError,RuntimeError):
                values=[]
            if values==[fn(xs) for xs in examples]:
                selected=candidate;break
        self.assertIsNotNone(selected)
        self.assertEqual(selected['candidate_kind'],'public_binary_operation_composition')
        self.assertEqual(selected['child_operation_ids'],[
            self.root_map['G01'],self.root_map['G05']])
        pair_event=next(e for e in composer.search_trace if e['event']=='ENUMERATE_PUBLIC_OPERATION_PAIRS')
        self.assertTrue(pair_event['library_only'])
        self.assertTrue(set(pair_event['pool_ids']).issubset({s.identity for s in library}))
        self.assertNotIn(selected['root_id'],pair_event['pool_ids'])
        for child_id in selected['child_operation_ids']:
            self.assertTrue(any(e['event']=='RETRIEVE_OPERATION' and e.get('structure_id')==child_id
                for e in composer.search_trace))
            self.assertTrue(any(e['event']=='SELECT_OPERATION' and e.get('structure_id')==child_id
                and e.get('selected_as')=='child' for e in composer.search_trace))
            self.assertTrue(any(e['event']=='BIND_PUBLIC_SLOTS' and e.get('structure_id')==child_id
                and e.get('selected_as')=='child' for e in composer.search_trace))
            self.assertTrue(any(e['event']=='USE_AS_CHILD_OPERATION' and e.get('structure_id')==child_id
                and e.get('parent_id')==selected['root_id'] for e in composer.search_trace))

    def test_masked_f_is_not_in_public_pair_pool(self):
        record=self.goals['HG01'];goal,_,_=self._goal(record)
        library=Library.load(LIBRARY_PATH)
        composer=GoalDrivenComposer(library,ExternalCandidateKV(),
            public_contracts=publish_recursive_operation_contracts(library))
        target=self.root_map['G01']
        list(composer.candidates(goal,excluded_ids=(target,)))
        pair_event=next(e for e in composer.search_trace if e['event']=='ENUMERATE_PUBLIC_OPERATION_PAIRS')
        self.assertNotIn(target,pair_event['pool_ids'])

    def test_clean_strict_root_has_exact_persistent_parents(self):
        library=Library.load(LIBRARY_PATH)
        target=self.root_map['G01']
        audit=audit_formation_parents(library[target],library)
        self.assertTrue(audit['passed'],audit['issues'])
        self.assertEqual(tuple(library[target].parents),tuple(audit['expected_parents']))
        self.assertTrue(all(parent in library._structures for parent in library[target].parents))
        self.assertTrue(is_first_class_operation(normalize_operation(library[target],
            publish_recursive_operation_contracts(library).get(target))))


if __name__=='__main__':
    unittest.main()
