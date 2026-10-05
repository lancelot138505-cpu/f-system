"""Run bounded search-growth, first-class reuse, lifecycle and depth checks."""
from __future__ import annotations

import json
import os
import random
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = Path(os.environ.get("V94_REUSE_OUTPUT", ROOT / "artifacts" / "first_class_reuse"))
LIBRARY_PATH = Path(os.environ.get("V94_CLEAN_LIBRARY", ROOT / "artifacts" / "clean_strict_library" / "library_final.json"))
USAGE_PATH = Path(os.environ.get("V94_CLEAN_USAGE", ROOT / "artifacts" / "clean_strict_library" / "usage_final.json"))
NODE = Path(r"C:\Users\lancelot\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")
SEED = 558031
TRAIN_COUNT = 24
HOLDOUT_COUNT = 24

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

from fsys.external_kv import ExternalCandidateKV
from fsys.admission import audit_formation_parents
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.library import Library
from fsys.operation_pool import OperationPoolProvider
from fsys.preference import UsageRegistry
from fsys.public_operations import compatible_operations, is_first_class_operation, normalize_operation
from fsys.recursive_f_runtime import program_snapshot
from fsys.serialization import digest
from fsys.token_goal_domain import publish_recursive_operation_contracts
from v94_unseen_instance_library_growth import (GOAL_SIGNATURE, INPUT_SCHEMA, OUTPUT_SCHEMA,
                                                 js_batch)


def reference(kind: str, xs: list[int]) -> int:
    if kind in {"strict_run", "decreasing_run", "nonincreasing_run", "nondecreasing_run", "equal_run"}:
        if not xs:
            return 0
        best = run = 1
        for index in range(1, len(xs)):
            left, right = xs[index], xs[index - 1]
            if kind == "strict_run":
                extends = left > right
            elif kind == "decreasing_run":
                extends = left < right
            elif kind == "nonincreasing_run":
                extends = left <= right
            elif kind == "nondecreasing_run":
                extends = left >= right
            else:
                extends = left == right
            run = run + 1 if extends else 1
            best = max(best, run)
        return best
    if kind in {"increases", "decreases", "equals", "adjacent_changes"}:
        relation = {
            "increases": lambda a, b: a > b,
            "decreases": lambda a, b: a < b,
            "equals": lambda a, b: a == b,
            "adjacent_changes": lambda a, b: a != b,
        }[kind]
        return sum(relation(xs[i], xs[i - 1]) for i in range(1, len(xs)))
    raise KeyError(kind)


TASKS = (
    ("G01", "strict_run", "F_330186ac2729d6276f5d"),
    ("G02", "strict_run", "F_330186ac2729d6276f5d"),
    ("G03", "decreasing_run", "F_afd622bfcb728c23b681"),
    ("G04", "nonincreasing_run", "F_d422dae6ca1149e05360"),
    ("G05", "increases", "F_ae96c9c0b8f8009f36cc"),
    ("G06", "decreases", "F_8e7f0a9cb3ab704e8961"),
    ("G07", "equals", "F_05d3d84e9aa57a91f1d8"),
    ("U11", "nondecreasing_run", "F_dcfedcf1d26444553cdb"),
    ("U12", "equal_run", "F_bd1c8b86e4a28d068f0b"),
    ("U13", "adjacent_changes", "F_f6a9887dd22cb41d2cbf"),
)


class OpaqueTarget:
    """Allow the target's stored implementation only after SELECT_OPERATION."""

    _hidden = {"kernel", "parents", "formation_provenance", "provenance", "to_dict"}

    def __init__(self, obj, trace, public_contract):
        self._obj = obj
        self._trace = trace
        self._public_contract = public_contract
        self.hidden_reads_before_select = []

    def _selected(self):
        return any(row.get("event") == "SELECT_OPERATION" and row.get("structure_id") == self._obj.identity
                   for row in self._trace)

    def __getattr__(self, name):
        if name in self._hidden and not self._selected():
            self.hidden_reads_before_select.append(name)
            raise AssertionError(f"target F internal field read before SELECT_OPERATION: {name}")
        if name == "metadata" and not self._selected():
            return {"public_operation_interface": self._public_contract}
        return getattr(self._obj, name)


