# V9.4 clean strict higher-order F composition

**Result: PASS — higher-order composition, exact parents, admission and cold reload.**

Baseline: `26` Structures; SHA-256 `a418fb29bcf1db35c87f01e0a08842e0bcca93433d0305c3dfcc953e220ad007`. The baseline file was not modified.

Each Goal was searched twice from the same clean strict Library and UsageRegistry snapshot. The Composer enumerated public binary operation pairs, then selected and bound only the two public operations. The accepted F parents exactly equal those selected persisted F IDs. Candidate admission, Python/F/JavaScript/oracle checks, exact-parent audit, promotion, public retrieval after cold load, and separate-process cold execution all passed.

| Goal | Selected F children | New F | attempts | bindings | depth | exact parents | semantic equality | cold reload |
|---|---|---|---:|---:|---:|---|---|---|
| HG01 | `F_c1afe69a1f8fa25c1f13` + `F_cac97282833230855ffa` | `F_13d4dc25417f2406b9e2` | 63 | 63 | 3 | PASS | PASS | PASS (128 cases) |
| HG02 | `F_a6aef3cbef65ed27e8d8` + `F_f621e7a403c970a7a71b` | `F_301c091cc230f0a27ad9` | 33 | 33 | 3 | PASS | PASS | PASS (128 cases) |


### HG01 trace

```text
RETRIEVE public child F: F_c1afe69a1f8fa25c1f13
RETRIEVE public child F: F_cac97282833230855ffa
SELECT public binary operation pair at rank 63 (search depth 3)
BIND only public slots for both children
COMPOSE and admit F_13d4dc25417f2406b9e2
exact formation parents: ['F_c1afe69a1f8fa25c1f13', 'F_cac97282833230855ffa']
```


### HG02 trace

```text
RETRIEVE public child F: F_a6aef3cbef65ed27e8d8
RETRIEVE public child F: F_f621e7a403c970a7a71b
SELECT public binary operation pair at rank 33 (search depth 3)
BIND only public slots for both children
COMPOSE and admit F_301c091cc230f0a27ad9
exact formation parents: ['F_a6aef3cbef65ed27e8d8', 'F_f621e7a403c970a7a71b']
```

## Admission checks

Every new F passed interface, public-operation/domain compatibility, execution, examples, fixed-seed oracle, JavaScript equality, separate-process cold execution, source/input leakage, and exact formation-parent validation. Promotion persisted the same canonical identity. The two acquisitions used clean-room Library copies; the official clean strict baseline remained unchanged.
