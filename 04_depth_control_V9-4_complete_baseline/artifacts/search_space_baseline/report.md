# V9.4 统一搜索空间治理基线

> 纵向：已 admission 的 F 作为一级 operation，避免重做其历史组合。
> 横向：OperationPoolProvider 按 FULL/ACTIVE/RETIRED 暴露持久操作；ACTIVE 仅使用现有显式等价类代表策略。

## 运行结论

**PASS — SEARCH REDUCTION (depth reuse); breadth result is reported separately**

本轮成功完成 `12` 个 Goal 的四视图比较；主 D 链连续完成 `12` 个 Goal，停点：`D12_COMPLETED`。
FIRST_CLASS 目标 edge 审计：`全部 PASS`；语义对照：`Python / JavaScript / oracle / cold reload 全部相等`。

## Clean V9.4 起始状态复审

Library 文件 SHA256：`a418fb29bcf1db35c87f01e0a08842e0bcca93433d0305c3dfcc953e220ad007`；持久 hash：`f097307e1697f10794e483f1907ac0de92ddc8eba195937b1a87662fa1ee58b0`。
UsageRegistry 文件 SHA256：`b3615b9bbc41cb29b89518d0deb42335bc9f456872134ba5a0d192e323c8f18e`；snapshot digest：`5f4de014ae6c56ac54e023a3ab207033518ca4ebfc2fa26033952b4992adf25a`。

| 指标 | clean 起始值 | 实验主链结束值 |
|---|---:|---:|
| L_all | 26 | 38 |
| L_exec | 24 | 36 |
| registry ACTIVE Structures | 26 | 38 |
| registry RETIRED Structures | 0 | 0 |
| ACTIVE executable F | 24 | 36 |
| RETIRED executable F | 0 | 0 |
| topology templates | 2 | 2 |
| equivalence classes / non-singleton | 26 / 0 | 38 / 0 |
| explicit equivalence links | 0 | 0 |
| max canonical level | 4 | 10 |
| composite exact-parent audit | 20/20 PASS; 0 FAIL | 32/32 PASS; 0 FAIL |

起始状态中的 2 个非 executable topology templates 不计入 executable exact-parent admission；全部 20 个其他复合 F 均 audit PASS。起始的 24 个 executable F 中，14 个有可供 Provider 暴露的 public operation interface；Goal `sequence<int> → int` 起始兼容池为 7。L_exec、lifecycle 数与 public/Goal-compatible 数分开记录。
本轮没有增加 equivalence link，没有加入 low-usage retirement，也没有引入 top-k；当前所有 equivalence 类均为 singleton，因此 ACTIVE 与 FULL 在 executable 暴露数量上相同。

## D01–D12 主链增长

| Goal | 状态 | F ID | level | 父项数 | CHAIN_QUALIFIED / SHORTCUT_FOUND | candidate attempts | admission |
|---|---|---|---:|---:|---|---:|---|
| D01 | ADMITTED | `F_0469fb712663a973ddfd` | 5 | 2 | CHAIN_QUALIFIED | 48 | PASS |
| D02 | ADMITTED | `F_4498b1a6b76423b16a06` | 6 | 2 | CHAIN_QUALIFIED | 62 | PASS |
| D03 | ADMITTED | `F_87a666b46ef0cfa2f32b` | 7 | 2 | CHAIN_QUALIFIED | 132 | PASS |
| D04 | ADMITTED | `F_1e7d52d4a8b8c16e9a21` | 8 | 2 | CHAIN_QUALIFIED | 180 | PASS |
| D05 | ADMITTED | `F_c38d01daa2e8e1d08cef` | 8 | 2 | SHORTCUT_FOUND | 62 | PASS |
| D06 | ADMITTED | `F_1d0325fdc92c61208f06` | 9 | 2 | CHAIN_QUALIFIED | 558 | PASS |
| D07 | ADMITTED | `F_18d2c0c7673525cc9d3d` | 10 | 2 | CHAIN_QUALIFIED | 186 | PASS |
| D08 | ADMITTED | `F_8f991df15e01e06dffae` | 6 | 2 | SHORTCUT_FOUND | 86 | PASS |
| D09 | ADMITTED | `F_d9415b58d9849bee0d4d` | 7 | 2 | SHORTCUT_FOUND | 430 | PASS |
| D10 | ADMITTED | `F_b7bf2fef39b93e5686c2` | 8 | 2 | CHAIN_QUALIFIED | 756 | PASS |
| D11 | ADMITTED | `F_d03839a9818e072066fa` | 9 | 2 | CHAIN_QUALIFIED | 916 | PASS |
| D12 | ADMITTED | `F_359adfc45bfb1819aae0` | 8 | 2 | SHORTCUT_FOUND | 604 | PASS |

