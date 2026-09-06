# ATP ↔ device / simulator 对接协作文档（草案 v0.1 · 2026-09-06）

> **状态：草案，未定稿，ATP 侧未做任何代码改动。**
> 三方 agent（ATP / tz_patrol_box_device / pbox-simulator）经两轮问答形成；待补项见 §7。
>
> **规则（M 定）**：device 与 simulator 是**已验证侧**，接口原则上不改；**由 ATP 提问、ATP 调整**。
> 确有值得改动对侧之处，只出方案、**决策权归 M**（本文档 §7.2 列出候选）。
>
> **本文档的事实来源**：device 侧为 `schemas/port_*.messages.schema.json` 与 `src/tz_device_service/`；
> simulator 侧为其 README / `scripts/data_check.py` / `docs/scene-io.md` / `ISSUES.md`。
> ⚠ device 仓里被引用的 `docs/device_external_interface.md`、`CLAUDE.md` **实际不存在**，勿找。

---

## 1. 一句话结论：我原来的方案是错的

**错的**：ATP 写一个 `IBackendPlugin → IWorld` 的进程内适配器。
**对的**：`IBackendPlugin` 是 **backend 侧的 ABC**（simulator / S10 / TRON1 各自实现，由 `DeviceFactory` 加载），
**消费者永远不碰它**。ATP 作为消费者对接的是 **`tz_device_service.DeviceClient` 的四个口**。

```
                    ┌──────────────── device 层内部（ATP 不感知）─────────────┐
                    │  IBackendPlugin ← GzSimBackend / ReplayBackend /        │
                    │                    S10 / TRON1 / 松灵 adapter / mock    │
                    └────────────────────────┬────────────────────────────────┘
                                             │ 同一个 DeviceServer 进程
        ATP                                  ▼ 四个口（tzcomm）
  ┌─────────────┐   INFO   能力/源清单/钟域表/外参（description, calib_hash）
  │ DeviceWorld │◀──DATA───  逐路样本（/device/data/<source_id>）
  │(DeviceClient│   STATUS  状态/clock_event/pending_mode
  │  消费者)     │──CMD───▶  指令（velocity 等，自带 arbiter/seq/stamp_ns）
  └─────────────┘
```

**收益**：sim / 真机 / replay 在 ATP 侧**天然合一** —— 只换 server 侧配置里 `backend:` 一行，
ATP 配置与代码不动。比原方案简单一个量级。

**依赖形态**：ATP **单向依赖** `tz-patrol-box-device`（pip 包名），如同依赖 tzcomm。反向依赖不存在。

### 1.1 三源归属重新划分

| ATP 侧 | 归属 | 说明 |
|---|---|---|
| `ctrl.invp.sim` / `nav2d.sim` 等插件仿真 | **保留** | 算法域仿真，与机器人本体无关 |
| `pipe.slam.rosbag` 等 rosbag 回放 | **保留** | 与 device 无关 |
| `DeviceWorld` | **重写** | 改为 `DeviceClient` 消费者；sim/real/replay 三源在此合一 |
| device 来源的 `DatasetWorld` | **退役** | 由 device 侧 `backend: replay` 取代 |

---

## 2. ★确定性：ATP 的一条硬纪律在 device 面上不成立

ATP 现有纪律（教程第 4 关 §4.3 ⑧）：*同样的 testcase + 同样的 action 序列，必须产生完全相同的
观测序列；否则基线回归失去意义——分不清指标波动是算法改了还是运气变了。*

**经 device 面走仿真时这条不成立。** simulator 侧逐段核实，链路上有**四处墙钟驱动**：

| # | 位置 | 性质 |
|---|---|---|
| 1 | gz 物理本身 | 封闭系统理论可重复，但**未做、未验证**（其 README「不做清单」明列） |
| 2 | gz-transport 投递 | 异步回调 + 「每话题最新一帧」+ seq 去重，**哪一帧被哪次 poll 取走取决于线程调度** |
| 3 | `DeviceServer` tick 循环 | 按 `time.monotonic()` 跑 `tick_hz`（默认 200），心跳看门狗同样墙钟 |
| 4 | `PolicyRuntime`（policy 模式） | 独立线程 50Hz 推理，**与物理步完全解耦** |

要做到逐位一致须同时改 device tick 与 locomotion 为仿真时间驱动 —— **跨两仓的架构改动，不在本轮范围。**

### 2.1 ★分档的轴不是「哪个 backend」，是「谁驱动 `now_ns`」

两侧答复合起来给出了比「sim 不行 / 其他待定」更细的结论。**先调和一处粒度差**：

- simulator 说「经 `serve.py` + tzcomm 一律不逐位」——着眼**投递时序**；
- device 说「`backend: replay` 经 wire 时**内容**仍逐位确定」——把**时序**与**内容**拆开了。

**两者不矛盾，各自答的是不同的问题，合起来是**：

