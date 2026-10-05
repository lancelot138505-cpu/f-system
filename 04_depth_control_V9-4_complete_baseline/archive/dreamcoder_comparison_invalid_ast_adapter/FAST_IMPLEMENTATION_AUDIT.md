# DreamCoder × F-system FAST 实现审计

审计对象：F-system V9.4 complete baseline 下的 DreamCoder List Processing FAST 对标实验  
审计方式：只读检查源代码、冻结配置、现有运行记录与产物。未修改代码，未启动或重跑任何实验。  
报告状态：完成；FAST rev4 中 DreamCoder 没有完成运行，因此本文不把它写成算法性能对比。

## 审计结论摘要

1. 实际 F-system 路径是“先做整项 F 检索；未命中后，用适配器自己的 typed lambda-calculus AST 产生式搜索，再把成功表达式形成 F”。它不是当前 V9.4 的 Goal → topology → typed slots → f/F → alpha 形成路径。
2. 这次只冻结了一个通用 AST 形成 schema：lambda、application、bound variable、semantic function reference。没有 map/fold/filter 等 Goal-level topology selection。
3. 冷启动每个 seed 都是 18/174 可见样例命中，且 18 个全部通过独立 3 个 holdout；剩余 156 个全以 SELECTOR_BUDGET_EXHAUSTED 结束，并都到达 1,800 次 expansion。
4. 累积训练的 admitted composite 数是 seed 17/29/43 分别为 8/11/7。所有记录的形成都通过九项 admission checks；现有记录没有显示形成候选被 verifier 拒绝。该 adapter 路径没有 KV promote 阶段。
5. 30 个 unseen 在 FIRST_CLASS 与 EXPANDED 下均为 0/30；每个 Goal 两个条件都到达 1,800 expansions。FIRST_CLASS 增加了 terminal 扫描，却没有让当前数据中的 composite 成为正确的 unseen 解。
6. 终端 AST 中出现 composite ID 并不等于复用成功。seed 17 的 24 条引用里都是同一个会在 car(empty) 处抛 IndexError 的 AST；seed 43 的 3 条引用得到错误的 holdout 输出。三 seed 都没有 verified direct reuse。
7. DreamCoder rev4 三个 seed 均记录为 NOT_RUN_AFTER_PRIOR_PLATFORM_ERROR。rev3 的三个进程在 CPU-only 配置下仍以 0xC0000005 / nvdxgdmal64.dll_unloaded 退出，日志停在 recognition 训练所需的 prior/Helmholtz 采样阶段。没有完成的 DreamCoder 指标，故不能据此比较两套系统的解题率。
8. 结果有效地说明“这个特定 AST adapter 在其 expansion 配额内的表现与失败位置”，不能据此评价 V9.4 原生 topology-first 核心。

### 证据约定

- 路径相对于 V9.4 仓库根目录 D:/code/pycode/f_system/04_depth_control_V9-4_complete_baseline。
- 代码引用给出仓库内相对路径及行号；数字以 artifacts/dreamcoder_list_comparison_fast 下 rev4 文件为准。
- 若没有实际 trace 或 target program 作为依据，则明确写 UNKNOWN_FROM_CURRENT_ARTIFACTS。源代码推导的说明会标作“由代码可确定”，不会伪装成运行时 trace。
- frozen task snapshot 在 FAST artifacts 与实际 runner 读取的 experiments/dreamcoder_list_comparison 目录中当前 SHA-256 完全相同。Protocol rev4 记录的 task_manifest、train_tasks、unseen_goal_tasks、holdout_instances、per_seed_curricula hashes 与文件当前 hashes 相同；例如 task_manifest 为 82bcfd8556d824259ab126d591cd1890aa13f81b0b80c4c5021bb49b3bc348bc。

---

# 第一部分：真实 benchmark 实现架构

## 1.1 入口与调用链

这里没有一个同时驱动两套系统的单进程 benchmark 函数。FAST 用独立启动器运行 DreamCoder；F-system 则从另一个薄包装器进入现存 adapter。

| 文件 | 函数/对象 | 实际职责 |
|---|---|---|
| experiments/dreamcoder_list_comparison_fast/run_parallel_dreamcoder.py | main、command_for | 启动三个 DreamCoder seed 进程，注入 FAST artifact 目录，记录进程状态。 |
| experiments/dreamcoder_list_comparison_fast/fast_runner.py | 模块入口 | 将 output root 指向 FAST 目录，然后调用已 pin 的 dreamcoder_runtime.main。 |
| experiments/dreamcoder_list_comparison/adapters/dreamcoder_runtime.py | main、run_official、_load_cli_and_modules | 复用 pinned DreamCoder CLI 与 task generator；校验 task snapshot；调用 vendor DreamCoder List Processing main。 |
| experiments/dreamcoder_list_comparison/vendor/dreamcoder/bin/list.py | 官方 CLI | 进入官方 List domain 命令行入口。 |
| experiments/dreamcoder_list_comparison/vendor/dreamcoder/dreamcoder/domains/list/main.py | main、explorationCompression | 创建官方 common grammar，做 task split，再进入 DreamCoder wake-sleep / compression 循环。 |
| experiments/dreamcoder_list_comparison_fast/run_fsystem_fast.py | main | 仅重定向 artifact dir、线程环境和状态记录，再调用现有 run_fsystem.main。 |
| experiments/dreamcoder_list_comparison/adapters/run_fsystem.py | main、solve_one、encode_task_result | 读 frozen tasks，执行冷启动与累积阶段，保存 library，逐个重载 library 跑 unseen。 |
| experiments/dreamcoder_list_comparison/adapters/list_domain.py | DreamCoderListDomain | 定义 primitive 语义、类型、AST state、action 产生式、evaluator、formation 与 admission。 |
| fsys/runtime.py | Engine.solve | 首先运行普通 Library retrieval；允许学习时未命中则交给 FormationEngine。 |
| fsys/formation.py | FormationEngine.learn | 调用 DepthPlanner，构造 candidate，验证后提交 Library。 |
| fsys/search.py | DepthPlanner._plan_stage | 通用优先队列搜索；调用 domain.initial_state/search_actions/step/is_complete。 |
| fsys/library.py | Library.commit/save/load | 只接受 passed verification 的结构；持久化和重载 JSON Library。 |

关键入口位置：FAST DreamCoder wrapper 为 fast_runner.py:L6-L18；F wrapper 为 run_fsystem_fast.py:L10-L24、L34-L68。F runner 设定数据目录和 artifacts 见 adapters/run_fsystem.py:L21-L34、L230-L236。Rev4 protocol 位于 artifacts/dreamcoder_list_comparison_fast/protocol_fast.json，hash 为 6516317018c950cc9b7f4b98467fd4f8fd0d181be9474b40889b065bd4a68639。

## 1.2 task、primitive、Goal、搜索和形成

1. **task 数据读取**：run_fsystem.main 从 experiments/dreamcoder_list_comparison 读取 task_manifest.json、train_tasks.json、unseen_goal_tasks.json、holdout_instances.json、per_seed_curricula.json（run_fsystem.py:L230-L236）。FAST wrapper 没有把 runner 改为从 FAST artifacts 读这些文件；它只改 ARTIFACT_DIR（run_fsystem_fast.py:L20-L24）。审计时 FAST 冻结副本与 source 目录副本的五个 snapshot 文件 SHA 完全相同。
2. **DreamCoder task → F Goal**：build_task_from_row/rows_to_problems 将 row 的 task_id、name、type、visible_examples、split、source_class 转成 ListProblem（list_domain.py:L1189-L1197、L1223-L1228）。不传 target program；search 的目标是 request type 加可见输入输出例子。
3. **primitive 定义**：PRIMITIVE_TYPES 定义函数类型；primitive_value 提供 Python 可执行语义（list_domain.py:L61-L171）。initial_grammar 将 22 个 semantic leaves 和 11 个 generic syntax slot leaves 写进初始 Library（L959-L989）。
4. **topology**：没有多 topology selector。formation 输出统一写成 operation_id=list.lambda_ast、topology.kind=typed_lambda_calculus、version=1（list_domain.py:L795-L829）。topology_manifest.json 记载的是 AST 产生式种类，不是 map/fold 等 Goal-level topology 菜单。
5. **整项 F 检索**：Engine.solve 首先拿 domain.retrieval_candidates 构成 Operation Pool 可见集合，再执行 retrieval；匹配到整个任务且 verifier 通过，结果是 FULL_HIT（runtime.py:L126-L179、L92-L117）。FIRST_CLASS 允许已 admission 的 composite 参与整项检索；EXPANDED 排除它们（list_domain.py:L935-L942）。
6. **F-system 搜索**：未命中且 allow_learning=True 时，Engine.solve 调用 FormationEngine.learn；learn 调 DepthPlanner.plan，后者调用 domain.initial_state/search_actions/step/is_complete（runtime.py:L180-L184；formation.py:L79-L83；search.py:L94-L132）。
7. **candidate 生成**：list_domain.search_actions 在当前第一个 hole 上产生 lambda、application、De Bruijn variable、typed semantic-function-reference actions（L645-L722）。除语法 slot 外，它扫描可见且通过 audit 的 Library structures，按 structure.identity 排序，并以类型 unify 剪枝。
8. **candidate 执行**：_eval_node 对 lambda、application、variable、function reference 求值；primitive 调 primitive_value，composite 递归解释其 kernel AST（list_domain.py:L551-L569）。
9. **可见样例检查**：is_complete 只在 AST 无 hole 后对可见例子求值；异常或任一输出不一致都返回 false（L613-L626）。失败搜索期间没有“已通过所有可见例子”的完整 candidate 被作为成功计划返回。
10. **形成与 verification**：FormationEngine.learn 获取 plan 后调用 domain.abstract_instance、construct、prepare_candidate，然后调用 verifier；失败会返回 FORMATION_REJECTED 或 VERIFY_FAIL，成功状态为 FORMED（formation.py:L66-L83）。11. **admission 和 Library**：ListVerifier 的 admission_evidence 检查 interface、domain_audit、executable、examples、randomized_oracle、cross_language、cold_instance、x_leakage、formation_parents 共九项（list_domain.py:L1155-L1174）。Library.commit 对 verified structure 做 commit（library.py:L22-L45）；没有单独 KV、PROMOTABLE 阶段或 PromotionEngine promotion 调用。这里的“admitted”就是通过 verifier 后提交到 in-memory Library。
12. **累积和 reload**：累积阶段一个 Engine 按 seed curriculum 顺序训练，再写 library.json 与 library.usage.json（run_fsystem.py:L292-L329）。每个 unseen Goal、每个 condition 都从同一保存文件新建 Library.load，并校验 persistent hash（L337-L353）；paired Goal 间不共享 in-memory learning，也不将测试 usage 写回保存文件。
13. **FIRST_CLASS / EXPANDED**：FIRST_CLASS 将 composites 包含在 retrieval 与 AST function-reference 集合；EXPANDED 在两处排除所有 parents 非空的 composite（list_domain.py:L935-L942、L697-L722）。它仍保留同一个 Library 文件、primitive set、AST syntax、DepthPlanner、budget 和 verifier。

