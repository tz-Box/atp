# 附录 · scenario.yaml Schema 权威定义

> 读者：要写/改 scenario.yaml 的工程师（算法工程师写 manifest，测试工程师维护场景清单与阈值）。
> 第一次接触请先按[导读](00-导读与学习路径.md)的五关路径读教程正文——
> manifest 与场景文件的通俗解释在[第 2 关](02-第2关-接入你的算法.md) §2.6 与[第 3 关](03-第3关-定义benchmark.md) §3.2。
> 本文是查阅用的权威字段表（批次 F / R2，2026-08-26 定稿）。
>
> **地位**：本文档是算法仓库测试定义的**唯一权威 Schema**（《三系统并行开发规划-2026-08-26》R2 ATP 侧核心交付）。
> Hub 的 manual-check scenario 勾选（GitHub Contents API 读仓内本文件）与 ATP 的执行面都以本文档为准。
> 定位对齐：`scenario.yaml + Hub + ATP` ≈ `workflow.yaml + GitHub Actions + runner`——
> 测试定义权威在**被测仓内**，ATP 是通用执行器，不存在"ATP 端测试目录"接口。

## 1. 三层模型

```
算法仓库根目录 scenario.yaml   ← manifest（本 Schema 主体）：启动方式 + 场景清单 + runtime 声明
        │ scenarios[].scenario ┐
        ▼                      ▼
scenarios/*.yaml          场景文件：body + dataset + checker（评测内容本体，v1.1 §8 冻结）
        │
        ▼
ATP 执行面                 checkout → 按 submit.scenario 选场景 → 逐场景评测 → 汇总 results
```

- **manifest**（仓根 `scenario.yaml`）：描述"这个仓怎么被测"——启动命令、消费 schema、场景清单、运行环境。
- **场景文件**：描述"一次评测跑什么数据、用什么判定"——本体/数据集/checker，字段见 §4。
- **submit.scenario**（`POST /atp/evaluations`）：描述"这次跑哪些场景"——`null`(全跑) | `"id"` | `["a","b"]`。

## 2. manifest 字段（scenario.yaml）

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `launch` | str | ✅ | 算法启动命令（工作目录 = 本文件所在目录；docker runtime 时为容器内 `/workspace`） |
| `consumes` | list[str] | ✅ | 算法消费的 observation schema（命名空间键，如 `ctrl.invp.InvpObs`），与插件 `produces` 归属校验 |
| `scenarios` | list[obj] | — | **场景清单**（§3）；省略时等价于以旧字段 `scenario` 为唯一匿名场景（向后兼容） |
| `scenario` | str | — | 旧式单场景文件引用（相对本文件解析）；与 `scenarios` 同时存在时仅作缺省项 |
| `runtime` | obj | — | **运行环境声明**（§5）；缺省 `{type: host}`（宿主机直跑，现状零迁移） |
| `image` | str | — | 旧式 docker+bind 镜像名（填了则以 docker+bind 拉起算法）；与 `runtime.type: docker` 并存时以 `runtime` 为准 |
| `required_sensors` | dict | — | 传感器需求 `{类型: [实例名...]}`；纯仿真任务 `{}` |
| `hyperparams` | dict | — | 算法超参默认值，经 INIT 下发；场景清单项可覆盖 |
| `output_topic` | str | — | 算法自有产物 topic（ROS 侧，可选） |

## 3. scenarios 场景清单（R2 新增）

```yaml
scenarios:
  - id: small_push            # 必需;^[a-z0-9_]+$,清单内唯一;Hub 勾选与 submit.scenario 引用的就是它
    description: 小推力阶跃   # 可选,展示用(Hub 勾选列表/PMS 卡片)
    scenario: scenarios/small_push.yaml   # 必需;场景文件,相对本文件解析
    hyperparams: {kp: 12.0}   # 可选;深合并覆盖 manifest.hyperparams 与场景文件 hyperparams
    checker_config: {}        # 可选;深合并覆盖场景文件 checker_config(判定阈值即经此表达)
    dataset_config: {}        # 可选;深合并覆盖场景文件 dataset.config
    baseline: baselines/small_push.json   # 可选;仓内参考基线(相对仓根),见 §6
    expect: pass              # 可选;pass(缺省)|fail —— 本场景的**期望结果**,见 §3.1
```

### 3.1 `expect` —— 场景级期望结果（总契约 §10 A11）

`expect: fail` 声明「本场景**设计成必须失败**」，用于体检**判据本身还在不在工作**——
一套只含必过工况的 benchmark 无法证明判据没坏（教程[第 3 关](03-第3关-定义benchmark.md) §3.3 第 ③ 层）。

结论按「**实际 vs 期望**」判定，四态：