| 通路 | 谁驱动 `now_ns` | 时序 | **内容** | 基线语义 |
|---|---|---|---|---|
| 进程内插件仿真（`ctrl.invp` / `nav2d`） | ATP | 确定 | **逐位** | 逐位一致 |
| device · mock / replay · **进程内 `DeviceFactory.open()`** | ATP | 确定 | **逐位** | 逐位一致 |
| device · **replay** · 经 `serve.py` + tzcomm | 墙钟 | 不确定 | **逐位**※ | **开环可逐位一致** |
| device · **mock** · 经 `serve.py` + tzcomm | 墙钟 | 不确定 | **否** | 统计一致 |
| device · sim / 真机（任何方式） | 墙钟 + gz + policy 线程 | 不确定 | 否 | 统计一致 + 容差带 |

**为什么 replay 与 mock 在 wire 上分开**（关键，别记混）：
- **replay 不积分**。`ReplaySource._acquire` 走 `parse_sample`，按录制的**全局顺序**在
  `poll_all` 固定遍历序里认领；`ts` 是 `parse_timestamp_set(d["ts"])` **原样还原，不是重算**
  （device 更正其初稿措辞）。`freeze_at_snapshot` 冻的是**钟域表的新鲜度判据**
  （pairwise / sweep_stale 用），**样本头一个字节都不动**。
  所以确定性比「重算后一致」更硬 —— **就是录制文件里那份**。
  **tick 抖动只改「什么时候拿到」，不改「拿到什么」。**
- **mock 要积分**，三处都指向不确定：① `MockWorld.integrate(now_ns)` 的 dt 取自传入值；
  ② 每路 `_due(now_ns)` 按 `rate_hz` 与传入时刻比 → **样本条数**随墙钟变，**序列长度都不同**；
  ③ `_device_ts` 用 `now_ns` 造 `t_src` → 时戳全是墙钟。
  经 `serve.py` 时 `now_ns = box_now_ns()` 是墙钟，故三条全中；
  进程内由 ATP 递增 `now_ns` 时才逐位（噪声取 `seed:source_id` 派生的独立 PRNG）。

※ **replay 经 wire 的两个前提，缺一不可**：
1. 该路 DATA 用 **`tcp`**（udp 组播丢片 → 序列变 → 内容就不再一致）；
2. ATP **只用 `ts.t_ref_ns`，不用到达时刻**。ATP 现有 `_stamp_from_dict` 回退 `time.time()`
   正好违反第 2 条 —— **§5.1-6 那条不只是「不精确」，它直接摧毁 replay 档的确定性。**

### 2.2 三条限定，免得分档被用错地方

1. **replay 不能做闭环。** 它回放录好的样本与回执，发什么 action 都不影响下一帧观测，
   回执是录制当时的 `clamped_mask`/`preempted_by`。**只适合开环（SLAM/感知）的严格回归。**
2. **mock 可以闭环，但世界是假的。** 匀速积分 + 高斯噪声，**无碰撞、无地形、无传感器物理**，
   点云是占位帧（`device_mock.yaml` 雷达只发 `lidar_preview.v2` 标量，连 `raw` 都没有）。
   它能给控制器一个逐位可复现的**运动学**回归，**不能验证避障、通过性、倾覆**。
   > **写死在文档里**：谁也不许拿 mock 上的「避障 100% 通过」当避障通过。
   > mock 档的 testcase 命名与报告里应带 `kinematic-only` 标记，堵住这个误读。
3. **进程内 open 的代价**：ATP 进程即 device 进程，指令 `stamp_ns` 必须与 `tick` 的 `now_ns`
   **同一个钟**，否则 `T_hb=200ms` 看门狗误判（device ISSUE C1）。
   且 Service↔SUT 的 STEP→ACTION 必须保持**一问一答同步**（现状如此）——
   中间加任何按墙钟节拍的东西，逐位一致立刻失效。

### 2.3 由此确定的三档用法（ATP 的判据体系）

| 档 | 通路 | 用途 |
|---|---|---|
| **严格回归档** | 开环走 wire+replay(tcp)；闭环走进程内 mock | 基线逐位比对，**指标变动 = 算法变了**，无噪声解释空间 |
| **真实性验证档** | sim（gz） | 验证在真实动力学/几何下成立，用统计一致 + 容差带 |
| **真机档** | 真机 | 同上，容差带更宽 |

「回归走 mock/replay 拿严格判据、sim 拿真实性验证」的分工**成立**，两侧均确认。

**容差带凭什么有依据** —— 必须先固定下列各项，波动才只剩调度噪声（simulator 提供）：
policy 权重 SHA-256 · scene id · seed · 出生位姿（`set_pose` 回同一点）· 摩擦系数 · device 配置文件。
**不固定这些，容差带只是个拍脑袋的数。**

> **ATP 待办**：教程第 4 关那条纪律现写成无条件的，**是错的**，须改为
> 「逐位可复现只对**由 ATP 驱动 `now_ns`** 的通路、以及 **replay 开环**成立；经 sim / 真机不成立」。

## 3. 进程模型与编排

### 3.1 四进程（simulator 建议，ATP 采纳）

```
① gz sim                                   世界
② devlink/serve.py <device_*.yaml>         device，唯一持 GzBridge
③ ATP Service：DeviceClient（喂帧/发指令）+ WorldProbe（真值/复位）
④ SUT 子进程                                被测算法
```