## 1.3 当前实现流程图

~~~mermaid
flowchart TD
  A[DreamCoder task snapshot row] --> B[ListProblem: request type + visible examples]
  B --> C{Engine.solve: exact whole-F retrieval}
  C -->|passed match| D[FULL_HIT]
  C -->|miss, allow_learning| E[DepthPlanner]
  E --> F[typed AST actions: lambda / application / variable / F reference]
  F --> G[visible-example evaluator]
  G -->|complete exact expression| H[abstract_instance / construct list.lambda_ast]
  H --> I[Verifier and 9 admission checks]
  I -->|passed| J[Library.commit]
  I -->|failed| K[VERIFY_FAIL or FORMATION_REJECTED]
  E -->|1800 expansions exhausted| L[SELECTOR_BUDGET_EXHAUSTED]
  J --> M[library.json + usage sidecar]
  M --> N[per-Goal cold Library.load]
  N --> O[FIRST_CLASS includes composites / EXPANDED masks all composites]
~~~

流程图中的“topology”没有作为 Goal 到 AST 之间的 topology-selection 阶段出现；lambda/application/reference 是 AST 语法 action。

---

# 第二部分：DreamCoder DSL 到 F-system 的 primitive 映射

## 2.1 全部 22 个 semantic primitives

primitive_mapping.json 给出官方 common DSL 与 F semantic leaf 的同名映射：official primitive count=22、F semantic leaf count=22、按名称集合相同；22/22 个 artifact spot checks 通过。每个 primitive 实际只有一个代表性 spot check，这不是对所有输入的形式化等价证明。

| DreamCoder primitive | 类型 | 操作语义 | F 对象 | 对象类别 | 实现位置 |
|---|---|---|---|---|---|
| map | (t0 -> t1) -> list(t0) -> list(t1) | 对 list 每项应用函数，保留列表长度。 | f_list_map | semantic leaf | list_domain.py:L61-L87、L119-L125 |
| unfold | t0 -> (t0 -> bool) -> (t0 -> t1) -> (t0 -> t0) -> list(t1) | 从 seed 开始，predicate 为 true 时停止；每步 emit 并 advance。adapter 最多执行 50 步。 | f_list_unfold | semantic leaf | list_domain.py:L99-L111、L119-L125 |
| range | int -> list(int) | 生成 [0,n)；n<=0 返回空表，adapter 对过大 n 作限制。 | f_list_range | semantic leaf | list_domain.py:L113-L117、L119-L125 |
| index | int -> list(t0) -> t0 | 取指定索引元素。 | f_list_index | semantic leaf | list_domain.py:L119-L125 |
| fold | list(t0) -> t1 -> (t0 -> t1 -> t1) -> t1 | 以初始 accumulator 折叠列表，回调参数为元素与 accumulator。 | f_list_fold | semantic leaf | list_domain.py:L167-L171、L119-L125 |
| length | list(t0) -> int | 列表长度。 | f_list_length | semantic leaf | list_domain.py:L119-L125 |
| if | bool -> t0 -> t0 -> t0 | 根据 bool 选择同类型分支值。它是普通语义函数，不是 AST conditional 节点。 | f_list_if | semantic leaf | list_domain.py:L119-L125 |
| + | int -> int -> int | 整数加法。 | f_list_plus | semantic leaf | list_domain.py:L119-L125 |
| - | int -> int -> int | 整数减法。 | f_list_minus | semantic leaf | list_domain.py:L119-L125 |
| empty | list(t0) | 空列表常量。 | f_list_empty | semantic leaf | list_domain.py:L119-L125 |
| cons | t0 -> list(t0) -> list(t0) | 前置一个元素。 | f_list_cons | semantic leaf | list_domain.py:L119-L125 |
| car | list(t0) -> t0 | 取首元素；空列表求值会报错。 | f_list_car | semantic leaf | list_domain.py:L119-L125 |
| cdr | list(t0) -> list(t0) | 去掉首元素。 | f_list_cdr | semantic leaf | list_domain.py:L119-L125 |
| empty? | list(t0) -> bool | 测试空列表。 | f_list_empty_test | semantic leaf | list_domain.py:L119-L125 |
| 0 | int | 整数零常量。 | f_list_const_0 | semantic leaf | list_domain.py:L119-L125 |
| 1 | int | 整数一常量。 | f_list_const_1 | semantic leaf | list_domain.py:L119-L125 |
| * | int -> int -> int | 整数乘法。 | f_list_multiply | semantic leaf | list_domain.py:L119-L125 |
| mod | int -> int -> int | 整数取模。 | f_list_mod | semantic leaf | list_domain.py:L119-L125 |
| gt? | int -> int -> bool | 整数大于比较。 | f_list_greater_than | semantic leaf | list_domain.py:L119-L125 |
| eq? | int -> int -> bool | 整数相等比较。 | f_list_equal | semantic leaf | list_domain.py:L119-L125 |
| is-prime | int -> bool | 素数测试；adapter 用其有限 prime lookup 表。 | f_list_is_prime | semantic leaf | list_domain.py:L119-L125 |
| is-square | int -> bool | 平方数测试。 | f_list_is_square | semantic leaf | list_domain.py:L119-L125 |

映射问题逐项结论：

- **数量**：两边都声明 22 个 semantic primitives；F 初始 Library 另外有 11 个 grammar.production_slot 结构，但其 metadata.semantic_leaf=false，只是非执行语法 marker，不算 semantic primitive（list_domain.py:L977-L989）。
- **拆分、合并、额外语义 primitive**：映射表中无一对多拆分、无多对一复用、无额外 F semantic leaf；按名称一对一。
- **缺少等价对象**：primitive_mapping artifact 的 22 个单点测试都相符，但这不足以证明任意输入下全域等价。代码级限制包括 adapter unfold 步数上限、range 范围限制及 is-prime 的有限查表。Pinned DreamCoder source 的 unfold 另有一个 free-variable typo，protocol 注明 bridge 绑定为预期 emit 参数并让两边使用这个修正。超出这些已测值/限制时，是否与官方无限制解释一致：UNKNOWN_FROM_CURRENT_ARTIFACTS。
- **constants**：只有 0 和 1 是独立 nullary leaves；其他数字要用表达式组合，不能由 task name 的参数直接变成运行时常量。
- **comparisons**：只有整数 gt? 和 eq?；它们是函数 leaf，不是比较 AST 节点。
- **list constructor/accessor**：empty、cons、car、cdr、empty? 各自映射独立 leaf。
- **higher-order**：map、fold、unfold 都是有函数类型的执行 primitive。它们不是 topology，也不是 executor opcode。adapter 用 curried Python callable 承载这些语义。Filter 没有专用 primitive。

---

# 第三部分：F-system 是否被强制成 DreamCoder-style AST enumeration

## 3.1 直接判定

**实际是混合流程，但 miss 路径的 candidate formation/search 明确是 adapter 实现的 typed AST/program enumeration。**

更精确地说：

- 首先由 V9.4 Engine 对已存 whole-F 做一次普通 retrieval；exact candidate 通过 verifier 即 FULL_HIT。
- miss 后，不是调用 topology selector。它进入 DreamCoderListDomain 的 lambda-calculus AST 搜索，再把通过检查的完整 AST 包装成一个 composite F。
- 因此，不能把它称作 V9.4 原生 topology-first search；也不能说整个程序未经改动地使用 DreamCoder 原生 enumerator。这里是 adapter 写出的 AST grammar/typed enumerator，外面借用了 F-system Engine、DepthPlanner、formation、verifier 和 Library 生命周期。

## 3.2 AST node、grammar、顺序和 pruning