| expect | 实际 | 结论 | 含义 |
|---|---|---|---|
| `pass` | 通过 | ✅ 符合预期 | 正常 |
| `fail` | 失败 | ✅ 符合预期 | 体检用例在工作 |
| `pass` | 失败 | ❌ 不符合预期 | 真的坏了 |
| **`fail`** | **通过** | ❌ 不符合预期 | **判据坏了**——它已不能拒绝坏算法。**这不是「失败」，是更值得警觉的信号** |

**只要所有场景都符合预期，整次评测即 `success`。** 没有这个字段时，一个设计成必红的
场景会把整次评测拖红，而那条 `failure` 会同时流进四个消费者（GitHub check-run、
PMS「失败必通知」推飞书、Hub 概览失败数、交付物冻结判据），**每一个都被喂了错误事实**。

**原始事实不被改写**：`results` 里每条 testcase 的 `passed` 保持真实，
`report.metrics.testcases` 的计数也照实报——否则没人知道该场景到底跑没跑过。
改的只是**结论**的算法。

> **期望值必须写在被测仓内**（本 Schema），不能挂在 Hub 规则里：
> 那等于把测试语义搬出被测仓（违反 §1 的定位），场景名在仓里、期望值在 Hub，两处必然漂。

约束：
- `id` 重复 / 非法字符 / 缺 `scenario` 文件引用 → manifest_invalid（§7）。
- `expect` 取值非 `pass|fail` → manifest_invalid。
- 清单为空列表 `[]` ≡ 省略（走旧字段 `scenario`）。
- 同一 `scenario` 文件可被多个清单项引用（不同 hyperparams/阈值 = 不同场景）。

## 4. 场景文件 Schema（scenarios/*.yaml，v1.1 §8 已冻结，此处仅汇总）