**D15 的精确边界**：同一 Python 进程里只要有活着的 **gz 订阅**，gz 的 `Node.request()` 会间歇性永不返回。
它限制的是 **gz 订阅与 gz 服务请求不能同进程**，**不限制 tzcomm**。
因 device 跑在 ②（唯一持 `GzBridge`），ATP 进程里同时持 `WorldProbe` + `DeviceClient` **是可以的**。

**两条环境约束**：
- `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python` 必须在**任何 protobuf import 之前**设好；
- 所有进程 `GZ_PARTITION` / `GZ_IP` 须一致；多网卡机器不设 `GZ_IP=127.0.0.1` 会随机掉订阅（D19）。

**局间复位**：用 `probe.set_pose()` 或 `probe.reset(model_only=True)`；
**不要 `reset.all`** —— 它销毁 contact 传感器且时间归零（D22）。

### 3.2 data_check 作为接入前置（串行，不并存）

```
gz sim → data_check --strict --out <dir> → （通过后）serve.py → ATP Service → SUT
```

data_check 是**一次性进程**：自己 open device（不走 DeviceClient）、采 `--seconds` 秒、close、退出；
其 `GzBridge` 与它拉起的 `truth_tap` 子进程随之结束。**因此与 ATP 不会并存**，tcp 单消费者冲突不存在。

两条注意：① data_check 默认 `reset(model_only)` 且 `--vx` 会把本体开走 —— **ATP 开第一局前须
`set_pose` 到起点，不要假设本体还在出生点**；② 默认 `--seconds 20` + 复位落稳 3s，预算约 30s。

**门禁判据（ATP 侧）**：`verdict` 四态 `PASS/WARN/FAIL/SKIP`，`--strict` **只对 FAIL 非零**。
**SKIP 不是通过。** 而 `checks.json` 的 `Check` 只有 `id/layer/verdict/summary/metrics/source_id`，
**没有结构化的 skip 原因**（`why` 只进 `summary` 文本）。故 ATP 必须：

> **先按 device 配置 + 本次参数算出「应该出结论的 id 集合」，再核对 `checks.json`；缺的和不该 SKIP 的都算失败。**

这与 ATP 刚修的一个缺陷同形（**以声明的清单枚举，不以观测到的结果枚举**）。判定依据（id 稳定）：
- 给了 `--model` 且无 `--no-truth` 时仍 SKIP 的：`truth_cross_checks` / `odom_vs_truth` /
  `attitude_vs_truth` / `imu_orientation_vs_truth` / `imu_accel_vs_truth` / `extrinsics_vs_truth`（L3）、
  `imu_preintegration_drift` / `lidar_imu_time_alignment`（L4）→ **素材没到位，判失败**
- 给了 `--scene` 仍 SKIP 的 cloud_geom 组（L4）→ **判失败**
- 天然可 SKIP（按 device 配置可预先算出）：裸 IMU 的 `imu_orientation_vs_truth` /
  `imu_gyro_vs_orientation`；无型号声明的 `imu_saturation_rate`；无 `rate_hz` 的 `rate_vs_declared`
- 「样本不足 / 凑不出窗口」类 SKIP → 采集时长不够或本体早摔，**判失败**

**分层适用**：L1/L2 不需真值、真机同样适用，**最适合当门禁**；L3/L4 需 `truth_tap` + `--scene`（+`--model`）。
`--out <dir>` 目录（`samples.jsonl` + `clouds/` + `checks.json`）可直接归档为评测留痕；
`--replay <dir>` 换阈值重算，与实时结论**逐字节相同**。

---

## 4. 时间模型：ATP 观测信封缺了一整个维度

device 每帧带七字段 `ts`；ATP 观测信封只有 `{timestamp, module, data}` **一个标量**。
后果：同一算法跑在 S3 同步与 S0 同步的数据上 ATE 差很多，而**两份报告长得一样**
—— ATP 现在**无法回答「这两次评测可比吗」**，而基线回归与跨版本对比都建立在这个没被记录的前提上。

### 4.1 采纳方案：(a) 逐帧透传 + 评测级快照

**逐帧**：原样透传完整七字段，不裁：
`t_ref_ns` / `t_src_ns` / `t_rx_ns` / `clock_domain` / `sync_class` / `sigma_ns` / `domain_locked`。
`t_ref_ns` 继续作为算法唯一看到的标量时间（现状不变）；`ts` 整体作为**新增可选字段**挂在观测上。

> **`t_src` 必须留**：它是「历史数据可用新估计器重算 `t_ref`」的唯一依据。
> device 侧原话：*只录换算后的值，所有已录 dataset 就被永久锁死在录制当时的钟差估计上。*
> **ATP 现有的 dataset 回放正好踩在这句话上。**

**评测级**：开局把 INFO 四组落 artifacts —— `clock_table`（含 `id_to_name`）、`description`
（外参 + `calib_hash`）、`capability`、`source_list`；整场录 STATUS 的 `clock_event` 与 `clock_domain_update`。