- AST node kind 在序列化和 evaluator 中为 lambda、application、variable、function（list_domain.py:L401-L417、L551-L569）。
- 每次扩展当前第一个 hole：若目标类型可统一为 arrow，则给 lambda action；总能尝试通用 application；按 de Bruijn 环境中变量顺序尝试兼容 variable；随后扫描可见 semantic structures 并按 identity 排序（L645-L722）。- lambda 以新参数类型扩展 environment；variable 和 function-reference 通过 _unify / _freshen 做类型剪枝。候选 program 不是靠 Goal-specific behavior score 排序。
- 每个完整 AST 必须通过所有 12 个 visible examples；异常当作不匹配（L613-L626）。
- DepthPlanner 每个 popped state 增加 expansion，停止条件是 expansion_budget、max_depth 或 queue exhausted；每个 state 最多保留 search_width 个 action（search.py:L94-L132）。
- adapter 的 action_priority 返回 (depth,)（list_domain.py:L740-L742）。heap key 先比较 path depth，再以 serial number 保持同层插入顺序。这实际先展开浅层，属于 shallowest-first / breadth-by-action-count 的层次顺序；源代码注释却称“depth-first tie policy”。注释与实际 heap priority 语义不一致。
- depth 控制不是 DreamCoder 的 AST node size / PCFG log probability，也不是 F topology 的 slot-depth。此处限制是 DepthPlanner path action count ≤11、expansions≤1,800，每步 action width≤64。
- 由于每个状态的 action 上界在当前池中最多约 33 semantic terminals + 至多 11 个 bound vars + lambda/application =46，小于 search_width 64，当前设置下 width 通常不是主要截断项；这是由当前 pool 和 max depth 的代码上界推得，不是单独计数 artifact。
- candidate_probes 是每个 hole/扩展节点被扫描的 structures 计数，在类型 unify 之前累加；它不是完整 program 数、unique AST 数或 DreamCoder program checks。

### “为何采用”与“原生归属”

topology_manifest 的 design_basis 写明“same typed lambda-calculus programs used by DreamCoder's list grammar”。代码也确实实现了类似表达能力的 AST。**这说明 adapter 的设计目标是直接复刻表达式 AST 表示，不说明这个是 V9.4 原生搜索路线。**源文件 list_domain.py 文档与 topology_manifest 一起显示：AST 是此 benchmark adapter 的搜索表示；V9.4 core 则只提供通用 DomainAdapter hooks 和 DepthPlanner。

在这条 miss 路径里，topology.kind=typed_lambda_calculus 是最终 composite 的统一标签；lambda/application/variable/function-reference 实际成为 grammar productions。FIRST_CLASS composite 是额外 function-reference terminal，见 list_domain.py:L697-L722，不是某个 F-native formal slot 的检索结果。

---

# 第四部分：topology 的实际实现

## 4.1 当前存在的 topology/schema

| 当前对象 | input/output contract | 产生式/slots | child constraint | 用途 | Goal-specific |
|---|---|---|---|---|---|
| typed_lambda_calculus v1 | candidate 的 type scheme 从 AST inference/generalization 得出，外部 public interface 是 arg0 输入、result 输出。 | generic lambda、application、bound variable、semantic function reference。 | HM-style type instantiation/unification；变量必须在 De Bruijn lexical environment 中。 | 表示一个任意受类型约束的 lambda calculus program，并将 AST 保存为 F kernel。 | 否；topology_manifest 的 goal_specific_names_or_rules 是空列表。 |

没有独立的 top-level map、fold、filter、conditional、list-recursion topology。甚至表里的“formal slots”也是 topology_manifest 对语法构造的描述；在运行时由 search_actions 逐次产生 AST action，不存在先挑拓扑、再把 child F 填进业务 slot 的步骤。

## 4.2 具体名字的对象类别

| 构造 | 当前真实类别 | 说明 |
|---|---|---|
| map | primitive F / typed function terminal | 22 个 semantic leaves 之一；map 接受函数值与列表。不是 topology、AST node 或 executor opcode。 |
| fold | primitive F / typed function terminal | 22 个 leaf 之一；fold 调用一个函数值。没有专用 fold topology。 |
| filter | 无专用对象 | 可以尝试用现有 map/fold/if/cons 等构造表达，但当前 artifact 没有 filter composite，全部任务 closure 也未验证。 |
| conditional | if primitive leaf | AST 只有 lambda/application/variable/function，没有 conditional node。 |
| composition | application AST production | 以通用 curried application 表达；没有 port-pipeline topology 被使用。 |
| recursion | 没有任意递归 AST node/production | 只有固定的 unfold semantic leaf；一般递归构造没有由 adapter AST grammar 支持。 |

composition_definition 和 composition_definition_from_children 两个 DomainAdapter hook 明确抛出 NotImplementedError，说明 list AST 形成走 typed application AST 和 DepthPlanner，不走 port-pipeline composition（list_domain.py:L1179-L1183）。

## 4.3 两个示例的真实 AST 表示

**map(xs, lambda x: x + 1)**

在当前 adapter 的表达式 schema 中，大致 AST 是：

~~~text
lambda xs.
  application(
    application(function(map), lambda x.
      application(application(function(+), variable(x)), function(1))),
    variable(xs))
~~~

真实对象层面：

1. Goal 是 request list(int) -> list(int) 与 visible examples；没有单独 map Goal 或 map topology。
2. 一个 hole 由泛型 application/lambda productions 展开。
3. function references 为 f_list_map、f_list_plus、f_list_const_1；bound vars 用 De Bruijn index。
4. 当整个 AST 是 complete 并匹配例子后，_definition 推断类型，生成 topology.kind=typed_lambda_calculus 的 list.lambda_ast structure。
5. F public interface 给外层一个 arg0；candidate 绑定的是运行时 visible input batch。搜索 term 里 lambda 参数的 lexical binding 是 AST 环境变量，不是一个独立的 topology slot alpha。

这是根据实际产生式可写出的 AST schema，不是本次日志记录到的某条完整 candidate；候选 rank/trace 未保存，故此具体项没有运行时出现证据。

**fold(xs, 0, lambda x: lambda acc: x + acc)**

在相同 AST 语法中表示为：

~~~text
lambda xs.
  application(
    application(
      application(function(fold), variable(xs)),
      function(0)),
    lambda x. lambda acc.
      application(application(function(+), variable(x)), variable(acc)))
~~~

fold 是函数 terminal；两个 lambda 与多层 application 是普通 AST actions。没有 fold-specific topology、fold-slot retrieval 或 fold-child constraint 机制。上述表达同样是从语法可表达性导出的形式示意，不是 saved run trace。

---

# 第五部分：lambda、高阶与类型系统

DreamCoder 的 List grammar 使用 Abstraction、Application、Index 等表达式节点；F adapter 自己用 lambda/application/De Bruijn AST 重建这些构造。因此这里描述的是 adapter 的支持能力，不是已验证的 V9.4 native topology capability。

| construct | DreamCoder 支持 | 当前 F adapter | 实现方式 | 语义边界 |
|---|---:|---:|---|---|
| lambda abstraction | 是 | 是，EMULATED_BY_ADAPTER | typed lambda production 与 Python closure。 | 支持的表达式语义对应 lambda abstraction。 |
| bound variable | 是 | 是，EMULATED_BY_ADAPTER | De Bruijn index + 当前 lexical environment。 | 不支持未绑定变量。 |
| nested lambda | 是 | 是，EMULATED_BY_ADAPTER | 多次 lambda action，把 type/environment 推入 hole。 | depth/action 上限限制可搜到的嵌套量。 |
| higher-order argument | 是 | 是，EMULATED_BY_ADAPTER | function value 作为 application argument；map/fold/unfold leaf 接受 callable。 | primitive 语义有 adapter 的范围/步数边界。 |
| typed function value | 是 | 是，EMULATED_BY_ADAPTER | 函数类型用 arrow scheme；Python 运行时返回 curried callable。 | 只在支持的 AST/primitive evaluator 范围内。 |
| partial application | 是 | 是，EMULATED_BY_ADAPTER | 通用 application 逐个喂 curried 参数。 | 搜索仍受 11 action depth 与 1,800 expansions 限制。 |
| function composition | 是 | 是，EMULATED_BY_ADAPTER | 通用 lambda/application；无专用 composition production。 | 由应用语义组合，不是 port topology。 |
| recursion / unfold | 固定 unfold primitive | 部分；EMULATED_BY_ADAPTER | 有固定 unfold primitive，adapter loop 有 50-step cap。 | 任意递归 NOT_SUPPORTED；固定 unfold 边界是否覆盖全部任务 UNKNOWN。 |
| polymorphic type | 是 | 是，EMULATED_BY_ADAPTER | _freshen、_unify、_generalize 处理多态 type variables。 | 多态项搜索仍受同一预算限制。 |

**174 个 train task 是否全部在 adapter 可表达闭包内：NOT VERIFIED。**原因是本次 frozen task rows 记录 request、例子与任务名，没有为每个任务固定 target program；没有完整的 target construct/closure audit。22 primitive mapping spot checks 不能替代 204 个任务的闭包证明，也不能证明在 1,800 expansions 内可搜到。

---

# 第六部分：cold 18/174 与失败分类

## 6.1 顶层结果状态（每个 seed 完全一致）

| final category | 每 seed 数量 | 证据与含义 ||---|---:|---|
| solved | 18 | 11 FORMED + 7 FULL_HIT；18/18 都通过 3 个 post-hoc holdout。 |
| timeout / budget exhausted | 156 | 状态为 SELECTOR_BUDGET_EXHAUSTED；这不是时间 timeout，而是 DepthPlanner 的 expansion 配额耗尽。 |
| no candidate generated | 0 个显式 final status | action/candidate probe 确实发生；未找到完整正确解的搜索分支数无法从 raw 结果精确统计。 |
| no compatible topology | 0 / 不适用 | 没有 topology selection 阶段，所以此类无法发生为一个独立选择结果。 |
| type mismatch | 0 个显式 final status | 类型不合的 action 在 _unify 被局部剪枝；raw 未记录所有被类型剪枝的数量。 |
| unsupported construct | 0 个显式 final status | 没有单独 unsupported 状态码；目标所需 construct 是否超出闭包 UNKNOWN_FROM_CURRENT_ARTIFACTS。 |
| candidate generated but verifier failed | 0 个 cold final rows | cold 失败在预算状态；只有成功形成的候选进入 verifier 并全通过。 |
| selector exhausted | 156 | 与 budget exhausted 是同一批记录，不能重复相加。 |
| runtime error | 0 个 top-level cold status | 某些失败行的 post-hoc partial-candidate holdout prediction 可报 TypeError，但不是成功搜索候选上的 verifier failure。 |
| other | 0 | 没有其它顶层终止状态。 |

