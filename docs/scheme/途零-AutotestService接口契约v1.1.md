# 途零 · Autotest Service 接口契约(v1.1 · 重构版 · 待评审)

> 本文档定义 Patrol Box **统一自动化测试服务(autotest service)**的架构与接口契约。
> **v1.0 → v1.1 三项结构性修订(2026-08-18 评审反馈)**:
> ① **核心/模块分离**:核心契约不再含任何具体算法的数据 schema;算法模块为自包含插件(§7)。
> ② **数据面 schema 标签**:observation/action/result 的 data 统一为 `{schema, v, enc, blob}` 信封,补齐 tzcomm 应用层的协议绑定(§4)。
> ③ **本体 profile**:urdf/传感器布局独立成注册资产,场景显式引用(§8)。
> 另:联合测试 suite 立为扩展点(§12)。
>
> 定位、两种评测模式、时钟/RESET 语义、tzcomm 传输、Client/CI 边界**沿用 v1.0**,本文档为完整替代版。

---

## 1. 定位与设计原则

把测试从"散落的测试脚本"升级为**数据源透明的评测服务**(OJ/judger 哲学的时序化+闭环化机器人版本)。

**第一原则(本版确立):autotest 核心只处理交互逻辑,不处理数据本身。**
- 核心定义:会话怎么建立、帧怎么推、结果怎么收、分怎么打、痕迹怎么留。
- 核心**不出现**任何具体算法名、传感器字段、评判指标——这些全部归属算法插件。
- 新增一类算法测试 = 新增一个插件包,核心代码与核心契约**零改动**。

其余原则不变:算法零修改(三源透明)、三方解耦(World/checker/SUT)、可复现(时钟受控)。

### 1.1 两种评测模式(一套协议)

| 模式 | 适用 | SUT 回复 |
|---|---|---|
| A · 开环回放 | SLAM 等 | `RESULT`(估计量) |
| B · 闭环交互 | Nav / Manip 等 | `ACTION`(控制量) |

模式由 SUT 回 RESULT 还是 ACTION 自然区分;插件可两者都支持。

---

## 2. 分层架构

```
┌─ Client(python -m autotest.client:run / matrix / report)──────────┐
├─ Autotest Core(常驻 service,python -m autotest.server)────────────┤
│   protocol(信封/消息/话题) · world(IWorld/回放/实时流/同步)        │
│   eval(Runner/ClosedLoop/IChecker/Loader) · server(job池/留痕)     │
│   launcher(宿主/docker+bind) · registry(插件注册表)                │
│   ※ 本层无任何算法知识                                             │
├─ 插件层 plugins/<命名空间>/(§7,每算法一个自包含包)─────────────────┤
│   contract.md · schemas · data/datasets/checkers(/sim) · scenarios │
├─ 本体资产 body/<name>.yaml(§8,多插件共享)─────────────────────────┤
├─ SUT(算法进程/容器,黑盒):只见协议 + tzcomm ──────────────────────┤
└─ 底层通信:tzcomm(§3)─────────────────────────────────────────────┘
```

**进程模型(冻结)**:Service 常驻,按 manifest 拉起 SUT;环境变量注入 `AUTOTEST_SESSION` / `AUTOTEST_TOPICS`;`image` 字段决定宿主直启或 docker+bind(`--network host -v <算法根目录>:/workspace`);每个 job = 独立 session(`autotest-<8位hex>`)。

---

## 3. 传输层(冻结:tzcomm)

底层通信唯一使用 **tzcomm**(纯 Python、仿 ROS2 API、daemon+Redis 发现层)。SUT 唯一依赖是 `tzcomm`,**不依赖 ROS2**;ROS2 生态经 bridge 接入(§7.5)。

### 3.1 通道 ↔ 通信方式映射(冻结)

| 通道 | tzcomm 方式 | 理由 |
|---|---|---|
| `ctl`(init/reset/terminate) | Service(TCP 请求-响应) | 确认语义,fail fast |
| `obs`(观测推流) | SUBPUB **qos=1**(TCP) | 点云超 UDP 1MB 帧上限;可靠有序;docker/跨网段可路由 |
| `result`/`action` | SUBPUB(默认 UDP,`single_pub=True`) | 小消息低延迟;大结果(地图等)插件可声明 qos=1 |
| `autotest/control`、`autotest/job/status` | Service | Client 提交/轮询 |

### 3.2 序列化分层(本版明确)