> **「这两次可比吗」的答案** = 钟域表 + 每路样本 `sync_class` 的最低值 + `sigma_ns` 分布 + `calib_hash`
> 四者是否一致。**不采用方案 (c)（塞进 GT）**：GT 是世界真值，`sync_class`/`sigma` 是**观测质量**，
> 属 checker 该读的元数据而非真值。

### 4.2 三条语义细节（易错）

- `sync_class` 是**本帧实际达到的等级**，不是声明值；域失锁/估计过期/无 `t_src` 时降 S0。
  原则上逐帧，实际只在事件时跳变，且每次跳变 STATUS 推 `clock_event`。
- `sigma_ns` **逐帧变**（含随「距上次钟差更新时长」增长的外推惩罚）；`-1` = `SIGMA_UNKNOWN`，**不得当 0**。
- `clock_domain` 是 **1–255 数字 id**；名字与属性只在钟域表快照里。**帧头离了快照读不出「这帧属于哪个域」。**

### 4.3 `FrameAssembler` 的容差是猜的，须换掉

ATP 现有 `tolerance=0.05` 是写死的猜测值。device 侧对「这两路能不能对齐」有**唯一实现**：
`DeviceClient.pairwise_sigma_ns(a, b)`（返回 ns 或 `SIGMA_UNKNOWN`）。
**不要在 ATP 侧写第二套对齐判定**；至少记进报告，更好是用它决定容差、并在 UNKNOWN 时显式标注
「未对齐即评测」。

### 4.4 ★仿真时钟与墙钟的偏移（`sim_to_wall_offset_ns`）——自估方案不成立

device 样本的 `t_src_ns/t_ref_ns` 是**仿真戳 + 偏移映到墙钟**（为与真机 UDP 的 `rx_ns` 同域），
而 `WorldProbe.sim_time_ns` 是**仿真刻**。要把真值轨迹与 SLAM 输出对齐必须知道该偏移。

**我原打算「ATP 每局自估一次」。device 读了 `pbox_sim/gz_bridge.py` 后指出这不成立：**

- `sim_to_wall_offset_ns = wall − sim` 在**每次 `/clock` 推进时刷新**（`_on_clock`），不是常数。
  只要 gz 实时因子不恒为 1（headless 下经常 >1 或抖动），**offset 随时间漂**，
  长跑里可漂到几十 ms 以上。
- 后果不是「精度差一点」：**ATE 被污染，而且看不出来** —— 报告里没有任何字段会变红。
  这正是 ATP 刚立的那条纪律要防的形态（**「报不出来」和「没有问题」不能由同一句话表达**）。

**已知的干净解法（device 读码发现，ATP 与 simulator 都需要知道）**：
bridge 在 lidar 快照里**其实算了 `t_sim_ns`**，但 `GzLidar._NOT_FIELDS` 把它**刻意剔出** `fields`，不上线。

→ **对 simulator 的明确请求（§7.2-1）**：把 `t_sim_ns` 留在 lidar（及 foot_contact）的 `fields` 里。
`GenericSample.fields` 是自由字典，schema 合法，**不动契约**。
每帧点云自带 `(t_src_ns, t_sim_ns)` 一对 → ATP 可**逐帧精确**换算到 WorldProbe 的仿真轴，
且顺带**看见 offset 的漂移**（从不可见变成可观测，比精度本身更重要）。
IMU/odom 是强类型样本没有 `fields` 可放，但 SLAM 的 GT 对齐按点云帧就够。

#### ★更正（simulator 核查 + ATP 复核代码，2026-09-06）：要的不是 `t_sim_ns`

我上一版把请求写成「把 `t_sim_ns` 放出来」。**这是错的，原样放出去会给一个带系统偏差的 offset。**

`_wall_stamps()` 返回 `(t_src_wall, t_rx_wall, sim)`，第三项 `sim = self._latest.sim_time_ns`
是**回调被投递那一刻的最新 `/clock` 值**，不是这一帧的 header 仿真戳。
雷达 10Hz + GPU 渲染，两者在仿真轴上差「渲染 + 传输」延迟，可达几十 ms **且逐帧抖**。
它在 bridge 里的用途是接触过期判定（用「现在的仿真时刻」判 age，**那是对的**），当帧时戳就错了。

**ATP 已自行复核 `gz_bridge.py:762-779`，确认属实**（不采信转述）。

要的对齐量是 **`t_src_ns − 帧的 header 仿真戳`** —— 由 `t_src = header_sim + offset` 整数运算，
这个差**恰好等于该帧实际用的 offset，逐帧精确**。而 header 仿真戳现在没单独存进快照：
`_on_lidar` 把 `_stamp_to_ns(msg.header.stamp)` 直接喂给 `_wall_stamps` 就丢了。

**正确请求：快照多存一个键 `stamp_sim_ns = _stamp_to_ns(msg.header.stamp)`，
`_NOT_FIELDS` 不剔它。** `t_sim_ns` 维持现状继续剔除（它是内部量）。

> **这次更正本身值得记一笔**：我为了避开「自估 offset 会静默漂」，提了个
> **会静默偏**的替代方案 —— 同一个失败形态，量级更小、更难发现。
> device 读码时也没看出来。**两个人各自读代码都没发现，是第三个人逐行核出来的。**

