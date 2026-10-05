# V9.4：统一搜索空间治理基线

V9.4 是一个具有统一验证准入、持久 F Library、一等 F 复用、递归组合和搜索空间治理能力的程序合成基线。它把两个独立问题放在同一套 Library、Composer、verifier 和 admission 流程中测量：

- **深度治理**：把已经通过 admission 的高阶 F 当作普通 public operation 直接复用，避免再次形成其历史组合。
- **宽度治理**：通过 `UsageRegistry`、等价类代表和 `OperationPoolProvider` 控制在线可见操作。

## 正式搜索路径

```text
Persistent F Library
        ↓
UsageRegistry / 显式 equivalence
        ↓
OperationPoolProvider
        ↓
FULL / ACTIVE / RETIRED lifecycle view
        ↓
public interface / contract / type / slot compatibility
        ↓
GoalDrivenComposer / Planner
        ↓
FIRST_CLASS reuse 或普通合法组合
        ↓
formation
        ↓
KV / strict admission
        ↓
persistent F Library
```

`OperationPoolProvider` 是搜索候选的统一入口。Composer 按公开 interface、类型、slot 和 contract 工作，不根据 provenance、level 或形成来源给予 F 特权。FIRST_CLASS trace 记录 provider 检索、直接选中目标 F、public formal slots 绑定，以及选择前未读取内部 formation 字段的审计结果。

`FULL` 暴露全部持久、可执行且公开的操作；`ACTIVE` 使用现有 UsageRegistry 状态；`RETIRED` 仅用于既有 fallback。Admission 判断 F 是否合法，lifecycle 判断搜索时暴露哪些合法 F，两者独立。

本基线没有 low-usage retire、top-k 或自动等价检测。只要没有正式等价证据，singleton 保持 ACTIVE。因此即使宽度治理路径已接入，当前数据也可能真实地得到 `ACTIVE = FULL`。

## Clean 起点与实验隔离

规模实验每个 Goal 都从 clean strict Library 的持久快照分支运行，不修改该 clean 起点。基准快照：[`library_final.json`](artifacts/clean_strict_library/library_final.json)；Usage sidecar：[`usage_final.json`](artifacts/clean_strict_library/usage_final.json)。clean 状态为 26 个 Structure、24 个 executable F、2 个非 executable topology template、20/20 个复合 F exact-parent PASS、0 个 RETIRED、0 个非单例 equivalence class。

D01–D12 主链的新增结果写入 [`artifacts/search_space_baseline/libraries/`](artifacts/search_space_baseline/libraries/)，不是 clean 起点。12 个 Goal 全部形成并 admission 通过。主链末端为 38 个 Structure、36 个 executable F、ACTIVE 36、RETIRED 0，最大 canonical level 为 10。另行分支的 EXPANDED/FIRST_CLASS 对照不会并入 clean 起点。

## 统一 2×2 实验

入口：[`v94_search_space_baseline.py`](experiments/v94_search_space_baseline.py) · 结果：[`report.md`](artifacts/search_space_baseline/report.md)

| lifecycle | 历史组合处理 | 所回答的问题 |
|---|---|---|
| FULL + EXPANDED | 屏蔽目标及其历史链 F，从较低层合法操作重新组合 | 重新支付历史组合成本 |
| FULL + FIRST_CLASS | 目标 F 作为一级 operation 直接进入搜索 | 单独测深度复用 |
| ACTIVE + EXPANDED | ACTIVE 集合内重新组合 | 单独测 lifecycle 暴露宽度 |
| ACTIVE + FIRST_CLASS | ACTIVE 集合内直接复用高阶 F | 完整在线基线 |

每个 Goal 的四条路径使用相同的 Goal、训练实例、holdout、oracle、seed、verifier、candidate limit 和起始 Library snapshot。Composer 的排序、评分与组合规则不因模式改变；只切换 lifecycle view 和历史高阶 F mask。

### 已完成结果

| 量 | FULL + EXPANDED | FULL + FIRST_CLASS | 变化 |
|---|---:|---:|---:|
| candidate attempts | 16,570 | 144 | 减少 99.1% |
| binding combinations | 16,570 | 144 | 减少 99.1% |
| composition expansions | 37,300 | 0 | 减少 100% |
| intermediate materializations | 16,778 | 0 | 减少 100% |
| verification batches | 17,116 | 192 | 减少 98.9% |
| 新形成的 D 级搜索推导层数合计 | 78 | 0 | 历史组合不再重做 |

FIRST_CLASS 的 search derivation depth 为 0，表示当前搜索直接使用已持久化目标 F；其 runtime call depth 仍为 5–12，历史 formation DAG 也仍保留。它不表示程序的完整历史或运行调用深度为 0。