| 字段 | 类型 | 必需 | 说明 |
|---|---|---|---|
| `body` | str | ✅ | 本体资产引用（body/*.yaml 的 stem，如 `invp_sim`）；数据集与本体必须对齐 |
| `dataset.type` | str | ✅ | 数据集插件命名空间键（如 `ctrl.invp.log`） |
| `dataset.config` | dict | — | 数据集插件配置 |
| `checker` | str | — | 判定插件命名空间键；空 = 数据流验证（不判 pass/fail） |
| `checker_config` | dict | — | 判定插件配置（**判定阈值在此**，如 `settle_time_max: 2.0`） |
| `metrics` | dict | — | **指标元信息声明**（2026-09-11 增补）：`{指标名: {direction: lower\|higher, expect: pass\|fail}}`，两键均可选但至少一个，见 §4.1 |
| `sensor_config` | dict | — | `{类型: {实例名: topic}}`，经 INIT 下发 |
| `hyperparams` | dict | — | 场景级算法超参 |

### 4.1 `metrics` —— 指标元信息声明（方向 + 判据级期望）

```yaml
metrics:
  survived: { direction: higher }      # 越大越好
  settle_error: { direction: lower }   # 越小越好
  ate_rmse: { direction: lower, expect: fail }  # 判据级期望：本场景该判据必须失败（体检）
  # peak_force 有意不声明：观测事实，孰优取决于工况 → 回归报告只给数值与 delta，不上色
```

**`expect`（判据级期望，A11 批 1，2026-09-11 增补）**：语义同 §3.1 场景级 `expect`
下沉到单条判据——「我预期这条判据会失败」，体检该判据本身还在不在工作。缺省 `pass`。
- 只影响回调载荷 `metrics.testcases_detail` 的 `met` 层（判据五态由 expected/actual 推导），
  **原始 `passed` 不改写、评测结论（conclusion）的算法不变**（仍由场景级 met 决定）；
- 声明了 `expect: fail` 的场景，其**清单项的场景级 `expect` 通常也应声明 `fail`**——
  否则原始 failed 会把场景判不符合预期。两级声明由被测仓自洽，ATP 不代为推导。

- 回归对比（`vs_baseline`）**只对声明了方向的指标给「变好/变差」判断**；
  缺声明的指标只报数值与 delta，落「未判定」（undetermined）——**缺省不猜**。
  修订动机：此前方向写死「越小越好」，`survived`/`upright_ratio` 这类越大越好的指标，
  劣化会被反报成改善（2026-09-11 M 按缺陷批准修正）。
- `same` 只表示「逐指标数值确实没变」；「声明的指标有好有坏」是 `mixed`、
  「无可比指标」是 `no_comparable`——四件事各自成态，消费方不要把它们当同义词。
- **声明写错会报错**（direction 非 `lower|higher`、多余键、扁平写法），不静默降级——
  拼错的声明若被当成「未声明」，该指标会悄悄退回未判定态，没人发现。
- 嵌套 dict 形状为趋势判据（`regression_tolerance` 等，批 2）在同处扩展预留。
- 方向是指标的内在属性，不随清单项变化，故只在场景文件声明，清单项无覆盖键。

## 5. runtime 运行环境声明（R3，ATP 主责）

```yaml
runtime:
  type: host | venv | docker   # 缺省 host(现状:宿主机 python 直跑,零迁移)
  # type=venv  :checkout 后 python3 -m venv .atp-venv && pip install -r requirements.txt(仓根),
  #             评测进程切 venv 解释器(F1 期,覆盖纯 Python 依赖场景)
  # type=docker:仓根 Dockerfile 构建,或 image: 指定镜像拉取;容器内评测,产物目录卷挂载回传(F2 期)
  image: registry/xxx:tag      # 仅 type=docker 可选;缺省用仓根 Dockerfile
```

- 缺省 / 缺字段 ≡ `type: host`，与现行行为完全一致。
- 旧字段顶层 `image:`（docker+bind）继续有效；`runtime` 存在时以 `runtime` 为准。
- F2 前置：评测机 docker daemon + 评测用户 docker 组（部署文档补）。

**venv 实现语义（M-F4，F1 期已落地）**：

- 仓根 `.atp-venv` 复用/创建（`python3 -m venv --system-site-packages`），随后
  `pip install -r requirements.txt`（仓根）；环境准备为 job 级一次（同 job 全场景共享）。
- 评测进程经 `PATH` 前置 `.atp-venv/bin` 切 venv 解释器（`launch` 命令不变）；venv 自有包位于
  `sys.path` 前段、优先于系统包（版本隔离），runner 预装基础设施经 system-site 保持可见。
- ATP SDK（autotest/tzcomm）由 runner 环境提供，经 `PYTHONPATH` 透传入评测进程——
  算法仓 `requirements.txt` **无需**（也不应）重复声明 SDK。
- 仓根无 `requirements.txt` → 裸 venv + session.log WARNING（不阻塞）；pip/venv 失败 → job 级
  failure（results 含 `<runtime>` 失败条目）。
- 算法仓应将 `.atp-venv/` 加入 `.gitignore`（评测现场产物，不入版本控制）。
- `type: docker` 在 F1 期提交即报 job 级 failure（"尚未实现（M-F5）"），不静默回退。

## 6. baseline 基线语义

- **ATP 滚动基线**（现状不变，M-D3）：评测产物对比 `artifacts/baseline.json`，`save_baseline=true` 且成功时先对比、后滚动；summary 携带 `vs_baseline` 计数。
- **仓内参考基线**（清单项 `baseline:` 字段，可选）：仓根相对路径，作为该场景**首次评测**（ATP 侧尚无滚动基线）时的对比种子；ATP 侧已有滚动基线后以滚动基线为准。基线文件格式 = `client report --json` 的 results 数组。

## 7. submit.scenario 参数与错误码（§3.3 契约变更）

```
POST /atp/evaluations
  scenario: null              → 跑清单全部场景(旧仓=唯一匿名场景,现状兼容)
  scenario: "small_push"      → 只跑该 id
  scenario: ["a","b"]         → 按清单顺序跑这两个
  scenario: "scenarios/x.yaml" → 含 "/" 或以 .yaml 结尾的值保持旧语义(manifest 路径选择),平滑过渡
```

错误响应统一 `{"ok": false, "error": "<人类可读>", "code": "<机读>"}`（4xx，Hub 判 failure 免等超时）：

| code | 触发 |
|---|---|
| `manifest_missing` | 仓内找不到 manifest（submit.scenario 指定路径或仓根 scenario.yaml 均不存在） |
| `manifest_invalid` | manifest 解析/校验失败（缺 launch、scenarios 项缺 id/scenario、id 非法/重复、runtime.type 未知） |
| `scenario_unknown` | submit.scenario 引用的 id 不在清单中（报文含可用清单：`未知 scenario: xxx（可用: a, b）`） |

## 8. 示例

### 8.1 现状兼容（cicd_test，无需任何改动）

```yaml
launch: "python3 invp_sut.py"
consumes: ["ctrl.invp.InvpObs"]
scenario: scenarios/sim_basic.yaml
required_sensors: {}
hyperparams: {}
```

### 8.2 多场景 + venv（R2/R3 全量形态）

```yaml
launch: "python3 invp_sut.py"
consumes: ["ctrl.invp.InvpObs"]
required_sensors: {}
hyperparams: {kp: 8.0, kd: 1.2}

runtime:
  type: venv                  # 仓根需有 requirements.txt

scenarios:
  - id: sim_basic
    description: 纯仿真基线（默认参数）
    scenario: scenarios/sim_basic.yaml
    baseline: baselines/sim_basic.json
  - id: small_push
    description: 小推力阶跃（低增益）
    scenario: scenarios/sim_basic.yaml    # 同数据集,不同超参 = 不同场景
    hyperparams: {kp: 4.0}
    checker_config: {overshoot_max: 0.35}
  - id: disturbance
    description: 扰动恢复
    scenario: scenarios/disturbance.yaml
```

对应触发：`scenario: null`（三个全跑）/ `"small_push"` / `["sim_basic","disturbance"]`。