每个 seed 的 raw cold status 都是 156 SELECTOR_BUDGET_EXHAUSTED、11 FORMED、7 FULL_HIT；每 seed 的 strict heldout solved=18。三组数字由 artifacts/dreamcoder_list_comparison_fast/fsystem_raw_results.json 及 summary_fast.json 交叉确认。

## 6.2 成功与失败结构

- cold 的 7 个 FULL_HIT 是普通整项 F retrieval，在 DepthPlanner AST 搜索前返回。
- 另外 11 个 FORMED 是通过当前 typed AST 搜索形成并通过 admission 的表达式。
- 失败集没有来自 topology selector、slot contract filter、alpha binding 或 verifier 的最终拒绝记录。156 条全在 selector/search 层 budget exhausted。
- cold 每个 seed 的 search expansions 合计、probe 与 wall 汇总大体是：4,486,526 candidate_probes、1,688,699 legal_actions、约 226.9–230.2 秒累计 planner wall。candidate_probes 不代表这么多 complete programs。
- solved Goal 的 target program construct 分类无法从文件得出。结果中可见若干简单 first-order/macros 与 primitive exact hits；这不支持“成功只靠 first-order primitive”的普遍判断。map/fold/lambda/higher-order target-wise 成功率：UNKNOWN_FROM_CURRENT_ARTIFACTS。实际 admitted composite 里出现 map 与 lambda；没有一个已保存 composite 的 AST 使用 fold，但这不是 target-wise fold success rate。

## 6.3 五个代表性失败 Goal 的可追溯摘要

不能据现有 artifacts 给 174 个 task 的 target AST 排序，因此无法客观证明哪五个“最简单”。以下挑选五个 task label 直观、且 trace 可查的例子作为代表；“最简单”只能是任务名层面的直觉，不是 target-program 复杂度结论。seed 43 的 cold trace如下：

| task_id | benchmark task name / request | final status | expansions / probes / legal actions | saved final depth |
|---|---|---|---|---:|
| 2e010b8895e3b4502550 | take-k with k=3, list(int) -> list(int) | SELECTOR_BUDGET_EXHAUSTED | 1800 / 28204 / 10595 | 6 |
| ee06ee1c80f39f7a260a | drop-k with k=3, list(int) -> list(int) | SELECTOR_BUDGET_EXHAUSTED | 1800 / 28204 / 10595 | 6 |
| f36e327018f97811d653 | append-k with k=0, list(int) -> list(int) | SELECTOR_BUDGET_EXHAUSTED | 1800 / 28204 / 10595 | 6 |
| 6df01790f64d4eddceb1 | add-k with k=3, list(int) -> list(int) | SELECTOR_BUDGET_EXHAUSTED | 1800 / 28204 / 10595 | 6 |
| c5131f9a6646551ce6ea | index-k with k=3, list(int) -> int | SELECTOR_BUDGET_EXHAUSTED | 1800 / 24794 / 8478 | 6 |

对这五个 Goal 都可以确认：无 topology 选择；冷库可见 22 个 semantic primitive 和 11 个语法 marker；同一个 1,800 expansion cap 被用尽；最终没有 recorded complete candidate / verification result。task name 所暗示的参考程序、候选的具体生成先后、正确目标 AST 的 rank：UNKNOWN_FROM_CURRENT_ARTIFACTS。search_depth 是 row 中由当前 state 记录的 action_count，不等于对所有 explored/frontier states 的真实最大深度；不能把 6 解释为搜索只到深度 6 的完整统计。

---

# 第七部分：累积形成的 composite F

## 7.1 admission、数量与 parent 解释

累积训练每个 seed 都有 174 个 task rows。得到的 training statuses 和新增 composites：

- seed 17：18 个累积训练 solved，8 个 newly formed/admitted composite；另有 10 个 FULL_HIT。
- seed 29：21 个 solved，11 个 newly formed/admitted composite；另有 10 个 FULL_HIT。
- seed 43：17 个 solved，7 个 newly formed/admitted composite；另有 10 个 FULL_HIT。

每个被记录的 formation_admission 九项均为 true，最后进入 persistent Library。形成状态的计数可明确写为：completed/formed=8/11/7，九项验证全通过=8/11/7，admitted=8/11/7；promotable=不适用，因为当前路径没有此生命周期状态或 KV staging / PromotionEngine，rejected=0 个已记录 admission rejection。未完成正确 plan 的候选尝试没有逐项写日志，因此“所有搜索中生成/拒绝的半成品候选总数”是 UNKNOWN_FROM_CURRENT_ARTIFACTS。

composite parents 中出现 grammar.production_slot.xx。这些 slot 是非语义语法 marker，但 formation plan 将它们与语义 function references 一起写入 parents/call_plan；所以此处 parents 表示 AST derivation/provenance ids，不等同于纯 semantic F ancestry。

下表列所有唯一 composite，并给出在哪个 seed 的 accumulated Library 中存在。use 数来自训练结束时 library.usage.json；它是在 passed verification 的路径上记录的节点计数，不应解释为“unseen task 成功复用次数”。

## 7.2 composite 清单

| F ID | Seed 与形成任务 | AST/语义 | 类型 | parents（实际结构 ID） | level | formation admission | 训练 usage |
|---|---|---|---|---|---:|---|---|
| F_206c1d47010ac75a9028 | 17/29/43: prepend-k with k=0 | cons 0：在任意 list 前加 0 | list(int) -> list(int) | grammar.production_slot.00, f_list_cons, f_list_const_0 | 1 | 九项全通过 | 17:0, 29:0, 43:0 |
| F_5a52c7736bac3ea5c2ea | 17/29/43: is-mod-k with k=1 | lambda x. empty?(empty)，忽略 x，恒为 true | a -> bool | grammar.production_slot.00, grammar.production_slot.01, f_list_empty_test, f_list_empty | 1 | 九项全通过 | 17:1, 29:1, 43:0 |
| F_68037c96b4655b60bd85 | 17/29: drop-k with k=2 | lambda xs. cdr(cdr xs) | list(a) -> list(a) | grammar.production_slot.00, .01, f_list_cdr, .03, .04, .05 | 1 | 九项全通过 | 17:0, 29:2 |
| F_8522fd789d3dee78e972 | 17/29/43: add-k with k=1 | map(lambda x. x + 1) | list(int) -> list(int) | grammar.production_slot.00, f_list_map, .02, f_list_plus, f_list_const_1 | 1 | 九项全通过 | 17:0, 29:0, 43:0 |
| F_a99d3e90300532309665 | 17/29/43: bool-identify-k with k=1 | map(lambda x. x == 1) | list(int) -> list(bool) | grammar.production_slot.00, f_list_map, .02, f_list_equal, f_list_const_1 | 1 | 九项全通过 | 17:0, 29:0, 43:0 |
| F_ae032006e0f616f4718c | 17/29/43: mult-k with k=0 | map(lambda x. 0)，输出全零列表 | list(a) -> list(int) | grammar.production_slot.00, f_list_map, .02, f_list_const_0 | 1 | 九项全通过 | 17:1, 29:1, 43:1 |
| F_ee695df324b51902f39c | 17: Caesar k=1,n=2; 29/43: Caesar k=3,n=2 | map(lambda x. x - 1) | list(int) -> list(int) | grammar.production_slot.00, f_list_map, .02, f_list_minus, f_list_const_1 | 1 | 九项全通过 | 17:2, 29:2, 43:2 |
| F_9110a33c4b3dc9fc1493 | 17/29: bool-identify-is-mod-k with k=1 | map(F_5a52...)；对每项返回 true | list(a) -> list(bool) | grammar.production_slot.00, f_list_map, F_5a52c7736bac3ea5c2ea | 2 | 九项全通过 | 17:0, 29:0 |
| F_0a059a88ba877d9bb13e | 29: index-k with k=3 | lambda xs. car(F_68037...(xs)) | list(a) -> a | grammar.production_slot.00, .01, f_list_car, .03, F_68037c96b4655b60bd85, .05 | 2 | 九项全通过 | 2 |
| F_64a84ca3954bedced586 | 29: index-k with k=4 | lambda xs. F_0a059...(cdr xs) | list(a) -> a | grammar.production_slot.00, .01, F_0a059a88ba877d9bb13e, .03, f_list_cdr, .05 | 3 | 九项全通过 | 0 || F_ab58b2399f43f724f724 | 29: index-k with k=5 | lambda xs. F_0a059...(F_680...(xs)) | list(a) -> a | grammar.production_slot.00, .01, F_0a059a88ba877d9bb13e, .03, F_68037c96b4655b60bd85, .05 | 3 | 九项全通过 | 0 |
| F_f6754da680ad67b5714e | 43: bool-identify-is-mod-k with k=1 | map(lambda x. empty?(empty))，结构上直接生成每项恒真函数 | list(a) -> list(bool) | grammar.production_slot.00, f_list_map, .02, .03, f_list_empty_test, f_list_empty | 1 | 九项全通过 | 0 |

注：为便于阅读，点号数字位置表示与同一行前缀相同的 grammar.production_slot.；真实 parents 按上一列顺序逐项展开。例如 F_680 的 parents 是 grammar.production_slot.00、grammar.production_slot.01、f_list_cdr、grammar.production_slot.03、grammar.production_slot.04、grammar.production_slot.05。formation source、type、parents、level 均可在 fsystem_raw_results.json 的 accumulated_runs[].formations 和该 seed 的 library.json 交叉确认。

