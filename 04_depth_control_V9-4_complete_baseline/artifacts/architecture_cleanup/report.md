# V9.4 架构清理与正式基线报告

## 范围与审计

本轮只整理 V9.4、统一生命周期候选暴露并重跑既有核心回归；没有新建版本、增加算法 Goal、运行 breadth/depth 规模实验、调整打分或排序、改变生命周期规则、训练或自治形成新 F。

删除前递归审计表见 [`audit_inventory.csv`](audit_inventory.csv)，审计记录覆盖 1,030 个文件、149,174,295 字节（不计审计表自身）。逐文件删除记录和 SHA-256 见 [`removed_files.json`](removed_files.json)。最终清理删除 938 个文件、53 个目录，共 147,642,605 字节。保留的 `artifacts/test_lean/` 是 Lean 测试按需写入的输出目录；其中旧生成文件已清理，`LeanVerifier` 会在测试运行时重新创建输出。

删除内容主要为：

- 32 个失败、重复或已被严格正式结果替代的旧实验脚本；
- 631 个历史运行产物、失败 trace、临时 KV、旧 Library copy、调试 dump 和旧 scale 结果；
- 265 个 Python、打包和 Lean 生成缓存；
- 旧 token-domain fixture、一个由旧 first-class 脚本支撑的重复回归测试、旧通用测试 runner，以及 7 个过期根目录产物。

清单同时保存每个删除文件的分类、字节数和 SHA-256。删除前依赖检查未发现正式保留代码对待删文件的导入或相对路径引用。唯一保留的输出目录引用是 `tests/common.py` 中的 `artifacts/test_lean`；该路径由 `LeanVerifier` 自动建立，不依赖已删除的生成文件。

## 当前正式入口

当前保留 3 个正式实验入口：

| 入口 | 验证内容 | 固定结果 |
|---|---|---|
| `experiments/v94_unseen_instance_library_growth.py` | 从干净 seed 对 7 个 Goal 进行未见实例搜索、step/root 形成、完整 admission、exact-parent 和冷重载 | 新增 14 个 F；严格入库后 Library 为 26 Structures |
| `experiments/v94_library_growth_validation.py` | U11–U13 的 EXPANDED 与 FIRST-CLASS F 同库配对搜索、public interface 和 oracle 对照 | attempts 38 → 14；bindings 38 → 14；总 depth 6 → 3 |
| `experiments/higher_order_composition.py` | 从已持久化的跨 Goal F 直接组合 higher-order F，并完成 exact-parent、准入和冷重载 | HG01、HG02 均通过；各自独立产出 F |

`data/library_seed.json` 是 acquisition 的唯一正式 12-Structure seed；`data/higher_order_goals.json` 是 higher-order 实验 Goal 输入。正式结果分别保存在 `artifacts/clean_strict_library/`、`artifacts/first_class_reuse/` 和 `artifacts/higher_order_composition/`。strict formation、exact-parent admission、跨语言执行和 cold reload 是 acquisition 与 higher-order 入口内的验证步骤，不另留一套重复脚本。

## Clean strict Library

| 项目 | 结果 |
|---|---:|
| seed 文件 SHA-256 | `5ea44a0ac094923fccdd4db12a5174aba878732ff7bccbf2e9bbfa127900dd3b` |
| final Library 文件 SHA-256 | `a418fb29bcf1db35c87f01e0a08842e0bcca93433d0305c3dfcc953e220ad007` |
| persistent hash | `f097307e1697f10794e483f1907ac0de92ddc8eba195937b1a87662fa1ee58b0` |
| Structure 总数 | 26 |
| executable F / Structure | 24：4 个 leaf、20 个复合 F |
| 非 executable topology template | 2 |
| 复合 executable exact-parent audit | 20/20 PASS；0 失败 |
| ACTIVE / RETIRED | 26 / 0 |
| equivalence class | 26；显式等价链接 0 |
| 最大 level | 4 |

所有 20 个 executable 复合 F 均由当前 strict formation/admission 流程产生；parents 与真实 executable F 引用一致，parent 在形成时已持久化。4 个 leaf 没有 F parents。2 个 topology template 单独计为非 executable 形成数据。未将旧的 audit-failed legacy executable F 搬入 clean Library。七个 Goal root 均在独立进程冷重载后通过 192 个 holdout oracle case。

## 搜索暴露架构

```text
Persistent Library
        ↓
OperationPoolProvider（操作池提供层）
        ↓
ACTIVE / FULL / RETIRED 生命周期视图
        ↓
public interface、domain 与契约兼容过滤
        ↓
GoalDrivenComposer / recipe search / DepthPlanner / Engine retrieval
        ↓
formation → ExternalCandidateKV / admission → Library
```

`GoalDrivenComposer.candidates()`、`retrieve_operations()`、`search_operations()` 和 recipe composition 通过 Provider 获得公开 operation 或 recipe basis。Composer 收到 public operation interface；不会读取 UsageRegistry 内部计数、provenance 或 executable graph 来决定候选暴露。acquisition 和 higher-order 入口向 Composer 注入与 Library 绑定的同一个 UsageRegistry/Provider。