#### ATP 复核时多发现的一处（加强了这条请求的理由）

`gz_bridge.py:778`：`t_src = (int(t_src_sim_ns) + offset) if t_src_sim_ns > 0 else wall`

**header 仿真戳为 0/缺失时，`t_src` 直接退化成纯墙钟。** 此时 offset 根本不存在，
而 ATP 若只拿 `t_src` 去减，会算出一个**量级荒谬但不会报错**的数。

有了 `stamp_sim_ns`，ATP 能**当场判出这一帧的 offset 无定义**（`stamp_sim_ns == 0`）并标注；
没有它，就只能静默算出垃圾。**所以这个键不只是提精度，它让退化情形从不可见变成可判**
—— 与 §2 「报文要能发现自己漏了东西」是同一条。

**在此之前，ATP 不得声称 device·sim 面的 SLAM 绝对精度可信**，只能做相对比较。

## 5. ATP 侧必改清单

### 5.1 已发生的错（`pipe.slam.device` 从未真跑过，converter 是照 ROS PointCloud2 猜写的）

| # | 现状 | 事实 |
|---|---|---|
| 1 | 订 `/device/source/<id>/data` | device 发 **`/device/data/<source_id>`**；STATUS `/device/status`、INFO `/device/info`、CMD `/device/cmd`。**无通配订阅** —— 先从 INFO `source_list` 拿清单、按 `role` 筛、再逐路订 |
| 2 | 自行解析 tzcomm 信封 | 信封 `{v,topic,port,rt,kind,id,seq,tx_ns,src,payload}`，样本在 `payload`；`tx_ns` 仅传输诊断、**永不进时戳**。用 `DeviceClient.subscribe()`，回调 `on_sample(source_id, kind, payload)`，信封已剥 |
| 3 | `by_name[n]["datatype"]` + `raw["data"]` | 载荷是 `GenericSample`：字节在 **`raw`**；`fields["fields"]` 是**压平字符串** `"x:0:7:1,y:4:7:1,..."`（名:偏移:PointField枚举:count）；`point_step/width/height/...` 是标量。两处均 KeyError |
| 4 | `latin-1` 容错解 raw | raw 编码随传输而异：msgpack 上是原生 bin，JSON（录制/legacy）上是 **base64**。统一走 `serde._bytes_from_wire` |
| 5 | IMU `raw.get("angular_velocity", {})` 当 dict | 是 **list `[x,y,z]`**（现状会静默得到全 0）；`orientation_quat_xyzw` **缺失即键不出现**（不发 null）；`ImuSample` 内**无 `source_id`**，身份只在 topic |
| 6 | 时戳回退 `time.time()` | device 保证每帧必有 `ts`；**该回退分支不该存在**，缺了应报错。仿真里混进墙钟会把帧间隔拉平成节拍 |

### 5.2 接 simulator 时必踩

| # | 事项 |
|---|---|
| 7 | **有序距离图**：`device_sim_quad.yaml`（Airy）发 `range_u16/v2` —— `fields` 里**没有 x/y/z**，只有 `range/intensity` + 六个扩展键。xyz 解码器**必须按字段名分支**（查不到 x/y/z 就走距离图路径），**不能按 `pc_layout` 判**（那是审计标签）。只有 `device_sim_mid360.yaml` 是 18B 的 xyz 布局 |
| 8 | **mock 无点云**：`device_mock.yaml` 雷达只发 `lidar_preview.v2` 标量，无 `raw`。冒烟拿不到点云，须用 simulator 或 `replay` |
| 9 | **DATA 传输两端必须一致**：逐路 `udp`（组播/可多订阅/跨机）或 `tcp`（本机回环、**单连接槽**）。device 侧当前默认 tcp、雷达三路显式 tcp。**两个消费者同订一路 tcp 会各拿一半流量且不报错**（实测 200Hz → 98+98）。udp 上 >1200B 分片、丢一片整帧丢、整帧上限 1MB |
| 10 | **配置选错则判据全假**：三份 velocity_control 配置下本体**掉不下去也翻不过来**（不受重力），稳定性/倾覆/通过性判据全是假数据；导航评测**只能用 `device_sim_nav.yaml`（policy）**。另三份雷达垂直 FOV 全在水平面以上，看不到地面 |
| 11 | **odom 已是六自由度如实转发**（`feature/dynamics`），**不要假设 z/roll/pitch 为 0** |
| 12 | **暂停不是故障**：`WorldProbe.pause` 时 `/clock` 照发但 sim 冻住，device 报「仿真已暂停」而非掉线 |
| 13 | **`delivery: SHM` 的源现仍走 DATA 口**（device ISSUE C3），接真雷达时点云会灌进控制面。现状不改，需知悉 |
| 14 | **`fields` 里同时装着两类东西**：概览标量（`point_count/valid_ratio/range_min/max`、`sector_00..11`）与结构描述（压平的 `fields` 串、`point_step/width/height/...`）。`sector_*` 是**便利概览，不是判据**，别拿它替代解点云 |
| 15 | **`foot_contact` 是 `generic` 但无 `raw`、无 `pc_layout`** —— 不要按「generic 必有 raw」写解码分支 |
| 16 | **`body_odom` 的 `origin` 在仿真里是 `GROUND_TRUTH`**（本体被直接施加 twist）。但 `cov_origin` 不是 `TZ_CHARACTERIZED`。**绝不可当 GT 用**：GT 只走 `WorldProbe`。这条与 §6-1 是同一条原则 |
| 17 | **`STAIR` 档没有 `vy`**（`cmd_dof=[vx,wz]`，`vx∈[-0.5,0.8]`）；`FLAT` 三自由度全有。**结果状态要写精确**（device 更正）：不在 `cmd_dof` 的维**置零 + `clamped_mask` 置位**（bit1=vy），回执是 **`CLAMPED` 不是 `REJECTED`**，`applied[1]==0.0`。判据写成 `ack.status=="CLAMPED" and clamped_mask & 0b010`。`REJECTED` 只出现在：状态机门控（非 ACTIVE/DEGRADED）、未声明档位、adapter 下发失败；仲裁被抢占是 **`PREEMPTED`** |
| 18 | **切档存在包络的中间态**：切档回执 ACCEPTED 之后、STATUS `pending_terrain_mode` 清空之前，**包络仍是旧档的**（mock/replay 当场落定，gz/真机不一定）。发新档指令前必须等 `pending_*` 清空（§5.3） |