这些 composite 是可执行、有 type scheme、带 public arg0 interface 的 AST-backed F；不能因此称它们为经过 Lean/定理 prover 证明的对象。它们是局部宏式复用，包括固定常数 map、两次 cdr、用既有 composite 组成 index；没有形成 filter、sum、max、count 等一般库能力。任一 task family 名称中的特定 k 被具体常量固定在该 F 中，不能将其描述成泛化的 add-k(k-variable) 一族。

不同 seed 总数不同，观察到的来源是各自 curriculum/history 下完成并 admission 的 expression 集合不同；目标 sequence 次序和每个 task 的搜寻状态会变化。现有记录没有重放其他 curriculum 的 counterfactual，因此“差异由 seed 中哪一个具体因素造成”的严格因果分解：UNKNOWN_FROM_CURRENT_ARTIFACTS。没有证据支持“许多候选先被 promotion 淘汰”的解释：此路径没有 promotion stage，且所有记录形成都通过 admission。

---

# 第八部分：FIRST_CLASS 暴露路径与兼容性

1. **reload 与 operation pool**：run_fsystem 对每个 unseen task/condition 重新 Library.load，再建 Engine（run_fsystem.py:L337-L353）。是，Engine 构造时实例化 OperationPoolProvider；ordinary retrieval 与 DepthPlanner 的 lifecycle/domain exposure 经 operation_pool.get_search_structures（runtime.py:L54-L72、L83-L91、L126-L135；search.py:L17-L35）。但 AST hole 的候选 F 并不是由 OperationPoolProvider 的 topology-slot retrieval 生成：list_domain.search_actions 仍直接扫描 context.library 并做类型 unify（L689-L722）。
2. **AST terminal 的实际来源**：在 list adapter 的 search_actions 中，函数 references 是直接扫描 context.library，再跳过 _slot_ids、过滤 search_scope、EXPANDED composite mask、audit_structure、identity 排序，然后 freshen type scheme 并 unify 到 hole.expected（list_domain.py:L689-L722）。因此 Operation Pool 参与框架 lifecycle 过滤，但“哪个 function F 作为当前 AST hole 的 terminal”是在 adapter 内部按 Library 扫描/类型匹配，不是 topology formal-slot API 查询。
3. **public projection fields**：operation kind、capability=list.lambda_calculus、execution_entry=Structure.execute、formal_slots=[arg0]、binding_schema(arg0/type)、input/output ports、protocol version；定义于 _public_metadata（list_domain.py:L774-L793）。
4. **Goal compatibility**：
   - whole-F direct retrieval：先 type compatibility，再在 visible examples 上 execution equality；composite exact 时建立 arg0 binding（L899-L929）。
   - AST terminal：将 F 的 polymorphic scheme freshen 后与当前 hole expected type unify（L707-L715）。此处没有完整 Goal semantics relevance contract。
5. **topology/slot compatibility**：没有 topology selector 或 map/fold-specific formal slots，所以没有这类检索。只有 AST hole expected type 的统一。
6. **FIRST_CLASS 信息使用**：
   - parents 被用来区分 composite / leaf、EXPANDED mask 和执行 composite kernel AST。
   - kernel AST 被 evaluator 使用；audit 也检查 AST reference 与 parents/call_plan 一致（L1011-L1021；L551-L568）。
   - metadata.call_plan / grammar_profile 可被 audit，但不会变成候选 relevance score。
   - provenance/formation recipe 不是 action rank 或 typed candidate scoring 输入。formation provenance 随 Library structure 保留，但 search_actions 依赖的是 AST、scheme、parents 判定、semantic structure audit。
7. **alpha**：AST 的 de Bruijn variable 是表达式 lexical environment，不是 F-system AlphaTensor。Whole composite retrieval 时，encode_binding/decode_alpha 将当前 dynamic visible input batch 作为 arg0 alpha binding；F 本身不存具体 task X（list_domain.py:L870-L897）。这一步不负责选 AST child。

---

# 第九部分：candidate 里出现 composite 但没有 successful reuse

结果 artifact 里三 seed 的 unseen candidate reference 数为 seed17=24、seed29=0、seed43=3；所有 FIRST_CLASS unseen solved=0，direct reuse=0。EXPANDED 不生成这些 reference。

## 9.1 seed 17：24 个引用，全部同一个坏 AST

24 条都是以下形式：

~~~text
lambda x.
  application(application(function(car), function(empty)),
              function(F_206c1d47010ac75a9028))
~~~

F_206c... 是“cons 0”的 composite。AST 在 eager application evaluation 下先求 car(empty)，产生 IndexError；不会走到 composite 参数的执行。24 条的 heldout 三个例子全部 IndexError；verification 字段为 passed=false、verifier=null，说明未进入外层 ListVerifier/admission；first_class_direct_reuse=false。

| task_id | task name |
|---|---|
| c79cf3d2b95ee7086af1 | add-k with k=5 |
| 915bbd94bdde8bc1c933 | append-k with k=1 |
| 7d39e4671f6d71698931 | caesar-cipher-k-modulo-n with k=2 and n=3 |
| bcb1b907ffacb9f942f8 | caesar-cipher-k-modulo-n with k=2 and n=5 |
| beae06b7d51a6e7dc9b9 | keep-mod-k with k=2 |
| 9d83abf284d364343431 | keep-mod-k with k=4 |
| 6a7f4bc943d6db1a313d | keep-mod-k with k=5 |
| bb21df6e5706485c3cd5 | mult-k with k=4 |
| c9e0988859ae88b67932 | pow-k with k=4 |
| da568a5036a5a607e6ed | remove-index-k with k=3 |
| a62c813a5b7e1660a5f6 | remove-mod-head |
| c71d25d3df618e988de7 | remove-mod-k with k=4 |
| b83b8ca1c59e83805893 | repeat-k with k=5 |
| 29c6204b189dd2bae890 | replace-all-with-index-k with k=5 |
| 45d5ded195144f06ed1c | rotate-k with k=2 |
| ed49a8f4ef88016c37b0 | slice-k-n with k=1 and n=3 |
| dd5a13e0ad5c8b5aa10a | slice-k-n with k=1 and n=5 |
| a4607c8565d8c07152b4 | slice-k-n with k=3 and n=2 |
| 81e93673674bdef10709 | slice-k-n with k=4 and n=2 |
| 24782ae48d30a7d08705 | slice-k-n with k=5 and n=1 |
| 40d3bc54a6fb9b6c5605 | slice-k-n with k=5 and n=5 |
| cd65d2a310afced3e830 | keep gt 0 |
| c455acc26c353b39c31a | remove gt 1 |
| 730a0415ed85cb2af860 | keep eq 3 |

The AST is a persisted terminal/partial search snapshot associated with a failed search row. It is not an accepted F reuse result. The stored artifact calls out the composite ID, but the bad expression does not successfully execute that child before raising.

## 9.2 seed 43：3 个错误输出的 composite references

| task_id / Goal | composite IDs | candidate’s semantic shape | post-hoc heldout | verifier/failure |
|---|---|---|---|---|
| 46b96c113500c46ddba5 — bool-identify-geq-k with k=5 | F_a99d... + F_5a52... | ignores an argument and returns the existing map(eq 1) function; it does not compare each element against 5. | 0/3 exact | candidate row is SELECTOR_BUDGET_EXHAUSTED; verifier null/not passed. |
| 031f7e55322044724382 — bool-identify-is-mod-k with k=5 | F_a99d... + F_5a52... | same AST/function shape, map(eq 1), not modulo 5. | 0/3 exact | SELECTOR_BUDGET_EXHAUSTED; verifier null/not passed. || 4505297c0efd50a99c62 — is-mod-k with k=4 | F_5a52... | candidate is constant true despite task examples containing false output. | 2/3 exact | SELECTOR_BUDGET_EXHAUSTED; verifier null/not passed. |

These candidate references are executable in the evaluator sufficiently to produce the logged seed43 holdout arrays, but they were not verified successful solutions; output did not match the full holdout and the search did not return a successful complete candidate.

## 9.3 What the evidence does and does not show

- Composite IDs in serialized AST mean a function-reference node appeared in a saved candidate snapshot. They do not establish correct full-expression reuse.
- There are zero FIRST_CLASS direct whole-F hits among 30 unseen tasks per seed, and zero verifier-approved unseen paths containing a composite.
- Some composite kernels are valid reusable functions on their own; the failure is that type compatibility is only type compatibility. The search can admit semantically irrelevant F terminals with the right type.
- It is not possible to determine whether a correct task-level F exists but was late in enumeration, whether none exists, or its exact rank. There is no target program, per-node score, candidate rank or complete-candidate trace. These are UNKNOWN_FROM_CURRENT_ARTIFACTS.
- For seed17's 24 references, the recorded child F_206 itself has valid admission history; it is the surrounding AST that fails before executing it. For seed43, output mismatch shows that the compositional use is semantically irrelevant/wrong for those goals. No evidence points to a failed alpha binding.

---

# 第十部分：FIRST_CLASS 为何 probe 更多、速度更慢

累积后 executable semantic function-reference pools 的数量如下。每个 Library 还存有 11 个 syntax marker，但 search_actions 将这些 marker 排除，不把它们当 function terminals。

| seed | persisted structures total | generic syntax markers | semantic leaves | composites | FIRST_CLASS semantic terminals | EXPANDED semantic terminals |
|---:|---:|---:|---:|---:|---:|---:|
| 17 | 41 | 11 | 22 | 8 | 30 | 22 |
| 29 | 44 | 11 | 22 | 11 | 33 | 22 |
| 43 | 40 | 11 | 22 | 7 | 29 | 22 |