- **wire 层(tzcomm 内部,不可见、不改动)**:UDP 帧 `[topic, seq, ts_ms, payload(bin)]`;TCP 帧 `[4B长度][msgpack dict]`。seq/ts 服务于丢包统计与分片重组。
- **应用层(本契约管辖)**:publish 前由发送方把对象编码为 msgpack(dict 或 bytes 均可,bin 原生支持)。**tzcomm 的 publisher 不绑定任何消息协议**——协议绑定由本契约 §4 的 schema 标签在应用层补齐。
- 控制面消息(§5 九条)载荷小、演化快,保持 msgpack dict;数据面(§4)走 schema 标签。

### 3.3 部署约束(硬性)

1. UDP 组播 TTL=1、docker 桥接网络不通组播 → SUT 容器必须 `--network host`;跨机数据面一律 qos=1 TCP。
2. Redis + tzcomm daemon 随评测宿主机常驻(systemd);业务进程只连 daemon,**禁止直连 Redis**。
3. 节点 5s 心跳、15s 清理——SUT 崩溃由 daemon 发现,Service 据此判失败。
4. 评测执行面(Redis/daemon/server/runner/SUT)同机;Hub/PMS 在管理面机器,**不感知 tzcomm**,唯一跨机接口是 workflow 回调(§11)。

---

## 4. 数据面 schema 机制(本版新增,冻结)

### 4.1 动机

tzcomm 的 publisher 只绑 topic,不绑协议(弱协议);机器人端协议必须强对齐(device/sim/real 同 schema、未来 C++ 原生接入)。在应用层补协议绑定,不动 tzcomm。

### 4.2 data 信封(冻结)

observation/action/result 的 `data` 字段统一为:

```python
{
  "schema": "pipe.slam.SlamObs",   # 命名空间.类型名,插件注册
  "v": 1,                          # schema 版本(插件内递增)
  "enc": "msgpack",                # 编码:msgpack(dict blob)| pb(protobuf bytes blob)
  "blob": { ... }                  # enc=msgpack 为 dict;enc=pb 为 protobuf 序列化 bytes
}
```

- **observation 外层**不变:`{timestamp, module, data}`(module = 插件命名空间)。
- **注册** = 插件调用 `register_data(kind, schema_id, decoder)`;核心按 `(kind, schema)` 分派解码。
- **校验时机(冻结)**:会话建立时核心已知 module→schema 映射;收发双侧遇未知 schema / 版本不兼容**拒收并记error**(不静默透传)。唯一例外:核心转储/调试工具可按原样透传。
- **enc=pb 路径**:`.proto` 文件随插件存放,是跨语言事实源;msgpack 的 bin 类型承载 bytes,tzcomm 零改动。**现阶段默认 enc=msgpack**(Python dataclass 为 schema 单一来源,代码即契约);C++ 原生 SUT(无 ROS2 bridge)接入前,对应插件必须补 .proto 并切 enc=pb。

### 4.3 演进规则

schema 内加字段 → `v` 不变(消费者忽略未知键);改语义/删字段 → `v+1` 且插件须同时注册新旧 decoder 一个过渡周期。

---

## 5. 算法 ↔ Service 协议(核心,冻结)

### 5.1 消息信封

```python
{"type": str, "session_id": str, "payload": dict, "seq": int|None}
```

`seq` 为数据帧序号(推流自增 0..n-1,丢帧对账);控制消息为 None。非法 type / 空 session_id 直接抛错。

### 5.2 消息定义(九条,不变)

| type | 方向 | 通道 | payload | 响应 |
|---|---|---|---|---|
| `init` | Svc→SUT | ctl | `{sensor_config, body, hyperparams}` | `ready` |
| `ready` | SUT→Svc | ctl 响应 | `{module, version, required_sensors}` | — |
| `reset` | Svc→SUT | ctl | `{testcase_id}` | `reset_ack{ok}` |
| `step` | Svc→SUT | obs | `{done, observation}` | 异步 `result`/`action` |
| `result` / `action` | SUT→Svc | result/action | `{module, data(§4.2)}` | — |
| `terminate` | Svc→SUT | ctl | `{reason}` | `final{self_stats}` |

**握手校验(本版强化)**:`init.sensor_config` = `{类型: {实例名: topic}}`,实例名与 §8 本体声明对齐;`ready.required_sensors` 缺实例 → fail fast 终止评测。`init.body` 下发本体 profile(§8),SUT 据此获得传感器外参等。

### 5.3 RESET 语义(冻结)

RESET = 清运行状态、保模型/参数/权重,跨 testcase 复用实例;换超参/版本/组件 = 走完整 INIT 新会话。算法实现 `on_reset()` 恢复干净态,禁止状态残留。

### 5.4 时钟约定(冻结)