### 5.3 闭环下行（CMD）语义

- 载荷必须**自带** `arbiter / seq / stamp_ns` —— `DeviceClient.send_cmd` **不补**
- CMD 口**同一时刻一个调用方**，按信封 `src`（节点名）认，租约 1.5s
- `VelocityCommand` 只在 `ACTIVE/DEGRADED` 被接受，**且它本身就是心跳** ——
  停发超过 `T_hb`（默认 200ms）本体落 DEGRADED 保护性停。
  **须持续以 ≥20Hz 发；松手要发零速，不是不发**
- **回执答「受理」不答「完成」**；`CLAMPED`/`REJECTED`/`clamped_mask` 要进 records，**不得当 ACCEPTED**
- 切档/切模式：看 STATUS 的 `pending_mode`/`pending_terrain_mode` 清空且 `terrain_mode` 变了，
  再重读 INFO `capability` 的包络（`DeviceClient` 内部已自动重拉）。切档后 `vy` 可能消失、`vx` 可能出现下界
- 超时回执**必是 `REJECTED`**
- `stamp_ns` 用调用方的钟；跨机钟差接近 `T_hb` 时看门狗失真（device ISSUE C1，已知未修）

### 5.4 闭环模型不匹配

ATP 的 `IWorld.step(action)` 是**一观测一动作锁步**；device 面是**实时流 + 心跳看门狗**。
→ 先按 `StreamWorld` 实时形态接（同现有 `DeviceWorld`），**action 由后台线程按 ≥20Hz 续发**。

---

## 6. simulator 独有、不在 device 面上的三样

**设计上故意如此**，ATP 须单独对接：

| # | 能力 | 入口 | 为什么不在 device 面 |
|---|---|---|---|
| 1 | **世界真值 + 世界控制**（真值位姿 / `set_pose` / `pause`/`step` / `reset` / `spawn`） | `pbox_sim.gz_bridge.WorldProbe` | **原则问题**：设备观测不到自己的世界系位姿，注册成 Source 就是**伪造能力**；评测拿被测的 odom 当裁判是**自证** |
| 2 | **场景导入/装配** | `pbox_sim.scene`（`build_scene` / `build_world` / `SceneManifest`） | 离线工具链，不需要仿真在跑 |
| 3 | **劣化注入**（时戳抖动/丢包/下线某路） | `sim_config.yaml` | **配置期**生效，device 打开时读一次，非运行时开关。帧率/FOV 退化不走它（那是「换一颗雷达」，改型号 yaml 重生成模型） |

> 第 1 条的论证与 ATP 的 GT 纪律（**真值只交给 checker，永不进算法观测**）是同一条原则的两侧表述。

### 6.1 等价性边界

**只有 A 级标准 role 源（IMU / ODOMETRY / LIDAR）+ capability 是可移植的。**
B 级 CUSTOM 源（sim 的 `foot_contact`、S10 的四路 ASDU 流）每个 backend 各不相同，**ATP 不得依赖**。

### 6.2 GT 有两种，转换在 ATP 侧做

| 类型 | 来源 | 对应 ATP checker |
|---|---|---|
| **静态几何** | `SceneManifest`（`scenes/<id>/scene.yaml`）：障碍表（⚠ **`obstacles_complete` 为 false 时不是全集**）、bounds、`start/goal_candidates`、`regions`、`connectivity` | nav 类（与 `nav2d.NavGoal` 的 `{goal, obstacles}` 同构，只是障碍是带位姿的 box/cylinder/sphere） |
| **动态轨迹** | `WorldProbe.latest_pose()` 序列 / `truth_tap.py` jsonl（`t_sim_ns, x,y,z, qx..qw, yaw, seq`） | SLAM APE 类 |