`DepthPlanner` 的 ACTIVE 与 RETIRED 候选 ID 均由 Provider 按 lifecycle view 生成，Engine 的直接 retrieval 也在 Provider 过滤后才把结构交给检索器。领域适配器可以先给出本领域允许参与检索的 ID 范围；该范围不会绕开 Provider 的 ACTIVE/RETIRED 过滤。recipe 模板是组合 grammar 数据，不计作 executable public operations。

之前 Composer、recipe search、Engine retrieval 和 planner 各自维护候选或结构 ID 扫描路径；现在它们的正式候选暴露统一经过 Provider。Admission 只负责 validity、interface、execution、examples/oracle、跨语言、冷重载、exact parents 与 leakage 检查；它不登记等价类。当前没有自动等价检测或低使用量 retire 策略。

### Lifecycle 语义

- **ACTIVE**：UsageRegistry 当前标记为 ACTIVE 的持久 executable operations；也是默认在线 Composer/Planner 暴露。
- **FULL**：所有持久 executable public F，仅供显式对照或诊断。
- **RETIRED**：Registry 标记为 RETIRED 的 F；不删除、不改变身份、provenance、语义或 parent 资格。
- **Singleton**：没有显式 equivalence peer 的 F 仍为 ACTIVE。
- **Fallback**：保留原有 ACTIVE 失败后的 RETIRED fallback 条件和顺序；fallback 从同一个 Provider 请求 RETIRED 集合，没有新增触发条件。

Provider 回归验证 ACTIVE/FULL/RETIRED 三种视图、singleton 与显式等价代表。测试还验证 RETIRED F 能从 Library 加载、执行并冷重载。

## 回归与正式实验结果

本轮核心回归：**39 tests passed**，命令见 README。覆盖 OperationPoolProvider、ACTIVE/RETIRED exposure、FULL view、Composer 公共 interface、Engine/Planner fallback、admission 和 higher-order composition。

- **Acquisition：PASS。** 从 seed 对 G01、G03、G05、G07、U11、U12、U13 完成搜索、formation、admission 和 promotion；step 在 root formation 前持久化。G03 verifier 使用与 Goal 一致的严格递减 `<`。Python、JavaScript、oracle、独立冷进程结果一致。
- **Exact-parent：PASS。** 20/20 复合 executable F 的真实 parent 集合与实际 formation 调用一致，失败数为 0。
- **First-class F reuse：PASS — SEARCH REDUCTION。** 三个配对 Goal 的 oracle/output 相同。EXPANDED 与 FIRST-CLASS 总 candidate attempts 为 38 与 14，binding combinations 为 38 与 14，search depth 总和为 6 与 3；目标 F 在 topology/binding 组合展开前作为 public operation edge 被选择，只绑定公开 formal slots。
- **Higher-order composition：PASS。** HG01、HG02 均直接检索已入库 public F，再组合成普通新 F；exact parents、完整 admission、跨语言/oracle 和 128-case 独立进程冷重载均通过。
- **Cold reload：PASS。** Acquisition 七个 root 各通过 192 个 holdout case；higher-order 两个新 F 各通过 128 个独立冷进程 case。

本轮没有运行 breadth-control 或 depth-growth 规模实验。当前目录可作为这两类后续实验的干净 V9.4 基线；本报告不声称已经得到规模趋势结论。

## 对最终 17 项问题的回答

1. 删除 938 个文件和 53 个目录；精确删除字节数见 `removed_files.json`。
2. 主要删除失败/替代实验、旧 scale 与 fixture、临时 KV/Library/trace、重复报告、debug/cache/打包产物、旧 token Goal 回归和过期根目录文件。
3. 正式实验入口保留 3 个：unseen-instance acquisition、first-class F reuse、cross-Goal higher-order composition。
4. clean strict Library 有 26 个 Structure。
5. 其中 24 个是 executable F/Structure。
6. 是；20/20 复合 executable F exact-parent audit PASS，失败数 0。
7. ACTIVE 26，RETIRED 0。
8. 26 个 equivalence class，显式等价链接为 0。
9. acquisition Composer 候选暴露不再绕过 UsageRegistry/lifecycle；使用绑定同一 Library 的 Provider。领域适配器只限定其本领域 eligible ID 范围，最终 exposure 仍由 Provider 过滤。
10. 是；正式 Composer、recipe search、Planner 和 Engine operation/retrieval exposure 都经 OperationPoolProvider。
11. 是；ACTIVE、FULL、RETIRED 视图有回归覆盖，singleton 默认 ACTIVE。
12. 是；原有 ACTIVE 失败后 RETIRED fallback 顺序由 Provider 提供候选并有 Engine/Planner 回归覆盖。
13. 是；first-class F reuse 语义一致，并减少三组配对 Goal 的 attempts、bindings 和 depth。
14. 是；HG01、HG02 均通过 exact-parent、完整准入和冷重载。
15. 是；所有正式 root/new higher-order F 的规定冷重载用例通过。
16. 清理后正式目录只保留三个当前入口；旧 failed/obsolete/duplicate 实验脚本、生成缓存和结果不再留在正式树中。删除前依赖检查无保留代码导入或路径依赖。
17. 是；V9.4 已具备可继承的干净基线，但尚未执行 breadth/depth 规模实验。