所有进入当前 hole 的结构都先被扫描和记为 candidate_probes，再按 hole expected type 做 unify。每个 topology 的 compatible leaf/composite 数不适用，因为此 adapter 没有多个 topology；每个不同 hole 的实际 type-compatible F 数也没有被独立保存，为 UNKNOWN_FROM_CURRENT_ARTIFACTS。

| seed | FIRST_CLASS probes / 30 goals | EXPANDED probes / 30 goals | FIRST_CLASS legal_actions | EXPANDED legal_actions | FIRST_CLASS planner wall | EXPANDED planner wall |
|---:|---:|---:|---:|---:|---:|---:|
| 17 | 1,053,990 | 844,492 | 301,173 | 318,048 | 52.57 s | 44.80 s |
| 29 | 1,076,229 | 844,492 | 271,728 | 318,048 | 约 53 s | 约 45 s |
| 43 | 1,011,897 | 844,492 | 299,730 | 318,048 | 51.00 s | 45.09 s |

Paired per-task EXPANDED-minus-FIRST_CLASS median probe deltas are -6,596 / -6,908 / -5,117 by seed; corresponding median wall deltas are approximately -0.221 / -0.284 / -0.147 seconds. All conditions nevertheless consumed 54,000 total expansions per seed (30 × 1,800), and all solved zero.

原因可以直接由实现确认：

1. FIRST_CLASS 增加 7–11 个 audited composites 作为额外 function terminals。
2. terminals 同 leaf 按 identity 排序；没有 composite relevance score、bonus/penalty、top-k 或 Goal→F ranker（list_domain.py:L697-L722）。
3. 每个 hole 都扫描可见 structures；probe 在 type unify 前计数。新增 composite 增加扫描和结构检查。
4. composite 没有被改写成 planner 的 macro-edge 或一次拓扑 shortcut；它是一个额外 AST function-reference token。它可能缩短某个正确 program 的 AST 长度，但这次没有提供 rank/shortcut trace 证据，实际 FIRST_CLASS unseen 并未找到解。
5. 由此，当前 adapter 的 FIRST_CLASS 实际表现是“在 AST enumeration 池内暴露更多可选 terminals”，不是已证明能缩短历史搜索路径的 macro-edge。
6. 类别判断排序：ADAPTER REPRESENTATION PROBLEM → FIRST_CLASS EXPOSURE BLOWS UP SEARCH → BUDGET PROBLEM。正确 task-level F 是否不存在/排名太后无法判断；不能把它当成本次测量到的原因。

---

# 第十一部分：EXPANDED 的精确定义

- EXPANDED mask 的是所有 parents 非空的 composite structures，不只 mask 当前目标 F 或某个 subset（list_domain.py:L697-L699、L935-L939）。
- 被 mask 后，search_actions 不会依 parents 递归重建它；没有从 parent graph 合成同一 expression 的程序。primitive 仍然可用。
- EXPANDED 与 FIRST_CLASS 使用相同的 typed lambda calculus syntax/topology tag、22 semantic primitive、task row、initial Library snapshot、ArchConfig、DepthPlanner、Verifier 与 holdout 协议。
- FIRST_CLASS 与 EXPANDED 搜索中的 composite 终端集合不同；额外 terminals 按 identity 穿插在排序 pool，因而 queue 同层 tie 的后续候选顺序可以不同。故不可说每一个 action order byte-for-byte 相同。
- 各 paired unseen task 对 condition 独立加载同一 persisted library/usage state，library hashes 相同；测试时形成的 in-memory 结果不写回 persisted library（run_fsystem.py:L337-L353 及后续测试保存逻辑）。
- Protocol 没有记录其他系统性条件差异；本地唯一预期的实质切换是 composite exposure。正确 target AST rank 无记录，无法检查两个条件中正确解会排到哪里。

---

# 第十二部分：公平性（信息量而非语法外观）

## DreamCoder 收到的先验

- 官方 22-primitives common List DSL、相同 task snapshot 与 12 visible examples。
- typed lambda grammar、Abstraction/Application/Index。
- base uniform grammar；FAST 设定启用 learned recognition extractor。
- wake-sleep / Helmholtz sampling 与 compression/abstraction learning；protocol 设为 2 iterations、recognition 180 s/iteration、enum 1 s/task、test 1 s/task、CPU 1、CUDA off、topK 2、max frontier 10。
- FAST 官方 kernel 选择与配置存在，但实际 rev4 DreamCoder 3 seed 皆未运行，因先前 platform crash 而记为 NOT_RUN。
- pinned official source commit cb0e63f5c33cd2de360b791038b0f5272750270e。

## F-system adapter 收到的先验

- 同一按名称映射的 22 primitive leaf set。
- 通用 typed lambda/application/bound-variable/function-reference grammar，type instantiation/unification。
- DepthPlanner 的 priority 仅为 path depth；无 Goal-conditioned relevance score 或 recognition model。
- accumulated Library 包含通过 adapter admission 的 AST-backed composites；FIRST_CLASS 搜索把它们当 terminal，EXPANDED 全部隐藏。
- 每条 formation candidate 经本 adapter verifier 九项 checks，成功即进 Library；无 KV/PromotionEngine lifecycle stages。
- expansion_budget 1800、max_depth 11、search_width 64；没有 F-side 1 sec per Goal wall timeout。

## 公平性结论

AST expression representation 形状接近，不等于语义与搜索先验一样。DreamCoder 的 grammar probabilities、learned recognition proposals、wake-sleep frontiers、abstraction/compression loop，与 F adapter 的 unscored typed breadth-by-depth AST enumerator并不是相同的 prior information。反过来说，F adapter 则具有持久 F Library、whole-F retrieval、verification/admission 与 exposure conditions 等 DreamCoder kernel 不同的生命周期机制。

本次主要混淆点确实是把 same computational representation 当作 same prior semantic information。F adapter 未接收对应 DreamCoder recognizer/probabilistic grammar/learned proposal mechanism；它选择把 DreamCoder AST 构造作为唯一 generic syntax grammar，但没有实现上述搜索先验。两边还使用了无法一对一换算的搜索预算。加上 DreamCoder FAST 0/3 completed，所以本次不能作完成度对称的算法公平比较。

---

# 第十三部分：unseen 30 个 Goal 的来源与难度
- 官方生成任务在 identity-task removal 之后有 218 个；官方 list main 先按 train/test split，再从 test 移除 EASYLISTTASKS 中的 14 个任务（vendor/dreamcoder/domains/list/main.py:L343-L346、L381-L405；makeListTasks.py:L22-L35）。
- 剩余 204 个官方任务中，train=174，test/unseen=30；task_manifest 标记全是 OFFICIAL_TASK，无新增压力任务。
- 因此这 30 个是官方 split 的剩余 test set，不是 FAST adapter 按搜索失败或某个“初始 DSL 限时无法解决”算法筛出来的 hard set。源注释说排除项是 base DSL 可解且不感兴趣的简单项，但对留下的 30 个没有提供一个固定的 measured-hardness 判定。
- split 的官方函数 testTrainSplit(x, trainingFraction, seed=0) 以默认 seed=0 shuffle（utilities.py:L550-L568）；task generation seed 与 run algorithm seeds 另由 adapter/protocol 管理。因此不能只根据 manifest 的 random_seed=17 就说 split shuffle 使用了 17。
- F adapter 对这 30 个 Goal 的 full closure 没有审计：NOT VERIFIED。
- frozen task rows 保存 task label/type/examples，不保存 target program AST；所以按 target 的 primitive-only、shallow composition、map、fold、lambda、nested higher-order、unfold complexity 分类以及 target-wise F solve rate 都是 UNKNOWN_FROM_CURRENT_ARTIFACTS。

| target complexity | task count | F-system solve |
|---|---:|---:|
| primitive only | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |
| shallow composition | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |
| map | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |
| fold | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |
| lambda | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |
| nested higher-order | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |
| recursion/unfold | UNKNOWN_FROM_CURRENT_ARTIFACTS | UNKNOWN_FROM_CURRENT_ARTIFACTS |

---

# 第十四部分：预算与已有历史记录

## 当前 FAST rev4 的 F-system budget

DreamCoder protocol 的 enumeration_timeout_seconds_per_task=1，但 F-system 实际不以 1 秒作为停止条件。F 的 ArchConfig 是 slot_capacity=8、max_level=6、search_width=64、max_depth=11、expansion_budget=1800（fsys/config.py:L3-L12；raw JSON 同时记录该配置）。

- cold 的每个 seed 156 个失败都正好 expansion=1800。
- unseen 的 30 task × 两条件也全部正好 expansion=1800。
- 代表性 cold take-k k=3：1,800 expansions、28,204 candidate_probes、10,595 legal_actions，planner wall 大约 1.4–1.52 秒。
- unseen 每 seed FIRST_CLASS candidate_probes 合计约 1.01–1.08M；54,000 expansions，planner wall约51–53秒，约1.9万 probes/秒。EXPANDED 是844,492 probes、54,000 expansions、约45秒。
- 以上 probe throughput 是结构扫描数/记录的planner wall，不是 complete candidate programs/s；系统没有保存每个 Goal 找到第几个完整 program 的数据。
- 7 个 cold FULL_HIT 是 planner 前整项 retrieval；11 个形成成功项有 expansion 数但没有 complete-candidate ordinal/rank。具体解是第几个 candidate：UNKNOWN_FROM_CURRENT_ARTIFACTS。
- SELECTOR_BUDGET_EXHAUSTED 表示 expansion cap 结束且 priority queue 尚未给出成功 plan，不是 1 秒 wall timeout。程序搜索还有 max_depth 11、每个 popped state 最多 64 子 action 的限制。
- 156 个失败 row 的 recorded search_depth 分布（131 个为 6，25 个为 7）来自最后记录的 current_state action_count；它不是所有 popped states 的最大深度或最大 frontier depth。

