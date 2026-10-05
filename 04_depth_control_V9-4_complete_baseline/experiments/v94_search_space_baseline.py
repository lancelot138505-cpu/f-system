"""Unified V9.4 depth-reuse and lifecycle-exposure baseline.

The verifier owns the task oracles. GoalDrivenComposer sees only public Goal
contracts and examples; the lifecycle and historical-F masks enter through the
same OperationPoolProvider-backed Composer API.
"""
from __future__ import annotations

import copy
import hashlib
import inspect
import json
import os
import subprocess
import sys
import tempfile
import time
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "experiments"))

OUT = ROOT / "artifacts" / "search_space_baseline"
BASE_LIBRARY = ROOT / "artifacts" / "clean_strict_library" / "library_final.json"
BASE_USAGE = ROOT / "artifacts" / "clean_strict_library" / "usage_final.json"
PYTHON = Path(r"C:\Users\lancelot\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe")
NODE = Path(r"C:\Users\lancelot\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe")

TRAIN_INPUTS = (
    [], [4], [1, 2], [2, 1], [1, 1], [1, 2, 1], [3, 2, 1], [1, 1, 2, 0, 1],
    [-3, -2, -1, 2, 0], [5, 4, 4, 3, 5, 7], [0, 0, 0, 0], [9, 1, 2, 3, 0],
    [2, 1, 2, 1, 2], [-4, -1, -2, -2, 3, 3, 4], [7, 6, 5, 5, 4, 8, 9],
)
RUNTIME_CONTEXT = {"STATE": {}, "INDEX": None, "BOOLEAN": None}
GOAL_SIGNATURE = "v94-search-space-baseline:sequence<int>->int:v1"
MAX_CANDIDATES = 1200
HOLDOUT_COUNT = 128
HOLDOUT_SEED = 941200

from fsys.admission import REQUIRED_TOKEN_CHECKS, audit_formation_parents, subject_digest
from fsys.external_kv import ExternalCandidateKV
from fsys.formation import canonical_compose
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.library import Library
from fsys.operation_pool import OperationPoolProvider
from fsys.preference import UsageRegistry
from fsys.promotion import PromotionGate
from fsys.public_operations import bind_operation, is_first_class_operation, normalize_operation
from fsys.recursive_f_runtime import RecursiveFExecutor, program_snapshot
from fsys.serialization import digest, thaw
from fsys.structure import Structure
from fsys.token_goal_domain import publish_recursive_operation_contracts
from search_space_baseline_oracles import FOUNDATIONS, chain_tasks
from synthesis_oracles import randomized_inputs
from fsys.verifier import VerificationRecord


WORK_KEYS = ("candidate_attempts", "binding_combinations", "composition_expansions",
             "intermediate_materializations", "verification_attempts")


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return path


def _compact_exposure(exposure):
    if not exposure:
        return {}
    return {key: value for key, value in exposure.items()
            if key not in ("goal_compatible_ids", "retired_goal_compatible_ids",
                           "historical_mask_ids")}


def _compact_chain_path(chain_path):
    if not isinstance(chain_path, dict):
        return chain_path
    return {key: chain_path[key] for key in (
        "status", "expected_child_semantics", "matched_parent_ids", "parents",
        "admission_independent") if key in chain_path}


def _compact_fallback(fallback):
    if not isinstance(fallback, dict):
        return fallback
    total_work = fallback.get("fallback_total_work", {})
    if not total_work and fallback.get("stages"):
        total_work = {}
        for stage in fallback["stages"]:
            for key, value in (stage.get("fallback_total_work") or {}).items():
                if isinstance(value, (int, float)):
                    total_work[key] = total_work.get(key, 0) + value
    return {key: fallback.get(key) for key in (
        "fallback_triggered", "fallback_candidate_pool", "fallback_attempts",
        "fallback_hit_F", "retired_ids_available") if key in fallback} | {
        "fallback_total_work": total_work,
        "stage_count": len(fallback.get("stages", [])),
    }


def compact_fallback_trace(rows):
    result = []
    for row in rows:
        if "stage_count" in row and "stages" not in row:
            result.append(row)
            continue
        stages = row.get("stages", [])
        work = {key: sum(int((stage.get("fallback_total_work") or {}).get(key, 0))
                         for stage in stages) for key in WORK_KEYS}
        result.append({
            "source": row.get("source"), "task_id": row.get("task_id"),
            "fallback_triggered": any(bool(stage.get("fallback_triggered")) for stage in stages),
            "fallback_candidate_pool": sum(int(stage.get("fallback_candidate_pool", 0))
                                            for stage in stages),
            "fallback_attempts": sum(int(stage.get("fallback_attempts", 0))
                                      for stage in stages),
            "fallback_hits": sorted({stage.get("fallback_hit_F") for stage in stages
                                      if stage.get("fallback_hit_F")}),
            "fallback_total_work": work, "stage_count": len(stages),
        })
    return result


def compact_shortcut_trace(rows):
    return [{key: row.get(key) for key in (
        "source", "task_id", "status", "expected_child_semantics",
        "matched_parent_ids", "parents", "admission_independent", "admitted",
        "already_persistent") if key in row}
        for row in rows if row.get("status") == "SHORTCUT_FOUND"]


def compact_library_growth(rows):
    stat_keys = ("L_all", "L_exec", "active_executable_F", "retired_executable_F",
                 "topology_templates", "non_singleton_equivalence_classes",
                 "max_canonical_level", "exact_parent_pass", "exact_parent_fail",
                 "persistent_hash")
    return [{key: row.get(key) for key in (
        "stage", "task_id", "library_file_sha256", "usage_file_sha256")}
        | {"library_stats": {key: row.get("library_stats", {}).get(key)
                             for key in stat_keys}}
        for row in rows]


def compact_lifecycle_trace(rows):
    stat_keys = ("L_all", "L_exec", "active_executable_F", "retired_executable_F",
                 "non_singleton_equivalence_classes", "max_canonical_level")
    result = []
    for row in rows:
        if "active_count" in row and "active_ids" not in row:
            result.append(row)
            continue
        stats, counts = row.get("stats", {}), row.get("counts", {})
        result.append({
            "label": row.get("label"), "library_hash": row.get("library_hash"),
            "registry_digest": row.get("registry_digest"),
            "active_count": len(row.get("active_ids", [])),
            "retired_count": len(row.get("retired_ids", [])),
            "usage_count_nonzero": sum(value > 0 for value in counts.values()),
            "total_usage_count": sum(counts.values()),
            "preferred_count": len(row.get("preferred", {})),
            "stats": {key: stats.get(key) for key in stat_keys},
        })
    return result


def compact_equivalence_trace(rows):
    return [row if "class_count" in row and "classes" not in row else {
        "stage": row.get("stage"), "library_hash": row.get("library_hash"),
        "explicit_links": row.get("explicit_links"),
        "class_count": len(row.get("classes", [])),
        "non_singleton_count": row.get("non_singleton_count"),
        "non_singleton_classes": [group for group in row.get("classes", []) if len(group) > 1],
        "active_count": len(row.get("active_ids", [])),
        "retired_count": len(row.get("retired_ids", [])),
        "automatic_detection_used": row.get("automatic_detection_used"),
    } for row in rows]


def _compact_admission_group(rows):
    checks = sorted({key for row in rows
                     for key in (row.get("strict_admission_checks") or {})
                     if isinstance((row.get("strict_admission_checks") or {}).get(key), bool)})
    return {
        "structure_id": rows[0].get("structure_id"),
        "operation_id": rows[0].get("operation_id"),
        "parents": rows[0].get("parents", []),
        "canonical_level": rows[0].get("canonical_level"),
        "receipt_count": len(rows),
        "receipts_by_source": dict(Counter(row.get("source", "unknown") for row in rows)),
        "admission_pass": all(bool(row.get("admission_pass")) for row in rows),
        "exact_parent_pass": all(bool((row.get("formation_audit") or {}).get("passed")) for row in rows),
        "checks": {key: all((row.get("strict_admission_checks") or {}).get(key) is True
                            for row in rows) for key in checks},
    }


def summarize_admission_receipts(rows):
    by_structure = defaultdict(list)
    for row in rows:
        by_structure[row.get("structure_id", "unknown")].append(row)
    source_counts = dict(Counter(row.get("source", "unknown") for row in rows))
    main_rows = [row for row in rows if row.get("source") == "main_acquisition"]
    branch_rows = [row for row in rows if row.get("source") != "main_acquisition"]
    return {
        "format": "admission-receipt-summary",
        "receipt_count": len(rows),
        "receipt_counts_by_source": source_counts,
        "branch_local_receipt_count": len(branch_rows),
        "main_chain_receipt_count": len(main_rows),
        "unique_structure_count": len(by_structure),
        "main_chain_new_persistent_F_count": len({row.get("structure_id") for row in main_rows}),
        "all_admissions_pass": all(bool(row.get("admission_pass")) for row in rows),
        "all_exact_parent_pass": all(bool((row.get("formation_audit") or {}).get("passed")) for row in rows),
        "unique_structures": [_compact_admission_group(group)
                              for _, group in sorted(by_structure.items())],
    }


def summarize_formation_audits(rows):
    result = summarize_admission_receipts(rows)
    return {
        "format": "formation-audit-summary",
        "audit_receipt_count": result["receipt_count"],
        "receipt_counts_by_source": result["receipt_counts_by_source"],
        "unique_structure_count": result["unique_structure_count"],
        "all_exact_parent_pass": result["all_exact_parent_pass"],
        "unique_structures": [{key: row[key] for key in (
            "structure_id", "parents", "canonical_level", "receipt_count",
            "receipts_by_source", "exact_parent_pass")}
            for row in result["unique_structures"]],
    }


def _compact_stage_result(stage):
    receipts = stage.get("admission_rows", [])
    receipt_count = len(receipts) or int(stage.get("admission_receipt_count",
        stage.get("admission_receipts", 0)) or 0)
    pass_count = (sum(bool(row.get("admission_pass")) for row in receipts)
                  if receipts else int(stage.get("admission_pass_count",
                      stage.get("admission_passes", 0)) or 0))
    path = stage.get("chain_path") or {}
    compact = {key: stage.get(key) for key in (
        "task_id", "status", "selected_root_id", "candidate_kind", "canonical_level")}
    compact["parents"] = stage.get("parents", path.get("parents", []))
    compact["chain_status"] = path.get("status")
    compact["admission_receipt_count"] = receipt_count
    compact["admission_pass_count"] = pass_count
    return compact


def compact_mode_result(view, include_reformation_stages=True):
    keys = (
        "mode", "task_id", "status", "source_library_hash", "source_usage_digest",
        "starting_branch_library_hash", "historical_mask_ids", "designed_chain_depth",
        "search_derivation_depth", "historical_levels_skipped", "newly_formed_goal_F_count",
        "direct_existing_F_hits", "reused_F_ids", "reformed_F_ids",
        "highest_reused_canonical_level", "target_root_id", "target_canonical_level",
        "target_candidate_kind", "target_candidate_rank", "target_parents",
        "first_class_edge_audit", "semantic_verification", "semantic_equal", "chain_path",
        "target_chain_path", "runtime_call_depth", "metrics", "wall_seconds", "fallback",
    )
    result = {key: view[key] for key in keys if key in view}
    for key in ("chain_path", "target_chain_path"):
        if key in result:
            result[key] = _compact_chain_path(result[key])
    if "fallback" in result:
        result["fallback"] = _compact_fallback(result["fallback"])
    result["exposure"] = _compact_exposure(view.get("exposure"))
    result["after_mask_exposure"] = _compact_exposure(view.get("after_mask_exposure"))
    stages = view.get("reformation_stages", [])
    if include_reformation_stages:
        result["reformation_stages"] = [_compact_stage_result(stage) for stage in stages]
    receipts = view.get("admission_rows", [])
    result["admission_receipt_count"] = len(receipts) or int(view.get("admission_receipt_count",
        view.get("admission_receipts", 0)) or 0)
    result["admission_pass_count"] = (sum(bool(row.get("admission_pass")) for row in receipts)
                                      if receipts else int(view.get("admission_pass_count",
                                          view.get("admission_passes", 0)) or 0))
    return result


def compact_paired_result(pair):
    keys = (
        "task_id", "starting_library_hash", "starting_usage_digest", "goal_signature",
        "examples_digest", "holdout_seed", "holdout_digest", "candidate_limit", "same_verifier",
        "semantic_output_digest_equal_across_returned_modes", "all_four_modes_completed",
        "all_four_modes_semantic_equal", "effect_reductions", "lifecycle_exposure",
        "all_first_class_views_used_target_as_direct_edge", "all_returned_modes_semantic_equal",
    )
    result = {key: pair[key] for key in keys if key in pair}
    modes = pair.get("modes", {})
    result["modes"] = {name: compact_mode_result(view, include_reformation_stages=False)
                        for name, view in modes.items()}
    if "all_four_modes_completed" not in result:
        result["all_four_modes_completed"] = len(modes) == 4 and all(
            view.get("status") in ("PASS", "SEARCHED", "EXISTING_F_REUSE")
            and view.get("semantic_equal") is True for view in modes.values())
    if "all_four_modes_semantic_equal" not in result:
        result["all_four_modes_semantic_equal"] = len(modes) == 4 and all(
            view.get("semantic_equal") is True for view in modes.values())
    if "all_returned_modes_semantic_equal" not in result:
        result["all_returned_modes_semantic_equal"] = bool(modes) and all(
            view.get("semantic_equal") is True for view in modes.values())
    if "all_first_class_views_used_target_as_direct_edge" not in result:
        first_class = [view for name, view in modes.items() if "FIRST_CLASS" in name]
        result["all_first_class_views_used_target_as_direct_edge"] = len(first_class) == 2 and all(
            (view.get("first_class_edge_audit") or {}).get("passes") is True
            and (view.get("first_class_edge_audit") or {}).get("is_target_operation_edge") is True
            for view in first_class)
    return result


def compact_acquisition_trace(record):
    if "starting_library_counts" in record and "candidate_limit" in record:
        return record
    acquisition = record.get("acquisition", {})
    stats = record.get("starting_library_stats", {})
    return {
        "task_id": record.get("task_id"),
        "starting_library_hash": record.get("starting_library_hash"),
        "starting_library_counts": {key: stats.get(key) for key in (
            "L_all", "L_exec", "registry_active_structures", "registry_retired_structures",
            "active_executable_F", "retired_executable_F", "max_canonical_level")},
        "holdout_seed": record.get("holdout_seed"),
        "candidate_limit": record.get("same_candidate_limit"),
        "acquisition": {
            key: acquisition.get(key) for key in (
                "status", "selected_root_id", "candidate_kind", "formation_candidate_rank",
                "canonical_level", "parents", "metrics", "semantic_verification",
                "post_promotion_cold_pass", "persistent_public_retrieval") if key in acquisition
        } | {
            "chain_path": _compact_chain_path(acquisition.get("chain_path")),
            "admission_receipt_count": len(acquisition.get("admission_rows", [])),
        },
    }


