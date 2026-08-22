# ctrl.invp 插件契约

> 命名空间：`ctrl.invp`。经典车-倒立摆**闭环**控制评测（仿真 World + checker），
> 是「新建插件流程」的最简参考实现：schema/checker/sim/scenario 全套，
> 且是首个经 `register_dataset` 装配、可走 `client run` 服务通路的闭环插件。

## 1. 数据 schema（register_data 注册，跨进程、msgpack 编码）

| schema | kind | payload 类型 | 说明 |
|---|---|---|---|
| `ctrl.invp.InvpObs` | observation | `InvpObs` | x/x_dot（小车位置 m / 速度 m/s）+ theta/theta_dot（摆角 rad / 角速度 rad/s，theta=0 竖直向上） |
| `ctrl.invp.InvpAction` | action | `InvpAction` | 闭环指令：force（作用于小车的水平力 N，仿真按 ±force_limit 截断） |

observation 外层信封 `{timestamp, module="ctrl.invp", data}` 的 timestamp 取仿真时钟
（reset=0，每 step 推进 dt）。

## 2. GT 约定（{schema, v, data}，进程内不编码）

| schema | data 内容 | 产出方 |
|---|---|---|
| `ctrl.invp.InvpTask` | `{"dt", "max_steps", "theta_limit", "x_limit"}` | InvpSimWorld（按当前 scenario + physics） |

## 3. 数据源（闭环 World，closed_loop=True，produces = [`ctrl.invp.InvpObs`]）

| 键 | 类 | 说明 |
|---|---|---|
| `ctrl.invp.sim` | InvpSimWorld | 车-倒立摆动力学仿真（Sutton-Barto 参数化，半隐式欧拉）；step 收 action payload（`{module, data}`），`decode_action` 得 InvpAction 后按 dt 积分；终止条件：fell（\|θ\|>theta_limit）/ out_of_bounds（\|x\|>x_limit）/ survived（撑满 max_steps） |

dataset.config 字段：

```yaml
testcases:                  # 必填，{testcase_id: 场景参数}
  small_push: { theta0: 0.05, theta_dot0: 0.0, x0: 0.0, x_dot0: 0.0, dt: 0.02, max_steps: 500 }
physics:                    # 可选，物理参数覆盖
  { g: 9.8, cart_mass: 1.0, pole_mass: 0.1, half_length: 0.5, force_limit: 10.0,
    theta_limit: 0.2095, x_limit: 2.4 }
```

默认场景集见 `basic_invp_scenarios()`；预置场景 `scenarios/sim_basic.yaml`（small/medium 两档扰动）。

## 4. checker（consumes = [`ctrl.invp.InvpObs`]）

| 键 | 类 | 评测内容 | checker_config |
|---|---|---|---|
| `ctrl.invp.upright` | InvpChecker | survived（撑满 max_steps）+ settle_error（末段 10% 平均 \|θ\|）≤ 阈值判 passed；附 max_abs_theta / survival_time / upright_ratio | settle_threshold（默认 0.02 rad） |

records 约定：闭环 observation 外层信封列表（首帧为 RESET 观测，不计入执行步数）；
checker 过滤 `data.schema == ctrl.invp.InvpObs` 后 `decode_observation` 取 theta 序列评分。

## 5. 本体要求

纯仿真任务引用台架本体 `body/invp_sim.yaml`（无传感器实例）；SUT 的 `required_sensors` 应为空。

## 6. 典型用法

```bash
# 服务通路（推荐）：manifest 指向本插件场景，client 提交评测
python3 -m autotest.client run examples/invp_sut.scenario.yaml --json
```

```python
# 进程内通路（调试）：直接构造 World + ClosedLoopSession
load_plugin("ctrl.invp")
world = InvpSimWorld(basic_invp_scenarios())        # 或 InvpSimWorld.from_config(cfg)
checker = InvpChecker()
# 闭环编排见 autotest.eval.closed_loop.ClosedLoopSession
```