`observation.timestamp` 为唯一权威时间,算法禁读墙上时钟。`clock_rate`:默认 1.0(回放源按原始帧间隔实时复现,保留波动);>1 加速;<1 减速;0/缺省 = 全速(仅冒烟)。实时源(device/rostopic)自带节奏,clock_rate 不生效。结果收集超时 30s。

---

## 6. World 接口(核心,冻结)

```python
class IWorld(ABC):
    realtime: bool = False
    testcases -> list[str]                        # 实时源通常 ["live"]
    reset(testcase_id) -> Observation
    step(action=None) -> (Observation|None, done, info)   # 开环忽略 action
    get_ground_truth() -> GroundTruth
    close()
```

- **DatasetWorld**(回放)/ **SimWorld**(闭环,step 消费 action)/ **StreamWorld**(实时流基类:帧缓冲、`idle_timeout` 默认 5s、`max_frames`)。
- 多实例时间戳对齐:FrameAssembler 按容差窗口(默认 0.05s)对齐声明实例;IMU 取最近帧。
- **GroundTruth(本版修订)**:`GroundTruth = {"schema": str, "v": int, "data": dict}`——GT 也带 schema 标签,由产出它的 World 声明、由消费它的 checker 解析,核心不感知内容。

---

## 7. 插件规范(核心定义的"如何写一个算法模块",冻结)

### 7.1 插件包结构(目录约定)

```
plugins/<命名空间>/            # 如 pipe.slam / nav2d / manip.force
├── contract.md              # 模块契约:数据 schema 字段级定义、指标与阈值、数据源清单、本体要求
├── data.py                  # register_data:observation/action/result 的 schema + decoder
├── datasets.py              # register_dataset:dataset/sim/real 数据源工厂
├── checkers.py              # register_checker:评判插件
├── sim.py                   # 可选:闭环 SimWorld
├── scenarios/*.yaml         # 预置场景
└── proto/*.proto            # 可选:跨语言 schema(enc=pb 时必填)
```

### 7.2 注册与归属校验

- 三件套注册:`register_data` / `register_dataset` / `register_checker`,键一律带命名空间(如 `pipe.slam`)。
- **归属关系(冻结)**:datasource 声明 `produces=[schema_id...]`;checker 声明 `consumes=[schema_id...]` + 兼容 module 集;场景装配时核心校验 `produces ⊇ consumes`,不匹配 fail fast。checker↔data↔算法的相互归属经命名空间确立,不挂靠在核心。
- 场景省略 `checker` → 只做数据流验证(不评分)。

### 7.3 模块契约(contract.md)必备要素

① observation/action/result 的 schema 字段级定义(含 v);② checker 指标定义与默认阈值;③ 数据源清单与各 config 字段;④ required_sensors 与本体要求(§8);⑤ 预置场景说明;⑥ 版本与变更记录。

### 7.4 算法接入(契约面)

算法仓库根目录放 manifest `scenario.yaml`,二选一实现协议:原生 tzcomm(读 `AUTOTEST_SESSION`/`AUTOTEST_TOPICS`)或 SutBase SDK(覆写 `on_init/on_reset/on_step/on_terminate`)。

```yaml
module: pipe.slam            # 插件命名空间(必填)
launch: "python3 main.py"    # 启动命令(算法根目录/容器 /workspace 执行)
scenario: <路径>             # 可选,建议场景
required_sensors: {lidar: [front, rear]}   # 与本体实例名对齐
hyperparams: {...}           # 经 INIT 下发
image: <镜像名>              # 可选,docker+bind
output_topic: <topic>        # 可选,ROS 侧自有产物
```

### 7.5 ROS2 算法接入(bridge 范式)

bridge 进程做 tzcomm↔ROS 双向转换(算法侧文件,不 import 框架;INIT.sensor_config 给出 ROS 话题映射)。模板 `examples/ros2_pipe_bridge.py`。ROS2 依赖隔离在算法容器内;实机无 ROS 环境另评估 tzcomm `bridge/ros2_dds`(现阶段不押注)。

---

## 8. 本体 profile(body,本版新增,冻结)

**动机**:测试语义依赖本体——传感器安装位置/外参/规格与算法模块解耦(多算法共享同一本体),dataset 可能缺标定,必须给预定义。

```yaml
# body/pbox_v1.yaml
name: pbox_v1
urdf: <路径或包内引用>          # 可选但建议
sensors:                        # 实例名 → 布局(与 sensor_config / required_sensors 同名对齐)
  lidar:
    front: {frame: base_lidar_front, extrinsics: {xyz: [...], rpy: [...]}, spec: {...}}
    rear:  {frame: base_lidar_rear,  extrinsics: {...}}
  imu:
    imu:   {frame: base_imu, extrinsics: {...}}
```

