# manip.force 插件契约

> 命名空间：`manip.force`。单自由度末端执行器接触力控**闭环**评测（仿真 World + checker），
> 是「新建插件流程」的第二个验证样例（首个为 ctrl.invp）：与状态镇定（invp）、
> 导航到点（nav2d）正交的第三类控制任务——力跟踪。

## 1. 数据 schema（register_data 注册，跨进程、msgpack 编码）

| schema | kind | payload 类型 | 说明 |
|---|---|---|---|
| `manip.force.ManipObs` | observation | `ManipObs` | x/x_dot（末端位置 m / 速度 m/s）+ f_contact（当前接触力 N，未接触为 0）+ target_force（目标接触力 N，随 testcase 变、经 obs 下发） |
| `manip.force.ManipAction` | action | `ManipAction` | 闭环指令：force（作用于末端的推力 N，仿真按 ±force_limit 截断） |

observation 外层信封 `{timestamp, module="manip.force", data}` 的 timestamp 取仿真时钟
（reset=0，每 step 推进 dt）。target_force 在 obs 中下发（对齐 nav2d goal 先例），SUT 无需场景先验。

## 2. GT 约定（{schema, v, data}，进程内不编码）

| schema | data 内容 | 产出方 |
|---|---|---|
| `manip.force.ForceTask` | `{"dt", "max_steps", "target_force", "x_wall", "f_limit"}` | ForceSimWorld（按当前 scenario + physics） |

## 3. 数据源（闭环 World，closed_loop=True，produces = [`manip.force.ManipObs`]）

| 键 | 类 | 说明 |
|---|---|---|
| `manip.force.sim` | ForceSimWorld | 单自由度质量块 + 弹簧-阻尼接触壁面（x<x_wall 自由空间 f=0；x≥x_wall 时 f=k·(x-x_wall)+c·ẋ），半隐式欧拉；step 收 action payload（`{module, data}`），`decode_action` 得 ManipAction 后按 dt 积分；终止条件：force_exceeded（f>f_limit，压坏）/ survived（撑满 max_steps） |

dataset.config 字段：

```yaml
testcases:                  # 必填，{testcase_id: 场景参数}
  light_touch: { target_force: 3.0, x0: 0.48, x_dot0: 0.0, x_wall: 0.5, dt: 0.001, max_steps: 5000 }
physics:                    # 可选，物理参数覆盖
  { mass: 1.0, k_contact: 200.0, c_contact: 1.0, force_limit: 50.0, f_limit: 25.0 }
```

默认场景集见 `basic_force_scenarios()`；预置场景 `scenarios/sim_basic.yaml`（light/firm 两档目标力）。

## 4. checker（consumes = [`manip.force.ManipObs`]）

| 键 | 类 | 评测内容 | checker_config |
|---|---|---|---|
| `manip.force.track` | ForceChecker | survived（未触发 force_exceeded 撑满 max_steps）+ settle_error（末段 10% 平均 \|f - f_target\|）≤ 阈值判 passed；附 peak_force / overshoot / tracking_ratio | settle_threshold（默认 0.5 N） |

records 约定：闭环 observation 外层信封列表（首帧为 RESET 观测，不计入执行步数）；
checker 过滤 `data.schema == manip.force.ManipObs` 后 `decode_observation` 取 f_contact 序列评分，
f_target 从 GT 读取（任务参数），阈值松紧从 checker_config 读取（评测配置）。

## 5. 本体要求

纯仿真任务引用台架本体 `body/manip_sim.yaml`（无传感器实例）；SUT 的 `required_sensors` 应为空。

## 6. 典型用法

```bash
# 服务通路（推荐）：manifest 指向本插件场景，client 提交评测
python3 -m autotest.client run examples/manip_sut.scenario.yaml --json
```

```python
# 进程内通路（调试）：直接构造 World + ClosedLoopSession
load_plugin("manip.force")
world = ForceSimWorld(basic_force_scenarios())      # 或 ForceSimWorld.from_config(cfg)
checker = ForceChecker()
# 闭环编排见 autotest.eval.closed_loop.ClosedLoopSession
```

示例 SUT：[examples/manip_sut.py](../../examples/manip_sut.py)（PI 力控 + 速度阻尼 + 抗积分饱和，
增益经 hyperparams 覆盖 kp/ki/kd/i_limit；注意 ki·i_limit 须 ≥ 目标力量级，否则积分饱和出静差）。
