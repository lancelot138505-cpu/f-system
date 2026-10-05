from .common import *

import tempfile
from pathlib import Path

from fsys.admission import audit_formation_parents
from fsys.external_kv import ExternalCandidateKV
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.library import Library
from fsys.operation_pool import OperationPoolProvider
from fsys.preference import UsageRegistry
from fsys.public_operations import is_first_class_operation, normalize_operation
from fsys.recursive_f_runtime import RecursiveFExecutor, program_snapshot
from fsys.serialization import digest, thaw
from fsys.token_goal_domain import publish_recursive_operation_contracts
from fsys.verifier import VerificationRecord

from experiments.v94_search_space_baseline import (
    _first_class_public_audit, candidate_exposure, classify_chain_path, clone_library,
    cold_execute_saved, task_goal,
)
from experiments.search_space_baseline_oracles import FOUNDATIONS, chain_tasks


LIBRARY_PATH = ROOT / 'artifacts' / 'clean_strict_library' / 'library_final.json'
USAGE_PATH = ROOT / 'artifacts' / 'clean_strict_library' / 'usage_final.json'
RUNTIME_CONTEXT = {'STATE': {}, 'INDEX': None, 'BOOLEAN': None}


def _clean():
    library = Library.load(LIBRARY_PATH)
    usage = UsageRegistry.load(USAGE_PATH, library)
    contracts = publish_recursive_operation_contracts(library)
    return library, usage, contracts


def _foundation_root(library, oracle):
    inputs = [[], [1], [1, 3, 2], [5, 4, 3, 2], [-2, -1, 0, 1]]
    executor = RecursiveFExecutor(library)
    matches = []
    for obj in library:
        if not thaw(obj.kernel).get('executable'):
            continue
        try:
            actual = [executor.execute(obj.identity, [xs]) for xs in inputs]
        except (ValueError, KeyError, TypeError, IndexError, LookupError, RuntimeError):
            continue
        if actual == [oracle(xs) for xs in inputs]:
            matches.append(obj.identity)
    if not matches:
        raise AssertionError('clean Library lacks the requested oracle operation')
    return sorted(matches)[0]


