# FAST experiment report

**Status:** `INCOMPLETE_OR_FAILED` — F-system rev4 completed; DreamCoder rev4 was not run.  
**Protocol SHA-256:** `6516317018c950cc9b7f4b98467fd4f8fd0d181be9474b40889b065bd4a68639`  
**F-system:** 3/3 seeds complete; **DreamCoder:** 0/3 seeds run under this revision.

This is a resource-bounded run using the official DreamCoder List Processing implementation and DSL. It is not a reproduction of DreamCoder's published peak performance. Because DreamCoder could not run reliably in the supplied Windows environment, this artifact does **not** contain a completed cross-system comparison.

## DreamCoder coverage

The three revision-3 DreamCoder processes each passed the frozen 174/30 split check, then exited during the first recognition setup with native code `0xC0000005`; Windows Application Error 1000 named `nvdxgdmal64.dll_unloaded`. Revision 4 marks these seeds `NOT_RUN_AFTER_PRIOR_PLATFORM_ERROR` and does not retry them. Revision 1–3 partial logs and raw records are archived separately and excluded from revision-4 results. Therefore Q1 is **N/A**; no DreamCoder mean, standard deviation, or median is reported.

The separate full-default reference is archived as `INCOMPLETE_FULL_DEFAULT_REFERENCE` and is sanity-only. It is excluded from all FAST aggregates.

## Q1. Did DreamCoder improve unseen performance after wake-sleep and compression?

**N/A.** No DreamCoder seed ran under revision 4, so this experiment cannot assess wake-sleep, compression, or learned-grammar reuse.

## Q2. Did the accumulated F-system Library solve unseen Goals by reusing earlier F?

Each seed completed 174 cold Goals, 174 accumulation Goals, and 30 unseen Goals in both conditions. The persisted Library had **8 / 11 / 7 composites** for seeds 17 / 29 / 43; all Library reload hashes matched, all usage sidecars loaded, paired Goal runs were isolated, and test runs were not written back.

| Seed | Cold strict solves | Accumulation strict solves | Composites | FIRST_CLASS visible / strict unseen | EXPANDED visible / strict unseen | Terminal candidates mentioning composites | Successful verified composite reuse |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 17 | 18/174 | 18/174 | 8 | 0/30 / 0/30 | 0/30 / 0/30 | 24/30 | 0 |
| 29 | 18/174 | 21/174 | 11 | 0/30 / 0/30 | 0/30 / 0/30 | 0/30 | 0 |
| 43 | 18/174 | 17/174 | 7 | 0/30 / 0/30 | 0/30 / 0/30 | 3/30 | 0 |

The candidate-reference counts mean that a terminal search candidate AST mentioned a composite. Those Goals ended with `SELECTOR_BUDGET_EXHAUSTED`; the candidates were **not solved programs** and failed held-out verification. Successful direct reuse and successful hidden-example-verified composite reuse were both **0**. Under this budget, the accumulated Library formed composites but did not produce a verified unseen solution.

## Q3. Did FIRST_CLASS reduce search cost relative to EXPANDED?

Paired delta is **EXPANDED minus FIRST_CLASS**. A negative value means EXPANDED used less.

| Metric | Seed 17 | Seed 29 | Seed 43 | Mean | Std. dev. | Median | Interpretation |
|---|---:|---:|---:|---:|---:|---:|---|
| Candidate attempts | -6,596 | -6,908 | -5,117 | -6,207 | 956.77 | -6,596 | FIRST_CLASS used more attempts |
| Search nodes | 0 | 0 | 0 | 0 | 0 | 0 | no measured difference |
| Wall time (s) | -0.221 | -0.284 | -0.147 | -0.217 | 0.068 | -0.221 | FIRST_CLASS took longer |
| Composition expansions | N/A | N/A | N/A | N/A | N/A | N/A | adapter does not expose counter |
| Intermediate materializations | N/A | N/A | N/A | N/A | N/A | N/A | adapter does not expose counter |

With zero unseen solves in both conditions, the measured results do not show a search-cost benefit for FIRST_CLASS. The terminal partial candidates sometimes referenced composites, but that did not yield successful reuse.

## Q4. How did formation cost compare with later reuse savings?

| Seed | Formation attempts | Formation wall time | Expanded-minus-FIRST_CLASS mean attempts / unseen Goal | Expanded-minus-FIRST_CLASS mean wall time / Goal |
|---:|---:|---:|---:|---:|
| 17 | 65,936 | 3.80 s | -6,983.27 | -0.259 s |
| 29 | 159,207 | 8.73 s | -7,724.57 | -0.308 s |
| 43 | 66,306 | 3.89 s | -5,580.17 | -0.197 s |
| **Total / mean** | **291,449** | **16.41 s** | **-6,762.67** | **-0.255 s** |

The measured paired deltas are negative for every seed: EXPANDED used fewer candidate attempts and less wall time than FIRST_CLASS. These results provide no evidence that the measured reuse savings amortized formation cost under this configuration. This is not an amortization claim for other budgets or domains.

## Protocol and artifacts

- FAST parameters: 2 iterations, 1 s enumeration/task, 180 s recognition/iteration, 1 s testing/task; 1 CPU and 1 numeric thread per DreamCoder seed; fixed Lucas-old/common task snapshot with 22 semantic primitives, 174 train and 30 unseen Goals.
- The one 15-second startup smoke was run once and excluded from results.
- F-system wrapper runtime: 1,806.3 s wall and 1,457.1 s CPU for all three seeds.
- Rev1–3 attempts and the incomplete full-default run remain in separate archives and are not mixed into these metrics.
- `summary_fast.csv`: per-seed outcomes; `task_level_metrics_fast.csv`: per-Goal outcomes and costs; `paired_task_metrics_fast.csv`: paired FIRST_CLASS/EXPANDED costs.
- `fsystem_raw_results.json` and each `fsystem/seed_XX/library.json` plus usage sidecar hold the raw results and persistent Libraries. `dreamcoder/seed_XX/seed_status.json` records why the current revision did not run DreamCoder.
- Composition-expansion and intermediate-materialization counters were unavailable and are reported as N/A. DreamCoder program checks and F-system candidate attempts are separate units.
- V9.4 core files remain hash-identical to the pre-experiment baseline. Only experiment adapters were corrected for seed isolation, result serialization, and output directory creation.
