# 第 3 关：定义 benchmark（Q3 · 考什么 / Q4 · 算不算过）

> **学习目标**：在**不碰算法代码、不碰判据实现**的前提下，设计出一套能真正区分"好算法"和"坏算法"的题，并且让它可长期比较。
> **前置**：完成[第 1 关](01-第1关-跑通第一次评测.md)、[第 2 关](02-第2关-接入你的算法.md)。
> **用时**：读 40 分钟 + 动手半天。**角色**：测试工程师主场；算法工程师也要读——你得知道别人拿什么尺子量你。

---

## 3.1 benchmark 不是一个数字，是一套约定

先把词说清楚。日常说"跑个 benchmark"，通常指"测一下看个数"。这里说的 benchmark 是更强的东西：

> **benchmark = 一组固定的 testcase + 一组固定的 metric + 一条明示的及格线 + 一份可比的历史基线**

四个"固定"，缺任何一个，测试就退化成"看个数"：

| 缺什么 | 后果 |
|---|---|
| testcase 不固定 | 这次跑了 3 个工况、下次跑了 5 个，两次结果**没有可比性**。改进无法被证明 |
| metric 不固定 | 上个月看 RMSE，这个月看中位数误差——**指标一换，历史全废** |
| 及格线不明示 | 每次评审都在争"这算不算过"。**判过标准应该在跑之前就写下来** |
| 没有基线 | 你只知道"这次过了"，不知道"比上次差了 15%"。**回归会静悄悄溜进主干** |

框架把这四件事分别落在了具体位置：

```
你的算法仓/scenarios/xxx.yaml          ← testcase 集合（dataset.config）+ 及格线（checker_config）
ATP 仓/plugins/<ns>/checker.py         ← metric 定义（第 4 关）
评测机 artifacts/baseline.json         ← 滚动历史基线
你的算法仓/scenario.yaml 的 scenarios: ← benchmark 分级与组合
```

本关管前面三个中你**能改的部分**：场景文件和基线策略。metric 的实现在第 4 关。

## 3.2 场景文件逐字段

一个 benchmark 的完整定义就是一份场景文件。真实例子（`cicd_test/scenarios/sim_basic.yaml`）：

```yaml
body: invp_sim                      # ① 本体资产
dataset:
  type: ctrl.invp.sim               # ② 用哪个世界
  config:                           # ③ 世界的配置 —— testcase 集合在这里
    testcases:
      small_push:  { theta0: 0.05 }
      medium_push: { theta0: 0.10, theta_dot0: 0.2 }
checker: ctrl.invp.upright          # ④ 用哪把尺子
checker_config:                     # ⑤ 尺子上的刻度 —— 及格线在这里
  settle_threshold: 0.02
# sensor_config: {...}              # ⑥ 传感器 topic 映射（可选，覆盖本体派生值）
# hyperparams: {...}                # ⑦ 这套题指定的算法超参（可选）
```

| 字段 | 必填 | 说明 |
|---|---|---|
| ① `body` | ✅ | **本体资产**：这台机器人是什么型号、传感器装在哪、外参多少。纯仿真台架用 `invp_sim`（无传感器）。现有资产见 `body/*.yaml`，新机型才需要新建 |
| ② `dataset.type` | ✅ | 数据源的注册键，带命名空间。**这一项决定了"怎么考"**：`ctrl.invp.sim` 是闭环仿真，`pipe.slam.rosbag` 是数据集回放，`pipe.slam.device` 是接真机 |
| ③ `dataset.config` | — | 传给数据源的配置。**闭环这里是 testcase 参数表，开环这里是数据集路径/帧数上限等**。字段全集查该插件的 `contract.md` §3 |
| ④ `checker` | — | 判据的注册键。**留空 = 只验证数据流跑得通，不打分**（接入初期很有用） |
| ⑤ `checker_config` | — | 判据阈值。**及格线就在这里表达** |
| ⑥ `sensor_config` | — | `{类型: {实例名: topic}}`，经 INIT 下发给算法。缺省由 body 派生 |
| ⑦ `hyperparams` | — | 这套题指定的算法超参（第 2 关 §2.7 的中间层） |

**注意 ② 和 ④ 的分工**：`dataset.type` 决定"世界怎么产生工况"，`checker` 决定"怎么判分"。它们是**解耦**的——同一个仿真世界可以配不同判据（比如一个判存活、一个判能耗），同一个判据也可以用在仿真和真机数据上。这个解耦是三源透明的另一半。

## 3.3 设计 testcase：怎么选工况

这是测试工程师最核心的手艺。堆一百个随机工况没用——**要用尽量少的工况，覆盖尽量多的失败模式**。