class TestSearchSpaceBaseline(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.library, cls.usage, cls.contracts = _clean()
        cls.definitions, cls.oracles = chain_tasks()

    def test_01_clean_counts_separate_executable_and_public_exposure(self):
        executable = [obj for obj in self.library if (thaw(obj.kernel).get('executable')
            or obj.metadata.get('translation_primitive') or obj.metadata.get('translation_rule')
            or obj.metadata.get('translation_dispatch') or obj.metadata.get('exec_capability'))]
        composites = [obj for obj in self.library if obj.level > 0 and obj.metadata.get('role') != 'topology']
        provider = OperationPoolProvider(self.library, self.usage, public_contracts=self.contracts)
        goal = task_goal(FOUNDATIONS[0]['oracle'])
        self.assertEqual(len(self.library), 26)
        self.assertEqual(len(executable), 24)
        self.assertEqual(len(composites), 20)
        self.assertEqual(sum(audit_formation_parents(obj, self.library)['passed'] for obj in composites), 20)
        self.assertEqual(len(self.usage.active_ids()), 26)
        self.assertEqual(len(self.usage.retired_ids()), 0)
        self.assertEqual(len(provider.get_operations(goal, lifecycle_view='FULL')), 7)
        self.assertEqual(len(provider.get_operations(goal, lifecycle_view='ACTIVE')), 7)

    def test_02_all_lifecycle_views_flow_through_the_same_provider(self):
        domain = NumericDomain()
        library = domain.bootstrap()
        library.record_equivalence('f_double', 'f_increment', {'evidence': 'fixture receipt'})
        usage = UsageRegistry(library)
        usage.record_successful_path(('f_double',) * 2,
            VerificationRecord(True, 'test verifier', 'fixture passed'))
        provider = OperationPoolProvider(library, usage)
        goal = TaskSpec('lifecycle', ({'name': 'value', 'type': 'float'},), {'type': 'float'}, (),
                        digest({'goal': 'float -> float'}))
        views = {view: {item.identity for item in provider.get_operations(goal, lifecycle_view=view)}
                 for view in ('ACTIVE', 'FULL', 'RETIRED')}
        self.assertEqual(views['ACTIVE'], {'f_double'})
        self.assertEqual(views['FULL'], {'f_double', 'f_increment'})
        self.assertEqual(views['RETIRED'], {'f_increment'})
        composer = GoalDrivenComposer(library, ExternalCandidateKV(), operation_pool=provider)
        self.assertEqual({item.identity for item in composer.retrieve_operations(goal)}, views['ACTIVE'])

    def test_03_four_search_views_run_with_only_provider_view_or_mask_changed(self):
        goal = task_goal(FOUNDATIONS[0]['oracle'])
        target = _foundation_root(self.library, FOUNDATIONS[0]['oracle'])
        runs = {}
        for lifecycle in ('FULL', 'ACTIVE'):
            for reuse in ('EXPANDED', 'FIRST_CLASS'):
                excluded = (target,) if reuse == 'EXPANDED' else ()
                composer = GoalDrivenComposer(self.library, ExternalCandidateKV(),
                    public_contracts=self.contracts,
                    operation_pool=OperationPoolProvider(self.library, self.usage,
                        public_contracts=self.contracts))
                first = next(composer.candidates(goal, lifecycle_view=lifecycle,
                                                 excluded_ids=excluded))
                runs[lifecycle + '+' + reuse] = {
                    'candidate_kind': first['candidate_kind'],
                    'candidate_id': first.get('root_id'),
                    'lifecycle': lifecycle,
                    'excluded': list(excluded),
                    'provider_pool': [row['structure_id'] for row in composer.search_trace[0]['candidate_structures']],
                }
        self.assertEqual(set(runs), {'FULL+EXPANDED', 'FULL+FIRST_CLASS',
                                     'ACTIVE+EXPANDED', 'ACTIVE+FIRST_CLASS'})
        self.assertEqual(runs['FULL+FIRST_CLASS']['candidate_id'], runs['ACTIVE+FIRST_CLASS']['candidate_id'])
        self.assertIn(target, runs['FULL+FIRST_CLASS']['provider_pool'])
        self.assertNotIn(target, runs['FULL+EXPANDED']['provider_pool'])
        self.assertEqual(runs['FULL+FIRST_CLASS']['candidate_kind'], 'operation_edge')
        self.assertEqual(runs['ACTIVE+FIRST_CLASS']['candidate_kind'], 'operation_edge')

    def test_04_first_class_edge_selects_then_uses_only_public_formal_slots(self):
        target = _foundation_root(self.library, FOUNDATIONS[0]['oracle'])
        goal = task_goal(FOUNDATIONS[0]['oracle'])
        composer = GoalDrivenComposer(self.library, ExternalCandidateKV(),
            public_contracts=self.contracts,
            operation_pool=OperationPoolProvider(self.library, self.usage,
                public_contracts=self.contracts))
        candidate = next(item for item in composer.candidates(goal, lifecycle_view='FULL')
                         if item.get('candidate_kind') == 'operation_edge'
                         and item.get('root_id') == target)
        composer.execute_candidate(candidate, goal, goal.examples[2]['input'],
                                   runtime_context=RUNTIME_CONTEXT)
        audit = _first_class_public_audit({"candidate": candidate, "trace": composer.search_trace,
            "composer": composer}, target)
        self.assertTrue(audit['passes'], audit)
        self.assertEqual(set(audit['bound_slots']), set(audit['public_formal_slots']))
        self.assertEqual(audit['preselection_sensitive_fields_observed'], [])

    def test_05_retired_f_stays_persistent_executable_and_fallback_counts_it(self):
        domain = NumericDomain()
        library = domain.bootstrap()
        library.record_equivalence('f_double', 'f_increment', {'evidence': 'fixture receipt'})
        usage = UsageRegistry(library)
        usage.record_successful_path(('f_double',) * 2,
            VerificationRecord(True, 'test verifier', 'fixture passed'))
        engine = fs.Engine(domain, library, usage_registry=usage)
        result = engine.solve(NumericProblem(2.0, 3.0))
        self.assertEqual(result.structure_id, 'f_increment')
        self.assertTrue(result.metrics['preference']['retired_fallback_used'])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'library.json'
            engine.save_knowledge(path)
            restored = fs.Library.load(path)
            restored_engine = fs.Engine(NumericDomain(), restored)
            self.assertTrue(restored_engine.load_usage_sidecar(path))
            self.assertEqual(restored_engine.usage.status('f_increment'), 'RETIRED')
            self.assertAlmostEqual(restored_engine.execute(
                restored['f_increment'], NumericProblem(2.0, 3.0)).value, 3.0)

    def test_06_shortcut_classification_does_not_fail_exact_parent_admission(self):
        definition = self.definitions[0]
        root_id = _foundation_root(self.library, FOUNDATIONS[0]['oracle'])
        classification = classify_chain_path(self.library[root_id], self.library,
            ExternalCandidateKV(), definition, self.oracles,
            [[], [1, 2], [4, 3, 2]])
        self.assertEqual(classification['status'], 'SHORTCUT_FOUND')
        audit = audit_formation_parents(self.library[root_id], self.library)
        self.assertTrue(audit['passed'], audit['issues'])

    def test_07_recipe_generated_parent_set_is_the_exact_executable_call_set(self):
        goal = task_goal(FOUNDATIONS[0]['oracle'])
        composer = GoalDrivenComposer(self.library, ExternalCandidateKV(),
            public_contracts=self.contracts,
            operation_pool=OperationPoolProvider(self.library, self.usage,
                public_contracts=self.contracts))
        recipe_candidate = next(row for row in composer.candidates(goal)
                                if row.get('candidate_kind') == 'recipe_composition')
        from fsys.structure import Structure
        for role in ('step', 'root'):
            candidate_id = recipe_candidate['candidate_ids'][role + '_candidate_id']
            record = composer.kv.get(candidate_id)
            obj = Structure.from_dict(record['structure'])
            executable = thaw(obj.kernel)['executable']
            refs = []
            def collect(value):
                if isinstance(value, dict):
                    for key, child in value.items():
                        if key in ('f', 'body_f', 'left_f', 'right_f') and isinstance(child, str):
                            refs.append(child)
                        collect(child)
                elif isinstance(value, list):
                    for child in value:
                        collect(child)
            collect(executable)
            direct = tuple(dict.fromkeys(refs))
            self.assertEqual(tuple(obj.parents), tuple(dict.fromkeys((recipe_candidate['topology_id'], *direct))))
            recipe = thaw(obj.kernel)['formation_recipe']
            self.assertEqual(set(recipe['operation_bindings'].values()), set(direct))
            if role == 'step':
                audit = audit_formation_parents(obj, self.library, record['formation_path'])
                self.assertTrue(audit['passed'], audit['issues'])
                step_obj = obj
        root_id = recipe_candidate['candidate_ids']['root_candidate_id']
        root_record = composer.kv.get(root_id)
        root_obj = Structure.from_dict(root_record['structure'])
        branch = clone_library(self.library)
        branch._commit_verified(step_obj, VerificationRecord(True, 'test branch materialization',
                                                               digest(step_obj.kernel)))
        root_audit = audit_formation_parents(root_obj, branch, root_record['formation_path'])
        self.assertTrue(root_audit['passed'], root_audit['issues'])

    def test_08_python_javascript_and_oracle_match_for_a_persisted_root(self):
        target = _foundation_root(self.library, FOUNDATIONS[0]['oracle'])
        inputs = [[], [1], [1, 2, 1], [3, 2, 1]]
        expected = [FOUNDATIONS[0]['oracle'](xs) for xs in inputs]
        goal = task_goal(FOUNDATIONS[0]['oracle'])
        composer = GoalDrivenComposer(self.library, ExternalCandidateKV(),
            public_contracts=self.contracts)
        edge = next(row for row in composer.candidates(goal)
                    if row.get('candidate_kind') == 'operation_edge' and row.get('root_id') == target)
        python = [composer.execute_candidate(edge, goal, [xs], runtime_context=RUNTIME_CONTEXT)
                  for xs in inputs]
        from experiments.v94_search_space_baseline import js_batch
        javascript = js_batch(program_snapshot(self.library, target), inputs)
        self.assertEqual(python, expected)
        self.assertEqual(javascript, expected)

    def test_09_cold_process_reload_preserves_semantics(self):
        target = _foundation_root(self.library, FOUNDATIONS[0]['oracle'])
        inputs = [[], [1, 3, 2], [4, 5, 6, 2]]
        expected = [FOUNDATIONS[0]['oracle'](xs) for xs in inputs]
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / 'library.json'
            self.library.save(path)
            self.usage.save(path.with_suffix('.usage.json'))
            self.assertEqual(cold_execute_saved(path, target, inputs), expected)

    def test_10_higher_order_f_remains_an_ordinary_binary_child(self):
        _, oracles = chain_tasks()
        goal = task_goal(oracles['D01'])
        composer = GoalDrivenComposer(self.library, ExternalCandidateKV(),
            public_contracts=self.contracts,
            operation_pool=OperationPoolProvider(self.library, self.usage,
                public_contracts=self.contracts))
        binary = next(row for row in composer.candidates(goal)
                      if row.get('candidate_kind') == 'public_binary_operation_composition')
        self.assertTrue(all(self.library[sid].level >= 3 for sid in binary['child_operation_ids']))
        root_candidate_id = binary['candidate_ids']['root_candidate_id']
        record = composer.kv.get(root_candidate_id)
        root = fs.Structure.from_dict(record['structure'])
        self.assertEqual(tuple(root.parents), tuple(binary['child_operation_ids']))
        self.assertTrue(audit_formation_parents(root, self.library, record['formation_path'])['passed'])
        for role, child_id in zip(('left', 'right'), binary['child_operation_ids']):
            interface = composer._interfaces[child_id]
            self.assertEqual(set(binary['child_bindings'][role]), set(interface.formal_slots))

    def test_11_clean_equivalence_is_explicit_and_no_retirement_is_inferred(self):
        self.assertEqual(len(self.library.equivalence), 0)
        self.assertTrue(all(len(group) == 1 for group in self.library.equivalence_classes()))
        self.assertEqual(self.usage.active_ids(), frozenset(obj.identity for obj in self.library))
        self.assertEqual(self.usage.retired_ids(), frozenset())


if __name__ == '__main__':
    unittest.main()
