"""Acquire reusable V9.4 algorithm Fs from fresh input/output instances.

The Composer sees only a public sequence<int> -> int contract and fresh
input/output examples.  Its selected materializations come from recipes stored
inside already-persistent topology Fs.  Exact formation-parent checks run in
the shared Library admission path.  The generated candidates are canonicalized
with exact parents, the step F is admitted, then the root F is formed from that
persistent step.
"""
from __future__ import annotations

import atexit
import hashlib
import json
import os
import random
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
BASE = Path(os.environ.get("V94_BASE_DIR", ROOT / "data"))
OUT = Path(os.environ.get("V94_OUTPUT_DIR", ROOT / "artifacts" / "clean_strict_library"))
OUT.mkdir(parents=True, exist_ok=True)
RUN_DIR: Path | None = None
sys.path[:0] = [str(ROOT), str(ROOT / "experiments")]

from fsys.admission import (REQUIRED_TOKEN_CHECKS, audit_formation_parents,
                            subject_digest, validate_structure)
from fsys.autonomous_composition import AutonomousCompositionDiscovery
from fsys.external_kv import ExternalCandidateKV
from fsys.formation import canonical_compose
from fsys.goal_composer import GoalDrivenComposer, TaskSpec
from fsys.library import Library
from fsys.preference import UsageRegistry
from fsys.promotion import PromotionGate
from fsys.public_operations import bind_operation, is_first_class_operation, normalize_operation
from fsys.recursive_f_runtime import RecursiveFExecutor, program_snapshot
from fsys.serialization import digest, thaw
from fsys.structure import Structure
from fsys.token_goal_domain import (
    publish_recursive_operation_contracts,
    recursive_operation_contract,
    recursive_step_operation_contract,
)
from synthesis_oracles import randomized_inputs

PYTHON = Path(sys.executable)
NODE = Path(
    r"C:\Users\lancelot\.cache\codex-runtimes\codex-primary-runtime\dependencies\node\bin\node.exe"
)
GOAL_SIGNATURE = digest(
    {"goal_family": "sequence-int-to-int", "input": "sequence<int>", "output": "int"}
)
INPUT_SCHEMA = ({"name": "nums", "type": "sequence<int>"},)
OUTPUT_SCHEMA = {"type": "int"}

# These references live only in the verifier side of this script.  The
# GoalDrivenComposer receives no function text, AST, task name, or oracle ID.
TASKS = (
    ("U11", "longest_non_decreasing_run", "nondecreasing_run"),
    ("U12", "longest_equal_run", "equal_run"),
    ("U13", "count_adjacent_changes", "adjacent_changes"),
    ("G01", "longest_increasing_run", "increasing_run"),
    ("G03", "longest_decreasing_run", "decreasing_run"),
    ("G05", "count_adjacent_increases", "increase_count"),
    ("G07", "count_adjacent_equals", "equal_count"),
)

def selected_tasks():
    requested={item.strip() for item in os.environ.get("V94_GOALS", "").split(",") if item.strip()}
    selected=tuple(row for row in TASKS if not requested or row[0] in requested)
    if requested and {row[0] for row in selected}!=requested:
        raise ValueError("V94_GOALS contains unknown IDs: "+", ".join(sorted(requested-{row[0] for row in selected})))
    return selected


def oracle(kind: str, xs: list[int]) -> int:
    if kind in ("nondecreasing_run", "equal_run", "increasing_run", "decreasing_run"):
        if not xs:
            return 0
        best = run = 1
        for index in range(1, len(xs)):
            matched = (
                xs[index] >= xs[index - 1] if kind == "nondecreasing_run" else
                xs[index] < xs[index - 1] if kind == "decreasing_run" else
                xs[index] > xs[index - 1] if kind == "increasing_run" else
                xs[index] == xs[index - 1]
            )
            run = run + 1 if matched else 1
            best = max(best, run)
        return best
    if kind in ("adjacent_changes", "increase_count", "equal_count"):
        if kind == "increase_count":
            return sum(xs[index] > xs[index - 1] for index in range(1, len(xs)))
        if kind == "equal_count":
            return sum(xs[index] == xs[index - 1] for index in range(1, len(xs)))
        return sum(xs[index] != xs[index - 1] for index in range(1, len(xs)))
    raise KeyError(kind)


def _historical_input_keys() -> set[tuple[int, ...]]:
    fixed = [
        [],
        [3],
        [1, 2, 3, 1, 2],
        [5, 4, 3],
        [1, 2, 2, 3],
        [-2, -1, 0, 2, -3],
        [0, 0, 0],
        [9, 1, 2, 3, 0],
    ]
    # G01-G13 primary and independent acquisition cohorts from the previous
    # runs; none of their input vectors may re-enter as fresh training data.
    seeds = (*range(73117, 73130), *range(91271, 91284))
    keys = {tuple(row) for row in fixed}
    for seed in seeds:
        keys.update(tuple(row) for row in randomized_inputs(128, seed))
    return keys


def unseen_examples(kind: str, seed: int, count: int = 24) -> list[dict]:
    rng = random.Random(seed)
    forbidden = _historical_input_keys()
    chosen: list[list[int]] = []
    seen = set(forbidden)
    while len(chosen) < count:
        size = rng.randrange(3, 15)
        xs = [rng.randrange(-9, 10) for _ in range(size)]
        key = tuple(xs)
        if key in seen:
            continue
        seen.add(key)
        chosen.append(xs)
    return [{"input": [xs], "output": oracle(kind, xs)} for xs in chosen]


def holdout_inputs(kind_index: int, training: list[dict]) -> list[list[int]]:
    excluded = {tuple(row["input"][0]) for row in training}
    rows = []
    for xs in randomized_inputs(256, 640031 + kind_index):
        key = tuple(xs)
        if key in excluded:
            continue
        excluded.add(key)
        rows.append(xs)
        if len(rows) == 192:
            break
    if len(rows) != 192:
        raise RuntimeError("could not construct an independent holdout cohort")
    return rows