推荐一个四层结构（以倒立摆为例，但方法论通用）：

| 层 | 目的 | 期望结果 | 倒立摆的例子 | 跑的频率 |
|---|---|---|---|---|
| **① 名义工况** | 回归哨兵。它红了 = 一定出事了 | **必须过** | `theta0: 0.05`（小扰动） | 每次 PR |
| **② 边界工况** | 探测能力上限，量化余量 | 过，但余量小 | `theta0: 0.18`（接近 0.2095 的失败线） | 主干 / 每日 |
| **③ 失效工况** | **验证判据不会误判**——已知算法做不到的事，它必须判红 | **必须不过** | `theta0: 0.18, theta_dot0: 0.8` 且低增益 | 主干 / 每日 |
| **④ 参数扫描** | 找工作区间、给调参提供依据 | 部分过部分不过 | 同一工况 × `kp ∈ {8, 15, 30, 60}` | 按需 / 版本级 |

**第 ③ 层最容易被忽略，但它是整套测试的"体检"。** 一套只包含"必过工况"的 benchmark 有个致命隐患：判据可能根本没在工作（阈值写得太松、指标算错了、数据没接上），而你永远不会发现，因为它总是绿的。**必须有一个已知应该红的用例长期挂在那里，它一旦变绿，说明判据坏了。**

### ⚠ 造一个"必红"用例，比想象中难