## 之前已有记录（不重跑、不并入 FAST 聚合）

1. artifacts/dreamcoder_list_comparison/aborted_full_reference 是 DreamCoder full-default 的 incomplete partial reference：单个 seed 17，配置 enumeration/testing 10 s/task、10 iterations、recognition 3600 s/iteration；记录只有 1 iteration record、5 enumeration calls、2 test runs。partial wake 的 train hit counter 曾到 26 再到 45；两次 test 都是 0/30。状态 incomplete，不能解释为完整 solve count 或预算曲线。
2. 旧 development smoke 是 seed0 的部分启动记录：3 enumeration calls、0 iteration records、test 0；train hit counters 15 后 29。不是受控 10s/15s budget sweep。
3. Rev4 DreamCoder 0/3；rev1–rev3 在 summary 中排除；没有 F-system 10s/15s/1800-expansion 横向预算实验。

所以，已有部分记录不足以回答 solve count 是否随预算显著增加；不能把 partial train-hit counter 当作 held-out solve 或完整迭代结果。

---

# 第十五部分：DreamCoder native crash

## 可确认信息

- 固定实现：pinned official repo https://github.com/ellisk42/ec，commit cb0e63f5c33cd2de360b791038b0f5272750270e。
- protocol rev4 为 2 iterations、1s enumeration/task、180s recognition/iteration、1s test/task、1 CPU、learned recognition、CUDA=false。PyPy 未安装，protocol 记载官方 PyPy compressor function 在 CPython 进程内执行；这是 runtime deviation，不是这次 native crash 的已证原因。
- archived rev3 三个 seed 均 Windows exit code 0xC0000005，faulting module nvdxgdmal64.dll_unloaded / Windows Application Error 1000。rev4 的每个 seed status 都是 NOT_RUN_AFTER_PRIOR_PLATFORM_ERROR，并明确没有 retry。summary_fast 因而 DreamCoder completed seeds=0/3。
- rev3 stdout 的 CUDA is available=false、using cuda=false，seed_status 命令行包含 --no-cuda；rev2 也失败，rev3 改成 CPU-only PyTorch generator seeding 后仍失败。故“启用 CUDA 导致”的说法不受证据支持。
- 日志停在 “Sampling 500 programs from the prior on 1 CPUs...” 阶段，附近上下文是 recognition model training setup 与 Helmholtz frontier sampling。官方调用链由 adapter run_official → list domain main/explorationCompression → DreamCoder ecIterator/wake-sleep/recognition training。log 只能定位到 recognition/Helmholtz prior sampling 阶段。
- runtime bridge 还注释写明：检查已注册 accelerators 可能在 CUDA disabled 时仍触及已安装 NVIDIA driver（dreamcoder_runtime.py:L788-L789）。这说明 CPU-only 不能证明完全未加载/调用 driver；但不提供 faulting instruction 的 stack trace。

| 问题 | 审计答案 |
|---|---|
| exact native call stack / faulting instruction | UNKNOWN_FROM_CURRENT_ARTIFACTS；归档没有可解析堆栈。 |
| 是否是 model construction、tensor allocation、CUDA init 或训练中的具体哪一条指令 | UNKNOWN_FROM_CURRENT_ARTIFACTS。日志仅能定位 prior/Helmholtz sampling 阶段；不应更精确推断。 |
| Python version | 当前记录运行环境为 Python 3.11.15。 |
| PyTorch / CUDA build | 当前记录为 PyTorch 2.11.0+cu126；protocol 明确运行 CUDA=false。 |
| crash 时 GPU driver version | UNKNOWN_FROM_CURRENT_ARTIFACTS。协议归档没有在失败时固定 driver version；现在主机上读取的版本不能当作崩溃时证据。 |
| 是否有 official Linux-only / unsupported Windows dependency | UNKNOWN_FROM_CURRENT_ARTIFACTS；当前 artifacts 没有证据表明官方要求 Linux-only。 |
| WSL/Linux 是否可避免 | UNKNOWN_FROM_CURRENT_ARTIFACTS；没有运行证据，不能猜。 |

**分类：PLATFORM / NATIVE RUNTIME FAILURE。** 这是 Windows native access violation 的日志分类，不是 DreamCoder 算法搜索结果，也不是 0 个 solve。

---

# 第十六部分：三条端到端 Goal trace

## Case A：cold 成功 — prepend-k with k=0

- 原始 task：task_id e47719d6b4aa69272409；task label prepend-k with k=0；request list(int) -> list(int)；12 visible examples、3 holdouts。
- expected semantics：task label 表示把 0 前置到 list。冻结 row 未保存独立 target AST；可见语义由 task name/IO 对照。目标 AST 严格证据：UNKNOWN_FROM_CURRENT_ARTIFACTS。
- F Goal：ListProblem(task_id,name,request,visible_examples)，由 build_task_from_row 建出；无 target program 参数。
- selected topology：没有 Goal-specific topology selection。唯一 formation tag 是 typed_lambda_calculus v1。
- eligible operations：cold L0 的22 semantic leaves；11 grammar slots在 AST 中是 syntax markers，search 会排除它们作为 executable function terminals。- candidate order：具体逐步 queue/candidate rank 没保存。该 task 的计划用了 29 expansions、550 structure probes、113 legal actions；candidate ordinal UNKNOWN_FROM_CURRENT_ARTIFACTS。
- 保存的成功 AST：application(function(cons), function(0))，类型推导为 list(int) -> list(int)。
- composite：形成 F_206c1d47010ac75a9028；parents=[grammar.production_slot.00, f_list_cons, f_list_const_0]；level=1。
- alpha/binding：表达式内部没有 task-specific 常量绑定。新 F public interface 是 arg0/list(int)，执行时输入由当前 task 提供；task X 不写入 kernel。
- execution / verification：visible examples 通过；admission 九项 interface/domain audit/executable/examples/randomized oracle/cross-language/cold instance/x leakage/formation parents 全 true；三个 holdout outputs 均 exact。
- 结果：FORMED。formation search wall约0.0212 s。此条是 adapter 中真实的成功形成，不是 DreamCoder 对标成功。

## Case B：cold 失败 — take-k with k=3

- 原始 task：task_id 2e010b8895e3b4502550；request list(int) -> list(int)；12 visible / 3 holdout。
- expected semantics：task label 表示取列表前 k 项；target AST 未保存在 frozen row，精确程序 UNKNOWN_FROM_CURRENT_ARTIFACTS。
- F Goal representation：同样是 ListProblem，输入/输出 IO examples。
- selected topology：没有 topology selection；进入通用 typed AST Planner。
- eligible operations：22 L0 semantic leaves；无 accumulated composites，因为这是 cold。
- candidate order：无每步 action/AST 序列；rank UNKNOWN_FROM_CURRENT_ARTIFACTS。
- search stats：1,800/1,800 expansions；28,204 candidate probes；10,595 legal actions；last recorded search_depth=6。代表 failure 没有形成完整正确 plan。
- alpha/binding：没有可保存的成功 F candidate，所以没有 F-level alpha binding。
- verifier：没有 candidate plan 返回，verification verifier=null、passed=false；不是 verifier rejection。
- 结束点：SELECTOR_BUDGET_EXHAUSTED。三条 post-hoc heldout prediction 对 partial result 报 TypeError；它不是 verifier 运行失败。
- 为什么正确 candidate 没被找到：证据只支持“在 1,800 expansions 内没有找到并返回正确 plan”。是否在下一节点、后续深度或某个错误 ranking 以后可找到，UNKNOWN_FROM_CURRENT_ARTIFACTS。

## Case C：累积后 terminal 引用 composite 但失败 — add-k with k=5

- 原始 task：task_id c79cf3d2b95ee7086af1；request list(int) -> list(int)；12 visible / 3 holdout；seed17 accumulated FIRST_CLASS。
- expected semantics：task label 指向每项加5；task row 不含 target AST，target 程序细节 UNKNOWN_FROM_CURRENT_ARTIFACTS。
- F Goal representation：同一 ListProblem。
- selected topology：无 topology selection；泛型 typed AST search。
- eligible operations：L0 的22 semantic leaves + seed17 accumulated Library 的8 composites；grammar markers仍排除，故 FIRST_CLASS semantic terminal pool=30；无 topology-specific compatible-count log。
- candidate rank/order：candidate_probes 是相应 Goal search 内扫描的 structure 计数；此条具体终端 AST 的 rank/first-20 predecessors 未保存，UNKNOWN_FROM_CURRENT_ARTIFACTS。
- saved failed AST：lambda x. application(application(car, empty), F_206c1d47010ac75a9028)。F_206 是真实 admitted 的 cons0 composite，但 outer function eagerly computes car(empty) first。
- alpha/binding：该 snapshot 没有一个通过 whole-composite retrieval 的 arg0 match；first_class_direct_reuse=false。引用只是 expression terminal。
- execution：三个 post-hoc holdout 均 IndexError；因此这个 AST 无法提供正确 list output，更没能执行完整 composite argument。
- verifier：passed=false、verifier=null；search row 仍为 SELECTOR_BUDGET_EXHAUSTED，没有交给 formation/admission verifier。
- 结果：composite 身份出现在失败 AST 中，不构成 verified reuse。

### 三条 trace 共通的数据缺口

