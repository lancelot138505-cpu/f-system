# V9.4 clean strict Library — first-class F reuse

Source Library: 26 Structures; hash `f097307e1697f10794e483f1907ac0de92ddc8eba195937b1a87662fa1ee58b0`. The paired cohort contains 3 existing real Goals. Each pair used the same Library, examples, seed, verifier, oracle and initial state.

## Paired search results

| Goal | target F | EXPANDED attempts / bindings / depth | FIRST-CLASS attempts / bindings / depth | semantic equal | result |
|---|---|---:|---:|---|---|
| U11 | `F_dcfedcf1d26444553cdb` | 17 / 17 / 2 | 5 / 5 / 1 | yes | PASS — SEARCH REDUCTION |
| U12 | `F_bd1c8b86e4a28d068f0b` | 13 / 13 / 2 | 2 / 2 / 1 | yes | PASS — SEARCH REDUCTION |
| U13 | `F_f6a9887dd22cb41d2cbf` | 8 / 8 / 2 | 7 / 7 / 1 | yes | PASS — SEARCH REDUCTION |

Totals: attempts 38 → 14 (63.2% reduction); bindings 38 → 14 (63.2%); depth 6 → 3 (50.0%).

Overall: PASS — SEARCH REDUCTION. Each B route selected the target as a direct public operation edge. Exact-parent audit passes for every target.

## Per-Goal traces

### U11 — `nondecreasing_run`

```text
EXPANDED:
{"candidate_kind":"recipe_composition","masked_target":"F_dcfedcf1d26444553cdb","selected_topology":"F_topology_adjacent_state_run","selected_parameters":{"best_nonempty_initial":1,"best_relation":">","increment":1,"loop_start":1,"relation":">=","reset_value":1,"run_initial":1},"composed_child_F_ids":["F_exec_compare","F_exec_conditional_update","F_exec_assign","F_exec_range_loop","F_402cb20a4f904e5c8720","F_exec_return"],"root":"F_521765c1e90d86125486","search_depth":2}
FIRST-CLASS F:
{"candidate_kind":"operation_edge","candidate_rank":5,"event_order":["RETRIEVE_OPERATION","ENUMERATE_PUBLIC_BINDING","SELECT_OPERATION","BIND_PUBLIC_SLOTS","EXECUTE_OPERATION"],"retrieved_target":true,"public_interface_passed":true,"selected_target":"F_dcfedcf1d26444553cdb","candidate_binding":{"SEQUENCE":{"goal_input":"nums"},"STATE":{"runtime_context":"STATE"},"INDEX":{"runtime_context":"INDEX"},"BOOLEAN":{"runtime_context":"BOOLEAN"}},"binding":{"SEQUENCE":[1],"STATE":{},"INDEX":null,"BOOLEAN":null},"formal_slots":["SEQUENCE","STATE","INDEX","BOOLEAN"],"topology_or_internal_recipe_binding_enumerations_before_select":[],"hidden_reads_before_select":[]}
```

### U12 — `equal_run`

```text
EXPANDED:
{"candidate_kind":"recipe_composition","masked_target":"F_bd1c8b86e4a28d068f0b","selected_topology":"F_topology_adjacent_state_run","selected_parameters":{"best_nonempty_initial":1,"best_relation":">","increment":1,"loop_start":1,"relation":"==","reset_value":1,"run_initial":1},"composed_child_F_ids":["F_exec_compare","F_exec_conditional_update","F_exec_assign","F_exec_range_loop","F_8a75fb308f75d2140d76","F_exec_return"],"root":"F_8deeca101259f7d6cc55","search_depth":2}
FIRST-CLASS F:
{"candidate_kind":"operation_edge","candidate_rank":2,"event_order":["RETRIEVE_OPERATION","ENUMERATE_PUBLIC_BINDING","SELECT_OPERATION","BIND_PUBLIC_SLOTS","EXECUTE_OPERATION"],"retrieved_target":true,"public_interface_passed":true,"selected_target":"F_bd1c8b86e4a28d068f0b","candidate_binding":{"SEQUENCE":{"goal_input":"nums"},"STATE":{"runtime_context":"STATE"},"INDEX":{"runtime_context":"INDEX"},"BOOLEAN":{"runtime_context":"BOOLEAN"}},"binding":{"SEQUENCE":[1],"STATE":{},"INDEX":null,"BOOLEAN":null},"formal_slots":["SEQUENCE","STATE","INDEX","BOOLEAN"],"topology_or_internal_recipe_binding_enumerations_before_select":[],"hidden_reads_before_select":[]}
```

### U13 — `adjacent_changes`

```text
EXPANDED:
{"candidate_kind":"recipe_composition","masked_target":"F_f6a9887dd22cb41d2cbf","selected_topology":"F_topology_adjacent_event_count","selected_parameters":{"increment":1,"initial_count":0,"loop_start":1,"relation":"!="},"composed_child_F_ids":["F_exec_compare","F_exec_conditional_update","F_exec_assign","F_exec_range_loop","F_70f84b93a9d06f326f41","F_exec_return"],"root":"F_5a81f450cf70f003bee9","search_depth":2}
FIRST-CLASS F:
{"candidate_kind":"operation_edge","candidate_rank":7,"event_order":["RETRIEVE_OPERATION","ENUMERATE_PUBLIC_BINDING","SELECT_OPERATION","BIND_PUBLIC_SLOTS","EXECUTE_OPERATION"],"retrieved_target":true,"public_interface_passed":true,"selected_target":"F_f6a9887dd22cb41d2cbf","candidate_binding":{"SEQUENCE":{"goal_input":"nums"},"STATE":{"runtime_context":"STATE"},"INDEX":{"runtime_context":"INDEX"},"BOOLEAN":{"runtime_context":"BOOLEAN"}},"binding":{"SEQUENCE":[1],"STATE":{},"INDEX":null,"BOOLEAN":null},"formal_slots":["SEQUENCE","STATE","INDEX","BOOLEAN"],"topology_or_internal_recipe_binding_enumerations_before_select":[],"hidden_reads_before_select":[]}
```

## Clean Library and semantic checks

Current snapshot audit: 20 composite PASS, 0 executable failure, 2 non-executable topology templates. ACTIVE 26; RETIRED 0; equivalence classes 26; maximum level 4.
Every paired Python and JavaScript output equals the independent oracle on the same examples and holdout. Search did not change Library hash, lifecycle data, scoring or ranking.
Machine data and event traces: `results.json`.