两者都**不是** `{schema, v, data}` 信封 —— **转换在 ATP 侧做**，落点是 World 子类的
`get_ground_truth()`：`encode_ground_truth("pipe.slam.Trajectory", {...})`。
这是「simulator 不做评测」的分工，**不是缺口**。

### 6.3 body 外参不该由 ATP 手写

ATP 现有 `body/pbox_v1.yaml` 写的是 lidar front/rear + 相机 + 差速轮，
**与 sim 任何一份配置都不对应**（nav 主线只有一路 `lidar_box`，低头 30°）。

**外参的真值源是 device INFO 口的 `description`**（`extrinsics[]`，与 `sources[]` 的 `frame_id` 对齐）；
sim 在加载期还会拿 `scene/info` 对账。**ATP 的 body 应从 INFO 派生，或至少校验与 INFO 一致。**

> 佐证：ATP 直到 2026-09-05 才发现自己**从未校验过**「body 声明的传感器实例」与「数据实际给的实例」
> 是否一致 —— 契约写了要校验、代码没实现，仓内 3 个用非空 body 的场景 3 个都不一致。
> body 这块在 ATP 侧本就是薄弱环节。

---

## 7. 待决

### 7.1 已答复（本轮闭环）

- **(a) replay / mock 的确定性 → 已答，见 §2.1。** 结论比预期好：
  开环 replay **经 wire 也逐位**（内容），闭环 mock **进程内逐位**。
  → ATP 的严格回归档**不必等任何一侧改代码**就能建立。
- **(b) 真实样例 → 已收到**，见 §8。六条「已坏」逐条复核属实，另读出四条新事实（§5.2 #14–17）。

### 7.2 需 M 决策的候选（涉及改动已验证侧，ATP 不自行推进）

| # | 候选 | 提出方 | 我的建议 |
|---|---|---|---|
| **1** | **simulator 在 lidar（及 foot_contact）快照里新增 `stamp_sim_ns = _stamp_to_ns(msg.header.stamp)`，`_NOT_FIELDS` 不剔它**（`t_sim_ns` 维持现状继续剔除） | device 读码提出 → **simulator 核查后更正了字段** → ATP 复核代码确认 | **建议做。** 改动面：`gz_bridge.py` 两处回调各一行 + `sources.py` 一行 + `lidar_frame.v2.schema.json` 一条**可选**字段；**不碰契约层、不碰线格式**。三方各自查证均无副作用：`GenericSample.fields` 是无属性约束的 `object`，`Shaper` 只裁顶层键，`validate.py` 不逐样本按字段表校验，测试未钉键集合。收益有两层：① GT 对齐逐帧精确；② **`stamp_sim_ns==0` 时能判出 offset 无定义**（§4.4），退化情形从不可见变成可判。simulator 表示若批准可当场做并补一条测试钉住「它等于 header stamp 而不是 `/clock`」 |
| 2 | **契约级**：把仿真钟当独立钟域，`t_src` 保留仿真戳而不映到墙钟 | device | 这才是「`t_src` 永不改写」的本意，但**动契约语义 + simulator 时间模型**。仅在候选 1 被拒时才提 |

> **候选 1 若批准，ATP 侧的使用约束（simulator 提，ATP 接受并写进代码注释）**：
> `stamp_sim_ns` 是**真机没有的诊断键**，不是能力。**只许在 GT 对齐这条仿真专属路径上读它**，
> **不得进入 backend 无关的解析路径** —— 否则切到真机时那条路径会**静默失去对齐**。
> 这与「仿真只许更保守」不冲突（诊断键 ≠ 多给能力），但边界要守死。
| 3 | `Check` 增结构化 `skip_kind`（`not_applicable`/`missing_input`/`insufficient_data`） | simulator | **不必等。** ATP 按 id 集合推断即可（§3.2） |
| 4 | 四平台收件箱身份表加 simulator / device | ATP | 现走 session 消息：可行，但**不留档、不可审计**。三条查证纪律要求「结论要写进契约必须有对方确认」，而 session 消息给不出可回溯的回执。工具归 Hub owner |

### 7.3 ATP 侧待办（本轮不做，定稿后执行）

| # | 事项 | 依赖 |
|---|---|---|
| 1 | 重写 `DeviceWorld` 为 `DeviceClient` 消费者；退役 device 来源的 `DatasetWorld` | 无 |
| 2 | 重写 `plugins/pipe.slam/convert.py`（§5.1 六条 + §5.2 #7/#14/#15 分支） | 样例已到，**可立即做** |
| 3 | **去掉 `_stamp_from_dict` 的 `time.time()` 回退，改为报错** | 无。**这条不是精度问题，是 replay 严格档的前提**（§2.1 前提 2） |
| 4 | 观测信封增 `ts` 七字段（可选）+ 评测级 INFO/STATUS 快照落 artifacts | 无 |
| 5 | `FrameAssembler` 容差改用 `DeviceClient.pairwise_sigma_ns` | 无 |
| 6 | body 改为从 INFO `description` 派生或校验 | 无 |
| 7 | data_check 接入前置 + 「应出结论 id 集合」核对 | 无 |
| 8 | **教程第 4 关 §4.3 ⑧ 确定性纪律加限定**（现写成无条件的，是错的） | 无，**优先**：现状在教错 |
| 9 | 建**严格回归档**：开环 SLAM 走 wire+replay(tcp)；闭环控制走进程内 mock（带 `kinematic-only` 标记） | 依赖 1–3 |
| 10 | sim 档改统计一致 + 容差带（须先固定 §2.3 六项） | 依赖 9 建立对照 |