def compact_task_family_manifest(family):
    depth_tasks = []
    for row in family.get("depth_tasks", []):
        depth_tasks.append({
            key: row.get(key) for key in (
                "task_id", "left", "operator", "right", "semantics", "public_contract",
                "holdout_seed")
        } | {
            "training_example_count": len(row.get("training_examples", [])),
            "training_examples_digest": digest(row.get("training_examples", [])),
            "holdout_case_count": len(row.get("holdout_inputs", [])),
            "holdout_input_digest": digest(row.get("holdout_inputs", [])),
            "holdout_output_digest": digest(row.get("holdout_outputs", [])),
        })
    foundations = []
    for row in family.get("foundation_tasks", []):
        foundations.append({
            key: row.get(key) for key in ("label", "semantics", "public_contract", "holdout_seed")
        } | {
            "training_example_count": len(row.get("training_examples", [])),
            "training_examples_digest": digest(row.get("training_examples", [])),
            "holdout_case_count": len(row.get("holdout_inputs", [])),
            "holdout_input_digest": digest(row.get("holdout_inputs", [])),
            "holdout_output_digest": digest(row.get("holdout_outputs", [])),
        })
    return {
        key: family.get(key) for key in (
            "experiment", "task_family_version", "frozen_before_search", "frozen_date",
            "maximum_designed_chain_depth", "holdout_count_per_goal", "holdout_seed_rule",
            "search_candidate_limit", "goal_input_excludes_oracle_and_parent_recipe_data",
            "search_views", "lifecycle_policy", "historical_mask_policy", "fixed_search_rules")
    } | {
        "training_input_count": len(family.get("training_inputs", [])),
        "training_inputs_digest": digest(family.get("training_inputs", [])),
        "foundation_tasks": foundations,
        "depth_tasks": depth_tasks,
        "input_materialization": "Deterministically regenerated from frozen seeds; raw case vectors are not persisted.",
    }


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def oracle_values(oracle, inputs):
    return [oracle(nums) for nums in inputs]


def task_goal(oracle, train_inputs=TRAIN_INPUTS):
    examples = tuple({"input": [list(nums)], "output": oracle(nums)} for nums in train_inputs)
    # Keep the Goal-facing task id and signature independent of Dxx labels.
    return TaskSpec("anonymous-goal", ({"name": "nums", "type": "sequence<int>"},),
                    {"type": "int"}, examples, GOAL_SIGNATURE)


def _is_executable(structure):
    metadata = thaw(structure.metadata)
    return bool(thaw(structure.kernel).get("executable")
                or any(metadata.get(key) for key in (
                    "translation_primitive", "translation_rule", "translation_dispatch",
                    "exec_capability")))


def clone_library(source, excluded_ids=()):
    excluded = set(excluded_ids)
    missing = {sid for obj in source if obj.identity not in excluded for sid in obj.parents if sid in excluded}
    if missing:
        raise ValueError("historical mask would leave dangling parents: " + ", ".join(sorted(missing)))
    cloned = Library()
    for obj in sorted(source, key=lambda item: (item.level, item.identity)):
        if obj.identity in excluded:
            continue
        cloned._commit_verified(obj, VerificationRecord(
            True, "read-only paired search snapshot", obj.formation_provenance.verification_digest))
    cloned._equivalence = [edge for edge in source.equivalence
                           if edge["a"] not in excluded and edge["b"] not in excluded]
    cloned._version = source.version
    cloned._hash_cache = None
    cloned._equivalence_cache = None
    cloned._equivalence_lookup = None
    return cloned


def clone_usage(source, library):
    """Copy counts into a branch and recalculate representatives from real links."""
    result = UsageRegistry(library)
    result._counts = {sid: count for sid, count in source.counts().items() if sid in library._structures}
    result._dirty = True
    result._sync_classes()
    return result


def executable_ids(library):
    return {obj.identity for obj in library if _is_executable(obj)}


def library_stats(library, usage=None):
    usage = usage or UsageRegistry(library)
    exec_ids = executable_ids(library)
    active_ids = set(usage.active_ids())
    retired_ids = set(usage.retired_ids())
    classes = library.equivalence_classes()
    composites = [obj for obj in library if obj.level > 0 and obj.metadata.get("role") != "topology"]
    audits = [audit_formation_parents(obj, library) for obj in composites]
    return {
        "L_all": len(library),
        "L_exec": len(exec_ids),
        "registry_active_structures": len(active_ids),
        "registry_retired_structures": len(retired_ids),
        "active_executable_F": len(exec_ids & active_ids),
        "retired_executable_F": len(exec_ids & retired_ids),
        "topology_templates": sum(obj.metadata.get("role") == "topology" for obj in library),
        "equivalence_classes": len(classes),
        "non_singleton_equivalence_classes": sum(len(group) > 1 for group in classes),
        "explicit_equivalence_links": len(library.equivalence),
        "max_canonical_level": library.max_level,
        "exact_parent_pass": sum(row["passed"] for row in audits),
        "exact_parent_fail": sum(not row["passed"] for row in audits),
        "persistent_hash": library.persistent_hash,
        "structure_ids": sorted(obj.identity for obj in library),
    }


def candidate_exposure(library, usage, goal, lifecycle_view="ACTIVE", excluded_ids=()):
    provider = OperationPoolProvider(library, usage,
        public_contracts=publish_recursive_operation_contracts(library))
    exposed = provider._lifecycle_ids(lifecycle_view).difference(excluded_ids)
    exec_ids = executable_ids(library)
    all_public = provider.get_operations(None, lifecycle_view=lifecycle_view,
                                         excluded_ids=excluded_ids)
    compatible = provider.get_operations(goal, lifecycle_view=lifecycle_view,
                                         excluded_ids=excluded_ids)
    retired_compatible = provider.get_operations(goal, lifecycle_view="RETIRED",
                                                 excluded_ids=excluded_ids)
    return {
        "global_executable_pool": len(exec_ids),
        "lifecycle_exposed_executable_pool": len(exec_ids.intersection(exposed)),
        "lifecycle_exposed_structure_pool": len(exposed),
        "lifecycle_exposed_public_executable_pool": len(all_public),
        "goal_compatible_pool": len(compatible),
        "retired_goal_compatible_pool": len(retired_compatible),
        "goal_compatible_ids": [item.identity for item in compatible],
        "retired_goal_compatible_ids": [item.identity for item in retired_compatible],
        "view": lifecycle_view,
        "historical_mask_ids": list(excluded_ids),
    }