### 宽度结果

12 个 post-acquisition snapshot 上，FULL 与 ACTIVE 的 lifecycle executable exposure 累计都是 366；Goal-compatible public pool 累计都是 162。FIRST_CLASS 的 candidate attempts 都是 144，binding combinations 都是 144，fallback 触发 0 次。这里的 exposure 是跨 12 个 Goal 的逐快照计数总和，不是 366 个不同 F。

本次**没有观察到 production breadth reduction**：起点和主链都没有可信非单例等价类，也没有 RETIRED F；按现有规则所有 singleton 都 ACTIVE。类型/contract compatibility 过滤后的 Goal-compatible 数不能替代 lifecycle 缩宽证据。

### Shortcut 与语义

12/12 个 Goal 完成。主链有 4 个 `SHORTCUT_FOUND`，4 个都按相同 strict admission 通过并进入主链分支。Expanded 对照中还有 18 个不同的 target/stage shortcut 组合；FULL 与 ACTIVE 各观察一次，共 36 条 branch trace。branch 结果保留在各自实验快照，不混入 clean Library。

准入数字按 receipt（准入记录）统计：共 168 条记录只涉及 12 个唯一 Structure ID。主链 12 条记录对应 12 个持久新增 F。FULL+EXPANDED 与 ACTIVE+EXPANDED 各有 78 条 branch-local receipt：每个目标 Dk 都在自己的临时 snapshot 里依次重组 D01…Dk，所以每条路线的回执合计为 `1+2+…+12=78`。同一 F identity 会在后续目标的 branch 里再次形成并复验，但不会合并到主链 Library。因此持久 Library 是 +12，不是 +168；总数就是 `12 + 78 + 78 = 168`。

48 个 mode × Goal 结果全部通过 Python、JavaScript、oracle 和 cold reload 语义对照。FIRST_CLASS 目标 edge 审计全部 PASS；只绑定 public formal slots，选择前没有观察到 parents、recipe internals、internal bindings 或 topology。主链及各 branch 的 admission receipts 记录了 exact-parent、执行、样例、oracle、跨语言、cold instance 和 promotion 后公开检索检查。

## 机器产物

- [`report.md`](artifacts/search_space_baseline/report.md)：21 项问题的完整回答及逐 Goal 计数。
- [`summary.json`](artifacts/search_space_baseline/summary.json)：起点/终点哈希、库规模和三种效应汇总。
- [`paired_2x2_results.json`](artifacts/search_space_baseline/paired_2x2_results.json)：12 个 Goal × 4 模式的指标和审计摘要。
- [`traces/`](artifacts/search_space_baseline/traces/)：每 Goal 的 acquisition 与四条模式 trace，包含模式计数、formation stage、semantic 结果及 FIRST_CLASS public-slot 审计。
- [`admission_results.json`](artifacts/search_space_baseline/admission_results.json) 与 [`formation_audit.json`](artifacts/search_space_baseline/formation_audit.json)：按唯一 Structure ID 和来源汇总 receipt 数、exact-parent 与必要验证结果。
- `library_growth.json`、`lifecycle_trace.json`、`equivalence_trace.json`、`depth_metrics.json`、`breadth_metrics.json`、`combined_metrics.json`、`shortcut_trace.json`、`fallback_trace.json`：增长、等价、宽度与 fallback 的关键计数。
- `task_family.json` 只保留 Goal 定义、seed、样例/holdout 数量及 digest；样例与 holdout 可由固定种子和实验代码确定性重建。

候选级枚举事件、重复 promotion receipt 正文和 branch 中间搜索对象不持久化；逐 Goal trace 只保留模式计数、选中 F、shortcut、semantic 结果和 FIRST_CLASS public-slot 审计。

历史的 clean acquisition、FIRST_CLASS 小规模对照和 higher-order F 组合实验仍保留在本目录；本轮 `search_space_baseline` 是后续深度/宽度对照应继承的统一基线。

## 回归与复跑

项目 Python 环境下运行：

```powershell
python -m unittest tests.test_search_space_baseline tests.test_operation_pool_provider tests.test_public_operation_higher_order_composition -v
python experiments/v94_search_space_baseline.py
```

主实验每次从 clean strict Library 重新建立自己的分支。D01–D12 的 Goal、训练实例和 holdout 在搜索前冻结于 [`task_family.json`](artifacts/search_space_baseline/task_family.json)。完整报告：[`artifacts/search_space_baseline/report.md`](artifacts/search_space_baseline/report.md)。