def make_goal(goal_id: str, examples: list[dict]) -> TaskSpec:
    return TaskSpec(
        goal_id,
        INPUT_SCHEMA,
        OUTPUT_SCHEMA,
        tuple(examples),
        GOAL_SIGNATURE,
    )


def _runtime_context() -> dict:
    return {"STATE": {}, "INDEX": None, "BOOLEAN": None}


def _run_public_root(library, kv, obj: Structure, goal: TaskSpec, xs: list[int]):
    interface = normalize_operation(obj)
    if not is_first_class_operation(interface):
        raise AssertionError("selected F does not publish a complete public interface")
    bindings = bind_operation(
        interface,
        goal,
        [xs],
        runtime_context=_runtime_context(),
    )
    executor = RecursiveFExecutor(library, kv, trace=True)
    value = executor.execute_public(interface, bindings)
    return value, executor, bindings


def _execute_search_candidate(composer, candidate, goal, xs):
    return composer.execute_candidate(
        candidate,
        goal,
        [xs],
        runtime_context=_runtime_context(),
    )


def search_from_instances(library, usage, goal_id, examples, kind, holdout, run_label):
    goal = make_goal(goal_id, examples)
    kv = ExternalCandidateKV()
    composer = GoalDrivenComposer(
        library,
        kv,
        public_contracts=publish_recursive_operation_contracts(library),
        usage_registry=usage,
    )
    attempts = []
    selected = None
    for candidate in composer.candidates(goal):
        row = {
            "rank": candidate["rank"],
            "kind": candidate["candidate_kind"],
            "root_id": candidate["root_id"],
            "examples_pass": False,
            "holdout_pass": False,
        }
        try:
            row["examples_pass"] = all(
                _execute_search_candidate(composer, candidate, goal, row_example["input"][0])
                == row_example["output"]
                for row_example in examples
            )
            if row["examples_pass"]:
                row["holdout_pass"] = all(
                    _execute_search_candidate(composer, candidate, goal, xs) == oracle(kind, xs)
                    for xs in holdout
                )
        except (ValueError, KeyError, TypeError, IndexError, LookupError) as error:
            row["error"] = f"{type(error).__name__}: {error}"
        attempts.append(row)
        if row["examples_pass"] and row["holdout_pass"]:
            if candidate["candidate_kind"] != "recipe_composition":
                raise RuntimeError(
                    f"{goal_id}: target resolved to a pre-existing operation; expected fresh acquisition"
                )
            selected = candidate
            break
        for candidate_id in candidate.get("candidate_ids", {}).values():
            if candidate_id in kv.records and kv.get(candidate_id)["status"] == "CANDIDATE":
                kv.transition(candidate_id, "REJECTED", reason="unseen-instance verification failed")
    if selected is None:
        raise RuntimeError(f"{goal_id}: no Composer candidate passed unseen examples and holdout")
    return {
        "goal": goal,
        "composer": composer,
        "kv": kv,
        "candidate": selected,
        "attempts": attempts,
        "goal_id": goal_id,
        "run_label": run_label,
    }


def _ordered_execution_refs(executable) -> tuple[str, ...]:
    found = []

    def visit(node):
        if isinstance(node, dict):
            for key, value in node.items():
                if key in ("f", "body_f") and isinstance(value, str):
                    found.append(value)
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(executable)
    return tuple(dict.fromkeys(found))


def _ordered_parameter_names(template) -> tuple[str, ...]:
    found = []

    def visit(node):
        if isinstance(node, dict):
            if set(node) == {"parameter"} and isinstance(node["parameter"], str):
                found.append(node["parameter"])
            else:
                for value in node.values():
                    visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)

    visit(template)
    return tuple(dict.fromkeys(found))


def materialize_strict_f(library, raw_obj, topology_id, label, selected_ops, parameters, step_id=None):
    """Test-local strict formation using the Composer-selected F recipe output."""
    topology = library[topology_id]
    recipe = thaw(topology.kernel)["formation_recipe"]
    template = recipe["materialization"][label]
    operation_bindings = dict(selected_ops)
    if label == "root":
        if not step_id or step_id not in library._structures:
            raise AssertionError("root formation requires the step F to be persistent first")
        operation_bindings["step"] = step_id
    if any(identity not in library._structures for identity in operation_bindings.values()):
        raise AssertionError("formation attempted to consume a non-persistent F")

    bound_graph = GoalDrivenComposer._bind(template, operation_bindings, parameters)
    executable_graph = bound_graph["executable"]
    raw_kernel = thaw(raw_obj.kernel)
    raw_executable = raw_kernel["executable"]
    if label == "root" and step_id:
        raw_step_id = raw_kernel["formation_recipe"].get("materialized_step_id")
        if not raw_step_id:
            raise AssertionError("Composer root draft omitted its generated step reference")

        def replace_step_id(node):
            if isinstance(node, dict):
                return {key: replace_step_id(value) for key, value in node.items()}
            if isinstance(node, list):
                return [replace_step_id(value) for value in node]
            return step_id if node == raw_step_id else node

        if replace_step_id(raw_executable) != executable_graph:
            raise AssertionError("strict root differs from the Composer materialization graph")
    elif raw_executable != executable_graph:
        raise AssertionError("strict step differs from the Composer materialization graph")

    calls = _ordered_execution_refs(executable_graph)
    parents = tuple(dict.fromkeys((topology_id, *calls)))
    if not calls or any(parent not in library._structures for parent in parents):
        raise AssertionError("strict formation parents must exist before canonical composition")
    used_bindings = {
        name: identity
        for name, identity in operation_bindings.items()
        if identity in calls
    }
    if set(used_bindings.values()) != set(calls):
        raise AssertionError("recipe bindings do not exactly account for executable F calls")
    used_parameters = {
        name: parameters[name]
        for name in _ordered_parameter_names(template)
    }
    kernel = {
        "executable": executable_graph,
        "formation_recipe": {
            "recipe_version": recipe["recipe_version"],
            "topology_id": topology_id,
            "operation_bindings": used_bindings,
            "parameters": used_parameters,
            "materialization": label,
        },
    }
    if label == "root":
        kernel["formation_recipe"]["materialized_step_id"] = step_id

    metadata = dict(thaw(raw_obj.metadata))
    metadata.pop("candidate_role", None)
    metadata["exec_capability"] = "composition" if label == "step" else "composed_algorithm"
    metadata["public_operation_interface"] = (
        recursive_step_operation_contract(raw_obj.alpha_schema.binding_slots)
        if label == "step"
        else recursive_operation_contract(raw_obj.alpha_schema.binding_slots)
    )
    parent_levels = {structure.identity: structure.level for structure in library}
    topology_data = {
        "recipe": topology_id,
        "bindings": parameters,
        "materialization": label,
    }
    return canonical_compose(
        parents,
        topology_data,
        {
            "operation_id": raw_obj.operation_id,
            "ports": raw_obj.ports,
            "alpha_schema": raw_obj.alpha_schema,
            "kernel": kernel,
            "metadata": metadata,
        },
        parent_levels,
        source="unseen-instance composition",
        evidence={"topology_f": topology_id, "materialization": label},
    )