现成例子：[`cicd_test_slam`](https://github.com/tz-Box/cicd_test_slam) 的 `degraded` 档，
往回声 SLAM 的位姿估计里注入误差，期望 `pipe.slam.ape` 判红。

**第一次的做法失败了**——沿 x 轴每帧累加 0.05 m 的漂移，20 帧累计 1.0 m，
听起来错得离谱，实测 `ate_rmse` 只有 **0.02**（阈值 0.2），**判据不红**。

原因在判据的算法里：`ape` 的 ATE 是 **Umeyama 相似变换对齐之后**才算的
（旋转 + 平移 + 尺度）——这是 SLAM 评测的标准做法，为的是让"整条轨迹整体偏了一点"
不被算成精度问题。而**沿单轴的线性漂移，几乎正好就落在这个变换能吸收的范围内**。

改成零均值高斯噪声后立刻见效（噪声是非刚性的，对齐消不掉）：

| 注入方式 | ate_rmse | rpe_rmse | 结果 |
|---|---|---|---|
| 单轴线性漂移 `drift=0.05` | 0.0200 | 0.0094 | ❌ 没红（判据对它不敏感） |
| 零均值噪声 `noise_std=0.2` | **0.2087** | **0.3376** | ✅ 双双超阈值 |

**普适教训**：**注入的误差必须是判据真正敏感的那一类。** 否则用例会安静地变绿，
而你以为判据在工作——这比没有这个用例更糟，因为它给了你虚假的安全感。

推论：设计失效用例前，先读一遍 checker 的实现，搞清楚它到底在量什么、对什么免疫。
（`ape` 对"整体刚性偏移"免疫是**有意为之**，不是缺陷。）

**顺带一条**：注入随机噪声时**必须播种**，否则每次跑指标都不同，基线回归立刻失去意义
（见[第 4 关](04-第4关-开发评测插件.md) §4.3 ⑧ 确定性纪律）。`cicd_test_slam` 按
`(noise_seed, testcase_id)` 播种，连跑两次指标逐位一致。

补充几条实践建议：

- **工况要能用一句话说清它在考什么**。`theta0: 0.05` → "小扰动下能否稳住"。如果一个 testcase 的目的说不清楚，它多半该被删掉或拆开。
- **testcase 命名用语义，不用编号**。`medium_push` 比 `tc_2` 强得多——报告里出现 `full:medium_push: passed=False`，你立刻知道是中等扰动挂了。
- **别追求数量**。倒立摆用 2 个工况就能挡住绝大多数回归。工况多了 CI 变慢，慢了就没人等，没人等就绕过。
- **开环的 testcase 划分**：一段连续的数据集通常按"采集片段"切 testcase（一条走廊、一次绕环）。切太碎会让每段都不足以体现累积漂移；不切则一段崩了整场归零、定位不到问题。

## 3.4 metric 与阈值：及格线画在哪

### 先看看现有判据给了什么

倒立摆判据（`ctrl.invp.upright`）产出五个指标，第 1 关见过：

| metric | 方向 | 类型 | 说明 |
|---|---|---|---|
| `survived` | 越大越好 | **结论型** | 撑满 max_steps 了吗（1/0） |
| `settle_error` | 越小越好 | **结论型** | 末段 10% 的平均 \|θ\|，稳态残余 |
| `survival_time` | 越大越好 | 诊断型 | 撑了几秒 |
| `max_abs_theta` | 越小越好 | 诊断型 | 全程最大摆角，离失败线的余量 |
| `upright_ratio` | 越大越好 | 诊断型 | 立着的帧占比 |

`passed = survived AND (settle_error ≤ settle_threshold)`。你能配的只有 `settle_threshold`。

**结论型 / 诊断型的区分要坚持**：`passed` 必须由**尽可能少**的指标决定（这里是 2 个），否则没人说得清一次红是为什么；`metrics` 则要**尽可能丰富**，让人看一眼就知道差在哪、差多少。

### 阈值怎么定

三种定法，按可靠性排序：

1. **从物理/任务约束推导**（最硬）。倒立摆的 `theta_limit = 0.2095 rad ≈ 12°` 不是拍脑袋——超过这个角，可用的力矩就拉不回来了，是系统的物理边界。同理：机械臂力控的接触力上限来自被抓物体的强度；导航的安全距离来自本体尺寸。**能从物理推导的阈值，一律从物理推导。**
2. **从现有算法的实测分布反推**（最常用）。跑 20 次基线算法，取 P95 再留一点余量。这叫"守住现状"，能挡回归，但挡不住"大家都不够好"。
3. **从产品指标反推**（最有业务意义，也最难）。"巡检机器人定位误差 > 0.3 m 就会撞到管道支架" → SLAM 的 ATE 阈值定 0.3 m。

**给个实践建议**：新接入的算法先用第 2 种定一条**能过的**线，把回归防线立起来；同时在 `description` 里写清楚"此阈值来源=当前实现实测，待按 XXX 收紧"。**没有依据的阈值比没有阈值更糟**，因为它会被当成"标准"。

### ⚠ 一个必须知道的机制细节：回归比较假设"指标越小越好"

框架做基线回归对比时，逻辑是这样的（见 `src/autotest/server/report.py`）：

```
passed 从 false → true                  ⇒ improved（优先判定）
passed 从 true → false                  ⇒ regressed（优先判定）
passed 没变，且所有指标差值 ≤ 0          ⇒ improved
passed 没变，且所有指标差值 ≥ 0          ⇒ worse
其余（有的变好有的变差）                 ⇒ same
```

**注意第 3、4 条：它把所有 metric 都当成"越小越好"。** 倒立摆的指标集混了方向（`survived` 越大越好、`settle_error` 越小越好），所以只要有涨有跌，回归判定就会退化成 `same`，只剩 `passed` 翻转还能被可靠捕捉。

**对你的影响**：
- 设计判据时（第 4 关），**尽量把指标统一成"误差型"（越小越好）**。`survival_time` 不如用 `time_to_failure_deficit = max_time - survival_time`；`upright_ratio` 不如用 `non_upright_ratio`。
- 用现有判据时，别指望 `improved` / `worse` 这两个计数在混方向的指标集上有精确意义——**以 `passed` 翻转和你自己看 metrics 数值为准**。

## 3.5 分级：一个仓要几套题

一次完整评测跑几分钟没问题，但 PR 上等三分钟就有人绕过。所以要分级。`cicd_test` 的做法：

```yaml
scenarios:
  - id: small_push
    description: 小扰动单点冒烟（PR 快速反馈）
    scenario: scenarios/small_push.yaml      # 1 个工况
  - id: full
    description: 完整评测（小 + 中扰动两用例）
    scenario: scenarios/sim_basic.yaml       # 2 个工况，含 small_push
```

| 级别 | 内容 | 触发时机 | 时长目标 |
|---|---|---|---|
| **smoke** | 名义工况 1–3 个 | 每次 PR / push | < 1 分钟 |
| **full** | 名义 + 边界 + 失效 | 主干合入 / 每日 | 分钟级 |
| **sweep** | 参数扫描、大数据集 | 手动 / 版本节点 | 不限 |

注意 `full` **包含** `small_push` 这个工况——有意重复。这样 smoke 绿了 full 红了，你能立刻定位到"是新增的工况挂了，不是老工况回归了"。

### 用清单项覆盖，一份场景文件生成多个 benchmark

清单项可以深合并覆盖场景文件的三个字段，于是**同一份场景文件配不同参数 = 不同的 benchmark 条目**：

```yaml
scenarios:
  - id: nominal
    description: 名义增益
    scenario: scenarios/sim_basic.yaml
  - id: low_gain                          # 同一份场景文件
    description: 低增益鲁棒性
    scenario: scenarios/sim_basic.yaml
    hyperparams: { kp: 12.0 }             #   ← 覆盖算法超参
  - id: strict
    description: 严苛稳态要求
    scenario: scenarios/sim_basic.yaml
    checker_config: { settle_threshold: 0.005 }   # ← 覆盖及格线
  - id: long_run
    description: 长时程
    scenario: scenarios/sim_basic.yaml
    dataset_config:                       #   ← 覆盖世界配置
      testcases: { small_push: { max_steps: 5000 } }
```

三个覆盖键的**深合并**语义（清单项 > 场景文件 > manifest 默认）见[附录 §3](07-附录-scenario-yaml-schema.md)。

**多场景执行语义**（第 1 关见过前缀，这里补全规则）：

- 一次评测跑多个场景 = **单个 job 内逐场景顺序执行**，每个场景**独立拉起你的算法进程**、独立会话，场景之间不共享算法状态。
- 跑多于一个场景时，`testcase_id` 带 `场景id:` 前缀（`full:medium_push`）；只跑一个场景时无前缀。
- 单个场景异常**不阻塞**后续场景（记一条 `场景id:<scenario>` 失败条目继续跑），CI 能拿到完整的结果矩阵。

## 3.6 基线与回归：让"变差"被发现

`passed` 只回答"过没过"。要回答"比上次差了吗"，需要基线。

评测机维护一份**滚动基线** `artifacts/baseline.json`，每次评测自动按 `testcase_id` 对齐逐指标对比：

```bash
python3 -m autotest.client report <job_id>                    # 人读的对比表
python3 -m autotest.client report <job_id> --json             # 机读，CI 用
python3 -m autotest.client report <job_id> --save-baseline    # 先对比，后把本次滚为新基线
```

真实输出：

```
## 评测报告：autotest-d9b59415

| testcase | 结论 | 变化 | 当前指标 | 基线指标 | 差值 |
|---|---|---|---|---|---|
| small_push:small_push | ✅ | new | survived=1.0000, settle_error=0.0000, ... | - | - |
| full:small_push       | ✅ | new | survived=1.0000, settle_error=0.0000, ... | - | - |
| full:medium_push      | ✅ | new | survived=1.0000, settle_error=0.0001, ... | - | - |
```

五种 `change` 的含义与该做什么：

| change | 含义 | 你该做什么 |
|---|---|---|
| `regressed` | `passed` 从过变不过 | **停下来查**。要么代码引入回归，要么工况/阈值该更新 |
| `worse` | 还是过的，但所有指标都变差了 | **关注**。余量在被吃掉，离红线更近了 |
| `improved` | 变过了，或所有指标都变好 | 确认是真改进后，滚动基线锁定成果 |
| `same` | 有涨有跌 | 看具体数值（注意 §3.4 的方向陷阱） |
| `new` | 基线里没这个 testcase | 首次跑 / 新加的工况，正常 |

**什么时候该滚动基线**——这是个策略问题，值得团队明确约定：

- ✅ **该滚**：改进被评审接受、合入主干之后。基线代表"当前主干的水平"。
- ✅ **该滚**：故意放宽/收紧了工况或阈值，历史数值不再可比。
- ❌ **不该滚**：为了让红的变绿。**这是在掩盖回归**，等价于改判据。
- ❌ **不该滚**：在特性分支上。基线应当只由主干评测推进。

CI 里的做法（第 5 关细讲）：主干/发版评测置 `save_baseline=true`，语义是**先对比、后滚动**；PR 评测只对比不滚动。

**仓内参考基线**（可选）：清单项可以写 `baseline: baselines/small_push.json`，作为该场景**首次评测**（评测机上还没有滚动基线）时的对比种子；一旦有了滚动基线就以滚动基线为准。格式 = `report --json` 输出里的 `results` 数组。

> **一次性现象**：启用场景清单后，`testcase_id` 从 `tc-1` 变成 `smoke:tc-1`，命名空间变了，第一轮会全部记 `new`。下一次 `save_baseline` 后恢复正常。

## 3.7 动手：给倒立摆做一套分层工况

按 §3.3 的四层结构，给算例仓补上**边界**和**预期必须失败**的用例。在你第 1 关建的 `/tmp/invp_demo` 里：

```bash
cat > /tmp/invp_demo/scenarios/tiered.yaml <<'EOF'
body: invp_sim
dataset:
  type: ctrl.invp.sim
  config:
    testcases:
      nominal:  { theta0: 0.05 }                      # ① 名义：必须过
      boundary: { theta0: 0.18 }                      # ② 边界：应该过，但余量小
      beyond:   { theta0: 0.20, theta_dot0: 1.5 }     # ③ 失效：物理上救不回来，必须红
checker: ctrl.invp.upright
checker_config: { settle_threshold: 0.02 }
EOF

cat > /tmp/invp_demo/scenario.yaml <<'EOF'
launch: "python3 invp_sut.py"
consumes: ["ctrl.invp.InvpObs"]
required_sensors: {}
hyperparams: {}
scenarios:
  - id: tiered
    description: 分层工况（名义/边界/失效）
    scenario: scenarios/tiered.yaml
EOF

python3 -m autotest.client run /tmp/invp_demo/scenario.yaml --json
```

真实结果：

| testcase | passed | survival_time | max_abs_theta | settle_error |
|---|---|---|---|---|
| `nominal` | ✅ | 10.0 | 0.05 | 3.9e-05 |
| `boundary` | ✅ | 10.0 | **0.18** | 1.43e-04 |
| `beyond` | ❌ | **0.02** | 0.225517 | 0.225517 |

**读这张表——它在告诉你三件事：**

1. **这份 benchmark 现在能证明自己在工作。** `beyond` 红了，说明判据不是"永远绿"的摆设（§3.3 第 ③ 层的意义）。
2. **`boundary` 的 `max_abs_theta = 0.18`，恰好等于初始扰动**——说明控制器从第一步就在往回收，一次都没让摆角超过初值。离失败线 0.2095 还有 0.03 rad 的余量。**这个数字比 `passed: true` 有信息量得多**：它量化了算法的能力上限。
3. **`beyond` 只撑了 0.02 秒（一步）** ——初始角速度 1.5 rad/s 太大，`theta` 一步就冲过了 0.2095。这是**工况本身超出了物理可控范围**，不是算法差。写这类用例时要在 `description` 里说清楚"这是设计上不可能过的"，免得后人来"修"它。

**再做两个实验：**

- 把 `settle_threshold` 从 `0.02` 收紧到 `0.0001` 再跑一次——**算法一行没改**，看结论怎么变。这就是及格线的分量：它和算法本身一样决定红绿。
- 把 `beyond` 的 `theta_dot0` 从 `1.5` 逐步降到 `0.5`，找到这个 PD 控制器**恰好还能救回来**的临界点。这就是用 benchmark 做能力刻画。

### ⚠ 一个真实的坑：深合并**不能删键**

想用清单项覆盖出一个"只跑 nominal"的 smoke 级别？下面这个写法**是错的**：

```yaml
scenarios:
  - id: smoke
    scenario: scenarios/tiered.yaml
    dataset_config:
      testcases: { boundary: null, beyond: null }    # ❌ 想用 null 删掉两个工况
```

实跑报错：

```json
{"ok": false, "error": "TypeError: plugins.ctrl.invp.sim.InvpScenario() argument after ** must be a mapping, not NoneType"}
```

原因：清单项的三个覆盖键走的是**深合并**，语义是"逐键覆盖值"，**没有"删除键"这个操作**。`null` 会被原样合并进去，然后传给数据源当参数，于是炸在插件里。

**正确做法：拆成两份场景文件。**

```yaml
scenarios:
  - id: smoke
    description: 名义工况冒烟（PR 反馈）
    scenario: scenarios/smoke.yaml       # 只含 nominal
  - id: tiered
    description: 分层工况（名义/边界/失效）
    scenario: scenarios/tiered.yaml      # 三个都有
```

**通用原则**：清单项覆盖适合**改数值**（调超参、调阈值、调时长），不适合**改结构**（增删工况）。增删工况就老老实实写新的场景文件——**能读懂的配置永远优于聪明的配置**。

## 3.8 通关自检

- [ ] 我能说出 benchmark 的四个"固定"，以及各自缺失的后果
- [ ] 我的场景文件里，每个 testcase 都能用一句话说清它在考什么
- [ ] 我的工况里**有一个是预期必须红的**，用来体检判据
- [ ] 我知道我这套题的每条阈值是怎么来的（物理推导 / 实测反推 / 产品指标）
- [ ] 我分了 smoke 和 full 两级，smoke 在一分钟内
- [ ] 我知道什么时候该滚动基线、什么时候滚动基线等于作弊
- [ ] 我知道回归对比把指标当成"越小越好"，以及这对我的指标集意味着什么

---

**下一关** → [第 4 关 · 开发评测插件](04-第4关-开发评测插件.md)（回答 Q2 怎么考；平台已有你算法类型的插件可跳到[第 5 关](05-第5关-接入CI流水线.md)）