---

## 8. 样例（已入库，可复跑）

device 侧用**真实 codec 路径**产出（非手写）：`try_open(config, overrides={"backend":"mock"})`
取 INFO/STATUS（这四组只取决于配置，与 backend 无关）；点云用 simulator 线上真用的
`pbox_sim.pointcloud.pack_compact / pack_range` 打包，经 `codec.sample_to_wire` + `Shaper` 出线。

**落库**：`tests/fixtures/device/sim_mid360.json`、`tests/fixtures/device/sim_quad.json`
（顶层 `config / INFO / DATA / STATUS / notes`；DATA 按 `source_id` 索引，每条含
`topic / kind / payload / envelope_example`）。

**ATP 已逐条自查通过**（不轻信转述）：`fields` 确为压平串、字节在 `raw` 无 `data` 键、
IMU `angular_velocity` 确为 list 且无 `source_id`、`ts` 确为七字段、
`sim_quad/lidar_front` 确无 x/y/z（`range_u16/v2`）而 `sim_mid360/lidar_box` 有
（`xyz_ns_u32le_intensity_tag/v2`）。

**两处与真 gz 跑不同（用前须知）**：① 点坐标是合成的；
② `sim_quad` 只锁了 `sim_lidar_front`，故样例里 `sim_lidar_rear` 是**失锁态**
（`effective_sigma_ns=-1`），真 gz 跑两路都锁 —— 但这**反而是个好样本**：
`SIGMA_UNKNOWN` 与降级自检（STATUS 里那条 `TIME/SYNC_TIER_UNMET` fault）长什么样，
ATP 可以照它写解析与告警，不必等真机上出故障才发现自己没处理。

**用途**：converter 的回归 fixture（不需要 device 在跑）+ §3.2 门禁判据的样本。

### 8.1 建 replay 严格档前必须知道的三条（device 提供）

1. **replay 只吃 device 侧 `Recorder` 产出的 JSONL**（头行 `k:"header"` 带 `clock_snapshot` /
   `config_source`，随后 `sample` / `cmd` / 事件行）。产出：配置 `record.enabled: true`
   或 `tools/device_cli.py record <config> -o run.jsonl`。
   **rosbag 不能直接喂 replay** —— 那条仍走 ATP 自己的 `DatasetWorld`。
   > 这一条修正了我 §1.1 里「device 来源的 `DatasetWorld` 全部退役」的说法：
   > **rosbag 那条留着**，退役的只是「ATP 自己扒 device 数据当 dataset」那条。
2. **录制默认 `record.raw.mode: sidecar`**：点云字节在旁路 `<name>.jsonl.pc.bin`，
   JSONL 行里只有 `{file,offset,size,codec}` 引用。
   **拷 fixture 时两个文件必须一起走**，缺了读端抛 `RecordingError`。多分片才有 `.meta.json`。
3. **tape 放完后的 `SOURCE_TIMEOUT` 是「放完了」，不是故障。**
   `DeviceServer.open()` 对 replay 同样跑 `self_check + activate`；`ReplayBody.tick` 不跑心跳看门狗。
   **ATP 的 World 判「本局结束」要以样本停止为准，不要以那条故障为准**
   —— 否则每局 replay 都会以一条 fault 收尾，报告里天天挂着假故障。

## 变更记录

| 日期 | 变更 |
|---|---|
| 2026-09-06 | 草案 v0.1。三方两轮问答成文；ATP 侧未做任何代码改动。待补 §7.1。 |
| 2026-09-06 | **v0.3**：三方交叉核对。§4.4 **我的请求字段被 simulator 更正**（`t_sim_ns` 是投递时刻的 `/clock`，非帧 header 戳，会带系统偏差）→ 改为 `stamp_sim_ns`；ATP 复核代码确认，并另发现 header 戳为 0 时 `t_src` 退化为墙钟（加强该请求）。§2.1 replay 措辞更正（`ts` 原样还原非重算）、mock 不确定性补两条成因。§5.2 #17 更正为 `CLAMPED`（非 `REJECTED`）+ 新增 #18 切档中间态。新增 §8.1 replay 严格档三条前置。 |
| 2026-09-06 | **v0.2**：两侧答复到齐。§2 重写——分档轴改为「谁驱动 `now_ns`」，并调和 device/simulator 的时序-内容粒度差，得出**开环 replay 经 wire 亦逐位**；§4.4 **自估方案被证伪**，改为向 simulator 提 `t_sim_ns` 请求；§5.2 增 #14–17；新增 §8 样例已入库并逐条自查。 |
