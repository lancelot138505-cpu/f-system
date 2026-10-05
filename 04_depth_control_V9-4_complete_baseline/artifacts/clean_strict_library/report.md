# Clean strict Library acquisition

## 结果

状态：**PASS**。从官方 seed 通过未见实例驱动的 acquisition 形成 7 个真实 Goal 的 step/root F。每个新 F 都经过完整 admission；step 在 root formation 前已持久化；step 和 root 的 exact-parent audit 均通过。

| 项目 | 结果 |
|---|---:|
| seed Library | 12 Structures |
| Goal 数 | 7 |
| 新形成并 promotion 的 F | 14 |
| 两组独立训练实例 | 每 Goal 各 24 条，共 336 条 |
| root holdout | 每 Goal 192 条 |
| 最终 clean strict Library | 26 Structures |
| 可执行 exact-parent 失败 | 0 |

种子文件：`data/library_seed.json`  
种子 SHA-256：`5ea44a0ac094923fccdd4db12a5174aba878732ff7bccbf2e9bbfa127900dd3b`  
最终 Library 文件 SHA-256：`a418fb29bcf1db35c87f01e0a08842e0bcca93433d0305c3dfcc953e220ad007`  
Library persistent hash：`f097307e1697f10794e483f1907ac0de92ddc8eba195937b1a87662fa1ee58b0`

## 每个 Goal 的形成

| Goal | step F | root F | step/root parents exact | 192-case cold reload |
|---|---|---|---|---|
| U11 | `F_b0eed8df520ae260af57` | `F_dcfedcf1d26444553cdb` | PASS | PASS |
| U12 | `F_93302b6b7d8bd69c4f8c` | `F_bd1c8b86e4a28d068f0b` | PASS | PASS |
| U13 | `F_4e10ab1d0d80eb74a490` | `F_f6a9887dd22cb41d2cbf` | PASS | PASS |
| G01 | `F_44204b70253aa1408a91` | `F_c1afe69a1f8fa25c1f13` | PASS | PASS |
| G03 | `F_50bf23b4448cb8a0cb80` | `F_a6aef3cbef65ed27e8d8` | PASS | PASS |
| G05 | `F_58eac04f390c93643c95` | `F_cac97282833230855ffa` | PASS | PASS |
| G07 | `F_cbaa441e9f4fad8bc76e` | `F_f621e7a403c970a7a71b` | PASS | PASS |

每个 step 与 root 均由两次独立 Composer 搜索复现相同 identity，再由 canonical formation 形成。root 的 formation inputs 包含持久 step F；parent 列表与实际 executable F 引用一致。Python、JavaScript、oracle、examples、独立 cold process 和 leakage checks 均通过。

G03 Goal 是严格递减连续段，使用 `<`。曾有一版 runner 把 verifier 写成 `<=`；已修正 verifier 并从 seed 重跑全部 7 个 Goal。当前 seed、Library、UsageRegistry、summary 和 trace 均来自这次修正后的运行。

## 重跑

```powershell
python experiments/v94_unseen_instance_library_growth.py
```

正式输出：`library_final.json`、`usage_final.json`、`summary.json`、`seed_manifest.json`、`acquisition_traces.json`。冷重载请求、KV journal、promotion checkpoint 和完整临时 trace 在进程结束时清理。