def audit_exact_formation(obj, library, topology_id, label):
    result = audit_formation_parents(obj, library)
    recipe = thaw(obj.kernel).get("formation_recipe", {})
    return (
        result["passed"]
        and recipe.get("topology_id") == topology_id
        and recipe.get("materialization") == label
        and obj.level == 1 + max(library[parent].level for parent in obj.parents)
    )


def _compare(operator: str, left: int, right: int) -> bool:
    return {
        "==": left == right,
        "!=": left != right,
        ">": left > right,
        "<": left < right,
        ">=": left >= right,
        "<=": left <= right,
    }[operator]


def make_step_cases(kind: str, seed: int, count: int, parameters: dict) -> list[dict]:
    rng = random.Random(seed)
    cases = []
    for _ in range(count):
        nums = [rng.randrange(-15, 16) for _ in range(rng.randrange(2, 18))]
        index = rng.randrange(1, len(nums))
        if kind in ("adjacent_changes", "increase_count", "equal_count"):
            state = {"event_count": rng.randrange(0, 30)}
        else:
            state = {"current_run": rng.randrange(1, 18), "best_run": rng.randrange(1, 18)}
        expected = dict(state)
        extends = _compare(parameters["relation"], nums[index], nums[index - 1])
        if kind in ("adjacent_changes", "increase_count", "equal_count"):
            current = state["event_count"]
            expected["event_count"] = (
                current + parameters["increment"] if extends else current
            )
        else:
            current = (
                state["current_run"] + parameters["increment"]
                if extends
                else parameters["reset_value"]
            )
            expected["current_run"] = current
            expected["best_run"] = (
                current
                if _compare(parameters["best_relation"], current, state["best_run"])
                else state["best_run"]
            )
        cases.append({"nums": nums, "index": index, "state": state, "expected": expected})
    return cases


def execute_step_public(library, kv, step, case):
    interface = normalize_operation(step)
    step_goal = TaskSpec(
        "step-validation",
        INPUT_SCHEMA,
        {"type": "runtime_state"},
        (),
        GOAL_SIGNATURE,
    )
    bindings = bind_operation(
        interface,
        step_goal,
        [case["nums"]],
        runtime_context={
            # The public STATE slot carries the live interpreter state; the
            # public INDEX slot is also bound, while the materialized recipe
            # uses its loop variable name "i" inside STATE.
            "STATE": {**case["state"], "i": case["index"]},
            "INDEX": case["index"],
            "BOOLEAN": None,
        },
    )
    executor = RecursiveFExecutor(library, kv, trace=True)
    result = executor.execute_public(interface, bindings)
    return {key: result[key] for key in case["expected"]}, executor