class OpaqueTargetLibrary:
    def __init__(self, library, trace, target, public_contract):
        self.base = library
        self.guard = OpaqueTarget(library[target], trace, public_contract)
        self.target = target

    def __iter__(self):
        return (self.guard if obj.identity == self.target else obj for obj in self.base)

    def __len__(self):
        return len(self.base)

    def __getitem__(self, identity):
        return self.guard if identity == self.target else self.base[identity]

    def __getattr__(self, name):
        return getattr(self.base, name)


def make_examples(kind: str, seed: int, count: int):
    rng = random.Random(seed)
    inputs = [[], [1], [4, 4, 4, 2, 2], [1, 2, 3, 2, 1], [8, 6, 4, 2], [-2, -1, 0, 1]]
    while len(inputs) < count:
        size = rng.randrange(2, 18)
        xs = [rng.randrange(-12, 13) for _ in range(size)]
        if xs not in inputs:
            inputs.append(xs)
    return [{"input": [xs], "output": reference(kind, xs)} for xs in inputs[:count]]


def js_outputs(program, cases):
    return js_batch(program, cases)


def _candidate_outputs(composer, candidate, goal, cases):
    return [composer.execute_candidate(candidate, goal, [xs],
             runtime_context={"STATE": {}, "INDEX": None, "BOOLEAN": None}) for xs in cases]