raw result 保留成功 formation AST、部分失败 AST 与计数，但没有按 expansion 保存完整 action queue、候选 rank、前20个候选 AST。因而对于 Case B/C 的 exact candidate order 和“正确 AST 会排第几”必须用 UNKNOWN_FROM_CURRENT_ARTIFACTS，不能事后编造。

---

# 第十七部分：FINAL DIAGNOSIS

## Q1 — 是否采用 V9.4 原生 Goal → topology → typed slot → f/F → alpha → candidate F？

**NO。**

miss 路径直接使用适配器中的 typed lambda-calculus AST state/actions；未调用 topology selector、goal-conditioned topology inventory 或 typed topology-slot F retrieval。alpha 仅在 whole-F reuse 的 public arg0 binding path中，或最终 F 的普通执行接口中起作用。

## Q2 — 是否把 DreamCoder AST/program enumeration 移植给 F-system？

**YES。**

更准确地说：把 DreamCoder 风格的 Abstraction/Application/Index/Primitive 表达式结构移植成 adapter 自己的 typed AST enumerator；不是直接调用 DreamCoder 的 enumerator、PCFG ranking 或 recognition search。AST representation 被对齐，搜索机制没有对齐。

## Q3 — cold 18/174 的主要原因，按证据排序

1. **硬 expansion cap**：156 个 failed rows 每一个都以 1,800 expansions 结束；它是可以直接从 artifact 观察到的最靠近失败结果的原因。
2. **通用 AST enumeration 没有 Goal-relevance scoring**：action priority 只有 depth；adapter 没有 recognition prior/topology filter。源码事实成立，但与 budget 的独立因果贡献没有消融实验。
3. **目标表达闭包与 exact target AST 未核验**：candidate mapping 不是全任务 closure audit；目标程序不在 snapshot 中，因此有多少失败由 DSL expressivity mismatch、固定 unfold/range 边界或搜索顺序各自造成，UNKNOWN_FROM_CURRENT_ARTIFACTS。

目前证据不支持将 156 个失败归因于 verifier、alpha、admission 或一个已确认的 F-system core bug；它们都没有成为这些 cold rows 的最终失败点。

## Q4 — unseen 0/30 的主要原因，按证据排序

1. **每个 unseen search 都触顶**：FIRST_CLASS 与 EXPANDED 各 30/30 都达到 expansion_budget=1800。
2. **当前暴露方式不提供已验证的 useful reuse**：无 composite whole-F direct hit；seed17 的 24 条 composite AST 引用都是 IndexError 坏 partial；seed43 的3条是 holdout mismatch。
3. **正确目标 F/AST 是否存在、候选 rank 和 target closure 都没有 artifact 证据**：无法分辨 useful F 不在库里、在库里但未选中、或组合无法表达。

## Q5 — FIRST_CLASS 为什么更慢？

排序为：

1. **ADAPTER REPRESENTATION PROBLEM**：composite 被当作 AST function-reference terminals，而不是 topology slot macro-edge。
2. **FIRST_CLASS EXPOSURE BLOWS UP SEARCH WORK**：pool 从22增至29–33；跨 unseen 的 probes 增加，wall 增加，solve 数仍是0。
3. **BUDGET PROBLEM**：两条件每 Goal 都花满 1,800 expansion；但这是无解的近端 stopping condition，不是独立证明 composite rank 的原因。

不能据此分类为 USEFUL F NOT PRESENT 或 USEFUL F PRESENT BUT NOT RETRIEVED；缺少 target AST 和 rank。

## Q6 — 7–11 composites 是否是真实可复用语义？

**是可执行且已 admission 的 F semantics；不是形式化定理证明，也没有本次 unseen 成功复用。**具体每 seed library membership 与语义见第七部分。训练 usage sidecar 有的 composite count >0；但 FIRST_CLASS unseen verified reuse 在每 seed都是0。函数包括 cons0、map(+1)、map(eq1)、map constant zero/true、cdr(cdr xs)、map(-1) 和若干复合 index。没有 filter/sum/max/count 等 general operation macro。

## Q7 — 不改 V9.4 core，只改 adapter 是否可做 topology-first？

**YES（从已有 DomainAdapter hooks 判断，理论上可以）。**

Engine/FormationEngine 把 state 初始化、搜索 actions、step、complete check、abstract_instance、prepare_candidate 留给 domain adapter。adapter 可将 search state 改为 topology choice 与 typed child slots，并将成功实例组装为可审计 F；无需改 Library / Verifier 的核心定义。当前 adapter 明确采用 AST search，且 composition hooks raise NotImplementedError，故这是可实现性判断，不是本次做过的实现。

## Q8 — 与 DreamCoder DSL 语义等价的最小通用先验

不引入 Goal-specific rules，至少要在数据和 contract 层对齐：

1. 同一 22 个 semantic primitive 与相同类型/可观察行为，包括 map/fold/unfold 函数值约定、constants、list constructors/accessors、comparators。
2. typed lambda、application、bound variables、polymorphic type instantiation 与 higher-order function arguments 的通用组合能力。3. 对 unfold/range/prime 等 adapter 边界要明确纳入双方的同一语义合同；目前有上限与有限查表。
4. 若比较的是搜索效率而非纯表达能力，grammar prior/recognition proposals 或 topology ranking 的信息量也要可比，或明确声明没有对齐。仅有相同 AST syntax 不足以对齐 DreamCoder 的 learned grammar 和 recognition prior。
5. 若 F-system 采取 topology-first，topologies 应是与 DSL operation 语义一致的通用 schemas（例如 map 的 element-function/list contract、fold 的 list/accumulator/combiner contract、unfold 的 seed/predicate/emit/advance contract），不能把 train/unseen task name、具体 k 或具体示例编码成 Goal-specific topology。

## Q9 — 是否足以评价 F-system 核心架构？

**INVALID_FOR_ARCHITECTURAL_COMPARISON。**

因为搜索主路是 benchmark adapter 的 AST enumerator，不是原生 topology-first；DreamCoder rev4 0/3 完成；目标 AST closure/rank未保存；预算单位无法等价转换。可以评价这个 adapter 的实际行为，不能把它推广成 V9.4 原生核心的架构结果。

## Q10 — 当前结果分类

### VALID_FINDING

- 三个 F seeds 的 frozen cold 结果均为 18/174；strict holdout exact 也是18/174。
- cold 失败的156条均到达1,800 expansion cap。
- 累积 admitted composite 数为8/11/7，所有已记录 composite 通过九项 admission checks；保存后 reload hash通过。
- unseen 两条件均0/30；所有 Goal 两条件均到达预算上限。
- FIRST_CLASS pools 确实更大、probe与wall更高；未出现 verified successful composite reuse。
- seed17和seed43的 candidate snapshots 真实引用了composite ID，但对应后验输出未通过。

### ADAPTER-SPECIFIC-FINDING

- 失败位置、FIRST_CLASS slowdown、action ordering、typed pruning 反映当前 list_domain AST adapter 与该 DepthPlanner configuration。
- composite-as-terminal 的表现不能推广到原生 topology-slot F retrieval。
- task example 对 current primitive evaluator 的 behavior checks 是当前 adapter 的测量结果。

### INVALID_COMPARISON

- 用 FAST rev4 的 18/174 对 DreamCoder 求 solve-rate 胜负：DreamCoder 0/3 actual runs。
- 依据相同 AST 表示宣称双方搜索先验相同。
- 依据当前 adapter 18/174 或0/30结果评价整个 V9.4 核心 architecture。
- 将 rev3 native crash 写成 DreamCoder algorithm failure 或零解。

### UNRESOLVED

- 每个未解 task 的 canonical target AST、所有 train/unseen Goal 的表达闭包与 construct complexity。
- correct F 是否已存在、AST candidate rank、首20候选和真实 max explored depth。
- composite 的每类语义对 unseen task 的预期有效性。
- native crash 的 faulting instruction/stack、crash 时 driver version、Linux/WSL表现。
- full-default partial counters 能否在足够算完以后转成完整 train/test solve 数；现有材料不足。

---

# 第十八部分：文件与定位索引

- FAST protocol & hashes: artifacts/dreamcoder_list_comparison_fast/protocol_fast.json, protocol_fast.sha256
- FAST raw F rows: artifacts/dreamcoder_list_comparison_fast/fsystem_raw_results.json
- Aggregated report inputs: artifacts/dreamcoder_list_comparison_fast/summary_fast.json, task_level_metrics_fast.csv, paired_task_metrics_fast.csv
- Mapping: artifacts/dreamcoder_list_comparison_fast/primitive_mapping.json
- AST/topology schema: artifacts/dreamcoder_list_comparison_fast/topology_manifest.json
- Task split snapshot: artifacts/dreamcoder_list_comparison_fast/task_manifest.json, train_tasks.json, unseen_goal_tasks.json, holdout_instances.json, per_seed_curricula.json
- Library per seed: artifacts/dreamcoder_list_comparison_fast/fsystem/seed_17/library.json and library.usage.json; same pattern for seed_29 and seed_43
- DreamCoder rev4 status: artifacts/dreamcoder_list_comparison_fast/dreamcoder/seed_17/seed_status.json; same pattern for seed_29 and seed_43
- Prior native crash: artifacts/dreamcoder_list_comparison_fast/aborted_protocol_revision3/dreamcoder/seed_17/seed_status.json and dreamcoder_stdout.log; same pattern for seed_29 and seed_43
- F adapter: experiments/dreamcoder_list_comparison/adapters/run_fsystem.py and list_domain.py
- Generic V9.4 control: fsys/runtime.py, fsys/formation.py, fsys/search.py, fsys/library.py, fsys/config.py
- DreamCoder official List entry and split: experiments/dreamcoder_list_comparison/vendor/dreamcoder/dreamcoder/domains/list/main.py; utilities.py; makeListTasks.py