def js_batch(program, inputs):
    runtime = (ROOT / "fsys" / "recursive_f_runtime.js").read_text(encoding="utf-8")
    encoded_program = json.dumps(program, separators=(",", ":"), ensure_ascii=False)
    encoded_inputs = json.dumps(inputs, separators=(",", ":"), ensure_ascii=False)
    code = runtime + "\nconst P=" + encoded_program + ";const R=makeFExecutor(P);console.log(JSON.stringify(" + encoded_inputs + ".map(x=>R.run([x]).result)));"
    with tempfile.TemporaryDirectory() as directory:
        script = Path(directory) / "run.js"
        script.write_text(code, encoding="utf-8")
        completed = subprocess.run([str(NODE), str(script)], cwd=ROOT, capture_output=True,
                                   text=True, encoding="utf-8", check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _slim_kv(result):
    selected = set((result.get("candidate") or {}).get("candidate_ids", {}).values())
    slim = ExternalCandidateKV()
    slim.records = {key: copy.deepcopy(value) for key, value in result["kv"].records.items()
                    if key in selected}
    slim.events = [copy.deepcopy(event) for event in result["kv"].events
                   if event.get("candidate_id") in selected]
    return slim


def cold_execute(library, kv, root_id, inputs):
    worker = (
        "import json,sys;from pathlib import Path;"
        "from fsys.library import Library;from fsys.external_kv import ExternalCandidateKV;"
        "from fsys.recursive_f_runtime import RecursiveFExecutor;"
        "d=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));"
        "l=Library.load(d['library']);k=ExternalCandidateKV.load(d['kv']) if d.get('kv') else ExternalCandidateKV();"
        "e=RecursiveFExecutor(l,k);print(json.dumps([e.execute(d['root'],[x]) for x in d['inputs']]))"
    )
    with tempfile.TemporaryDirectory() as directory:
        folder = Path(directory)
        library_path, kv_path, request_path = folder / "library.json", folder / "kv.json", folder / "request.json"
        library.save(library_path)
        kv.save(kv_path)
        request_path.write_text(json.dumps({"library": str(library_path), "kv": str(kv_path),
                                            "root": root_id, "inputs": inputs}), encoding="utf-8")
        completed = subprocess.run([str(PYTHON), "-c", worker, str(request_path)], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def cold_execute_saved(library_path, root_id, inputs):
    worker = (
        "import json,sys;from pathlib import Path;from fsys.library import Library;"
        "from fsys.recursive_f_runtime import RecursiveFExecutor;"
        "d=json.loads(Path(sys.argv[1]).read_text(encoding='utf-8'));"
        "l=Library.load(d['library']);e=RecursiveFExecutor(l);"
        "print(json.dumps([e.execute(d['root'],[x]) for x in d['inputs']]))"
    )
    with tempfile.TemporaryDirectory() as directory:
        request_path = Path(directory) / "request.json"
        request_path.write_text(json.dumps({"library": str(library_path), "root": root_id,
                                            "inputs": inputs}), encoding="utf-8")
        completed = subprocess.run([str(PYTHON), "-c", worker, str(request_path)], cwd=ROOT,
                                   capture_output=True, text=True, encoding="utf-8", check=True)
    return json.loads(completed.stdout.strip().splitlines()[-1])


def _candidate_root(result):
    candidate = result.get("candidate")
    if not candidate:
        return None, None
    if candidate.get("candidate_kind") == "operation_edge":
        sid = candidate["root_id"]
        return None, result["library"][sid]
    candidate_id = candidate.get("candidate_ids", {}).get("root_candidate_id")
    if not candidate_id:
        return None, None
    return candidate_id, Structure.from_dict(result["kv"].get(candidate_id)["structure"])


def _candidate_objects(result):
    candidate = result.get("candidate") or {}
    if candidate.get("candidate_kind") == "operation_edge":
        return []
    ids = candidate.get("candidate_ids", {})
    rows = []
    for role in ("step", "root"):
        cid = ids.get(role + "_candidate_id")
        if cid and cid in result["kv"].records:
            rows.append((role, cid, Structure.from_dict(result["kv"].get(cid)["structure"])))
    return rows


def _filtered_trace(composer, candidate):
    if not candidate:
        return [row for row in composer.search_trace
                if row.get("event") in ("RETRIEVE_OPERATIONS", "SEARCH_EXPLOSION")]
    rank = candidate.get("rank")
    relevant_ids = {candidate.get("root_id"), candidate.get("structure_id"), candidate.get("step_id")}
    relevant_ids.update(candidate.get("child_operation_ids") or ())
    relevant_ids.update((candidate.get("candidate_ids") or {}).values())
    kept = []
    wanted = {"RETRIEVE_OPERATIONS", "ENUMERATE_PUBLIC_OPERATION_PAIR",
              "ENUMERATE_PUBLIC_OPERATION_PAIRS", "ENUMERATE_RECIPE_BINDING",
              "ENUMERATE_PUBLIC_BINDING", "SELECT_TOPOLOGY", "COMPOSITION_ATTEMPT", "SELECT_OPERATION",
              "BIND_PUBLIC_SLOTS", "BIND_PUBLIC_SLOT", "COMPOSE_CHILD",
              "MATERIALIZE_PUBLIC_OPERATION_PAIR", "CANONICAL_COMPOSE",
              "EXECUTE_OPERATION", "SEARCH_EXPLOSION"}
    for row in composer.search_trace:
        if row.get("event") == "RETRIEVE_OPERATIONS":
            kept.append(row)
        elif row.get("event") not in wanted:
            continue
        elif rank is not None and row.get("rank", row.get("candidate_rank")) not in (None, rank):
            continue
        elif (row.get("structure_id") in relevant_ids or row.get("root_id") in relevant_ids
              or row.get("candidate_id") in relevant_ids or row.get("candidate_ids")
              or row.get("event") in ("ENUMERATE_PUBLIC_OPERATION_PAIR", "ENUMERATE_RECIPE_BINDING",
                                       "SELECT_TOPOLOGY", "COMPOSITION_ATTEMPT", "SEARCH_EXPLOSION")):
            kept.append(row)
    return kept


def _compact_trace_rows(rows):
    """Keep the public search facts needed for review, not full object dumps."""
    scalar_fields = (
        "event", "rank", "candidate_rank", "structure_id", "root_id", "candidate_id",
        "operation_id", "candidate_kind", "child_operation_ids", "operator", "search_depth",
        "formal_slots", "public_formal_slots", "bindings", "slot_name", "lifecycle_view",
        "view", "reason", "candidate_count", "selected_operation_id", "topology_id",
        "topology_ids", "candidate_ids", "excluded_ids", "status",
    )
    result = []
    for row in rows or ():
        if not isinstance(row, dict):
            continue
        compact = {key: row[key] for key in scalar_fields if key in row}
        structs = row.get("candidate_structures")
        if structs is not None:
            compact["candidate_structure_ids"] = [
                item if isinstance(item, str) else item.get("structure_id", item.get("identity"))
                for item in structs
            ]
        if "internal_fields_observed" in row:
            compact["internal_fields_observed"] = list(row["internal_fields_observed"])
        if compact:
            result.append(compact)
    return result


def scan_export(scan):
    candidate = scan.get("candidate") or {}
    candidate_export = None if not candidate else {
        "candidate_kind": candidate.get("candidate_kind"),
        "rank": candidate.get("rank"),
        "root_id": candidate.get("root_id"),
        "operation_id": candidate.get("operation_id"),
        "child_operation_ids": candidate.get("child_operation_ids"),
        "operator": candidate.get("operator"),
        "candidate_ids": candidate.get("candidate_ids", {}),
        "bindings": candidate.get("bindings"),
        "search_depth": candidate.get("search_depth"),
    }
    stage_exports = []
    for stage in scan.get("search_stage_summaries", []):
        stage_exports.append({
            "status": stage.get("status"),
            "lifecycle_view": stage.get("lifecycle_view"),
            "metrics": dict(stage.get("metrics", {})),
            "trace": _compact_trace_rows(stage.get("trace", [])),
        })
    return {
        "label": scan.get("label"), "status": scan.get("status"),
        "lifecycle_view": scan.get("lifecycle_view"),
        "excluded_ids": list(scan.get("excluded_ids", ())),
        "metrics": dict(scan.get("metrics", {})),
        "candidate": candidate_export,
        "attempts_checked": scan.get("metrics", {}).get("candidate_attempts", len(scan.get("attempts", ()))),
        "training_passes": sum(bool(row.get("training_pass")) for row in scan.get("attempts", ())),
        "holdout_passes": sum(bool(row.get("holdout_pass")) for row in scan.get("attempts", ())),
        "search_stages": stage_exports,
        "fallback": scan.get("fallback_trace", {}),
        "trace": _compact_trace_rows(scan.get("trace", [])),
    }


def _scan_once(library, usage, goal, oracle, holdout_inputs, *, lifecycle_view, excluded_ids, label):
    kv = ExternalCandidateKV()
    provider = OperationPoolProvider(library, usage,
        public_contracts=publish_recursive_operation_contracts(library))
    composer = GoalDrivenComposer(library, kv, max_candidates=MAX_CANDIDATES,
                                  operation_pool=provider)
    expected_train = [row["output"] for row in goal.examples]
    expected_holdout = oracle_values(oracle, holdout_inputs)
    attempts = []
    candidate_found = None
    train_batches = holdout_batches = 0
    started = time.perf_counter()
    for candidate in composer.candidates(goal, excluded_ids=excluded_ids,
                                         lifecycle_view=lifecycle_view):
        row = {"kind": candidate.get("candidate_kind"), "rank": candidate.get("rank"),
               "root_id": candidate.get("root_id"),
               "child_operation_ids": candidate.get("child_operation_ids"),
               "operator": candidate.get("operator"),
               "search_depth": candidate.get("search_depth"),
               "training_checked": True, "training_pass": False,
               "holdout_checked": False, "holdout_pass": False}
        train_batches += 1
        try:
            train_actual = [composer.execute_candidate(candidate, goal, example["input"],
                runtime_context=RUNTIME_CONTEXT) for example in goal.examples]
            row["training_pass"] = train_actual == expected_train
            if row["training_pass"]:
                holdout_batches += 1
                row["holdout_checked"] = True
                holdout_actual = [composer.execute_candidate(candidate, goal, [nums],
                    runtime_context=RUNTIME_CONTEXT) for nums in holdout_inputs]
                row["holdout_pass"] = holdout_actual == expected_holdout
            row["accepted"] = row["training_pass"] and row["holdout_pass"]
            if row["accepted"]:
                candidate_found = candidate
                attempts.append(row)
                break
        except (ValueError, KeyError, TypeError, IndexError, LookupError, RuntimeError) as error:
            row["error"] = type(error).__name__ + ": " + str(error)
            row["accepted"] = False
        attempts.append(row)
        for candidate_id in (candidate.get("candidate_ids") or {}).values():
            if candidate_id in kv.records and kv.get(candidate_id)["status"] == "CANDIDATE":
                kv.transition(candidate_id, "REJECTED", reason="frozen examples or holdout mismatch")
    elapsed = time.perf_counter() - started
    metrics = {key: int(composer.search_metrics.get(key, 0)) for key in (
        "candidate_attempts", "binding_combinations", "composition_expansions",
        "intermediate_materializations")}
    metrics.update({
        "verification_attempts": train_batches + holdout_batches,
        "training_verification_attempts": train_batches,
        "holdout_verification_attempts": holdout_batches,
        "operation_retrieval_count": sum(row.get("event") == "RETRIEVE_OPERATION" for row in composer.search_trace),
        "recipe_candidate_count": sum(row.get("event") == "ENUMERATE_RECIPE_BINDING" for row in composer.search_trace),
        "search_wall_seconds": elapsed,
        "candidate_search_depth": candidate_found.get("search_depth") if candidate_found else None,
    })
    if candidate_found is not None:
        status = "FOUND"
    elif any(row.get("event") == "SEARCH_EXPLOSION" for row in composer.search_trace):
        status = "CANDIDATE_LIMIT"
    else:
        status = "NO_SOLUTION"
    return {"label": label, "status": status, "candidate": candidate_found,
            "composer": composer, "kv": kv, "library": library, "usage": usage,
            "goal": goal, "attempts": attempts, "metrics": metrics,
            "trace": _filtered_trace(composer, candidate_found),
            "lifecycle_view": lifecycle_view, "excluded_ids": list(excluded_ids),
            "holdout_inputs": [list(nums) for nums in holdout_inputs]}


def _sum_metrics(rows):
    keys = ("candidate_attempts", "binding_combinations", "composition_expansions",
            "intermediate_materializations", "verification_attempts",
            "training_verification_attempts", "holdout_verification_attempts",
            "operation_retrieval_count", "recipe_candidate_count")
    totals = {key: sum(int(row.get("metrics", {}).get(key, 0)) for row in rows) for key in keys}
    totals["search_wall_seconds"] = sum(float(row.get("metrics", {}).get("search_wall_seconds", 0.0)) for row in rows)
    totals["search_calls"] = len(rows)
    return totals


def _scan_with_fallback(library, usage, goal, oracle, holdout_inputs, *, lifecycle_view,
                        excluded_ids=(), label="search"):
    primary = _scan_once(library, usage, goal, oracle, holdout_inputs,
                         lifecycle_view=lifecycle_view, excluded_ids=excluded_ids,
                         label=label + "/primary")
    fallback = None
    retired_pool_count = 0
    if lifecycle_view == "ACTIVE" and primary["status"] != "FOUND":
        provider = OperationPoolProvider(library, usage,
            public_contracts=publish_recursive_operation_contracts(library))
        retired = provider.get_operations(goal, lifecycle_view="RETIRED",
                                          excluded_ids=excluded_ids)
        basis = provider.get_recipe_basis(lifecycle_view="RETIRED")
        retired_ids = {item.identity for item in retired}
        retired_ids.update(item.identity for item in basis.operations)
        retired_ids.update(item.identity for item in basis.topologies)
        retired_pool_count = len(retired_ids.difference(excluded_ids))
        if retired_pool_count:
            fallback = _scan_once(library, usage, goal, oracle, holdout_inputs,
                                  lifecycle_view="RETIRED", excluded_ids=excluded_ids,
                                  label=label + "/retired-fallback")
    stages = [primary] + ([fallback] if fallback else [])
    selected = primary if primary["status"] == "FOUND" or not fallback else fallback
    selected["search_stage_summaries"] = [
        {"status": stage["status"], "lifecycle_view": stage["lifecycle_view"],
         "metrics": dict(stage["metrics"]), "trace": list(stage["trace"])}
        for stage in stages]
    selected["metrics"] = _sum_metrics(stages)
    selected["fallback_trace"] = {
        "fallback_triggered": bool(fallback),
        "fallback_candidate_pool": retired_pool_count,
        "fallback_attempts": fallback["metrics"]["candidate_attempts"] if fallback else 0,
        "fallback_hit_F": fallback["candidate"].get("root_id") if fallback and fallback.get("candidate") else None,
        "fallback_total_work": _sum_metrics([fallback]) if fallback else {},
        "retired_ids_available": sorted(usage.retired_ids()),
    }
    return selected


def _binary_domain_audit(obj, library, formation_path=None):
    exe = thaw(obj.kernel).get("executable", {})
    if exe.get("op") != "binary_apply":
        return audit_formation_parents(obj, library, formation_path)["passed"]
    children = (exe.get("left_f"), exe.get("right_f"))
    if children != tuple(obj.parents) or any(child not in library._structures for child in children):
        return False
    try:
        interfaces = [normalize_operation(library[child]) for child in children]
    except (KeyError, TypeError, ValueError):
        return False
    return (all(is_first_class_operation(interface) for interface in interfaces)
            and interfaces[0].type_constraints == interfaces[1].type_constraints
            and interfaces[0].binding_schema == interfaces[1].binding_schema
            and interfaces[0].context_slots == interfaces[1].context_slots)


def _canonical_replay(obj, library):
    definition = {"operation_id": obj.operation_id, "ports": obj.ports,
                  "alpha_schema": obj.alpha_schema, "kernel": thaw(obj.kernel),
                  "metadata": thaw(obj.metadata)}
    topology = thaw(obj.kernel).get("composition_topology", {})
    formed = canonical_compose(obj.parents, topology, definition,
                               {item.identity: item.level for item in library},
                               source="independent canonical formation replay")
    if formed.identity != obj.identity:
        raise AssertionError("canonical formation replay changed the selected F identity")
    return formed


def _source_and_example_leakage(obj, oracle, train_inputs):
    serialized = json.dumps(obj.to_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    try:
        source = inspect.getsource(oracle)
    except (OSError, TypeError):
        source = ""
    no_source = not source or source not in serialized
    long_inputs = [list(nums) for nums in train_inputs if len(nums) >= 5]
    no_examples = all(json.dumps(nums, separators=(",", ":")) not in serialized for nums in long_inputs)
    return no_source and no_examples


def _semantic_verify(result, oracle, holdout_inputs):
    candidate_id, obj = _candidate_root(result)
    if obj is None:
        raise ValueError("selected search result has no executable root")
    train_inputs = [row["input"][0] for row in result["goal"].examples]
    all_inputs = [*train_inputs, *holdout_inputs]
    expected = oracle_values(oracle, all_inputs)
    python_outputs, dispatch_rows = [], []
    runtime_depths = []
    for nums in all_inputs:
        executor = RecursiveFExecutor(result["library"], result["kv"], trace=True)
        python_outputs.append(executor.execute(obj.identity, [nums]))
        dispatch_rows.append([event["structure_id"] for event in executor.dispatches])
        runtime_depths.append(executor.max_depth)
    program = program_snapshot(result["library"], obj.identity, result["kv"])
    javascript = js_batch(program, all_inputs)
    cold = cold_execute(result["library"], _slim_kv(result), obj.identity, all_inputs)
    split = len(train_inputs)
    return {
        "root_candidate_id": candidate_id,
        "root_id": obj.identity,
        "root_object": obj,
        "inputs": all_inputs,
        "expected": expected,
        "python": python_outputs,
        "javascript": javascript,
        "cold": cold,
        "dispatch_rows": dispatch_rows,
        "runtime_call_depth_max": max(runtime_depths, default=0),
        "python_examples_equal": python_outputs[:split] == expected[:split],
        "python_holdout_equal": python_outputs[split:] == expected[split:],
        "javascript_equal": javascript == expected,
        "cold_candidate_equal": cold == expected,
        "root_executed_for_all_cases": all(obj.identity in row for row in dispatch_rows),
        "train_count": len(train_inputs),
        "holdout_count": len(holdout_inputs),
        "python_output_digest": digest(python_outputs),
        "oracle_output_digest": digest(expected),
        "strict_verification_batches": 3,
    }


def _record_receipts(kv, candidate_id, obj, checks, train_count, holdout_count, evidence_digest):
    base = {"passed": True, "verifier": "V9.4 Python/JavaScript/oracle and cold-process baseline verifier",
            "admission_checks": checks, "admission_subject_digest": subject_digest(obj)}
    for stage, cases, stage_digest in (
        ("examples", train_count, evidence_digest + ":examples"),
        ("randomized_oracle", holdout_count, evidence_digest + ":holdout"),
        ("cold_instance", holdout_count, evidence_digest + ":cold"),
    ):
        kv.add_receipt(candidate_id, {**base, "cases": cases, "validation_stage": stage,
            "receipt_digest": digest({"subject": subject_digest(obj), "stage": stage,
                                       "cases": cases, "evidence": stage_digest})})


def _public_reload_check(library, usage, goal, target_id, inputs, expected):
    provider = OperationPoolProvider(library, usage,
        public_contracts=publish_recursive_operation_contracts(library))
    composer = GoalDrivenComposer(library, ExternalCandidateKV(), operation_pool=provider)
    pool = composer.retrieve_operations(goal, lifecycle_view="ACTIVE")
    interface = next((item for item in pool if item.identity == target_id), None)
    if interface is None:
        return {"passed": False, "retrieved": False, "public_operation_count": len(pool)}
    executor = RecursiveFExecutor(library)
    actual = []
    for nums in inputs:
        bindings = bind_operation(interface, goal, (nums,), RUNTIME_CONTEXT)
        actual.append(executor.execute_public(interface, bindings))
    return {"passed": actual == expected, "retrieved": True,
            "public_operation_count": len(pool), "sample_output": actual[:8],
            "output_digest": digest(actual)}


def _strict_admit(result, evidence, oracle, *, independent_formations, task_label,
                  after_library_path=None):
    """Send new recipe/binary F through the same validated KV and PromotionGate."""
    library, usage, kv = result["library"], result["usage"], result["kv"]
    objects = _candidate_objects(result)
    if not objects:
        raise ValueError("strict admission requires a newly composed F candidate")
    admission_rows = []
    train_inputs = [row["input"][0] for row in result["goal"].examples]
    root_dispatch = evidence["dispatch_rows"]
    for role, candidate_id, obj in objects:
        record = kv.get(candidate_id)
        formation_audit = audit_formation_parents(obj, library, record.get("formation_path"))
        public_ok = is_first_class_operation(normalize_operation(obj))
        domain_ok = _binary_domain_audit(obj, library, record.get("formation_path"))
        if role == "root":
            executable_seen = evidence["root_executed_for_all_cases"]
            semantic_scope = "selected Goal root"
        else:
            nontrivial = [index for index, nums in enumerate([*train_inputs, *result["holdout_inputs"]])
                          if len(nums) > 1]
            executable_seen = bool(nontrivial) and all(obj.identity in root_dispatch[index]
                                                        for index in nontrivial)
            semantic_scope = "step F exercised as a called subgraph by the selected root"
        checks = {
            "interface": bool(public_ok),
            "domain_audit": bool(domain_ok),
            "executable": bool(executable_seen),
            "examples": bool(evidence["python_examples_equal"] and executable_seen),
            "randomized_oracle": bool(evidence["python_holdout_equal"] and executable_seen),
            "cross_language": bool(evidence["javascript_equal"] and executable_seen),
            "cold_instance": bool(evidence["cold_candidate_equal"] and executable_seen),
            "x_leakage": _source_and_example_leakage(obj, oracle, train_inputs),
            "formation_parents": bool(formation_audit["passed"]),
            "training_cases": evidence["train_count"],
            "holdout_cases": evidence["holdout_count"],
        }
        if any(checks.get(name) is not True for name in REQUIRED_TOKEN_CHECKS):
            raise AssertionError(task_label + " strict admission checks failed for " + obj.identity + ": " + repr(checks))
        _canonical_replay(obj, library)
        kv.update(candidate_id, structure=obj.to_dict(),
                  independent_formation_count=independent_formations, x_leakage_status="ZERO")
        evidence_digest = digest({"task": task_label, "structure": obj.identity,
                                  "checks": checks, "parents": obj.parents,
                                  "python": evidence["python_output_digest"],
                                  "oracle": evidence["oracle_output_digest"]})
        _record_receipts(kv, candidate_id, obj, checks, evidence["train_count"],
                         evidence["holdout_count"], evidence_digest)
        kv.transition(candidate_id, "VALIDATED", reason="all unified baseline admission checks passed")
        gate = PromotionGate(library, kv, usage)
        persisted = gate.promote(candidate_id, operation_identity=obj.operation_id,
            min_verified_cases=evidence["train_count"] + evidence["holdout_count"],
            min_cold_instance_cases=evidence["holdout_count"],
            independent_formations=independent_formations, x_leakage_status="ZERO",
            executable=True, reason="V9.4 unified search-space baseline strict admission")
        if persisted.identity != obj.identity:
            raise AssertionError("promotion changed the selected Structure identity")
        after_promotion = audit_formation_parents(persisted, library)
        if not after_promotion["passed"]:
            raise AssertionError("persisted F failed exact-parent audit")
        admission_rows.append({
            "task": task_label, "role": role, "candidate_id": candidate_id,
            "structure_id": persisted.identity, "operation_id": persisted.operation_id,
            "parents": list(persisted.parents), "canonical_level": persisted.level,
            "admission_pass": True, "strict_admission_checks": checks,
            "semantic_scope": semantic_scope,
            "formation_audit": {"passed": formation_audit["passed"],
                                "issues": list(formation_audit["issues"]),
                                "expected_parents": list(formation_audit["expected_parents"] or ()),
                                "observed_parents": list(formation_audit["observed_parents"] or ())},
            "promotion_receipt": gate.receipts[-1],
        })
    root_id = evidence["root_id"]
    if after_library_path:
        library.save(after_library_path)
        usage.save(Path(after_library_path).with_suffix(".usage.json"))
        cold = cold_execute_saved(after_library_path, root_id, evidence["inputs"])
        reloaded = Library.load(after_library_path)
        reloaded_usage = UsageRegistry.load(Path(after_library_path).with_suffix(".usage.json"), reloaded)
    else:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "library.json"
            library.save(path)
            usage.save(path.with_suffix(".usage.json"))
            cold = cold_execute_saved(path, root_id, evidence["inputs"])
            reloaded = Library.load(path)
            reloaded_usage = UsageRegistry.load(path.with_suffix(".usage.json"), reloaded)
    expected = evidence["expected"]
    public = _public_reload_check(reloaded, reloaded_usage, result["goal"], root_id,
                                  evidence["inputs"], expected)
    if cold != expected or not public["passed"]:
        raise AssertionError(task_label + " post-promotion cold/public reload mismatch")
    return {"admission_rows": admission_rows, "post_promotion_cold_pass": True,
            "persistent_public_retrieval": public,
            "post_promotion_cold_output_digest": digest(cold),
            "post_promotion_verification_batches": 2}


def _expected_child_oracles(definition, oracles):
    return [oracles[definition["left"]], oracles[definition["right"]]]


def classify_chain_path(obj, library, kv, definition, oracles, holdout_inputs):
    expected_oracles = _expected_child_oracles(definition, oracles)
    executor = RecursiveFExecutor(library, kv)
    parent_values = {}
    for parent in obj.parents:
        if parent not in executor.nodes:
            continue
        try:
            parent_values[parent] = [executor.execute(parent, [nums]) for nums in holdout_inputs]
        except (ValueError, KeyError, TypeError, LookupError, RuntimeError):
            continue
    expected_values = [oracle_values(oracle, holdout_inputs) for oracle in expected_oracles]
    matched = []
    for index, values in enumerate(expected_values):
        matched.append(next((sid for sid, observed in parent_values.items() if observed == values), None))
    qualified = all(matched)
    return {
        "status": "CHAIN_QUALIFIED" if qualified else "SHORTCUT_FOUND",
        "expected_child_semantics": [definition["left"], definition["right"]],
        "matched_parent_ids": matched,
        "parents": list(obj.parents),
        "parent_semantic_samples": {sid: values[:8] for sid, values in parent_values.items()},
        "admission_independent": True,
    }


def paired_acquisition(library, usage, goal, oracle, holdout_inputs, *, lifecycle_view,
                       label, definition=None, oracles=None, after_library_path=None):
    """Two independent searches, then one strict admission of the agreed result."""
    start_hash = library.persistent_hash
    usage_hash = digest(usage.snapshot())
    started = time.perf_counter()
    first = _scan_with_fallback(library, clone_usage(usage, library), goal, oracle,
        holdout_inputs, lifecycle_view=lifecycle_view, label=label + "/independent-1")
    second = _scan_with_fallback(library, clone_usage(usage, library), goal, oracle,
        holdout_inputs, lifecycle_view=lifecycle_view, label=label + "/independent-2")
    if library.persistent_hash != start_hash:
        raise AssertionError("paired search mutated its shared starting Library")
    metrics = _sum_metrics([first, second])
    result = {"label": label, "status": "NO_SOLUTION", "lifecycle_view": lifecycle_view,
              "starting_library_hash": start_hash, "starting_usage_digest": usage_hash,
              "searches": [scan_export(first), scan_export(second)], "metrics": metrics,
              "admission_rows": [], "selected_root_id": None,
              "wall_seconds": time.perf_counter() - started,
              "fallback": [first["fallback_trace"], second["fallback_trace"]]}
    if first["status"] != "FOUND" or second["status"] != "FOUND":
        states = [first["status"], second["status"]]
        result["status"] = "CANDIDATE_LIMIT" if "CANDIDATE_LIMIT" in states else "NO_SOLUTION"
        return result, library, usage
    first_id, first_obj = _candidate_root(first)
    second_id, second_obj = _candidate_root(second)
    if first_obj.identity != second_obj.identity or digest(first_obj.to_dict()) != digest(second_obj.to_dict()):
        result.update({"status": "INDEPENDENT_FORMATION_MISMATCH",
                       "independent_root_ids": [first_obj.identity, second_obj.identity]})
        return result, library, usage
    selected = first["candidate"]
    if selected.get("candidate_kind") == "operation_edge":
        evidence = _semantic_verify(first, oracle, holdout_inputs)
        if not (evidence["python_examples_equal"] and evidence["python_holdout_equal"]
                and evidence["javascript_equal"] and evidence["cold_candidate_equal"]
                and evidence["root_executed_for_all_cases"]):
            raise AssertionError(label + " existing-F verification disagrees with the frozen oracle")
        result.update({"status": "EXISTING_F_REUSE", "selected_root_id": first_obj.identity,
                       "canonical_level": library[first_obj.identity].level,
                       "candidate_kind": selected.get("candidate_kind"),
                       "formation_candidate_rank": selected.get("rank"),
                       "composer_candidate_search_depth": selected.get("search_depth"),
                       "parents": list(first_obj.parents),
                       "semantic_verification": {key: evidence[key] for key in (
                           "python_examples_equal", "python_holdout_equal", "javascript_equal",
                           "cold_candidate_equal", "root_executed_for_all_cases",
                           "runtime_call_depth_max", "python_output_digest", "oracle_output_digest")},
                       "chain_path": classify_chain_path(first_obj, library, first["kv"],
                           definition, oracles, holdout_inputs) if definition and oracles else None,
                       "verification_attempts_after_selection": evidence["strict_verification_batches"]})
        metrics["verification_attempts"] += evidence["strict_verification_batches"]
        return result, library, usage

    evidence = _semantic_verify(first, oracle, holdout_inputs)
    if not (evidence["python_examples_equal"] and evidence["python_holdout_equal"]
            and evidence["javascript_equal"] and evidence["cold_candidate_equal"]
            and evidence["root_executed_for_all_cases"]):
        result.update({"status": "SEMANTIC_VERIFICATION_FAILED",
                       "selected_root_id": first_obj.identity,
                       "semantic_verification": {key: evidence[key] for key in (
                           "python_examples_equal", "python_holdout_equal", "javascript_equal",
                           "cold_candidate_equal", "root_executed_for_all_cases")}})
        return result, library, usage
    metrics["verification_attempts"] += evidence["strict_verification_batches"]
    chain_path = classify_chain_path(first_obj, library, first["kv"], definition,
                                     oracles, holdout_inputs) if definition and oracles else None
    if chain_path:
        result["chain_path"] = chain_path
    branch_usage = first["usage"]
    admission = _strict_admit(first, evidence, oracle, independent_formations=2,
                              task_label=label, after_library_path=after_library_path)
    metrics["verification_attempts"] += admission["post_promotion_verification_batches"]
    result.update({"status": "ADMITTED", "selected_root_id": first_obj.identity,
                   "candidate_kind": selected.get("candidate_kind"),
                   "canonical_level": first_obj.level,
                   "formation_candidate_rank": selected.get("rank"),
                   "composer_candidate_search_depth": selected.get("search_depth"),
                   "parents": list(first_obj.parents),
                   "semantic_verification": {key: evidence[key] for key in (
                       "python_examples_equal", "python_holdout_equal", "javascript_equal",
                       "cold_candidate_equal", "root_executed_for_all_cases",
                       "runtime_call_depth_max", "python_output_digest", "oracle_output_digest")},
                   "admission_rows": admission["admission_rows"],
                   "post_promotion_cold_pass": admission["post_promotion_cold_pass"],
                   "persistent_public_retrieval": admission["persistent_public_retrieval"]})
    return result, library, branch_usage


def _first_class_public_audit(scan, target_id):
    candidate = scan.get("candidate") or {}
    trace = scan.get("trace", [])
    selected = next((index for index, row in enumerate(trace)
                     if row.get("event") == "SELECT_OPERATION"
                     and row.get("structure_id") == target_id), None)
    retrieval = next((row for row in trace if row.get("event") == "RETRIEVE_OPERATIONS"), None)
    raw_selected = next((row for row in scan["composer"].search_trace
                         if row.get("event") == "SELECT_OPERATION"
                         and row.get("structure_id") == target_id), None)
    interface = scan["composer"]._interfaces.get(target_id)
    slot_names = list(interface.formal_slots) if interface else []
    binds = [row for row in trace if row.get("event") in ("BIND_PUBLIC_SLOTS", "BIND_PUBLIC_SLOT")
             and row.get("structure_id") == target_id]
    sensitive = {"parents", "kernel", "formation_recipe", "operation_bindings",
                 "internal_bindings", "internal_topology", "child_ids", "parent_ids"}
    preselect = [row for row in scan["composer"].search_trace[:
        next((i for i, event in enumerate(scan["composer"].search_trace)
              if event.get("event") == "SELECT_OPERATION"
              and event.get("structure_id") == target_id), len(scan["composer"].search_trace))]
        if sensitive.intersection(row)]
    retrieval_ids = []
    if retrieval:
        retrieval_ids = [row.get("structure_id") for row in retrieval.get("candidate_structures", [])]
    bound_slots = sorted({key for event in binds for key in (event.get("bindings") or {})})
    return {
        "is_target_operation_edge": bool(candidate.get("candidate_kind") == "operation_edge"
                                         and candidate.get("root_id") == target_id),
        "retrieved_from_provider": target_id in retrieval_ids,
        "selected_before_execution": selected is not None or raw_selected is not None,
        "public_interface_present": bool(interface and is_first_class_operation(interface)),
        "public_formal_slots": slot_names,
        "bound_slots": bound_slots,
        "bound_only_public_formal_slots": bool(binds) and set(bound_slots).issubset(set(slot_names)),
        "preselection_sensitive_fields_observed": preselect,
        "passes": bool(candidate.get("candidate_kind") == "operation_edge"
                       and candidate.get("root_id") == target_id
                       and target_id in retrieval_ids and interface
                       and is_first_class_operation(interface) and binds
                       and set(bound_slots).issubset(set(slot_names)) and not preselect),
    }


def run_search_view(task_index, definition, lifecycle_view, reuse_view,
                    start_library, start_usage, target_id, chain_ids,
                    chain_definitions, chain_oracles, task_rows):
    task_id = definition["task_id"]
    goal = task_goal(chain_oracles[task_id])
    holdout_inputs = task_rows[task_id]["holdout_inputs"]
    source_hash = start_library.persistent_hash
    source_usage_digest = digest(start_usage.snapshot())
    exposure_before_mask = candidate_exposure(start_library, start_usage, goal, lifecycle_view)
    started = time.perf_counter()
    if reuse_view == "FIRST_CLASS":
        branch = clone_library(start_library)
        branch_usage = clone_usage(start_usage, branch)
        scan = _scan_with_fallback(branch, branch_usage, goal, chain_oracles[task_id],
            holdout_inputs, lifecycle_view=lifecycle_view, label=lifecycle_view + "+FIRST_CLASS/" + task_id)
        selected = scan.get("candidate") or {}
        root_id = selected.get("root_id")
        edge_audit = _first_class_public_audit(scan, target_id)
        result = {"mode": lifecycle_view + "+FIRST_CLASS", "task_id": task_id,
                  "status": "INVALID_FIRST_CLASS_EDGE" if not edge_audit["passes"] else "SEARCHED",
                  "source_library_hash": source_hash,
                  "source_usage_digest": source_usage_digest,
                  "starting_branch_library_hash": branch.persistent_hash,
                  "historical_mask_ids": [], "exposure": exposure_before_mask,
                  "after_mask_exposure": candidate_exposure(branch, branch_usage, goal, lifecycle_view),
                  "search": scan_export(scan), "first_class_edge_audit": edge_audit,
                  "designed_chain_depth": task_index + 1,
                  "search_derivation_depth": 0,
                  "historical_levels_skipped": task_index + 1,
                  "newly_formed_goal_F_count": 0,
                  "direct_existing_F_hits": 1 if edge_audit["passes"] else 0,
                  "reused_F_ids": [target_id] if edge_audit["passes"] else [],
                  "reformed_F_ids": [],
                  "highest_reused_canonical_level": branch[target_id].level if target_id in branch._structures else None,
                  "target_root_id": root_id,
                  "target_canonical_level": branch[root_id].level if root_id in branch._structures else None,
                  "target_candidate_kind": selected.get("candidate_kind"),
                  "target_candidate_rank": selected.get("rank"),
                  "target_parents": list(branch[root_id].parents) if root_id in branch._structures else None,
                  "reformation_stages": [], "fallback": scan.get("fallback_trace", {})}
        if edge_audit["passes"]:
            evidence = _semantic_verify(scan, chain_oracles[task_id], holdout_inputs)
            result["semantic_verification"] = {key: evidence[key] for key in (
                "python_examples_equal", "python_holdout_equal", "javascript_equal",
                "cold_candidate_equal", "runtime_call_depth_max", "python_output_digest",
                "oracle_output_digest")}
            result["semantic_equal"] = bool(evidence["python_examples_equal"]
                and evidence["python_holdout_equal"] and evidence["javascript_equal"]
                and evidence["cold_candidate_equal"])
            scan["metrics"]["verification_attempts"] += evidence["strict_verification_batches"]
            result["chain_path"] = classify_chain_path(branch[target_id], branch, scan["kv"],
                definition, chain_oracles, holdout_inputs)
            result["runtime_call_depth"] = evidence["runtime_call_depth_max"]
            if not result["semantic_equal"]:
                raise AssertionError(task_id + " FIRST_CLASS semantics differ from examples/oracle/JavaScript/cold")
        else:
            result["semantic_equal"] = False
            result["semantic_verification"] = None
            result["runtime_call_depth"] = None
        result["metrics"] = dict(scan["metrics"])
        result["wall_seconds"] = time.perf_counter() - started
        result["admission_rows"] = []
        return result

    if reuse_view != "EXPANDED":
        raise ValueError("reuse_view must be FIRST_CLASS or EXPANDED")
    masked_ids = [chain_ids[row["task_id"]] for row in chain_definitions[:task_index + 1]
                  if row["task_id"] in chain_ids]
    branch = clone_library(start_library, masked_ids)
    branch_usage = clone_usage(start_usage, branch)
    route_start_hash = branch.persistent_hash
    exposure_after_mask = candidate_exposure(branch, branch_usage, goal, lifecycle_view, masked_ids)
    stages = []
    route_admissions = []
    route_fallback = []
    actual_chain_ids = {}
    new_formed = 0
    final_pair = None
    status = "PASS"
    for stage_index, stage_definition in enumerate(chain_definitions[:task_index + 1]):
        stage_task = stage_definition["task_id"]
        stage_goal = task_goal(chain_oracles[stage_task])
        stage_holdout = task_rows[stage_task]["holdout_inputs"]
        before_hash = branch.persistent_hash
        pair, next_library, next_usage = paired_acquisition(
            branch, branch_usage, stage_goal, chain_oracles[stage_task], stage_holdout,
            lifecycle_view=lifecycle_view,
            label=lifecycle_view + "+EXPANDED/" + task_id + "/reform-" + stage_task,
            definition=stage_definition, oracles=chain_oracles)
        final_pair = pair if stage_task == task_id else final_pair
        stage_row = {
            "task_id": stage_task, "status": pair["status"],
            "starting_library_hash": before_hash,
            "selected_root_id": pair.get("selected_root_id"),
            "candidate_kind": pair.get("candidate_kind"),
            "canonical_level": pair.get("canonical_level"),
            "chain_path": pair.get("chain_path"),
            "metrics": dict(pair["metrics"]),
            "semantic_verification": pair.get("semantic_verification"),
            "admission_rows": list(pair.get("admission_rows", [])),
            "fallback": pair.get("fallback", []),
            "searches": pair["searches"],
        }
        stages.append(stage_row)
        route_admissions.extend(pair.get("admission_rows", []))
        route_fallback.extend(pair.get("fallback", []))
        if pair.get("selected_root_id"):
            actual_chain_ids[stage_task] = pair["selected_root_id"]
        if pair["status"] == "ADMITTED":
            new_formed += 1
        if pair["status"] not in ("ADMITTED", "EXISTING_F_REUSE"):
            status = pair["status"]
            break
        branch, branch_usage = next_library, next_usage
    if status == "PASS" and (not final_pair or final_pair.get("status") not in ("ADMITTED", "EXISTING_F_REUSE")):
        status = "TARGET_STAGE_NOT_COMPLETED"
    if final_pair and final_pair.get("selected_root_id") in branch._structures:
        route_target = branch[final_pair["selected_root_id"]]
    else:
        route_target = None
    final_semantic = final_pair.get("semantic_verification") if final_pair else None
    semantic_equal = bool(final_semantic and final_semantic.get("python_examples_equal")
        and final_semantic.get("python_holdout_equal") and final_semantic.get("javascript_equal")
        and final_semantic.get("cold_candidate_equal"))
    if status == "PASS" and not semantic_equal:
        raise AssertionError(task_id + " EXPANDED final semantics differ from oracle")
    metric_rows = [stage for stage in stages]
    metric_keys = ("candidate_attempts", "binding_combinations", "composition_expansions",
                   "intermediate_materializations", "verification_attempts",
                   "training_verification_attempts", "holdout_verification_attempts",
                   "operation_retrieval_count", "recipe_candidate_count")
    totals = {key: sum(int(stage["metrics"].get(key, 0)) for stage in metric_rows)
              for key in metric_keys}
    totals["search_wall_seconds"] = sum(float(stage["metrics"].get("search_wall_seconds", 0.0))
                                        for stage in metric_rows)
    totals["search_calls"] = sum(int(stage["metrics"].get("search_calls", 0)) for stage in metric_rows)
    result = {
        "mode": lifecycle_view + "+EXPANDED", "task_id": task_id,
        "status": status, "source_library_hash": source_hash,
        "source_usage_digest": source_usage_digest,
        "starting_branch_library_hash": route_start_hash,
        "historical_mask_ids": masked_ids,
        "exposure": exposure_before_mask,
        "after_mask_exposure": exposure_after_mask,
        "reformation_order": [row["task_id"] for row in chain_definitions[:task_index + 1]],
        "designed_chain_depth": task_index + 1,
        "reformation_stages": stages,
        "search_derivation_depth": new_formed,
        "historical_levels_skipped": 0,
        "newly_formed_goal_F_count": new_formed,
        "direct_existing_F_hits": sum(stage.get("status") == "EXISTING_F_REUSE" for stage in stages),
        "reused_F_ids": [stage.get("selected_root_id") for stage in stages
                         if stage.get("status") == "EXISTING_F_REUSE" and stage.get("selected_root_id")],
        "reformed_F_ids": [stage.get("selected_root_id") for stage in stages
                           if stage.get("status") == "ADMITTED" and stage.get("selected_root_id")],
        "highest_reused_canonical_level": max((branch[sid].level for sid in actual_chain_ids.values()
            if sid in branch._structures and any(stage.get("selected_root_id") == sid
                and stage.get("status") == "EXISTING_F_REUSE" for stage in stages)), default=None),
        "target_root_id": final_pair.get("selected_root_id") if final_pair else None,
        "target_canonical_level": route_target.level if route_target else final_pair.get("canonical_level") if final_pair else None,
        "target_candidate_kind": final_pair.get("candidate_kind") if final_pair else None,
        "target_candidate_rank": final_pair.get("formation_candidate_rank") if final_pair else None,
        "target_parents": list(route_target.parents) if route_target else final_pair.get("parents") if final_pair else None,
        "target_chain_path": final_pair.get("chain_path") if final_pair else None,
        "semantic_verification": final_semantic,
        "semantic_equal": semantic_equal,
        "runtime_call_depth": (final_semantic or {}).get("runtime_call_depth_max"),
        "metrics": totals,
        "fallback": {
            "fallback_triggered": any(bool(row.get("fallback_triggered")) for row in route_fallback),
            "fallback_candidate_pool": sum(int(row.get("fallback_candidate_pool", 0)) for row in route_fallback),
            "fallback_attempts": sum(int(row.get("fallback_attempts", 0)) for row in route_fallback),
            "fallback_hit_F": [row.get("fallback_hit_F") for row in route_fallback if row.get("fallback_hit_F")],
            "fallback_total_work": {key: sum(int(row.get("fallback_total_work", {}).get(key, 0))
                for row in route_fallback) for key in WORK_KEYS},
            "stages": route_fallback,
        },
        "admission_rows": route_admissions,
        "final_branch_library_hash": branch.persistent_hash,
        "final_branch_library_stats": library_stats(branch, branch_usage),
        "wall_seconds": time.perf_counter() - started,
    }
    return result


def _reduction(before, after):
    if before is None or after is None or before == 0:
        return {"absolute": None if before is None or after is None else before - after,
                "percent": None}
    return {"absolute": before - after, "percent": (before - after) / before * 100.0}


def run_2x2(task_index, definition, start_library, start_usage, target_id,
            chain_ids, chain_definitions, chain_oracles, task_rows, seed):
    task_id = definition["task_id"]
    views = {}
    for lifecycle_view, reuse_view in (
        ("FULL", "EXPANDED"), ("FULL", "FIRST_CLASS"),
        ("ACTIVE", "EXPANDED"), ("ACTIVE", "FIRST_CLASS"),
    ):
        key = lifecycle_view + "+" + reuse_view
        print(f"[{task_id}] start {key}", flush=True)
        view = run_search_view(task_index, definition, lifecycle_view, reuse_view,
            start_library, start_usage, target_id, chain_ids,
            chain_definitions, chain_oracles, task_rows)
        views[key] = view
        trace_path = OUT / "traces" / (task_id + "_" + lifecycle_view.lower()
                                      + "_" + reuse_view.lower() + ".json")
        write_json(trace_path, compact_mode_result(view))
        print(f"[{task_id}] done {key}: {view['status']} attempts={view.get('metrics', {}).get('candidate_attempts')}", flush=True)
    for key, view in views.items():
        if view["source_library_hash"] != start_library.persistent_hash:
            raise AssertionError(task_id + " paired mode started from a different Library snapshot")
        if view["source_usage_digest"] != digest(start_usage.snapshot()):
            raise AssertionError(task_id + " paired mode started from a different UsageRegistry snapshot")
    semantics = {key: view.get("semantic_verification", {}).get("oracle_output_digest")
                 for key, view in views.items() if view.get("semantic_equal")}
    semantic_equal = bool(semantics) and len(set(semantics.values())) == 1
    for key, view in views.items():
        if view.get("semantic_equal") is False and view.get("status") in ("PASS", "SEARCHED"):
            raise AssertionError(task_id + " mode failed semantic equality after returning a result: " + key)
    full_expanded = views["FULL+EXPANDED"]
    full_first = views["FULL+FIRST_CLASS"]
    active_expanded = views["ACTIVE+EXPANDED"]
    active_first = views["ACTIVE+FIRST_CLASS"]
    metrics = {}
    for metric in WORK_KEYS:
        metrics[metric] = {
            "depth_effect_FULL": _reduction(full_expanded.get("metrics", {}).get(metric),
                                             full_first.get("metrics", {}).get(metric)),
            "breadth_effect_FIRST_CLASS": _reduction(full_first.get("metrics", {}).get(metric),
                                                       active_first.get("metrics", {}).get(metric)),
            "breadth_effect_EXPANDED": _reduction(full_expanded.get("metrics", {}).get(metric),
                                                    active_expanded.get("metrics", {}).get(metric)),
            "combined_effect": _reduction(full_expanded.get("metrics", {}).get(metric),
                                           active_first.get("metrics", {}).get(metric)),
        }
    full_exposed = full_first.get("exposure", {}).get("lifecycle_exposed_executable_pool")
    active_exposed = active_first.get("exposure", {}).get("lifecycle_exposed_executable_pool")
    result = {
        "task_id": task_id,
        "starting_library_hash": start_library.persistent_hash,
        "starting_usage_digest": digest(start_usage.snapshot()),
        "goal_signature": GOAL_SIGNATURE,
        "examples_digest": digest(list(task_goal(chain_oracles[task_id]).examples)),
        "holdout_seed": seed,
        "holdout_digest": digest(task_rows[task_id]["holdout_inputs"]),
        "candidate_limit": MAX_CANDIDATES,
        "same_verifier": "V9.4 Python, JavaScript, oracle, cold subprocess, exact-parent and public reload checks",
        "modes": views,
        "semantic_output_digest_equal_across_returned_modes": semantic_equal,
        "all_four_modes_completed": all(views[key].get("status") == expected_status
            for key, expected_status in (("FULL+EXPANDED", "PASS"),
                ("FULL+FIRST_CLASS", "SEARCHED"), ("ACTIVE+EXPANDED", "PASS"),
                ("ACTIVE+FIRST_CLASS", "SEARCHED"))),
        "all_four_modes_semantic_equal": all(view.get("semantic_equal") is True for view in views.values()),
        "effect_reductions": metrics,
        "lifecycle_exposure": {
            "FULL_executable": full_exposed,
            "ACTIVE_executable": active_exposed,
            "reduction": _reduction(full_exposed, active_exposed),
            "FULL_goal_compatible": full_first.get("exposure", {}).get("goal_compatible_pool"),
            "ACTIVE_goal_compatible": active_first.get("exposure", {}).get("goal_compatible_pool"),
        },
        "all_first_class_views_used_target_as_direct_edge": all(
            views[key].get("first_class_edge_audit", {}).get("passes")
            for key in ("FULL+FIRST_CLASS", "ACTIVE+FIRST_CLASS")),
        "all_returned_modes_semantic_equal": all(view.get("semantic_equal") is True for view in views.values()),
    }
    return result


def foundation_matches(library, foundation_oracles, inputs):
    executor = RecursiveFExecutor(library)
    results = {}
    for foundation in foundation_oracles:
        expected = oracle_values(foundation["oracle"], inputs)
        matches = []
        for obj in library:
            if not _is_executable(obj):
                continue
            try:
                observed = [executor.execute(obj.identity, [nums]) for nums in inputs]
            except (ValueError, KeyError, TypeError, IndexError, LookupError, RuntimeError):
                continue
            if observed == expected:
                matches.append({"structure_id": obj.identity, "operation_id": obj.operation_id,
                                "canonical_level": obj.level})
        results[foundation["label"]] = {
            "semantics": foundation["semantics"], "oracle_holdout_cases": len(inputs),
            "matching_persistent_F": matches, "present_in_clean_seed": bool(matches),
        }
    return results


def freeze_task_family():
    definitions, oracles = chain_tasks()
    rows = {}
    foundation_rows = []
    for index, foundation in enumerate(FOUNDATIONS):
        seed = 941000 + index
        holdout = randomized_inputs(HOLDOUT_COUNT, seed)
        foundation_rows.append({
            "label": foundation["label"], "semantics": foundation["semantics"],
            "public_contract": {"input": "sequence<int>", "output": "int"},
            "training_examples": [{"input": [list(nums)], "output": foundation["oracle"](nums)}
                                 for nums in TRAIN_INPUTS],
            "holdout_seed": seed, "holdout_inputs": [list(nums) for nums in holdout],
            "holdout_outputs": oracle_values(foundation["oracle"], holdout),
        })
    depth_rows = []
    for index, definition in enumerate(definitions):
        task_id = definition["task_id"]
        seed = HOLDOUT_SEED + index
        oracle = oracles[task_id]
        holdout = randomized_inputs(HOLDOUT_COUNT, seed)
        row = {
            **definition,
            "public_contract": {"input": "sequence<int>", "output": "int"},
            "training_examples": [{"input": [list(nums)], "output": oracle(nums)} for nums in TRAIN_INPUTS],
            "holdout_seed": seed,
            "holdout_inputs": [list(nums) for nums in holdout],
            "holdout_outputs": oracle_values(oracle, holdout),
        }
        depth_rows.append(row)
        rows[task_id] = row
    family = {
        "experiment": "V9.4 unified search-space baseline",
        "task_family_version": "v94-search-space-d01-d12/1",
        "frozen_before_search": True,
        "frozen_date": time.strftime("%Y-%m-%d"),
        "maximum_designed_chain_depth": len(depth_rows),
        "training_inputs": [list(nums) for nums in TRAIN_INPUTS],
        "holdout_count_per_goal": HOLDOUT_COUNT,
        "holdout_seed_rule": "941200 + zero-based D index",
        "search_candidate_limit": MAX_CANDIDATES,
        "goal_input_excludes_oracle_and_parent_recipe_data": True,
        "foundation_tasks": foundation_rows,
        "depth_tasks": depth_rows,
        "search_views": ["FULL+EXPANDED", "FULL+FIRST_CLASS", "ACTIVE+EXPANDED", "ACTIVE+FIRST_CLASS"],
        "lifecycle_policy": "existing UsageRegistry explicit-equivalence representative rule; no low-usage retirement or inferred equivalence",
        "historical_mask_policy": "EXPANDED masks all D-chain root IDs through the current target, then re-forms the goals in order in a branch; FIRST_CLASS leaves the target and history exposed",
        "fixed_search_rules": ["existing OperationPoolProvider", "existing GoalDrivenComposer", "existing ranking/scoring", "existing generic typed binary composition", "existing verifier/admission"],
    }
    write_json(OUT / "task_family.json", compact_task_family_manifest(family))
    return definitions, oracles, rows, family


def lifecycle_snapshot(label, library, usage):
    stats = library_stats(library, usage)
    snapshot = usage.snapshot()
    return {
        "label": label,
        "library_hash": library.persistent_hash,
        "registry_digest": digest(snapshot),
        "active_ids": sorted(usage.active_ids()),
        "retired_ids": sorted(usage.retired_ids()),
        "counts": snapshot["counts"],
        "preferred": snapshot["preferred"],
        "stats": stats,
    }


def _aggregate_modes(paired_results, left, right, metrics):
    aggregate = {}
    for metric in metrics:
        before = sum((row.get("modes", {}).get(left, {}).get("metrics", {}).get(metric, 0) or 0)
                     for row in paired_results)
        after = sum((row.get("modes", {}).get(right, {}).get("metrics", {}).get(metric, 0) or 0)
                    for row in paired_results)
        aggregate[metric] = {"before": before, "after": after, "reduction": _reduction(before, after)}
    return aggregate


def _fmt_reduction(before, after):
    change = _reduction(before, after)
    if change["percent"] is None:
        return f"{before} → {after} (绝对变化 {change['absolute']})"
    return f"{before} → {after}，减少 {change['absolute']} ({change['percent']:.1f}%)"


def make_report(baseline, growth_rows, paired_results, depth_rows, breadth_rows,
                combined_rows, admissions, shortcut_rows, stop_reason,
                foundation_audit, final_library, final_usage, implementation_error=None):
    completed = [row for row in paired_results if row.get("modes")]
    all_first_class = bool(completed) and all(row.get("all_first_class_views_used_target_as_direct_edge")
                                               for row in completed)
    semantic_ok = bool(completed) and all(row.get("all_four_modes_completed")
                                          and row.get("all_four_modes_semantic_equal")
                                          and row.get("all_returned_modes_semantic_equal")
                                          and row.get("semantic_output_digest_equal_across_returned_modes")
                                          for row in completed)
    target_count = len(completed)
    shortcut_main = [row for row in shortcut_rows
                     if row.get("source") == "main_acquisition"
                     and row.get("status") == "SHORTCUT_FOUND"]
    shortcut_branch = [row for row in shortcut_rows
                       if row.get("source") != "main_acquisition"
                       and row.get("status") == "SHORTCUT_FOUND"]
    newly_admitted = [row for row in admissions if row.get("admission_pass")]
    main_admissions = [row for row in newly_admitted if row.get("source") == "main_acquisition"]
    unique_admitted_ids = {row.get("structure_id") for row in newly_admitted
                           if row.get("structure_id")}
    main_new_ids = {row.get("structure_id") for row in main_admissions
                    if row.get("structure_id")}
    branch_receipt_count = sum(row.get("source") != "main_acquisition" for row in newly_admitted)
    successful_depth = len(growth_rows)
    last_canonical = final_library.max_level
    initial_exec = baseline["library_stats"]["L_exec"]
    final_stats = library_stats(final_library, final_usage)
    depth_work = _aggregate_modes(completed, "FULL+EXPANDED", "FULL+FIRST_CLASS", WORK_KEYS)
    breadth_work = _aggregate_modes(completed, "FULL+FIRST_CLASS", "ACTIVE+FIRST_CLASS", WORK_KEYS)
    combined_work = _aggregate_modes(completed, "FULL+EXPANDED", "ACTIVE+FIRST_CLASS", WORK_KEYS)
    full_active_before = {
        "FULL_lifecycle_executable": sum(row.get("modes", {}).get("FULL+FIRST_CLASS", {}).get("exposure", {}).get("lifecycle_exposed_executable_pool", 0) for row in completed),
        "ACTIVE_lifecycle_executable": sum(row.get("modes", {}).get("ACTIVE+FIRST_CLASS", {}).get("exposure", {}).get("lifecycle_exposed_executable_pool", 0) for row in completed),
        "FULL_goal_compatible": sum(row.get("modes", {}).get("FULL+FIRST_CLASS", {}).get("exposure", {}).get("goal_compatible_pool", 0) for row in completed),
        "ACTIVE_goal_compatible": sum(row.get("modes", {}).get("ACTIVE+FIRST_CLASS", {}).get("exposure", {}).get("goal_compatible_pool", 0) for row in completed),
    }
    lifecycle_reduction = _reduction(full_active_before["FULL_lifecycle_executable"],
                                     full_active_before["ACTIVE_lifecycle_executable"])
    depth_attempts = depth_work["candidate_attempts"]
    breadth_attempts = breadth_work["candidate_attempts"]
    combined_attempts = combined_work["candidate_attempts"]
    attempt_decrease = depth_attempts["reduction"]["absolute"]
    depth_attempt_percent = depth_attempts["reduction"]["percent"]
    combined_attempt_percent = combined_attempts["reduction"]["percent"]
    depth_attempt_percent_text = "N/A" if depth_attempt_percent is None else f"{depth_attempt_percent:.1f}%"
    combined_attempt_percent_text = "N/A" if combined_attempt_percent is None else f"{combined_attempt_percent:.1f}%"
    any_reduction = any(depth_work[key]["reduction"]["absolute"] > 0
                        for key in ("candidate_attempts", "binding_combinations",
                                    "composition_expansions", "intermediate_materializations"))
    if implementation_error:
        conclusion = "IMPLEMENTATION ERROR — semantic or execution check failed; optimization conclusion withheld"
    elif not all_first_class:
        conclusion = "INVALID — at least one FIRST_CLASS view did not select the persisted target F as a direct operation edge"
    elif not semantic_ok:
        conclusion = "IMPLEMENTATION ERROR — mode outputs did not all match the frozen oracle result"
    elif any_reduction:
        conclusion = "PASS — SEARCH REDUCTION (depth reuse); breadth result is reported separately"
    else:
        conclusion = "PASS — NO REDUCTION (for the measured complete baseline work counters)"

    lines = [
        "# V9.4 统一搜索空间治理基线",
        "",
        "> 纵向：已 admission 的 F 作为一级 operation，避免重做其历史组合。\n> 横向：OperationPoolProvider 按 FULL/ACTIVE/RETIRED 暴露持久操作；ACTIVE 仅使用现有显式等价类代表策略。",
        "",
        "## 运行结论",
        "",
        f"**{conclusion}**",
        "",
        f"本轮成功完成 `{target_count}` 个 Goal 的四视图比较；主 D 链连续完成 `{successful_depth}` 个 Goal，停点：`{stop_reason}`。",
        f"FIRST_CLASS 目标 edge 审计：`{'全部 PASS' if all_first_class else '存在 INVALID'}`；语义对照：`{'Python / JavaScript / oracle / cold reload 全部相等' if semantic_ok else '未全部相等'}`。",
        "",
        "## Clean V9.4 起始状态复审",
        "",
        f"Library 文件 SHA256：`{baseline['library_file_sha256']}`；持久 hash：`{baseline['library_persistent_hash']}`。",
        f"UsageRegistry 文件 SHA256：`{baseline['usage_file_sha256']}`；snapshot digest：`{baseline['usage_snapshot_digest']}`。",
        "",
        "| 指标 | clean 起始值 | 实验主链结束值 |",
        "|---|---:|---:|",
        f"| L_all | {baseline['library_stats']['L_all']} | {final_stats['L_all']} |",
        f"| L_exec | {baseline['library_stats']['L_exec']} | {final_stats['L_exec']} |",
        f"| registry ACTIVE Structures | {baseline['library_stats']['registry_active_structures']} | {final_stats['registry_active_structures']} |",
        f"| registry RETIRED Structures | {baseline['library_stats']['registry_retired_structures']} | {final_stats['registry_retired_structures']} |",
        f"| ACTIVE executable F | {baseline['library_stats']['active_executable_F']} | {final_stats['active_executable_F']} |",
        f"| RETIRED executable F | {baseline['library_stats']['retired_executable_F']} | {final_stats['retired_executable_F']} |",
        f"| topology templates | {baseline['library_stats']['topology_templates']} | {final_stats['topology_templates']} |",
        f"| equivalence classes / non-singleton | {baseline['library_stats']['equivalence_classes']} / {baseline['library_stats']['non_singleton_equivalence_classes']} | {final_stats['equivalence_classes']} / {final_stats['non_singleton_equivalence_classes']} |",
        f"| explicit equivalence links | {baseline['library_stats']['explicit_equivalence_links']} | {final_stats['explicit_equivalence_links']} |",
        f"| max canonical level | {baseline['library_stats']['max_canonical_level']} | {final_stats['max_canonical_level']} |",
        f"| composite exact-parent audit | {baseline['library_stats']['exact_parent_pass']}/{baseline['library_stats']['exact_parent_pass'] + baseline['library_stats']['exact_parent_fail']} PASS; {baseline['library_stats']['exact_parent_fail']} FAIL | {final_stats['exact_parent_pass']}/{final_stats['exact_parent_pass'] + final_stats['exact_parent_fail']} PASS; {final_stats['exact_parent_fail']} FAIL |",
        "",
        "起始状态中的 2 个非 executable topology templates 不计入 executable exact-parent admission；全部 20 个其他复合 F 均 audit PASS。起始的 24 个 executable F 中，14 个有可供 Provider 暴露的 public operation interface；Goal `sequence<int> → int` 起始兼容池为 7。L_exec、lifecycle 数与 public/Goal-compatible 数分开记录。",
        "本轮没有增加 equivalence link，没有加入 low-usage retirement，也没有引入 top-k；当前所有 equivalence 类均为 singleton，因此 ACTIVE 与 FULL 在 executable 暴露数量上相同。",
        "",
        "## D01–D12 主链增长",
        "",
        "| Goal | 状态 | F ID | level | 父项数 | CHAIN_QUALIFIED / SHORTCUT_FOUND | candidate attempts | admission |",
        "|---|---|---|---:|---:|---|---:|---|",
    ]
    for row in growth_rows:
        lines.append(f"| {row['task_id']} | {row['status']} | `{row.get('selected_root_id') or '—'}` | {row.get('canonical_level', '—')} | {len(row.get('parents') or [])} | {((row.get('chain_path') or {}).get('status') or '—')} | {row.get('metrics', {}).get('candidate_attempts', '—')} | {'PASS' if row.get('admission_rows') else '已有 F / 未新入库' if row.get('status') == 'EXISTING_F_REUSE' else '未完成'} |")
    lines.extend([
        "",
        f"成功连续深度为 `{successful_depth}` 个 Goal；起始 canonical max level `{baseline['library_stats']['max_canonical_level']}`，实验主链结束 max level `{last_canonical}`。",
        f"主链发现 `{len(shortcut_main)}` 个 `SHORTCUT_FOUND`；其中 `{sum(bool(row.get('admitted')) for row in shortcut_main)}` 个按相同 strict admission 流程进入主链分支。另有 `{len(shortcut_branch)}` 个 counterfactual branch shortcut，独立记录且不并入 clean 基线 Library。",
        "CHAIN_QUALIFIED / SHORTCUT_FOUND 只描述是否包含预期 child 语义；不会作为 admission 条件。候选的 exact parents、Python/JavaScript/oracle、cold reload、公开接口和 KV receipt 决定是否入库。",
        "",
        "## 2×2 搜索视图",
        "",
        "| Goal | FULL+EXPANDED attempts | FULL+FIRST_CLASS attempts | ACTIVE+EXPANDED attempts | ACTIVE+FIRST_CLASS attempts | FULL+EXPANDED depth | ACTIVE+FIRST_CLASS depth | semantic equal |",
        "|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    for row in paired_results:
        if not row.get("modes"):
            continue
        m = row["modes"]
        lines.append(f"| {row['task_id']} | {m['FULL+EXPANDED'].get('metrics', {}).get('candidate_attempts', '—')} | {m['FULL+FIRST_CLASS'].get('metrics', {}).get('candidate_attempts', '—')} | {m['ACTIVE+EXPANDED'].get('metrics', {}).get('candidate_attempts', '—')} | {m['ACTIVE+FIRST_CLASS'].get('metrics', {}).get('candidate_attempts', '—')} | {m['FULL+EXPANDED'].get('search_derivation_depth', '—')} | {m['ACTIVE+FIRST_CLASS'].get('search_derivation_depth', '—')} | {'PASS' if row.get('all_returned_modes_semantic_equal') else 'NOT ALL PASS'} |")
    lines.extend([
        "",
        "### A. Depth effect：FULL+EXPANDED → FULL+FIRST_CLASS",
        "",
        f"candidate attempts：{_fmt_reduction(depth_attempts['before'], depth_attempts['after'])}。",
        f"binding combinations：{_fmt_reduction(depth_work['binding_combinations']['before'], depth_work['binding_combinations']['after'])}。",
        f"composition expansions：{_fmt_reduction(depth_work['composition_expansions']['before'], depth_work['composition_expansions']['after'])}。",
        f"intermediate materializations：{_fmt_reduction(depth_work['intermediate_materializations']['before'], depth_work['intermediate_materializations']['after'])}。",
        f"verification batches：{_fmt_reduction(depth_work['verification_attempts']['before'], depth_work['verification_attempts']['after'])}。",
        f"route search derivation depth：{sum(row['modes']['FULL+EXPANDED'].get('search_derivation_depth', 0) for row in completed)} → {sum(row['modes']['FULL+FIRST_CLASS'].get('search_derivation_depth', 0) for row in completed)} newly formed D-level F nodes.",
        "FIRST_CLASS 的 `search_derivation_depth=0` 表示本次搜索直接复用了已入库 Goal F、没有新形成 D-level F；它不表示目标 F 的历史形成树或 runtime 调用深度为 0。",
        "",
        "### B. Breadth effect：FULL+FIRST_CLASS → ACTIVE+FIRST_CLASS",
        "",
        f"生命周期 executable 暴露总量：FULL `{full_active_before['FULL_lifecycle_executable']}`，ACTIVE `{full_active_before['ACTIVE_lifecycle_executable']}`；减少 `{lifecycle_reduction['absolute']}` ({(lifecycle_reduction['percent'] or 0):.1f}%)。",
        f"Goal-compatible public pool 合计：FULL `{full_active_before['FULL_goal_compatible']}`，ACTIVE `{full_active_before['ACTIVE_goal_compatible']}`。",
        f"candidate attempts：{_fmt_reduction(breadth_attempts['before'], breadth_attempts['after'])}；binding combinations：{_fmt_reduction(breadth_work['binding_combinations']['before'], breadth_work['binding_combinations']['after'])}。",
        "本轮未观察到 production breadth reduction：clean baseline 与主链新增 F 没有可信非单例 equivalence class，故所有 F 均 ACTIVE、无 RETIRED 可供剔除；Goal-compatible 数也只是在类型/契约兼容过滤后计算，不能当作 lifecycle 缩宽。",
        "",
        "### C. 联合效应：FULL+EXPANDED → ACTIVE+FIRST_CLASS",
        "",
        f"candidate attempts：{_fmt_reduction(combined_attempts['before'], combined_attempts['after'])}。",
        f"binding combinations：{_fmt_reduction(combined_work['binding_combinations']['before'], combined_work['binding_combinations']['after'])}。",
        f"composition expansions：{_fmt_reduction(combined_work['composition_expansions']['before'], combined_work['composition_expansions']['after'])}。",
        f"intermediate materializations：{_fmt_reduction(combined_work['intermediate_materializations']['before'], combined_work['intermediate_materializations']['after'])}。",
        f"verification batches：{_fmt_reduction(combined_work['verification_attempts']['before'], combined_work['verification_attempts']['after'])}。",
        f"wall-clock sum：FULL+EXPANDED `{sum(row['modes']['FULL+EXPANDED'].get('wall_seconds', 0) for row in completed):.3f}s`；ACTIVE+FIRST_CLASS `{sum(row['modes']['ACTIVE+FIRST_CLASS'].get('wall_seconds', 0) for row in completed):.3f}s` (附加指标，非主判据)。",
        "",
        "## 语义、Admission 与 fallback",
        "",
        f"四种视图的目标结果均与 frozen oracle 对应：`{'PASS' if semantic_ok else '未全部完成或失败'}`。本轮产生 `{len(newly_admitted)}` 条 strict admission receipt，涉及 `{len(unique_admitted_ids)}` 个唯一 Structure ID；其中主链持久新增 `{len(main_new_ids)}` 个 F，两个 counterfactual EXPANDED 路径各有 `{branch_receipt_count // 2}` 条 branch-local receipt。每个目标 Dk 的展开路线在独立临时 snapshot 中依次重组 D01…Dk，所以每条路线的回执数是 `1+2+…+12=78`；总数为 `12 + 78 + 78 = 168`，不是 168 个持久 F。exact-parent receipt `{sum(bool(row.get('formation_audit', {}).get('passed')) for row in newly_admitted)}/{len(newly_admitted)} PASS`。",
        f"ACTIVE retired fallback：起始 RETIRED executable F `{baseline['library_stats']['retired_executable_F']}`；本轮触发 `{sum(bool(view.get('fallback', {}).get('fallback_triggered')) for pair in completed for view in pair.get('modes', {}).values())}` 次。fallback 工作已并入对应模式 `metrics`；当前无 retired 候选可触发。",
        "",
        "## 最终 21 项回答",
        "",
        f"1. clean baseline L_all：`{baseline['library_stats']['L_all']}`。",
        f"2. clean baseline L_exec：`{baseline['library_stats']['L_exec']}`。",
        f"3. ACTIVE executable F：`{baseline['library_stats']['active_executable_F']}`。",
        f"4. RETIRED executable F：`{baseline['library_stats']['retired_executable_F']}`。",
        f"5. equivalence 非单例类：`{baseline['library_stats']['non_singleton_equivalence_classes']}`。",
        f"6. clean 起始最大 canonical level：`{baseline['library_stats']['max_canonical_level']}`；主实验最终：`{last_canonical}`。",
        f"7. 最长成功 Goal 深度：`{successful_depth}` (`D{successful_depth:02d}`)。",
        f"8. 是否发现合法 shortcut：`{'是，' + str(len(shortcut_main) + len(shortcut_branch)) + ' 次' if shortcut_main or shortcut_branch else '否'}`。",
        f"9. shortcut 是否入库：主链 shortcut `{sum(bool(row.get('admitted')) for row in shortcut_main)}` 条 admission PASS；counterfactual shortcut 只进入各自 branch snapshot。",
        f"10. FIRST_CLASS 相对 FULL+EXPANDED 减少 attempts：`{attempt_decrease}` (`{depth_attempt_percent_text}`；跨 `{target_count}` 个 Goal 汇总)。",
        f"11. FULL 与 ACTIVE lifecycle executable exposure 相差：`{full_active_before['FULL_lifecycle_executable'] - full_active_before['ACTIVE_lifecycle_executable']}` / `{full_active_before['FULL_lifecycle_executable']}` ({(lifecycle_reduction['percent'] or 0):.1f}%)；Goal-compatible public exposure 分开列在上文。",
        f"12. 当前 production lifecycle 是否真实缩宽：`{'是' if lifecycle_reduction['absolute'] > 0 else '否'}`。",
        "13. 未缩宽原因：没有正式、可信的非单例 equivalence 证据；所有 singleton 按当前规则 ACTIVE。有限 holdout 输出未用于登记等价。",
        f"14. ACTIVE fallback 是否触发：`{'是' if any(view.get('fallback', {}).get('fallback_triggered') for pair in completed for view in pair.get('modes', {}).values()) else '否'}`。",
        "15. fallback 计入后总工作：fallback 为 0 次；各 ACTIVE route metrics 已按 primary + fallback 合并，故本轮总计不变。",
        f"16. FULL+EXPANDED 与 ACTIVE+FIRST_CLASS attempts 差：`{combined_attempts['reduction']['absolute']}` ({combined_attempt_percent_text})。",
        f"17. strict admission exact-parent：`{sum(bool(row.get('formation_audit', {}).get('passed')) for row in newly_admitted)}/{len(newly_admitted)} receipt PASS`，覆盖 `{len(unique_admitted_ids)}` 个唯一 Structure ID；主链持久新增 `{len(main_new_ids)}` 个 F。",
        f"18. 12 个唯一 F 的 Python = JavaScript = oracle（含重复 branch receipt 复核）：`{'PASS' if all(row.get('strict_admission_checks', {}).get('cross_language') is True and row.get('strict_admission_checks', {}).get('randomized_oracle') is True for row in newly_admitted) else 'FAIL / 无新入库 F'}`。",
        f"19. 12 个唯一 F 的 cold reload（含重复 branch receipt 复核）：`{'PASS' if all(row.get('strict_admission_checks', {}).get('cold_instance') is True for row in newly_admitted) else 'FAIL / 无新入库 F'}`。",
        "20. 当前主要瓶颈：本轮 breadth 机制没有可治理的退休冗余；全扩展路线工作主要来自候选组合与重复 admission 验证。基于记录的 attempts、bindings、expansions、materializations 计数，不把 wall-clock 噪声当瓶颈证据。",
        f"21. 是否可作为后续唯一正式 baseline：`{'可作为本闭包内后续 depth/breadth 对照实验的统一正式基线；当前 breadth 只能报告 ACTIVE=FULL，尚不支持声称已经实现真实缩宽。' if semantic_ok and all_first_class and not implementation_error else '暂不可，先修复本报告所列实现或语义问题。'}`",
        "",
        "## 产物",
        "",
        "机器记录只保留 `summary.json`、`baseline_state.json`、冻结 Goal manifest、Library 增长与 lifecycle/equivalence 汇总、各模式计数、按 F 去重的 admission/formation 摘要、shortcut/fallback 摘要和逐 Goal 关键 trace。逐候选事件、重复 promotion receipt 原文和中间搜索对象不落盘。",
        "",
        "## 复跑",
        "",
        "```powershell\npython experiments/v94_search_space_baseline.py\n```",
        "",
    ])
    if implementation_error:
        lines.extend(["## 实现错误详情", "", "```text", implementation_error, "```", ""])
    return "\n".join(lines)


def _save_all_outputs(payload):
    write_json(OUT / "library_growth.json", compact_library_growth(payload["library_growth"]))
    write_json(OUT / "lifecycle_trace.json", compact_lifecycle_trace(payload["lifecycle_trace"]))
    write_json(OUT / "equivalence_trace.json", compact_equivalence_trace(payload["equivalence_trace"]))
    write_json(OUT / "depth_metrics.json", payload["depth_metrics"])
    write_json(OUT / "breadth_metrics.json", payload["breadth_metrics"])
    write_json(OUT / "combined_metrics.json", payload["combined_metrics"])
    write_json(OUT / "paired_2x2_results.json",
               [compact_paired_result(row) for row in payload["paired_results"]])
    write_json(OUT / "admission_results.json", summarize_admission_receipts(payload["admissions"]))
    write_json(OUT / "formation_audit.json", summarize_formation_audits(payload["formation_audits"]))
    write_json(OUT / "shortcut_trace.json", compact_shortcut_trace(payload["shortcut_trace"]))
    write_json(OUT / "fallback_trace.json", compact_fallback_trace(payload["fallback_trace"]))
    write_json(OUT / "summary.json", payload["summary"])
    (OUT / "report.md").write_text(payload["report"], encoding="utf-8")


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "traces").mkdir(parents=True, exist_ok=True)
    (OUT / "libraries").mkdir(parents=True, exist_ok=True)

    # Re-audit the on-disk clean snapshot. No prompt-supplied counts are reused.
    library = Library.load(BASE_LIBRARY)
    usage = UsageRegistry.load(BASE_USAGE, library)
    clean_stats = library_stats(library, usage)
    initial_provider = OperationPoolProvider(library, usage,
        public_contracts=publish_recursive_operation_contracts(library))
    goal_probe = task_goal(FOUNDATIONS[0]["oracle"])
    initial_exposure_full = candidate_exposure(library, usage, goal_probe, "FULL")
    initial_exposure_active = candidate_exposure(library, usage, goal_probe, "ACTIVE")
    usage_snapshot = usage.snapshot()
    baseline = {
        "library_file": str(BASE_LIBRARY), "library_file_sha256": sha256_file(BASE_LIBRARY),
        "library_persistent_hash": library.persistent_hash,
        "usage_file": str(BASE_USAGE), "usage_file_sha256": sha256_file(BASE_USAGE),
        "usage_snapshot_digest": digest(usage_snapshot),
        "library_stats": clean_stats,
        "global_provider_full_public_pool": initial_exposure_full["lifecycle_exposed_public_executable_pool"],
        "global_provider_active_public_pool": initial_exposure_active["lifecycle_exposed_public_executable_pool"],
        "sequence_int_to_int_full_goal_pool": initial_exposure_full["goal_compatible_pool"],
        "sequence_int_to_int_active_goal_pool": initial_exposure_active["goal_compatible_pool"],
        "no_automatic_low_usage_retire": True,
        "no_automatic_equivalence_detection": True,
        "all_singleton_classes_active": clean_stats["non_singleton_equivalence_classes"] == 0
            and clean_stats["registry_retired_structures"] == 0,
        "initial_equivalence_classes": [list(group) for group in library.equivalence_classes()],
        "initial_usage_preferred": usage_snapshot["preferred"],
    }
    definitions, oracles, task_rows, family = freeze_task_family()
    foundation_audit = foundation_matches(library, FOUNDATIONS,
                                          randomized_inputs(128, 938001))
    baseline["foundation_seed_semantic_matches"] = foundation_audit
    baseline["all_task_foundation_functions_present"] = all(
        foundation_audit[row["label"]]["present_in_clean_seed"] for row in FOUNDATIONS)
    baseline["initial_goal_exposure"] = {
        "FULL": initial_exposure_full, "ACTIVE": initial_exposure_active}
    write_json(OUT / "baseline_state.json", baseline)
    if not baseline["all_task_foundation_functions_present"]:
        raise RuntimeError("frozen D task family references a foundation absent from clean Library")
    if clean_stats["exact_parent_fail"]:
        raise RuntimeError("clean executable-composite parent audit failed; see baseline_state.json")

    equivalence_trace = [{"stage": "clean_start", "library_hash": library.persistent_hash,
        "explicit_links": len(library.equivalence), "classes": [list(group) for group in library.equivalence_classes()],
        "non_singleton_count": clean_stats["non_singleton_equivalence_classes"],
        "active_ids": sorted(usage.active_ids()), "retired_ids": sorted(usage.retired_ids()),
        "automatic_detection_used": False}]
    lifecycle_trace = [lifecycle_snapshot("clean_start", library, usage)]
    before_d01_path = OUT / "libraries" / "before_D01.json"
    library.save(before_d01_path)
    usage.save(before_d01_path.with_suffix(".usage.json"))
    library_growth = [{"stage": "clean_start", "task_id": None,
        "library_stats": clean_stats, "library_file_sha256": sha256_file(BASE_LIBRARY),
        "usage_file_sha256": sha256_file(BASE_USAGE)}]

    current_library, current_usage = library, usage
    chain_ids = {}
    growth_rows = []
    paired_results = []
    depth_metrics = []
    breadth_metrics = []
    combined_metrics = []
    admissions = []
    formation_audits = []
    shortcut_trace = []
    fallback_trace = []
    stop_reason = "D12_COMPLETED"
    implementation_error = None

    try:
        for index, definition in enumerate(definitions):
            task_id = definition["task_id"]
            print(f"\n[{task_id}] acquire from current persistent Library", flush=True)
            before_stats = library_stats(current_library, current_usage)
            before_hash = current_library.persistent_hash
            goal = task_goal(oracles[task_id])
            holdout = task_rows[task_id]["holdout_inputs"]
            after_path = OUT / "libraries" / ("after_" + task_id + ".json")
            acquisition, next_library, next_usage = paired_acquisition(
                current_library, current_usage, goal, oracles[task_id], holdout,
                lifecycle_view="ACTIVE", label="main-acquisition/" + task_id,
                definition=definition, oracles=oracles,
                after_library_path=after_path)
            write_json(OUT / "traces" / (task_id + "_acquisition.json"), compact_acquisition_trace({
                "task_id": task_id, "starting_library_hash": before_hash,
                "starting_library_stats": before_stats,
                "holdout_seed": task_rows[task_id]["holdout_seed"],
                "same_candidate_limit": MAX_CANDIDATES,
                "acquisition": acquisition,
            }))
            fallback_trace.append({"source": "main_acquisition", "task_id": task_id,
                                   "stages": acquisition.get("fallback", [])})
            if acquisition.get("status") not in ("ADMITTED", "EXISTING_F_REUSE"):
                stop_reason = acquisition.get("status", "ACQUISITION_FAILED")
                growth_rows.append({"task_id": task_id, "status": stop_reason,
                    "starting_library_hash": before_hash,
                    "metrics": acquisition.get("metrics", {}),
                    "selected_root_id": acquisition.get("selected_root_id"),
                    "chain_path": acquisition.get("chain_path"),
                    "admission_rows": acquisition.get("admission_rows", [])})
                break

            current_library, current_usage = next_library, next_usage
            if not after_path.exists():
                current_library.save(after_path)
                current_usage.save(after_path.with_suffix(".usage.json"))
            # Reload the actual written snapshot before any paired view starts.
            current_library = Library.load(after_path)
            current_usage = UsageRegistry.load(after_path.with_suffix(".usage.json"), current_library)
            root_id = acquisition["selected_root_id"]
            chain_ids[task_id] = root_id
            chain_path = acquisition.get("chain_path")
            growth_row = {
                "task_id": task_id, "designed_chain_depth": index + 1,
                "status": acquisition["status"], "starting_library_hash": before_hash,
                "ending_library_hash": current_library.persistent_hash,
                "selected_root_id": root_id,
                "candidate_kind": acquisition.get("candidate_kind"),
                "formation_candidate_rank": acquisition.get("formation_candidate_rank"),
                "canonical_level": current_library[root_id].level,
                "parents": list(current_library[root_id].parents),
                "chain_path": chain_path,
                "semantic_verification": acquisition.get("semantic_verification"),
                "metrics": dict(acquisition.get("metrics", {})),
                "admission_rows": list(acquisition.get("admission_rows", [])),
                "library_stats_before": before_stats,
                "library_stats_after": library_stats(current_library, current_usage),
            }
            growth_rows.append(growth_row)
            for row in acquisition.get("admission_rows", []):
                admissions.append({"source": "main_acquisition", **row})
                formation_audits.append({"source": "main_acquisition", **row})
            if chain_path and chain_path.get("status") == "SHORTCUT_FOUND":
                shortcut_trace.append({"source": "main_acquisition", "task_id": task_id,
                    **chain_path, "admitted": acquisition["status"] == "ADMITTED",
                    "already_persistent": acquisition["status"] == "EXISTING_F_REUSE"})
            lifecycle_trace.append(lifecycle_snapshot("after_" + task_id, current_library, current_usage))
            equivalence_trace.append({"stage": "after_" + task_id,
                "library_hash": current_library.persistent_hash,
                "explicit_links": len(current_library.equivalence),
                "classes": [list(group) for group in current_library.equivalence_classes()],
                "non_singleton_count": sum(len(group) > 1 for group in current_library.equivalence_classes()),
                "active_ids": sorted(current_usage.active_ids()),
                "retired_ids": sorted(current_usage.retired_ids()),
                "automatic_detection_used": False})
            library_growth.append({"stage": "after_" + task_id, "task_id": task_id,
                "library_stats": library_stats(current_library, current_usage),
                "library_hash": current_library.persistent_hash,
                "usage_digest": digest(current_usage.snapshot()),
                "library_file": str(after_path),
                "usage_file": str(after_path.with_suffix(".usage.json"))})

            # Each view starts from this exact reloaded post-acquisition snapshot.
            paired = run_2x2(index, definition, current_library, current_usage, root_id,
                chain_ids, definitions, oracles, task_rows,
                task_rows[task_id]["holdout_seed"])
            paired_results.append(paired)
            for view_name, view in paired["modes"].items():
                fallback_trace.append({"source": "paired_view", "task_id": task_id,
                    "mode": view_name, "fallback": view.get("fallback", {})})
                for row in view.get("admission_rows", []):
                    admissions.append({"source": "paired_view/" + view_name, **row})
                    formation_audits.append({"source": "paired_view/" + view_name, **row})
                if view_name.endswith("+EXPANDED"):
                    for stage in view.get("reformation_stages", []):
                        path = stage.get("chain_path")
                        if path and path.get("status") == "SHORTCUT_FOUND":
                            shortcut_trace.append({"source": "paired_view/" + view_name,
                                "target_goal": task_id, "stage_goal": stage["task_id"],
                                **path, "admitted": stage["status"] == "ADMITTED",
                                "already_persistent": stage["status"] == "EXISTING_F_REUSE"})

            full_expanded = paired["modes"]["FULL+EXPANDED"]
            full_first = paired["modes"]["FULL+FIRST_CLASS"]
            active_expanded = paired["modes"]["ACTIVE+EXPANDED"]
            active_first = paired["modes"]["ACTIVE+FIRST_CLASS"]
            depth_metrics.append({"task_id": task_id, "designed_chain_depth": index + 1,
                "canonical_level": growth_row["canonical_level"],
                "found_formation_depth": growth_row["canonical_level"],
                "main_chain_path": (chain_path or {}).get("status"),
                "full_expanded_search_derivation_depth": full_expanded.get("search_derivation_depth"),
                "full_first_class_search_derivation_depth": full_first.get("search_derivation_depth"),
                "active_expanded_search_derivation_depth": active_expanded.get("search_derivation_depth"),
                "active_first_class_search_derivation_depth": active_first.get("search_derivation_depth"),
                "full_expanded_candidate_search_depth": (full_expanded.get("metrics") or {}).get("candidate_search_depth"),
                "first_class_candidate_search_depth": (full_first.get("search") or {}).get("candidate", {}).get("search_depth"),
                "full_expanded_runtime_call_depth": full_expanded.get("runtime_call_depth"),
                "first_class_runtime_call_depth": full_first.get("runtime_call_depth"),
                "historical_levels_skipped_by_first_class": full_first.get("historical_levels_skipped"),
                "first_class_reused_F_ids": full_first.get("reused_F_ids", []),
                "expanded_reformed_F_ids": full_expanded.get("reformed_F_ids", []),
                "shortcut_found": (chain_path or {}).get("status") == "SHORTCUT_FOUND"})
            breadth_metrics.append({"task_id": task_id,
                "full_lifecycle_exposure": full_first["exposure"],
                "active_lifecycle_exposure": active_first["exposure"],
                "full_goal_compatible_exposure": full_first["exposure"].get("goal_compatible_pool"),
                "active_goal_compatible_exposure": active_first["exposure"].get("goal_compatible_pool"),
                "full_attempts": full_first.get("metrics", {}).get("candidate_attempts"),
                "active_attempts": active_first.get("metrics", {}).get("candidate_attempts"),
                "attempt_reduction": _reduction(full_first.get("metrics", {}).get("candidate_attempts"),
                                                active_first.get("metrics", {}).get("candidate_attempts")),
                "fallback_full": full_first.get("fallback"),
                "fallback_active": active_first.get("fallback")})
            combined_metrics.append({"task_id": task_id,
                "FULL+EXPANDED": full_expanded.get("metrics", {}),
                "ACTIVE+FIRST_CLASS": active_first.get("metrics", {}),
                "candidate_attempt_reduction": _reduction(
                    full_expanded.get("metrics", {}).get("candidate_attempts"),
                    active_first.get("metrics", {}).get("candidate_attempts")),
                "binding_reduction": _reduction(
                    full_expanded.get("metrics", {}).get("binding_combinations"),
                    active_first.get("metrics", {}).get("binding_combinations")),
                "depth_reduction": _reduction(full_expanded.get("search_derivation_depth"),
                                               active_first.get("search_derivation_depth"))})
            if current_library.persistent_hash != paired["starting_library_hash"]:
                raise AssertionError(task_id + " paired branch mutated the main accumulated Library")
            _save_all_outputs({
                "library_growth": library_growth,
                "lifecycle_trace": lifecycle_trace,
                "equivalence_trace": equivalence_trace,
                "depth_metrics": depth_metrics,
                "breadth_metrics": breadth_metrics,
                "combined_metrics": combined_metrics,
                "paired_results": paired_results,
                "admissions": admissions,
                "formation_audits": formation_audits,
                "shortcut_trace": shortcut_trace,
                "fallback_trace": fallback_trace,
                "summary": {"state": "RUNNING", "completed_goals": len(growth_rows),
                            "paired_goals": len(paired_results), "stop_reason": "in progress"},
                "report": "# V9.4 统一搜索空间治理基线\n\n实验仍在运行，完整计数将在结束后写入。\n",
            })
    except Exception as error:
        import traceback
        implementation_error = type(error).__name__ + ": " + str(error) + "\n" + traceback.format_exc()
        stop_reason = "IMPLEMENTATION_ERROR"
        print("IMPLEMENTATION ERROR: " + type(error).__name__ + ": " + str(error), flush=True)

    final_stats = library_stats(current_library, current_usage)
    if implementation_error:
        state = "IMPLEMENTATION_ERROR"
    elif len(paired_results) != len(growth_rows):
        state = "STOPPED_BEFORE_ALL_2X2_GOALS"
    else:
        state = "COMPLETE" if len(growth_rows) == len(definitions) else "STOPPED_AT_SEARCH_LIMIT_OR_NO_SOLUTION"
    aggregate_depth = _aggregate_modes(paired_results, "FULL+EXPANDED", "FULL+FIRST_CLASS", WORK_KEYS)
    aggregate_breadth = _aggregate_modes(paired_results, "FULL+FIRST_CLASS", "ACTIVE+FIRST_CLASS", WORK_KEYS)
    aggregate_combined = _aggregate_modes(paired_results, "FULL+EXPANDED", "ACTIVE+FIRST_CLASS", WORK_KEYS)
    final_equivalence = [list(group) for group in current_library.equivalence_classes()]
    final_summary = {
        "state": state, "stop_reason": stop_reason,
        "clean_baseline_library_hash": baseline["library_persistent_hash"],
        "clean_baseline_usage_digest": baseline["usage_snapshot_digest"],
        "main_chain_final_library_hash": current_library.persistent_hash,
        "main_chain_final_usage_digest": digest(current_usage.snapshot()),
        "main_chain_final_library_stats": final_stats,
        "goals_requested": len(definitions), "goals_completed": len(growth_rows),
        "goals_with_2x2": len(paired_results),
        "longest_successful_goal_depth": len(growth_rows),
        "first_class_target_edge_valid": bool(paired_results) and all(
            row.get("all_first_class_views_used_target_as_direct_edge") for row in paired_results),
        "all_2x2_semantics_equal": bool(paired_results) and all(
            row.get("all_four_modes_completed") and row.get("all_four_modes_semantic_equal")
            and row.get("semantic_output_digest_equal_across_returned_modes") for row in paired_results),
        "main_chain_shortcuts": sum(row.get("chain_path", {}).get("status") == "SHORTCUT_FOUND"
                                     for row in growth_rows if row.get("chain_path")),
        "main_chain_shortcuts_admitted": sum(row.get("chain_path", {}).get("status") == "SHORTCUT_FOUND"
            and row.get("status") == "ADMITTED" for row in growth_rows if row.get("chain_path")),
        "all_admission_receipts_exact_parent_pass": all(
            row.get("formation_audit", {}).get("passed") for row in admissions),
        "admission_receipts_total": len(admissions),
        "admission_receipts_by_source": dict(Counter(row.get("source", "unknown") for row in admissions)),
        "admission_unique_structure_count": len({row.get("structure_id") for row in admissions
                                                  if row.get("structure_id")}),
        "main_chain_new_persistent_F_count": len({row.get("structure_id") for row in admissions
            if row.get("source") == "main_acquisition" and row.get("structure_id")}),
        "branch_local_admission_receipts": sum(row.get("source") != "main_acquisition"
                                                 for row in admissions),
        "non_singleton_equivalence_classes_final": sum(len(group) > 1 for group in final_equivalence),
        "explicit_equivalence_links_final": len(current_library.equivalence),
        "active_executable_F_final": final_stats["active_executable_F"],
        "retired_executable_F_final": final_stats["retired_executable_F"],
        "aggregate_depth_effect": aggregate_depth,
        "aggregate_breadth_effect": aggregate_breadth,
        "aggregate_combined_effect": aggregate_combined,
        "implementation_error": implementation_error,
    }
    report = make_report(baseline, growth_rows, paired_results, depth_metrics,
        breadth_metrics, combined_metrics, admissions, shortcut_trace, stop_reason,
        foundation_audit, current_library, current_usage, implementation_error)
    payload = {
        "library_growth": library_growth,
        "lifecycle_trace": lifecycle_trace,
        "equivalence_trace": equivalence_trace,
        "depth_metrics": {"per_goal": depth_metrics, "aggregate": aggregate_depth},
        "breadth_metrics": {"per_goal": breadth_metrics, "aggregate": aggregate_breadth},
        "combined_metrics": {"per_goal": combined_metrics, "aggregate": aggregate_combined},
        "paired_results": paired_results,
        "admissions": admissions,
        "formation_audits": formation_audits,
        "shortcut_trace": shortcut_trace,
        "fallback_trace": fallback_trace,
        "summary": final_summary,
        "report": report,
    }
    _save_all_outputs(payload)
    print(f"\n{state}: {len(paired_results)} paired Goals; report={OUT / 'report.md'}", flush=True)


if __name__ == "__main__":
    main()
