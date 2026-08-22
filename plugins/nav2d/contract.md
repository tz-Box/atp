# nav2d 插件契约

> 命名空间：`nav2d`。2D 差速底盘导航**闭环**评测（仿真 World + checker），
> 是「闭环通路」（STEP→ACTION→step）的参考实现与冒烟场景。

## 1. 数据 schema（register_data 注册，跨进程、msgpack 编码）

| schema | kind | payload 类型 | 说明 |
|---|---|---|---|
| `nav2d.NavObs` | observation | `NavData` | robot_pose(Pose) + goal(Pose) + obstacles[(cx,cy,r)] |
| `nav2d.NavAction` | action | `NavAction` | 闭环指令：线速度 v + 角速度 w |

observation 外层信封 `{timestamp, module="nav2d", data}` 的 timestamp 取仿真时钟
（reset=0，每 step 推进 dt）。

## 2. GT 约定（{schema, v, data}，进程内不编码）

| schema | data 内容 | 产出方 |
|---|---|---|
| `nav2d.NavGoal` | `{"goal": (gx, gy), "obstacles": [(cx, cy, r), ...]}` | SimWorld（按当前 scenario） |

## 3. 数据源（闭环 World，produces = [`nav2d.NavObs`]）

| 键 | 类 | 说明 |
|---|---|---|
| `nav2d.sim` | SimWorld | 差速底盘运动学仿真；step 收 action payload（`{module, data}`），`decode_action` 得 NavAction 后按 dt 积分位姿；终止条件：arrived / collision / timeout(max_steps) |

`dataset.config` 格式：`{testcases: {id: {start: [x,y,yaw], goal: [gx,gy], obstacles: [[cx,cy,r],...], dt, max_steps, arrival_tolerance}}}`，
经 `SimWorld.from_config` 装配（dt/max_steps/arrival_tolerance 有默认值，可省）；进程内直构可用 `simple_nav_scenarios()`。

预置场景：[scenarios/sim_basic.yaml](scenarios/sim_basic.yaml)（body `nav2d_sim` 纯仿真台架，两个 testcase）；
服务通路（`client run`）参考 manifest [examples/nav_sut.scenario.yaml](../../examples/nav_sut.scenario.yaml)。

## 4. checker（consumes = [`nav2d.NavObs`]）

| 键 | 类 | 评测内容 | checker_config |
|---|---|---|---|
| `nav2d.default` | NavChecker | 末帧到点判定 + 全程最小安全裕度 + 路径成功率 | arrival_tolerance/safety_margin |

records 约定：闭环 observation 外层信封列表；checker 过滤 `data.schema == nav2d.NavObs`
后 `decode_observation` 解码，以 (外层 timestamp, robot_pose) 重建轨迹评分。

## 5. 典型用法

```python
load_plugin("nav2d")
world = SimWorld(simple_nav_scenarios())          # 进程内直构；或 SimWorld.from_config(cfg) 经场景装配
checker = NavChecker()
# 闭环编排见 autotest.eval.closed_loop.ClosedLoopSession
```

服务通路：`python3 -m autotest.client run examples/nav_sut.scenario.yaml`（server 按
`world.closed_loop=True` 自动装配 ClosedLoopSession）。
