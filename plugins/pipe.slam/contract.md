# pipe.slam 插件契约

> 命名空间：`pipe.slam`。本文件是插件的自描述事实源：数据 schema、GT 约定、数据源、checker。
> 跨插件通用规则（信封格式、注册、装配校验）见《途零-AutotestService接口契约》§4–§7。

## 1. 数据 schema（register_data 注册，跨进程、msgpack 编码）

| schema | kind | payload 类型 | 说明 |
|---|---|---|---|
| `pipe.slam.SlamObs` | observation | `SlamData` | SLAM 观测：`sensors{类型:{实例名:数据}}`（lidar=(N,3)float32 点云、imu=Imu）+ 可选 `odom`(Pose) |
| `pipe.slam.CylinderResult` | result | `CylinderResult` | 管道圆柱拟合：timestamp + 轴线上一点 center + 单位方向 direction + valid/straightness_residual/radius |
| `pipe.slam.StampedPose` | result | `StampedPose` | 带时间戳位姿（SLAM 轨迹输出） |

编码细节：lidar 点云按 `{shape, dtype, data(bytes)}` 保真传输；observation 外层信封
`{timestamp, module="pipe.slam", data}` 的 timestamp 为唯一权威时间（多实例 lidar 经
FrameAssembler 对齐后取对齐时刻）。

## 2. GT 约定（{schema, v, data}，进程内不编码）

| schema | data 内容 | 产出方 |
|---|---|---|
| `pipe.slam.Trajectory` | `{"trajectory": [StampedPose.to_dict(), ...]}` | synthetic / rosbag（`gt_traj.tum`，TUM 格式） |
| `pipe.slam.PipeSegment` | `{"pipe_segment": [(ts, cx, cy, cz, dx, dy, dz), ...]}` | rosbag（`pipe_segment_gt.csv`） |

GT 由 checker 自行解析：`decode_ground_truth()` 取 data 后按上表结构消费。

## 3. 数据源（register_dataset，produces = [`pipe.slam.SlamObs`]）

| 键 | World | config | 说明 |
|---|---|---|---|
| `pipe.slam.synthetic` | DatasetWorld(SyntheticSlamDataset) | n_testcases/n_steps/dt/radius/seed | 合成圆形轨迹 + 噪声点云，冒烟用 |
| `pipe.slam.rosbag` | DatasetWorld(RosbagSlamDataset) | root/topic_map/gt_dir?/max_frames?/sync_tolerance | rosbag2 回放，每包一个 testcase |
| `pipe.slam.rostopic` | RostopicWorld(SlamRostopicConverter) | topic_map/tolerance | 实时 ROS 话题（需 ROS 环境） |
| `pipe.slam.device` | DeviceWorld(SlamDeviceConverter) | topic_map/tolerance | Patrol Box device 层 tzcomm 样本话题 |

`topic_map` 统一形态：`{lidar: {实例名: 话题}, imu: {实例名: 话题}}`，lidar 必填；
多实例按时间戳容差（默认 0.05s）对齐，required 的 lidar 实例到齐才产帧。

## 4. checker（register_checker，consumes = [`pipe.slam.SlamObs`]）

| 键 | 类 | 评测内容 | checker_config |
|---|---|---|---|
| `pipe.slam.ape` | SlamChecker | 轨迹 ATE/RPE（Umeyama 对齐，按时间戳匹配 GT） | ate_threshold/rpe_threshold/time_tolerance/rpe_delta |
| `pipe.slam.pipe` | PipeChecker | 中轴线 center 距离 + direction 夹角 | center_tolerance/direction_tolerance_deg |

records 约定：开环 result payload（`{module, data}`）列表，checker 按 data.schema
过滤后 `decode_result` 解码（ape 取 StampedPose，pipe 取 CylinderResult）。

## 5. 典型场景

- 冒烟：`body: pbox_v1` + `pipe.slam.synthetic` + `pipe.slam.ape`（见 scenarios/synthetic_slam.yaml）
- 实袋回放：`pipe.slam.rosbag` + `pipe.slam.pipe`（见 scenarios/pipe_real.yaml）
- 真机实时：`pipe.slam.device`，省略 checker 即数据流验证（见 examples/device_slam.scenario.yaml）