def js_batch(program: dict, inputs: list[list[int]]) -> list[int]:
    runtime = (ROOT / "fsys" / "recursive_f_runtime.js").read_text(encoding="utf8")
    code = (
        runtime
        + "\nconst P="
        + json.dumps(program, separators=(",", ":"))
        + ";const R=makeFExecutor(P);console.log(JSON.stringify("
        + json.dumps(inputs, separators=(",", ":"))
        + ".map(x=>R.run([x]).result)));"
    )
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "execute.js"
        path.write_text(code, encoding="utf8")
        result = subprocess.run(
            [str(NODE), str(path)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            encoding="utf8",
            check=True,
        )
    return json.loads(result.stdout.strip().splitlines()[-1])


def cold_step_replay(library_path: Path, kv_path: Path, step_id: str, cases: list[dict]):
    request_path = RUN_DIR / "cold_step_request.json"
    request_path.write_text(
        json.dumps(
            {
                "library": str(library_path),
                "kv": str(kv_path),
                "step": step_id,
                "cases": cases,
            }
        ),
        encoding="utf8",
    )
    worker = r'''import json,sys
from pathlib import Path
from fsys.library import Library
from fsys.external_kv import ExternalCandidateKV
from fsys.goal_composer import TaskSpec
from fsys.public_operations import bind_operation,normalize_operation
from fsys.recursive_f_runtime import RecursiveFExecutor
d=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
l=Library.load(d['library']);k=ExternalCandidateKV.load(d['kv']);s=k.get('strict-step')['structure']
from fsys.structure import Structure
obj=Structure.from_dict(s);iface=normalize_operation(obj);e=RecursiveFExecutor(l,k,trace=True);out=[]
goal=TaskSpec('cold-step',({'name':'nums','type':'sequence<int>'},),{'type':'runtime_state'},(),'cold')
for c in d['cases']:
 b=bind_operation(iface,goal,[c['nums']],runtime_context={'STATE':dict(c['state'],i=c['index']),'INDEX':c['index'],'BOOLEAN':None})
 state=e.execute_public(iface,b);out.append({'state':{key:state[key] for key in c['expected']},'expected':c['expected']})
print(json.dumps({'results':out,'dispatches':e.dispatches}))
'''
    result = subprocess.run(
        [str(PYTHON), "-c", worker, str(request_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf8",
        check=True,
    )
    payload = json.loads(result.stdout.strip().splitlines()[-1])
    return payload


def cold_root_replay(library_path: Path, kv_path: Path, root_id: str, inputs: list[list[int]]):
    request_path = RUN_DIR / f"{root_id}_cold_request.json"
    request_path.write_text(
        json.dumps(
            {"library": str(library_path), "kv": str(kv_path), "root": root_id, "inputs": inputs}
        ),
        encoding="utf8",
    )
    worker = r'''import json,sys
from pathlib import Path
from fsys.library import Library
from fsys.external_kv import ExternalCandidateKV
from fsys.recursive_f_runtime import RecursiveFExecutor
d=json.loads(Path(sys.argv[1]).read_text(encoding='utf8'))
l=Library.load(d['library']);k=ExternalCandidateKV.load(d['kv']);e=RecursiveFExecutor(l,k,trace=True)
out=[e.execute(d['root'],[x]) for x in d['inputs']]
print(json.dumps({'outputs':out,'dispatches':e.dispatches}))
'''
    result = subprocess.run(
        [str(PYTHON), "-c", worker, str(request_path)],
        cwd=ROOT,
        capture_output=True,
        text=True,
        encoding="utf8",
        check=True,
    )
    return json.loads(result.stdout.strip().splitlines()[-1])


def add_validation_receipt(kv, candidate_id, obj, checks, case_count, verifier):
    if set(checks) != set(REQUIRED_TOKEN_CHECKS) or not all(checks.values()):
        raise RuntimeError(f"admission refused: incomplete or failed checks {checks}")
    receipt = {
        "passed": True,
        "cases": case_count,
        "verifier": verifier,
        "receipt_digest": digest({"structure": obj.identity, "checks": checks}),
        "validation_stage": "cold_instance",
        "admission_checks": checks,
        "admission_subject_digest": subject_digest(obj),
    }
    kv.update(candidate_id, x_leakage_status="ZERO")
    kv.transition(candidate_id, "VALIDATED", receipt=receipt, reason="all unseen-instance admission checks passed")


def form_pair_from_run(library, run, label="primary"):
    candidate = run["candidate"]
    kv = run["kv"]
    step_raw = Structure.from_dict(kv.get(candidate["candidate_ids"]["step_candidate_id"])["structure"])
    root_raw = Structure.from_dict(kv.get(candidate["candidate_ids"]["root_candidate_id"])["structure"])
    step_recipe = thaw(step_raw.kernel)["formation_recipe"]
    topology_id = candidate["topology_id"]
    selected = dict(step_recipe["operation_bindings"])
    parameters = dict(candidate["bindings"])
    step = materialize_strict_f(library, step_raw, topology_id, "step", selected, parameters)
    return {
        "step": step,
        "root_raw": root_raw,
        "topology_id": topology_id,
        "selected": selected,
        "parameters": parameters,
        "candidate": candidate,
        "label": label,
    }


def _x_leakage_free(obj, examples):
    payload = json.dumps(thaw(obj.kernel), ensure_ascii=False, separators=(",", ":"))
    for row in examples:
        encoded = json.dumps(row["input"][0], ensure_ascii=False, separators=(",", ":"))
        if len(encoded) > 2 and encoded in payload:
            return False
    return True


def _promote(lib, kv, usage, obj, candidate_id, checks, count, operation_id):
    validate_structure(obj)
    add_validation_receipt(kv, candidate_id, obj, checks, count, "unseen-instance verifier + cold replay")
    gate = PromotionGate(lib, kv, usage)
    persisted = gate.promote(
        candidate_id,
        operation_identity=operation_id,
        min_verified_cases=count,
        min_cold_instance_cases=count,
        independent_formations=2,
        reason="strict parent-consistent unseen-instance admission",
    )
    return persisted, gate.receipts[-1]


def process_goal(lib, usage, goal_row, index):
    goal_id, display_name, kind = goal_row
    train_seed_a = 880031 + index
    train_seed_b = 990041 + index
    train_a = unseen_examples(kind, train_seed_a)
    train_b = unseen_examples(kind, train_seed_b)
    if {tuple(row["input"][0]) for row in train_a} & {
        tuple(row["input"][0]) for row in train_b
    }:
        raise RuntimeError(f"{goal_id}: independent unseen example cohorts overlap")
    holdout = holdout_inputs(index, train_a + train_b)
    goal_a = make_goal(goal_id, train_a)
    goal_b = make_goal(goal_id, train_b)

    # Each acquisition starts from the same Library snapshot and sees only
    # separately generated examples plus the public type contract.
    run_a = search_from_instances(lib, usage, goal_id, train_a, kind, holdout, "independent-A")
    library_snapshot = lib.persistent_hash
    run_b = search_from_instances(lib, usage, goal_id, train_b, kind, holdout, "independent-B")
    if lib.persistent_hash != library_snapshot:
        raise AssertionError("candidate search mutated the starting Library")
    pair_a = form_pair_from_run(lib, run_a, "independent-A")
    pair_b = form_pair_from_run(lib, run_b, "independent-B")
    if (
        pair_a["topology_id"] != pair_b["topology_id"]
        or pair_a["parameters"] != pair_b["parameters"]
        or pair_a["selected"] != pair_b["selected"]
        or pair_a["step"].identity != pair_b["step"].identity
    ):
        raise RuntimeError(f"{goal_id}: independent unseen-instance searches did not reconverge")
    step = pair_a["step"]
    topology_id = pair_a["topology_id"]
    parameters = pair_a["parameters"]
    selected = pair_a["selected"]
    if step.identity in lib._structures:
        raise RuntimeError(f"{goal_id}: step F unexpectedly pre-existed in the starting Library")

    # The raw Composer root is used only to confirm its selected graph and
    # cross-language behavior. It is never admitted: the strict root below is
    # formed only after the step F has become persistent.
    raw_root_id = pair_a["candidate"]["root_id"]
    raw_root_values = [
        _execute_search_candidate(run_a["composer"], pair_a["candidate"], goal_a, xs)
        for xs in holdout
    ]
    raw_js = js_batch(
        program_snapshot(lib, raw_root_id, run_a["kv"]),
        holdout,
    )
    raw_oracle = [oracle(kind, xs) for xs in holdout]
    if raw_root_values != raw_oracle or raw_js != raw_oracle:
        raise RuntimeError(f"{goal_id}: Composer-produced graph failed the independent oracle")

    strict_kv = ExternalCandidateKV(RUN_DIR / "kv_working.json")
    strict_kv.add(
        "strict-step",
        step,
        operation_signature=step.operation_id,
        formation_path=[
            {
                "topology_f": topology_id,
                "operation_bindings": thaw(step.kernel)["formation_recipe"]["operation_bindings"],
                "parameters": thaw(step.kernel)["formation_recipe"]["parameters"],
                "materialization": "step",
                "parents_at_formation": list(step.parents),
            }
        ],
        formation_source="unseen-instance composition",
        task_signature=goal_a.signature(),
        goal_signature=goal_a.goal_signature,
        slot_schema=step.alpha_schema.binding_slots,
        binding_schema=tuple(thaw(step.kernel)["formation_recipe"]["parameters"]),
        candidate_graph=thaw(step.kernel),
        candidate_rank=pair_a["candidate"]["rank"],
        x_leakage_status="PENDING",
        independent_formation_count=2,
    )

    step_cases = make_step_cases(kind, 740031 + index, 256, parameters)
    step_values = []
    step_trace_has_calls = True
    for case in step_cases:
        state, executor = execute_step_public(lib, strict_kv, step, case)
        step_values.append(state)
        expected = case["expected"]
        step_trace_has_calls = step_trace_has_calls and any(
            event["structure_id"] == step.identity for event in executor.dispatches
        )
        if state != expected:
            raise RuntimeError(f"{goal_id}: step F transition oracle mismatch")
    step_examples_pass = all(value == case["expected"] for value, case in zip(step_values[:24], step_cases[:24]))
    step_random_pass = all(value == case["expected"] for value, case in zip(step_values, step_cases))
    step_exact_audit = audit_exact_formation(step, lib, topology_id, "step")
    if not step_exact_audit:
        raise RuntimeError(f"{goal_id}: step parents do not exactly match formation inputs")

    # Step cross-language evidence is the same step executable embedded in
    # the independent root run above. Its code payload must match exactly.
    step_template = thaw(lib[topology_id].kernel)["formation_recipe"]["materialization"]["step"]
    raw_step = Structure.from_dict(
        run_a["kv"].get(pair_a["candidate"]["candidate_ids"]["step_candidate_id"])["structure"]
    )
    if thaw(raw_step.kernel)["executable"] != thaw(step.kernel)["executable"]:
        raise AssertionError("step executable changed during strict admission preparation")
    if not step_template:
        raise AssertionError("F-resident step recipe missing")
    raw_dispatch = RecursiveFExecutor(lib, run_a["kv"], trace=True)
    dispatch_input = next(xs for xs in holdout if len(xs) > 1)
    raw_dispatch.execute(raw_root_id, [dispatch_input])
    step_is_called = any(
        event["structure_id"] == raw_step.identity for event in raw_dispatch.dispatches
    )
    if not step_is_called:
        raise AssertionError("independent root did not dispatch the selected step F")

    # Cold-replay this step candidate in a separate process before promotion.
    cold_step_library = RUN_DIR / f"{goal_id}_before_step_promotion_library.json"
    cold_step_kv = RUN_DIR / f"{goal_id}_before_step_promotion_kv.json"
    lib.save(cold_step_library)
    strict_kv.save(cold_step_kv)
    cold_step = cold_step_replay(cold_step_library, cold_step_kv, step.identity, step_cases)
    cold_step_pass = all(row["state"] == row["expected"] for row in cold_step["results"])
    if not cold_step_pass:
        raise RuntimeError(f"{goal_id}: cold step validation failed")

    step_checks = {
        "interface": is_first_class_operation(normalize_operation(step)),
        "domain_audit": step_exact_audit,
        "executable": step_trace_has_calls,
        "examples": step_examples_pass,
        "randomized_oracle": step_random_pass,
        "cross_language": raw_values_equal(raw_root_values, raw_js, raw_oracle)
        and thaw(raw_step.kernel)["executable"] == thaw(step.kernel)["executable"],
        "cold_instance": cold_step_pass,
        "x_leakage": _x_leakage_free(step, train_a + train_b),
        "formation_parents": audit_formation_parents(step, lib)["passed"],
    }
    step_persisted, step_receipt = _promote(
        lib,
        strict_kv,
        usage,
        step,
        "strict-step",
        step_checks,
        len(step_cases),
        step.operation_id,
    )

    # Re-materialize both independent roots after the step is persistent.
    root_a = materialize_strict_f(
        lib,
        pair_a["root_raw"],
        topology_id,
        "root",
        selected,
        parameters,
        step_id=step_persisted.identity,
    )
    root_b = materialize_strict_f(
        lib,
        pair_b["root_raw"],
        pair_b["topology_id"],
        "root",
        pair_b["selected"],
        pair_b["parameters"],
        step_id=step_persisted.identity,
    )
    if root_a.identity != root_b.identity:
        raise RuntimeError(f"{goal_id}: independent canonical root formations did not match")
    root = root_a
    root_exact_audit = audit_exact_formation(root, lib, topology_id, "root")
    if not root_exact_audit:
        raise RuntimeError(f"{goal_id}: root parents do not exactly match formation inputs")

    strict_kv.add(
        "strict-root",
        root,
        operation_signature=root.operation_id,
        formation_path=[
            {
                "topology_f": topology_id,
                "step_f_persisted_before_formation": step_persisted.identity,
                "operation_bindings": thaw(root.kernel)["formation_recipe"]["operation_bindings"],
                "parameters": thaw(root.kernel)["formation_recipe"]["parameters"],
                "materialization": "root",
                "parents_at_formation": list(root.parents),
            }
        ],
        formation_source="unseen-instance composition",
        task_signature=goal_a.signature(),
        goal_signature=goal_a.goal_signature,
        slot_schema=root.alpha_schema.binding_slots,
        binding_schema=tuple(thaw(root.kernel)["formation_recipe"]["parameters"]),
        candidate_graph=thaw(root.kernel),
        candidate_rank=pair_a["candidate"]["rank"],
        x_leakage_status="PENDING",
        independent_formation_count=2,
    )

    train_values = []
    public_bindings = None
    for row in train_a + train_b:
        active_goal = goal_a if row in train_a else goal_b
        result, executor, current_bindings = _run_public_root(
            lib, strict_kv, root, active_goal, row["input"][0]
        )
        if public_bindings is None:
            public_bindings = current_bindings
        train_values.append(result)
        if result != row["output"]:
            raise RuntimeError(f"{goal_id}: strict root failed an unseen training instance")
    holdout_values = []
    normalized_step_dispatch = False
    for xs in holdout:
        result, executor, _ = _run_public_root(lib, strict_kv, root, goal_a, xs)
        holdout_values.append(result)
        normalized_step_dispatch = normalized_step_dispatch or any(
            event["structure_id"] == step_persisted.identity for event in executor.dispatches
        )
    expected_holdout = [oracle(kind, xs) for xs in holdout]
    root_js = js_batch(program_snapshot(lib, root.identity, strict_kv), holdout)
    if holdout_values != expected_holdout or root_js != expected_holdout:
        raise RuntimeError(f"{goal_id}: strict root Python/JS/oracle mismatch")

    # The root is cold-loaded from a persisted step and an uncommitted root KV
    # record, then compared with the independent oracle before it can enter.
    cold_root_library = RUN_DIR / f"{goal_id}_before_root_promotion_library.json"
    cold_root_kv = RUN_DIR / f"{goal_id}_before_root_promotion_kv.json"
    lib.save(cold_root_library)
    strict_kv.save(cold_root_kv)
    cold_root = cold_root_replay(cold_root_library, cold_root_kv, root.identity, holdout)
    cold_root_pass = cold_root["outputs"] == expected_holdout
    if not cold_root_pass:
        raise RuntimeError(f"{goal_id}: cold root replay mismatch")

    root_checks = {
        "interface": is_first_class_operation(normalize_operation(root)),
        "domain_audit": root_exact_audit,
        "executable": bool(holdout_values) and normalized_step_dispatch,
        "examples": all(
            value == row["output"] for value, row in zip(train_values, train_a + train_b)
        ),
        "randomized_oracle": holdout_values == expected_holdout,
        "cross_language": root_js == expected_holdout,
        "cold_instance": cold_root_pass,
        "x_leakage": _x_leakage_free(root, train_a + train_b),
        "formation_parents": audit_formation_parents(root, lib)["passed"],
    }
    root_persisted, root_receipt = _promote(
        lib,
        strict_kv,
        usage,
        root,
        "strict-root",
        root_checks,
        len(holdout),
        root.operation_id,
    )

    # Reload both persisted objects and call the root through the ordinary
    # public operation path, using only its declared formal slots.
    reloaded = Library.load(RUN_DIR / f"{goal_id}_before_root_promotion_library.json")
    # The pre-promotion snapshot intentionally lacks root; final reload is
    # checked later after all goals have been promoted.
    if step_persisted.identity not in reloaded._structures:
        raise AssertionError("step F was not persistent before root formation")
    if root_persisted.parents.count(step_persisted.identity) != 1:
        raise AssertionError("root does not declare its persistent step F exactly once")

    # Reject the raw Composer drafts: their broad parent sets are search-only
    # artifacts and are never part of the resulting Library.
    for raw_run in (run_a, run_b):
        for candidate_id in raw_run["candidate"]["candidate_ids"].values():
            if candidate_id not in raw_run["kv"].records:
                continue
            record = raw_run["kv"].get(candidate_id)
            if record["status"] == "CANDIDATE":
                raw_run["kv"].transition(
                    candidate_id,
                    "REJECTED",
                    reason="search draft replaced by exact-parent canonical formation",
                )

    lib.save(RUN_DIR / f"{goal_id}_library_after.json")
    usage.save(RUN_DIR / f"{goal_id}_usage_after.json")
    strict_kv.save(RUN_DIR / f"{goal_id}_kv_after.json")

    # Ordinary reuse assertion for subsequent goals: the newly admitted root
    # is available through the same public operation enumeration as old Fs.
    next_probe = make_goal(goal_id + "-probe", train_a)
    probe_composer = GoalDrivenComposer(
        lib,
        ExternalCandidateKV(),
        public_contracts=publish_recursive_operation_contracts(lib),
        usage_registry=usage,
    )
    probe_pool = probe_composer.retrieve_operations(next_probe)
    ordinary_reuse = any(item.identity == root_persisted.identity for item in probe_pool)
    if not ordinary_reuse:
        raise AssertionError("admitted root is not an ordinary public operation candidate")

    trace = {
        "goal_id": goal_id,
        "display_name": display_name,
        "composer_inputs": {
            "task_id": goal_id,
            "inputs": INPUT_SCHEMA,
            "output": OUTPUT_SCHEMA,
            "goal_signature": GOAL_SIGNATURE,
            "training_examples_A": train_a,
            "training_examples_B": train_b,
            "oracle_code_passed_to_composer": False,
            "algorithm_name_passed_to_composer": False,
        },
        "seeds": {"training_A": train_seed_a, "training_B": train_seed_b, "holdout": 640031 + index},
        "unseen_input_count_per_independent_search": len(train_a),
        "independent_searches": [
            {
                "label": "A",
                "candidate_attempts": run_a["composer"].search_metrics["candidate_attempts"],
                "binding_combinations": run_a["composer"].search_metrics["binding_combinations"],
                "composition_expansions": run_a["composer"].search_metrics["composition_expansions"],
                "intermediate_materializations": run_a["composer"].search_metrics["intermediate_materializations"],
                "selected_rank": pair_a["candidate"]["rank"],
                "candidate_kind": pair_a["candidate"]["candidate_kind"],
                "topology_F": topology_id,
                "parameters": parameters,
                "selected_library_F_bindings": selected,
                "attempts": run_a["attempts"],
                "trace": run_a["composer"].search_trace,
            },
            {
                "label": "B",
                "candidate_attempts": run_b["composer"].search_metrics["candidate_attempts"],
                "binding_combinations": run_b["composer"].search_metrics["binding_combinations"],
                "composition_expansions": run_b["composer"].search_metrics["composition_expansions"],
                "intermediate_materializations": run_b["composer"].search_metrics["intermediate_materializations"],
                "selected_rank": pair_b["candidate"]["rank"],
                "candidate_kind": pair_b["candidate"]["candidate_kind"],
                "topology_F": pair_b["topology_id"],
                "parameters": pair_b["parameters"],
                "selected_library_F_bindings": pair_b["selected"],
                "attempts": run_b["attempts"],
                "trace": run_b["composer"].search_trace,
            },
        ],
        "formation": {
            "step_F": step_persisted.identity,
            "step_parents": list(step_persisted.parents),
            "step_formation_parents_exact": step_exact_audit,
            "step_admitted_before_root_formation": True,
            "root_F": root_persisted.identity,
            "root_parents": list(root_persisted.parents),
            "root_formation_parents_exact": root_exact_audit,
            "independent_step_identity_equal": pair_a["step"].identity == pair_b["step"].identity,
            "independent_root_identity_equal": root_a.identity == root_b.identity,
            "root_step_parent_is_library_at_formation": step_persisted.identity in lib._structures,
            "raw_composer_drafts_admitted": False,
        },
        "validation": {
            "step": step_checks,
            "root": root_checks,
            "step_verified_transitions": len(step_cases),
            "root_train_examples": len(train_a) + len(train_b),
            "root_holdout_oracle_cases": len(holdout),
            "raw_search_python_equal_oracle": raw_root_values == raw_oracle,
            "raw_search_javascript_equal_oracle": raw_js == raw_oracle,
            "root_python_equal_oracle": holdout_values == expected_holdout,
            "root_javascript_equal_oracle": root_js == expected_holdout,
            "root_cold_replay_equal_oracle": cold_root_pass,
            "step_receipt_digest": digest(step_receipt),
            "root_receipt_digest": digest(root_receipt),
            "public_root_binding": public_bindings,
            "ordinary_public_operation_reuse": ordinary_reuse,
        },
        "library_size_after": len(lib),
        "promoted_F": [step_persisted.identity, root_persisted.identity],
    }
    (RUN_DIR / f"{goal_id}_trace.json").write_text(
        json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf8"
    )
    return trace


def raw_values_equal(actual, javascript, expected):
    return actual == expected and javascript == expected


def compact_acquisition_trace(trace):
    composer_inputs=trace["composer_inputs"]
    independent=[]
    for search in trace["independent_searches"]:
        independent.append({key:search[key] for key in (
            "label","candidate_attempts","binding_combinations","composition_expansions",
            "intermediate_materializations","selected_rank","candidate_kind","topology_F",
            "parameters","selected_library_F_bindings")})
    validation=trace["validation"]
    return {
        "goal_id":trace["goal_id"],"display_name":trace["display_name"],
        "fresh_instances_per_independent_search":trace["unseen_input_count_per_independent_search"],
        "training_instance_counts":{"A":len(composer_inputs["training_examples_A"]),
            "B":len(composer_inputs["training_examples_B"])},
        "seeds":trace["seeds"],"independent_searches":independent,
        "formation":trace["formation"],
        "validation":{"step":validation["step"],"root":validation["root"],
            "step_verified_transitions":validation["step_verified_transitions"],
            "root_train_examples":validation["root_train_examples"],
            "root_holdout_oracle_cases":validation["root_holdout_oracle_cases"],
            "raw_search_python_equal_oracle":validation["raw_search_python_equal_oracle"],
            "raw_search_javascript_equal_oracle":validation["raw_search_javascript_equal_oracle"],
            "root_python_equal_oracle":validation["root_python_equal_oracle"],
            "root_javascript_equal_oracle":validation["root_javascript_equal_oracle"],
            "root_cold_replay_equal_oracle":validation["root_cold_replay_equal_oracle"],
            "step_receipt_digest":validation["step_receipt_digest"],
            "root_receipt_digest":validation["root_receipt_digest"]},
        "library_size_after":trace["library_size_after"],"promoted_F":trace["promoted_F"]}


def main():
    global RUN_DIR
    RUN_DIR=Path(tempfile.mkdtemp(prefix=".run-",dir=OUT))
    atexit.register(shutil.rmtree,RUN_DIR,ignore_errors=True)
    tasks=selected_tasks()
    base_library = Path(os.environ.get("V94_BASE_LIBRARY", BASE / "library_seed.json"))
    base_usage = Path(os.environ.get("V94_BASE_USAGE", BASE / "usage_seed.json"))
    library = Library.load(base_library)
    usage = UsageRegistry.load(base_usage, library) if base_usage.is_file() else UsageRegistry(library)
    start_ids = {structure.identity for structure in library}
    old_expansion_ids = {
        "F_402cb20a4f904e5c8720",
        "F_521765c1e90d86125486",
        "F_8a75fb308f75d2140d76",
        "F_8deeca101259f7d6cc55",
        "F_70f84b93a9d06f326f41",
        "F_5a81f450cf70f003bee9",
    }
    if start_ids & old_expansion_ids:
        raise RuntimeError("starting Library contains entries from the superseded expansion")
    start_hash = hashlib.sha256(base_library.read_bytes()).hexdigest()
    library.save(RUN_DIR / "library_start.json")
    usage.save(RUN_DIR / "usage_start.json")
    seed_manifest = {
        "starting_library": str(base_library.relative_to(ROOT)),
        "sha256": start_hash,
        "structure_count": len(library),
        "structure_ids": sorted(start_ids),
        "new_target_roots_preloaded": 0,
        "composer_receives_only_instance_examples_and_public_contract": True,
        "input_examples_disjoint_from_previous_expansion_and_acquisition_runs": True,
    }
    (OUT / "seed_manifest.json").write_text(
        json.dumps(seed_manifest, ensure_ascii=False, indent=2), encoding="utf8"
    )

    traces = []
    for index, row in enumerate(tasks):
        trace = process_goal(library, usage, row, index)
        traces.append(trace)
    known_inputs = _historical_input_keys()
    all_new_inputs = set()
    for trace in traces:
        for key in ("training_examples_A", "training_examples_B"):
            for example in trace["composer_inputs"][key]:
                instance = tuple(example["input"][0])
                if instance in known_inputs or instance in all_new_inputs:
                    raise RuntimeError("a training example is historical or repeated across Goals")
                all_new_inputs.add(instance)

    library.save(OUT / "library_final.json")
    usage.save(OUT / "usage_final.json")
    final_reload = Library.load(OUT / "library_final.json")
    UsageRegistry.load(OUT / "usage_final.json", final_reload)
    reload_checks = []
    for index, (task, trace) in enumerate(zip(tasks, traces)):
        goal_id, name, kind = task
        root_id = trace["formation"]["root_F"]
        step_id = trace["formation"]["step_F"]
        topology_id = trace["independent_searches"][0]["topology_F"]
        examples = trace["composer_inputs"]["training_examples_A"]
        xs = holdout_inputs(index, examples)
        actual = [RecursiveFExecutor(final_reload, ExternalCandidateKV()).execute(root_id, [x]) for x in xs]
        expected = [oracle(kind, x) for x in xs]
        step_exact = audit_exact_formation(final_reload[step_id], final_reload, topology_id, "step")
        root_exact = audit_exact_formation(final_reload[root_id], final_reload, topology_id, "root")
        ordinary_interface = is_first_class_operation(normalize_operation(final_reload[root_id]))
        reload_checks.append(
            {
                "goal_id": goal_id,
                "root_F": root_id,
                "root_present": root_id in final_reload._structures,
                "step_present": step_id in final_reload._structures,
                "step_parents_still_exact": step_exact,
                "root_parents_still_exact": root_exact,
                "root_is_ordinary_public_operation": ordinary_interface,
                "matches_oracle": actual == expected,
                "cases": len(xs),
            }
        )
    if not all(
        row["root_present"]
        and row["step_present"]
        and row["step_parents_still_exact"]
        and row["root_parents_still_exact"]
        and row["root_is_ordinary_public_operation"]
        and row["matches_oracle"]
        for row in reload_checks
    ):
        raise RuntimeError("final Library cold reload failed")
    expected_size=len(start_ids)+2*len(tasks)
    if len(final_reload) != expected_size:
        raise RuntimeError(f"expected {2*len(tasks)} strictly admitted Fs, got Library size {len(final_reload)}")

    summary = {
        "starting_library_size": len(start_ids),
        "final_library_size": len(final_reload),
        "goals": len(traces),
        "goal_root_map": {trace["goal_id"]:trace["formation"]["root_F"] for trace in traces},
        "source_library": str(base_library.relative_to(ROOT)),
        "source_library_sha256": start_hash,
        "strict_formation_from_seed": len(start_ids)==12 and base_library.name=="library_seed.json",
        "fresh_training_instances": len(all_new_inputs),
        "all_training_instances_disjoint_from_prior_runs": True,
        "newly_promoted_F": sum(len(trace["promoted_F"]) for trace in traces),
        "acquired_goals": len(traces),
        "all_composer_goals_were_example_only": all(
            not trace["composer_inputs"]["oracle_code_passed_to_composer"]
            and not trace["composer_inputs"]["algorithm_name_passed_to_composer"]
            for trace in traces
        ),
        "all_step_parents_exact": all(trace["formation"]["step_formation_parents_exact"] for trace in traces),
        "all_root_parents_exact": all(trace["formation"]["root_formation_parents_exact"] for trace in traces),
        "all_root_steps_persisted_before_formation": all(
            trace["formation"]["step_admitted_before_root_formation"]
            and trace["formation"]["root_step_parent_is_library_at_formation"]
            for trace in traces
        ),
        "all_semantic_checks_passed": all(
            all(trace["validation"][role].values())
            for trace in traces
            for role in ("step", "root")
        ),
        "final_reload": reload_checks,
        "five_round_experiment": False,
        "formal_composer_modified": False,
        "unified_admission_formation_parent_audit": True,
    }
    (OUT / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf8"
    )
    (OUT / "acquisition_traces.json").write_text(
        json.dumps([compact_acquisition_trace(trace) for trace in traces],
            ensure_ascii=False,indent=2),encoding="utf8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