- 注册资产:`register_body(name, profile)`;多插件/多场景共享。
- **场景必须引用 body**(`body: pbox_v1`);核心经 INIT 下发给 SUT(§5.2)。
- **一致性规则**:dataset 自带标定/外参时以 dataset 为准,但与 body 声明的实例集校验(缺失/多余即报错);sim/real 必须提供完整 body。
- World/converter 可消费 body(如按外参做坐标变换、按 spec 校验数据维度)。

---

## 9. 场景配置(核心格式,冻结)

```yaml
module: pipe.slam             # 插件命名空间
body: pbox_v1                 # 本体 profile 引用(必填)
dataset:
  type: pipe.slam.rosbag      # 带命名空间的数据源名
  config: {root: ..., topic_map: {...}, gt_dir: ..., max_frames: 5000}
checker: pipe.slam.ape        # 可省略(=数据流验证)
checker_config: {...}         # 阈值覆盖
hyperparams: {...}            # 算法超参,经 INIT 下发
```

---

## 10. Client 控制接口(沿用 v1.0,冻结)

- `autotest/control` 提交:`{manifest, scenario?, checker?, checker_config?, clock_rate?}` → `{job_id}`;`autotest/job/status` 轮询 → `{status, results, error}`。
- CLI:`run / matrix / report`;`--json` 供 CI;失败也写 report.json。
- matrix:`algorithms:` 条目列表(manifest + 可选覆盖),逐条提交聚合;同算法跨 testcase RESET 复用,多版本对比走多条目。
- 留痕:`artifacts/{job_id}/{report.json, session.log}`;回归:基线按 testcase 对齐逐指标对比(指标越小越好,passed 翻转优先判 improved/regressed)。

---

## 11. CI/CD 对接(沿用 v1.0 + 总契约修订)

- 现状:分支 push(`autotest`/`mannultest*`)或 dispatch → runner 上 `client run --json` → report.py 发 Issue/webhook。
- Hub 编排(Hub Phase 0/1):workflow_dispatch 入参 `correlation_id`/`check_type`;末步 POST Hub `/api/ci/callback`(Bearer)。
- **本版修订(承接总契约评审)**:**回调必须携带实际 checkout 的 `sha`**、`conclusion`、`finished_at`(手动通路下触发时 sha 不定,不补则 PMS 按 sha 查询链断裂);手动测试分支约定为 `mannultest/*` 前缀(可 per-user/per-run),不锁定单一 `mannultest` 分支。
- autotest 本体不因 Hub 改动;对 Hub 只暴露 workflow 回调一个应用层接口。

---

## 12. 联合测试 suite(扩展点,机制预留)

多模块联合测试(如 slam 输出喂 nav、整机多算法并行考核)定义为 **suite**:

- suite yaml:有序/并发的多模块场景引用 + **共享 body** + 可选数据管道声明(上游模块 result schema → 下游模块 observation schema 的适配器,适配器归属上游插件或独立 suite 插件)。
- 核心预留:一场景多会话编排、跨会话数据转储;**细节随 Phase B/C 实践冻结**,当前版本不实现、不阻塞单模块路径。

---

## 13. 交付批次、现状与重构迁移

| 批次 | 内容 | 状态/动作 |
|---|---|---|
| A | **收口+重构**:补 `protocol/data` 缺失包 → 核心去算法化(module 常量化移除、registry 命名空间、§4 schema 信封)→ `modules/` 迁 `plugins/`(pipe.slam、nav2d 两个插件包 + 各自 contract.md)→ body 机制 → 契约 v1.1 评审冻结 | 本版启动 |
| B | nav 闭环验证;manip 插件(作为"新建插件流程"首个验证);暂停/单步 | 待做 |
| C | device action 下发回路(RealWorld 双向);body↔device 契约对齐;三源一致 | 待做 |
| D | Hub 对接(§11 修订后);baseline 接入 CI | 待做 |
| E | suite 联合测试;enc=pb + .proto(C++ 接入前) | 方向性 |

---

## 14. 变更纪律

1. 核心契约与模块契约**分别版本化**:核心变更走"动机+影响面→M 批准→通知下游→版本递增";模块契约由模块 owner 管,影响核心接口时须回写本文档。
2. 协议实现单一来源:`autotest/protocol/`;schema 单一来源:各插件 `data.py`(enc=pb 时为 `.proto`)。
3. 文档与实现不符时,以实现为事实源回写文档并记变更。