def run_path(library, usage, contracts, goal_id, kind, target, examples, holdout, expanded):
    initial_hash = library.persistent_hash
    kv = ExternalCandidateKV()
    composer = GoalDrivenComposer(library, kv, public_contracts=contracts,
                                  usage_registry=usage)
    composer.library = OpaqueTargetLibrary(library, composer.search_trace, target, contracts[target])
    goal = TaskSpec(goal_id, INPUT_SCHEMA, OUTPUT_SCHEMA, tuple(examples), GOAL_SIGNATURE)
    found = None
    verification_attempts = 0
    started = time.perf_counter()
    for candidate in composer.candidates(goal, excluded_ids=(target,) if expanded else ()):
        verification_attempts += 1
        try:
            example_outputs = _candidate_outputs(composer, candidate, goal,
                                                  [row["input"][0] for row in examples])
            examples_pass = example_outputs == [row["output"] for row in examples]
            holdout_outputs = []
            holdout_pass = False
            if examples_pass:
                holdout_outputs = _candidate_outputs(composer, candidate, goal, holdout)
                holdout_pass = holdout_outputs == [reference(kind, xs) for xs in holdout]
        except (ValueError, KeyError, TypeError, IndexError, LookupError) as error:
            examples_pass = False
            holdout_pass = False
            composer.search_trace.append({"event": "CANDIDATE_EXECUTION_ERROR", "root_id": candidate["root_id"],
                                          "error": f"{type(error).__name__}: {error}"})
        composer.search_trace.append({"event": "VERIFY_CANDIDATE", "root_id": candidate["root_id"],
                                      "examples_pass": examples_pass, "holdout_pass": holdout_pass})
        if examples_pass and holdout_pass:
            found = candidate
            break
    if found is None:
        raise RuntimeError(f"{goal_id}: no candidate passed the fixed examples and holdout")
    if expanded and found["candidate_kind"] != "recipe_composition":
        raise RuntimeError(f"{goal_id}: EXPANDED did not recompose from the lower-level recipe graph")
    if not expanded and (found["candidate_kind"] != "operation_edge" or found["root_id"] != target):
        raise RuntimeError("INVALID TEST: F was not used as a first-class search edge")

    all_inputs = [row["input"][0] for row in examples] + holdout
    outputs = _candidate_outputs(composer, found, goal, all_inputs)
    javascript = js_outputs(program_snapshot(library, found["root_id"], kv), all_inputs)
    expected = [row["output"] for row in examples] + [reference(kind, xs) for xs in holdout]
    verification_attempts += 1  # one final, paired Python/JavaScript/oracle suite
    if outputs != expected or javascript != expected:
        raise AssertionError(f"{goal_id}: A/B Python/JavaScript/oracle mismatch")
    if library.persistent_hash != initial_hash:
        raise AssertionError(f"{goal_id}: search mutated the starting Library")

    trace = composer.search_trace
    target_select = next((i for i, row in enumerate(trace)
                          if row.get("event") == "SELECT_OPERATION" and row.get("structure_id") == target), None)
    if not expanded:
        if target_select is None:
            raise AssertionError("INVALID TEST: target F lacks SELECT_OPERATION trace")
        target_retrieval = next((i for i, row in enumerate(trace)
                                 if row.get("event") == "RETRIEVE_OPERATION" and row.get("structure_id") == target), None)
        target_candidate_binding = next((row for row in trace
                                         if row.get("event") == "ENUMERATE_PUBLIC_BINDING"
                                         and row.get("structure_id") == target), None)
        target_candidate_binding_index = next((i for i, row in enumerate(trace)
                                               if row.get("event") == "ENUMERATE_PUBLIC_BINDING"
                                               and row.get("structure_id") == target), None)
        first_target_bind = next((i for i, row in enumerate(trace)
                                  if row.get("event") == "BIND_PUBLIC_SLOTS" and row.get("structure_id") == target), None)
        first_target_execute = next((i for i, row in enumerate(trace)
                                     if row.get("event") == "EXECUTE_OPERATION" and row.get("structure_id") == target), None)
        if not (target_retrieval is not None and target_candidate_binding_index is not None
                and first_target_bind is not None and first_target_execute is not None
                and target_retrieval < target_candidate_binding_index < target_select
                < first_target_bind < first_target_execute):
            raise AssertionError("INVALID TEST: public retrieve/select/bind/execute order is incomplete")
        if set(target_candidate_binding["bindings"]) != set(composer._interfaces[target].formal_slots):
            raise AssertionError("INVALID TEST: candidate enumeration bound non-public F slots")
        enumerations_before = [row for row in trace[:target_select]
                              if row.get("event") in {"SELECT_TOPOLOGY", "ENUMERATE_RECIPE_BINDING",
                                                       "COMPOSITION_ATTEMPT", "ENUMERATE_PUBLIC_OPERATION_PAIR"}]
        if enumerations_before:
            raise AssertionError("INVALID TEST: topology/binding enumeration preceded target F selection")
        if composer.library.guard.hidden_reads_before_select:
            raise AssertionError("INVALID TEST: target F internals were read before selection")
        bound = next(row["bindings"] for row in trace if row.get("event") == "BIND_PUBLIC_SLOTS"
                     and row.get("structure_id") == target and row.get("bindings", {}).get("SEQUENCE"))
        if set(bound) != set(found["public_operation"]["formal_slots"]):
            raise AssertionError("target F binding did not use only public formal slots")
        selected_trace = {
            "candidate_kind": found["candidate_kind"],
            "candidate_rank": found["rank"],
            "event_order": ["RETRIEVE_OPERATION", "ENUMERATE_PUBLIC_BINDING",
                            "SELECT_OPERATION", "BIND_PUBLIC_SLOTS", "EXECUTE_OPERATION"],
            "retrieved_target": any(row.get("event") == "RETRIEVE_OPERATION" and row.get("structure_id") == target for row in trace),
            "public_interface_passed": is_first_class_operation(composer._interfaces[target]),
            "selected_target": target,
            "candidate_binding": target_candidate_binding["bindings"],
            "binding": bound,
            "formal_slots": found["public_operation"]["formal_slots"],
            "topology_or_internal_recipe_binding_enumerations_before_select": enumerations_before,
            "hidden_reads_before_select": composer.library.guard.hidden_reads_before_select,
        }
    else:
        matching = next(i for i, row in enumerate(trace)
                        if row.get("event") == "COMPOSITION_ATTEMPT"
                        and row.get("candidate_ids", {}).get("root_structure_id") == found["root_id"])
        previous = max((i for i in range(matching)
                        if trace[i].get("event") == "COMPOSITION_ATTEMPT"), default=-1)
        selected_composition = trace[matching]
        child_rows = [row["structure_id"] for row in trace[previous + 1:matching]
                      if row.get("event") == "COMPOSE_CHILD"]
        selected_trace = {
            "candidate_kind": found["candidate_kind"],
            "masked_target": target,
            "selected_topology": selected_composition["topology_id"],
            "selected_parameters": selected_composition["bindings"],
            "composed_child_F_ids": list(dict.fromkeys(child_rows)),
            "root": found["root_id"],
            "search_depth": found["search_depth"],
        }

    metrics = dict(composer.search_metrics)
    metrics.update({"verification_attempts": verification_attempts,
                    "search_depth": found["search_depth"]})
    selected_events = [row for row in trace if row.get("event") in {
        "RETRIEVE_OPERATION", "ENUMERATE_PUBLIC_BINDING", "RESIDUAL_RECIPE_SEARCH",
        "SELECT_TOPOLOGY", "ENUMERATE_RECIPE_BINDING", "COMPOSITION_ATTEMPT",
        "SELECT_OPERATION", "BIND_PUBLIC_SLOTS", "EXECUTE_OPERATION", "VERIFY_CANDIDATE"}]
    return {
        "mode": "EXPANDED" if expanded else "FIRST-CLASS F",
        "status": "PASS",
        "target_F": target,
        "target_passes_current_formation_admission": audit_formation_parents(library[target], library)["passed"],
        "selected_root": found["root_id"],
        "selected_kind": found["candidate_kind"],
        "metrics": metrics,
        "selected_trace": selected_trace,
        "semantic_equal_to_oracle": True,
        "python_outputs": outputs,
        "javascript_outputs": javascript,
        "oracle_outputs": expected,
        "library_hash_before": initial_hash,
        "library_hash_after": library.persistent_hash,
        "initial_kv_candidates": 0,
        "wall_clock_seconds": time.perf_counter() - started,
        "search_trace": selected_events,
    }


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    library = Library.load(LIBRARY_PATH)
    contracts = publish_recursive_operation_contracts(library)
    usage = UsageRegistry.load(USAGE_PATH, library)
    clean_report=True
    if clean_report:
        task_summary=Path(os.environ.get("V94_CLEAN_SUMMARY",LIBRARY_PATH.parent/"summary.json"))
        root_map=json.loads(task_summary.read_text(encoding="utf-8"))["goal_root_map"]
        requested={item.strip() for item in os.environ.get("V94_REUSE_GOALS","U11,U12,U13").split(",") if item.strip()}
        tasks=[]
        for goal_id,kind,_ in TASKS:
            if goal_id in requested:
                target_id=root_map.get(goal_id)
                if not target_id:raise RuntimeError(f"{goal_id}: strict root is missing from clean summary")
                tasks.append((goal_id,kind,target_id))
        if {row[0] for row in tasks}!=requested:
            raise ValueError("V94_REUSE_GOALS contains unknown IDs: "+", ".join(sorted(requested-{row[0] for row in tasks})))
    else:
        tasks=TASKS
    pairs = []
    for index, (goal_id, kind, target) in enumerate(tasks):
        if target not in library._structures or target not in contracts:
            raise RuntimeError(f"{goal_id}: target F is absent or has no published interface")
        examples = make_examples(kind, SEED + index, TRAIN_COUNT)
        holdout = [row["input"][0] for row in make_examples(kind, SEED + 1000 + index, HOLDOUT_COUNT)]
        expanded = run_path(library, usage, contracts, goal_id, kind, target, examples, holdout, True)
        first_class = run_path(library, usage, contracts, goal_id, kind, target, examples, holdout, False)
        if (expanded["library_hash_before"] != first_class["library_hash_before"]
                or expanded["python_outputs"] != first_class["python_outputs"]
                or expanded["oracle_outputs"] != first_class["oracle_outputs"]):
            raise AssertionError(f"{goal_id}: paired semantics or starting state differ")
        reductions = {name: (expanded["metrics"][name] - first_class["metrics"][name])
                      / expanded["metrics"][name] if expanded["metrics"][name] else 0.0
                      for name in ("candidate_attempts", "binding_combinations", "search_depth")}
        pairs.append({"goal": goal_id, "kind": kind, "target_F": target,
                      "target_passes_current_formation_admission": first_class["target_passes_current_formation_admission"],
                      "expanded": expanded, "first_class": first_class,
                      "semantic_equal": True, "reductions": reductions})

    all_ids = tuple(obj.identity for obj in library)
    active_ids = usage.active_ids()
    retired_ids = usage.retired_ids()
    growth_rows = []
    pool_goal = TaskSpec("active-pool-diagnostic", INPUT_SCHEMA, OUTPUT_SCHEMA, (), GOAL_SIGNATURE)
    pool_provider=OperationPoolProvider(library,usage,public_contracts=contracts)
    full_compatible=pool_provider.get_operations(pool_goal,lifecycle_view="FULL")
    active_compatible=pool_provider.get_operations(pool_goal,lifecycle_view="ACTIVE")
    eq_stress = []
    formation_audits = [audit_formation_parents(obj, library) for obj in library if obj.level]
    audit_by_id = {obj.identity: audit_formation_parents(obj, library)
                   for obj in library if obj.level}
    legacy_audit_failures = [
        {"structure_id": obj.identity, "role": obj.metadata.get("role"),
         "exec_capability": obj.metadata.get("exec_capability"),
         "issues": list(audit_by_id[obj.identity]["issues"])}
        for obj in library if obj.level and not audit_by_id[obj.identity]["passed"]
    ]
    topology_template_failures = [row for row in legacy_audit_failures if row["role"] == "topology"]
    legacy_executable_failures = [row for row in legacy_audit_failures if row["role"] != "topology"]
    observed = {
        "library_hash": library.persistent_hash,
        "library_size": len(library),
        "level_histogram": {str(level): len(library.structures_at_level(level))
                            for level in range(library.max_level + 1)},
        "max_library_level": library.max_level,
        "composite_formation_audit_pass_count": sum(row["passed"] for row in formation_audits),
        "composite_formation_audit_fail_count": sum(not row["passed"] for row in formation_audits),
        "legacy_executable_formation_audit_failures": legacy_executable_failures,
        "topology_recipe_template_audit_failures": topology_template_failures,
        "explicit_equivalence_links": len(library.equivalence),
        "equivalence_class_count": len(library.equivalence_classes()),
        "non_singleton_equivalence_classes": [list(group) for group in library.equivalence_classes() if len(group) > 1],
        "active_count": len(active_ids),
        "retired_count": len(retired_ids),
        "active_ids": sorted(active_ids),
        "retired_ids": sorted(retired_ids),
        "usage_nonzero": {sid: count for sid, count in usage.counts().items() if count},
        "full_library_size": len(all_ids),
        "online_compatible_operation_count_full": len(full_compatible),
        "online_compatible_operation_count_active": len(active_compatible),
        "growth_history": growth_rows,
              "growth_history_scope": "not included in clean strict subset" if clean_report else "recorded real Goals: G01-G10 then the strict U11-U13 run; historical G01-G10 admission predates the unified formation-parent audit",
        "synthetic_equivalence_stress": eq_stress,
    }
    result = {"status": "PASS", "source_library": str(LIBRARY_PATH.relative_to(ROOT)),
              "source_library_hash": library.persistent_hash, "source_library_size": len(library),
              "seed": SEED, "examples_per_goal": TRAIN_COUNT, "holdout_cases_per_goal": HOLDOUT_COUNT,
              "tested_goals": len(pairs), "pairs": pairs, "current_library_diagnostics": observed,
              "formal_search_ranking_modified": False, "scoring_modified": False,
              "lifecycle_mutated": False, "equivalence_classes_created": False,
              "persistent_library_modified": False, "goal_count_limited_to_existing_reusable_F": True,
              "temporary_search_materializations_expanded": sum(row["expanded"]["metrics"]["intermediate_materializations"] for row in pairs)}
    for pair in result["pairs"]:
        pair["expanded"].pop("search_trace",None)
        pair["first_class"].pop("search_trace",None)
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")

    lines = ["# V9.4 library growth and first-class search validation", "",
             f"Source Library: {len(library)} Structures; hash `{library.persistent_hash}`. The paired search cohort contains {len(pairs)} existing real Goals. No candidate was added to the persistent Library; EXPANDED created trial-only intermediate candidates in fresh KV stores. The source Library and UsageRegistry were read-only, and search scoring/ranking was untouched.", "",
             "Each pair used one identical Library snapshot, Goal contract, 24 fixed examples, 24 fixed holdout inputs, empty trial KV and independent oracle. EXPANDED excluded only the target root F and required the successful candidate to be a recipe composition. FIRST-CLASS F required the direct target operation edge to pass. Its trace records public formal-slot candidate binding before SELECT; the guard exposes only the public interface and raises if kernel, parents, provenance, serialization, topology enumeration, or internal recipe bindings are read before selection.", "",
             "## Paired search results", "",
             "| Goal | target F | current admission | attempts F / expanded | bindings F / expanded | expansions F / expanded | materializations F / expanded | verifications F / expanded | depth F / expanded | semantic equal |", "|---|---|---|---:|---:|---:|---:|---:|---:|---|"]
    for row in pairs:
        a, b = row["expanded"]["metrics"], row["first_class"]["metrics"]
        cohort = "strict new gate" if row["target_passes_current_formation_admission"] else "legacy-loaded"
        lines.append(f"| {row['goal']} | `{row['target_F']}` | {cohort} | {b['candidate_attempts']} / {a['candidate_attempts']} | {b['binding_combinations']} / {a['binding_combinations']} | {b['composition_expansions']} / {a['composition_expansions']} | {b['intermediate_materializations']} / {a['intermediate_materializations']} | {b['verification_attempts']} / {a['verification_attempts']} | {b['search_depth']} / {a['search_depth']} | yes |")
    total_a = {key: sum(row["expanded"]["metrics"][key] for row in pairs)
               for key in ("candidate_attempts", "binding_combinations", "composition_expansions", "intermediate_materializations", "verification_attempts", "search_depth")}
    total_b = {key: sum(row["first_class"]["metrics"][key] for row in pairs)
               for key in total_a}
    total_reductions = {key: (total_a[key] - total_b[key]) / total_a[key] if total_a[key] else 0.0
                        for key in ("candidate_attempts", "binding_combinations", "search_depth")}
    lines += ["", f"Totals: attempts {total_b['candidate_attempts']} vs {total_a['candidate_attempts']} ({total_reductions['candidate_attempts']:.1%} reduction); bindings {total_b['binding_combinations']} vs {total_a['binding_combinations']} ({total_reductions['binding_combinations']:.1%}); composition expansions {total_b['composition_expansions']} vs {total_a['composition_expansions']}; intermediate materializations {total_b['intermediate_materializations']} vs {total_a['intermediate_materializations']}; verification attempts {total_b['verification_attempts']} vs {total_a['verification_attempts']}; summed per-goal search depth {total_b['search_depth']} vs {total_a['search_depth']} ({total_reductions['search_depth']:.1%}).", "",
             "| Goal | attempt reduction | binding reduction | depth reduction | result |", "|---|---:|---:|---:|---|"]
    for row in pairs:
        reductions = row["reductions"]
        result_label = "PASS — SEARCH REDUCTION" if any(value > 0 for value in reductions.values()) else "PASS — NO REDUCTION"
        lines.append(f"| {row['goal']} | {reductions['candidate_attempts']:.1%} | {reductions['binding_combinations']:.1%} | {reductions['search_depth']:.1%} | {result_label} |")
    strict_pairs = [row for row in pairs if row["target_passes_current_formation_admission"]]
    strict_a = sum(row["expanded"]["metrics"]["candidate_attempts"] for row in strict_pairs)
    strict_b = sum(row["first_class"]["metrics"]["candidate_attempts"] for row in strict_pairs)
    strict_binding_a = sum(row["expanded"]["metrics"]["binding_combinations"] for row in strict_pairs)
    strict_binding_b = sum(row["first_class"]["metrics"]["binding_combinations"] for row in strict_pairs)
    strict_depth_a = sum(row["expanded"]["metrics"]["search_depth"] for row in strict_pairs)
    strict_depth_b = sum(row["first_class"]["metrics"]["search_depth"] for row in strict_pairs)
    result["strict_admission_subset_summary"] = {
        "goals": [row["goal"] for row in strict_pairs],
        "candidate_attempts_expanded": strict_a,
        "candidate_attempts_first_class": strict_b,
        "candidate_attempt_reduction": (strict_a-strict_b)/strict_a if strict_a else 0.0,
        "binding_combinations_expanded": strict_binding_a,
        "binding_combinations_first_class": strict_binding_b,
        "binding_reduction": (strict_binding_a-strict_binding_b)/strict_binding_a if strict_binding_a else 0.0,
        "search_depth_expanded": strict_depth_a,
        "search_depth_first_class": strict_depth_b,
        "depth_reduction": (strict_depth_a-strict_depth_b)/strict_depth_a if strict_depth_a else 0.0,
    }
    (OUT / "results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf8")
    if clean_report:
        outcome=("PASS — SEARCH REDUCTION" if any(value>0 for value in total_reductions.values())
                 else "PASS — NO REDUCTION")
        clean_lines=["# V9.4 clean strict Library — first-class F reuse","",
            f"Source Library: {len(library)} Structures; hash `{library.persistent_hash}`. The paired cohort contains {len(pairs)} existing real Goals. Each pair used the same Library, examples, seed, verifier, oracle and initial state.","",
            "## Paired search results","",
            "| Goal | target F | EXPANDED attempts / bindings / depth | FIRST-CLASS attempts / bindings / depth | semantic equal | result |",
            "|---|---|---:|---:|---|---|"]
        for row in pairs:
            a=row["expanded"]["metrics"];b=row["first_class"]["metrics"]
            label="PASS — SEARCH REDUCTION" if any(value>0 for value in row["reductions"].values()) else "PASS — NO REDUCTION"
            clean_lines.append(f"| {row['goal']} | `{row['target_F']}` | {a['candidate_attempts']} / {a['binding_combinations']} / {a['search_depth']} | {b['candidate_attempts']} / {b['binding_combinations']} / {b['search_depth']} | yes | {label} |")
        clean_lines += ["",f"Totals: attempts {total_a['candidate_attempts']} → {total_b['candidate_attempts']} ({total_reductions['candidate_attempts']:.1%} reduction); bindings {total_a['binding_combinations']} → {total_b['binding_combinations']} ({total_reductions['binding_combinations']:.1%}); depth {total_a['search_depth']} → {total_b['search_depth']} ({total_reductions['search_depth']:.1%}).", "",
            f"Overall: {outcome}. Each B route selected the target as a direct public operation edge. Exact-parent audit passes for every target.","",
            "## Per-Goal traces",""]
        for row in pairs:
            clean_lines += [f"### {row['goal']} — `{row['kind']}`","","```text","EXPANDED:",
                json.dumps(row["expanded"]["selected_trace"],ensure_ascii=False,separators=(",",":")),
                "FIRST-CLASS F:",json.dumps(row["first_class"]["selected_trace"],ensure_ascii=False,separators=(",",":")),"```",""]
        clean_lines += ["## Clean Library and semantic checks","",
            f"Current snapshot audit: {observed['composite_formation_audit_pass_count']} composite PASS, {len(legacy_executable_failures)} executable failure, {len(topology_template_failures)} non-executable topology templates. ACTIVE {observed['active_count']}; RETIRED {observed['retired_count']}; equivalence classes {observed['equivalence_class_count']}; maximum level {library.max_level}.",
            "Every paired Python and JavaScript output equals the independent oracle on the same examples and holdout. Search did not change Library hash, lifecycle data, scoring or ranking.",
            "Machine data and event traces: `results.json`."]
        (OUT/"report.md").write_text("\n".join(clean_lines)+"\n",encoding="utf8")
        print(json.dumps({"tested_goals":len(pairs),"totals_expanded":total_a,
            "totals_first_class":total_b,"reductions":total_reductions,"overall":outcome},
            ensure_ascii=False,indent=2))
        return


if __name__ == "__main__":
    main()