成功连续深度为 `12` 个 Goal；起始 canonical max level `4`，实验主链结束 max level `10`。
主链发现 `4` 个 `SHORTCUT_FOUND`，4 个均通过相同 strict admission 并进入主链分支。另有 `36` 次 counterfactual branch shortcut 检测，来自 18 个 target/stage 组合分别在 FULL 与 ACTIVE EXPANDED 路线中的观测；它们只进入各自 branch snapshot，不并入 clean 基线 Library。
CHAIN_QUALIFIED / SHORTCUT_FOUND 只描述是否包含预期 child 语义；不会作为 admission 条件。候选的 exact parents、Python/JavaScript/oracle、cold reload、公开接口和 KV receipt 决定是否入库。

## 2×2 搜索视图

| Goal | FULL+EXPANDED attempts | FULL+FIRST_CLASS attempts | ACTIVE+EXPANDED attempts | ACTIVE+FIRST_CLASS attempts | FULL+EXPANDED depth | ACTIVE+FIRST_CLASS depth | semantic equal |
|---|---:|---:|---:|---:|---:|---:|---|
| D01 | 48 | 8 | 48 | 8 | 1 | 0 | PASS |
| D02 | 110 | 9 | 110 | 9 | 2 | 0 | PASS |
| D03 | 242 | 10 | 242 | 10 | 3 | 0 | PASS |
| D04 | 422 | 11 | 422 | 11 | 4 | 0 | PASS |
| D05 | 484 | 12 | 484 | 12 | 5 | 0 | PASS |
| D06 | 1042 | 13 | 1042 | 13 | 6 | 0 | PASS |
| D07 | 1228 | 14 | 1228 | 14 | 7 | 0 | PASS |
| D08 | 1314 | 10 | 1314 | 10 | 8 | 0 | PASS |
| D09 | 1744 | 12 | 1744 | 12 | 9 | 0 | PASS |
| D10 | 2500 | 14 | 2500 | 14 | 10 | 0 | PASS |
| D11 | 3416 | 17 | 3416 | 17 | 11 | 0 | PASS |
| D12 | 4020 | 14 | 4020 | 14 | 12 | 0 | PASS |

### A. Depth effect：FULL+EXPANDED → FULL+FIRST_CLASS

candidate attempts：16570 → 144，减少 16426 (99.1%)。
binding combinations：16570 → 144，减少 16426 (99.1%)。
composition expansions：37300 → 0，减少 37300 (100.0%)。
intermediate materializations：16778 → 0，减少 16778 (100.0%)。
verification batches：17116 → 192，减少 16924 (98.9%)。
route search derivation depth：78 → 0 newly formed D-level F nodes.
FIRST_CLASS 的 `search_derivation_depth=0` 表示本次搜索直接复用了已入库 Goal F、没有新形成 D-level F；它不表示目标 F 的历史形成树或 runtime 调用深度为 0。

### B. Breadth effect：FULL+FIRST_CLASS → ACTIVE+FIRST_CLASS

生命周期 executable 暴露总量：FULL `366`，ACTIVE `366`；减少 `0` (0.0%)。
Goal-compatible public pool 合计：FULL `162`，ACTIVE `162`。
candidate attempts：144 → 144，减少 0 (0.0%)；binding combinations：144 → 144，减少 0 (0.0%)。
本轮未观察到 production breadth reduction：clean baseline 与主链新增 F 没有可信非单例 equivalence class，故所有 F 均 ACTIVE、无 RETIRED 可供剔除；Goal-compatible 数也只是在类型/契约兼容过滤后计算，不能当作 lifecycle 缩宽。

### C. 联合效应：FULL+EXPANDED → ACTIVE+FIRST_CLASS

candidate attempts：16570 → 144，减少 16426 (99.1%)。
binding combinations：16570 → 144，减少 16426 (99.1%)。
composition expansions：37300 → 0，减少 37300 (100.0%)。
intermediate materializations：16778 → 0，减少 16778 (100.0%)。
verification batches：17116 → 192，减少 16924 (98.9%)。
wall-clock sum：FULL+EXPANDED `1069.709s`；ACTIVE+FIRST_CLASS `66.522s` (附加指标，非主判据)。

## 语义、Admission 与 fallback

四种视图的目标结果均与 frozen oracle 对应：`PASS`。本轮共 168 条 strict admission receipt，涉及 12 个唯一 Structure ID：主链首次持久新增 12 个 F，FULL+EXPANDED 与 ACTIVE+EXPANDED 的反事实路线各产生 78 条 branch-local receipt。每个目标 Dk 都在独立临时 snapshot 中依次重组 D01…Dk，因此每条 EXPANDED 路线有 `1+2+…+12=78` 条 receipt；这些临时 F 不合并到主链 Library。总数是 `12+78+78=168` 条记录，不是 168 个持久 F。168 条 receipt 的 exact-parent 检查全部 PASS。
ACTIVE retired fallback：起始 RETIRED executable F `0`；本轮触发 `0` 次。fallback 工作已并入对应模式 `metrics`；当前无 retired 候选可触发。

## 最终 21 项回答

1. clean baseline L_all：`26`。
2. clean baseline L_exec：`24`。
3. ACTIVE executable F：`24`。
4. RETIRED executable F：`0`。
5. equivalence 非单例类：`0`。
6. clean 起始最大 canonical level：`4`；主实验最终：`10`。
7. 最长成功 Goal 深度：`12` (`D12`)。
8. 是否发现合法 shortcut：`是，主链 4 个；counterfactual branch 有 18 个不同 target/stage 组合（FULL 与 ACTIVE 各观察一次，共 36 条 trace）`。
9. shortcut 是否入库：主链 shortcut `4` 个全部 admission PASS；counterfactual shortcut 只进入各自 branch snapshot。
10. FIRST_CLASS 相对 FULL+EXPANDED 减少 attempts：`16426` (`99.1%`；跨 `12` 个 Goal 汇总)。
11. FULL 与 ACTIVE lifecycle executable exposure 相差：`0` / `366` (0.0%)；Goal-compatible public exposure 分开列在上文。
12. 当前 production lifecycle 是否真实缩宽：`否`。
13. 未缩宽原因：没有正式、可信的非单例 equivalence 证据；所有 singleton 按当前规则 ACTIVE。有限 holdout 输出未用于登记等价。
14. ACTIVE fallback 是否触发：`否`。
15. fallback 计入后总工作：fallback 为 0 次；各 ACTIVE route metrics 已按 primary + fallback 合并，故本轮总计不变。
16. FULL+EXPANDED 与 ACTIVE+FIRST_CLASS attempts 差：`16426` (99.1%)。
17. strict admission exact-parent：`168/168 receipt PASS`，覆盖 12 个唯一 Structure ID；主链持久新增 12 个 F。
18. 12 个唯一 F 的 Python = JavaScript = oracle（重复 branch receipt 复核也通过）：`PASS`。
19. 12 个唯一 F 的 cold reload（重复 branch receipt 复核也通过）：`PASS`。
20. 当前主要瓶颈：本轮 breadth 机制没有可治理的退休冗余；全扩展路线工作主要来自候选组合与重复 admission 验证。基于记录的 attempts、bindings、expansions、materializations 计数，不把 wall-clock 噪声当瓶颈证据。
21. 是否可作为后续唯一正式 baseline：`可作为本闭包内后续 depth/breadth 对照实验的统一正式基线；当前 breadth 只能报告 ACTIVE=FULL，尚不支持声称已经实现真实缩宽。`

## 产物

机器产物只保留汇总计数、seed/digest manifest、每模式指标、按 F ID/source 聚合的 admission/exact-parent 检查，以及 shortcut/fallback 和 FIRST_CLASS public-slot 关键 trace；候选级事件与重复临时对象不落盘。

## 复跑

```powershell
python experiments/v94_search_space_baseline.py
```
