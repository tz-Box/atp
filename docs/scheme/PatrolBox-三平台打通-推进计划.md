# Patrol Box · 三平台打通推进计划(v1 · 2026-08-19)

> **基准契约**:《三平台通信与接口总契约 v1.3》(冻结,最高基准)、《Autotest Service 接口契约 v1.1》、《Hub 技术路径规划》。
> **目标**:三平台(PMS / cicd-hub / Autotest)全链路打通,v1.3 §3 **四条通路**端到端可测。
> 前期不求功能全面,但架构、解耦性、模块扩展性必须清晰;扩展性在打通后补齐。
> 本文档为任务追踪事实源:状态列随开发更新,调整走 §6 变更记录。

## 1. 决策记录(2026-08-19,五项,已拍板)

| # | 议题 | 结论 |
|---|---|---|
| D1 | 打通范围 | v1.3 §3 **四条通路全部**打通(非两条) |
| D2 | D4 中继 | **废弃**。PMS 全部按 v1.3 新协议改造,不留中间版本;协作事件一律走 §4.4 events。**遗留**:PR merged→交付物冻结 无事件载体 → 待决 W1 |
| D3 | autotest 分支 push 触发 | **废弃**。一切触发经 Hub `workflow_dispatch`(必带 cid);workflow `on` 只留 `workflow_dispatch`(mannultest 分支仅作代码载体,不自动触发,防绕过 cid) |
| D4 | Hub 工程 | 新 repo `tz-Box/cicd-hub`(简称 hub),废弃 tz_cms 命名 |
| D5 | manip 模块 | **删除**;v1.1 批次 B 按"新建插件流程"重建,作为新流程首个验证 |

## 2. 冲突处置结论(以 v1.3 为准,详表见评审记录)

- **A 类 · CI 链路(Autotest 侧硬缺口)**:`examples/ci/autotest.yml` 触发器/inputs、
  `examples/ci/report.py`(Issue+INNER_WEBHOOK)整体重写为 Hub 回调模式 → Phase A2。
- **B 类 · Autotest 内部架构落差**(= v1.1 批次 A 未完成项):data 信封、未知 schema 拒收、
  module 去常量化、modules/→plugins/ 命名空间化、produces/consumes 校验、body 机制、
  GroundTruth 带标签、protocol/data 迁出核心 → Phase A1。
- **C 类 · Hub 规划文档落后**:实现范围以 v1.3 §4/§8.2 为准(该文档仅作架构背景);
  Hub 不直发飞书(§2.2 伪代码作废);中继废弃(D2)。

## 3. 阶段计划与任务追踪

### Phase A0 · 工程规整(本工程)
- [x] 契约与计划入库(docs/scheme/ 4 份 + 本文档)、.gitignore 未提交修改归位
- [x] remote 核对 = `git@github.com:tz-Box/atp.git`;提交格式 conventional
- [ ] 分支约定:master 主干 + `feature/*` 短分支(团队小,从简)

### Phase A1 · Autotest 核心去算法化(本工程主战场 = v1.1 批次 A)
- [x] protocol:data 信封 `{schema, v, enc, blob}`;`register_data(kind, schema_id, decoder)`;
      未知 schema **拒收记 error**(废静默透传);删除 SLAM/NAV/MANIP 常量
- [x] registry:`register_body` + 三件套键命名空间化;场景装配 `produces ⊇ consumes` fail fast
- [x] body:`body/pbox_v1.yaml` + scenario `body` 必填 + INIT 下发 + dataset 标定一致性校验
- [x] GroundTruth `{schema, v, data}`
- [x] `modules/slam` → `plugins/pipe.slam/`(data/datasets/checkers/scenarios + contract.md)
- [x] `modules/nav` → `plugins/nav2d/`;删除 `modules/manip`(D5)
- [x] 测试/examples/docs 跟随迁移(影响面 26 文件)
- [x] **验收**:pytest 全绿(93 passed,含 manifest→service→算法 端到端功能测试) → v1.1 契约提请冻结

### Phase A2 · CI 链路对齐 v1.3(本工程)
- [x] `autotest.yml`:`on` 只留 `workflow_dispatch`(inputs: correlation_id/check_type/commit);末步回调
- [x] `report.py` 重写:POST `/api/ci/callback`(Bearer;`{cid, sha, check_type, conclusion, report:{summary, run_url?}, finished_at}`)
- [x] report.json → summary 摘要文本生成(passed/n_records/metrics 概览)
- [x] **验收**:mock callback server 校验载荷通过(test_ci.py:Bearer 头 + 全字段 + ISO8601)

### Phase A3 · tzcomm 可靠性仪器化(本工程,2026-08-20 新增)
> 动机:评测链路全面依赖 tzcomm;失败时要能分钟级归因(框架 / tzcomm / 算法),
> 并把 tzcomm 沉淀为团队可复用的底层组件(自带健康输出)。
- [x] commcheck 预检:`python3 -m autotest.commcheck`(daemon 可达 / pubsub 回环 / service 回环,结构化输出 + 退出码)
- [x] 评测留痕:report.json 增 `comm_health`(Service 侧 + SUT 侧丢包/收发统计)+ session.log 阈值告警
- [x] 诊断日志:SUT 经 env 可选开启(`TZCOMM_LOG_LEVEL`);server 端 tzcomm 日志入 session.log
- [x] SUT final 自统计回传:SDK 自动附 `comm` 项(SUT 侧 obs 接收丢包),server 收齐入档
- [x] **tzcomm 修复(排查中实锤)**:
  - EADDRINUSE 根因=宿主机其他进程占用池内端口 + daemon 按 Redis 账本分配不探测主机;
    新增 `_rebind.reg_bind`(撞车注销换端口重试),应用于 ServiceServer/ActionServer/TcpPublisher/UdpPublisher 四处
  - 端口池环境变量可配(`TZCOMM_TCP_PORT_BASE/NUM`、`TZCOMM_UDP_PORT_BASE/NUM`)
  - create_service_client/create_action_client 纳入 Node 关闭编排(修 client 长连接泄漏 → "Task was destroyed" 告警消除)
- [x] **验收**:tzcomm 新增 test_port_rebind.py 3/3 + functional 124/124;atp 112/112 + 功能三连跑全绿;commcheck 实跑 PASS

### Phase A4 · 算法工程师接入手册(本工程,2026-08-20 新增)
- [x] `docs/算法测试接入手册.md`:最终形态定义链(body → plugin → scenario → manifest → SUT → CI),
      覆盖"已有插件复用"与"新算法类型自建插件"两条路径 + 调试归因指引(commcheck/comm_health 归因速查表)

### Phase B1 · v1.1 批次 B(本工程,纯内部深化,不阻塞打通)
- [x] **M-B0 倒立摆闭环插件 `plugins/ctrl.invp`**(2026-08-21):新建插件流程最简验证 + 用户可扮演算法工程师的实操样例
  - 插件全套:InvpObs/InvpAction schema、InvpSimWorld(车-摆动力学仿真)、InvpChecker(survived+settle_error)、
    contract.md、预置场景;`body/invp_sim.yaml` 纯仿真台架本体;示例 SUT `examples/invp_sut.py`(PD,增益经 hyperparams)
  - **核心打通(批次 B 前置缺口)**:IWorld 增 `closed_loop` 标记;ClosedLoopSession 与 Runner 特性对齐
    (sut_final/comm_snapshot/progress_cb/传感器校验);server 按标记自动选闭环/开环会话 → **闭环首次可走 `client run` 服务通路**;
    manifest 相对 scenario 路径按算法仓库根解析
  - **验收**:单元 7 + 功能 2(进程内会话 + server 服务通路,含 comm_health 双侧留痕),pytest 121 全绿;
    `client run` 实跑通过(2 testcase 全 passed,双侧 1000/1000 零丢包)
- [x] **M-B1 nav 闭环验证**(2026-08-21):nav2d 接入场景装配走服务通路,对齐 ctrl.invp 模式
  - SimWorld 补 `from_config` 工厂 + `register_dataset("nav2d.sim")` 注册;`body/nav2d_sim.yaml` 纯仿真台架;
    预置场景 `plugins/nav2d/scenarios/sim_basic.yaml`(straight/offset_goal);manifest 示例 `examples/nav_sut.scenario.yaml`;
    contract §3 消除"不经 register_dataset"缺口注记
  - **验收**:功能 +1(server 服务通路端到端),pytest 122 全绿;`client run` 实跑 2 testcase 全 passed(零丢包)
- [x] **M-B2 manip 插件 `plugins/manip.force`**(2026-08-21):D5 重建落地,"新建插件流程"第二个验证
  - 插件全套:ManipObs(x/x_dot/f_contact/target_force 经 obs 下发,对齐 nav2d goal 先例)/ManipAction schema、
    ForceSimWorld(单自由度质量块 + 弹簧-阻尼接触壁面,force_exceeded/survived)、ForceChecker(survived+settle_error,
    附 peak_force/overshoot/tracking_ratio)、contract.md、预置场景;`body/manip_sim.yaml` 台架;
    示例 SUT `examples/manip_sut.py`(PI 力控+阻尼+抗积分饱和,增益经 hyperparams)
  - **验收**:单元 8 + 功能 1(server 服务通路),pytest 131 全绿;`client run` 实跑 2 testcase 全 passed,
    **1kHz 高帧率闭环双侧 10000/10000 零丢包**(tzcomm 高帧率压力实测)
- [x] **M-B3 暂停/单步(调试语义)**(2026-08-21):算法调试现场观察/断点配套
  - `RunControl` 帧级闸门(`src/autotest/eval/run_control.py`):pause 停喂帧(时钟冻结,SUT 安全等待)、
    step(n) 暂停中配额放行 n 帧(耗尽自动回阻塞)、resume 清残余配额(无幽灵帧);仅数据帧过闸,终止帧直达
  - 挂载:Loader(开环)/ClosedLoopSession(闭环)帧循环 publish 前统一过闸;Job 随建 control;
    server control 服务增调试命令分支,job status 暴露 `run_state`/`frames`;
    client 增 `pause`/`step`/`resume` 子命令,`run` 提交后 stderr 打印 job_id(调试入口,--json stdout 保持干净);
    修存量 bug:`client/__main__.py` 补 `sys.exit(main())`(CLI 错误码曾被吞,CI 依赖退出码)
  - **验收**:单元 6(RunControl 语义:直通/阻塞/配额精确/累积/非暂停 step 拒绝/清残余)
    + 功能 3(开环实时复现 + 闭环进程内 + 服务通路含错误分支),pytest 140 全绿;
    CLI 实跑冒烟(run→pause 冻结→step +5 精确→resume→passed)通过;**批次 B 收口**

### 批次 D · 部署工程化 + 真实算法仓联调(2026-08-23 立项)

> 批次 B 收口后按 §4.1 既定路线推进;含 PMS 建议 A1/A3/A4 与批次 D 排期。

- [x] **M-D0 tzcomm 部署归位**:daemon 部署完全交 TzComm 工程——`install/setup.sh`
  (central/node/reconfigure/status/uninstall)+ unit 模板去硬编码 + EnvironmentFile
  单一事实源,于 TzComm 仓提交(6853ead)并系统级安装(`/etc/systemd/system/tzcomm-daemon.service`,
  127.0.0.1:17888);本仓不再持有 tzcomm unit
- [x] **M-D1 部署工程化(R5/R6 落地)**:
  - R5 定案(A4):runner 机 `pip install --user -e`(发行包名 **tz_atp**,import 名 autotest 不变)
  - HTTP 运维面 `server/http.py`(FastAPI,:2335;/health /api/submit /api/command /api/jobs/{id},
    与 tzcomm 面共享 Jobs 池;不参与 Hub 触发链路,v1.3 §2/§4.3 不变)
  - `deploy/autotest.service` 用户级常驻(2333=PMS/2334=cicd-hub 先例;daemon 未就绪靠
    Restart=on-failure 追上)
  - **验收**:8 单测(TestClient)+ pytest 148 全绿;冒烟 `curl /health` ✓、HTTP submit
    倒立摆 passed ✓、`client run` 2/2 passed ✓(systemd 实服务)
- [x] **M-D2 cicd_test 倒立摆接入(真实算法仓)**:`cicd_test@feat/invp-pd`(aadb1ac)——
  根 manifest `scenario.yaml` + `invp_sut.py`(PD)+ `scenarios/sim_basic.yaml` +
  `ci/report.py` + workflow 真实执行版(client run --json + 回调);
  本地等效 E2E(clone→评测→mock Hub 回调载荷校验)通过
- [ ] **M-D3 baseline 接入 CI(PMS 建议 A2,排期批次 D 后半)**:`report --save-baseline`
  进蓝本 workflow;蓝本归 Hub 仓,ATP 出改动需求/PR,Hub 合并分发;baseline 入 CI 后
  vs_baseline 才有真实消费者
- [ ] **M-D4 真实算法仓通路1 联调(终极验收,PMS 建议 A5)**:依赖 R4(runner 注册,人工);
  cicd_test pytest 探针已实证回调链路,真实 autotest(tzcomm+SUT)在 runner 上未跑过,
  与 M-D3 一并排期

### Phase H1 · Hub MVP:通路1 主链路(**cicd-hub 工程,同事主导**)
> 进度以同事仓《docs/阶段计划-双通路端到端联调.md》为准(软链 `__temp__/cicd_hub`,同步开发中,代码常变)。
> 已知:S0 公网入口 ✅;**T1a 真实 push E2E ✅(2026-08-20,cicd_test + 云端 runner,回调链路已真实验证)**;T0 字段对齐/T1b-d 进行中。
- [ ] webhooks / 路由 / Redis Stream 队列 + dispatcher / callback 归位 / pms_adapter(同事侧)
- [ ] **验收(通路1)**:push → Hub → workflow → autotest → 回调 → ci-results → PMS 任务卡 + 飞书

### Phase H2 · 通路3 手动触发(**cicd-hub 工程,同事主导**)
- [ ] `POST /hub/manual-check` + `mannultest/<user>/<ts>` 约定 + 终态清理临时分支
- [ ] 最简触发界面(CLI/表单即可)
- [ ] **验收(通路3)**:推前缀分支 → Hub 触发 → 回调带实际 sha → repo+sha 可查

### Phase H3 · 通路2 + 通路4(**cicd-hub 工程,同事主导**;PMS 侧见下)
- [ ] events 路由:`ci_alert`(无归位目标告警)/ `deliverable_review`(approve,Hub 解析 login→成员);(+W1 `pr_merged`)
- [ ] `GET /hub/ci-results`(latest_status 按 check_type 分组)、`GET /hub/repos`、`GET /hub/github-users`
- [ ] **验收(通路2/4)**:告警/评审事件 → PMS → 飞书;交付物本地未命中时 pull 兜底成功

### PMS 侧清单(同团队并行,按 v1.3 §8.1,不经本 repo)
`POST /pms/ci-results`(只认 status)+ `POST /pms/events` + `GET /pms/tasks?repo=` +
`/api/admin/projects` 放行 service-token + cid 本地表 + repo↔项目绑定(候选读 /hub/repos)+ 出站配置

### 后续(不阻塞打通)
lint 多检查编排(Hub Phase 1)、管理界面(Hub Phase 2)、v1.1 批次 B(nav 闭环/manip 插件)、suite 联合测试、enc=pb

## 4. 并行关系与依赖

```
A0 ─▶ A1 ─▶ A2 ─▶ A3/A4(本工程,不依赖外部,可全速)
      (H1 由同事主导推进;回调接口面 v1.3 §4.3 与 autotest 内部协议无关)
                ├─▶ 通路1 联调(需 A2 + H1 + runner/被测仓库 + PMS 端点齐备)
H1 ─▶ H2 ─▶ H3 ─┘
```

### 4.1 联调就绪度盘点(2026-08-20,按 v1.3 四通路)

| 通路 | Autotest 侧 | 就绪度 | 阻塞项 / 依赖 |
|---|---|---|---|
| **通路1**(自动闭环) | A2 已完成(workflow 模板 + 回调脚本,v1.3 全字段) | **待联调** | ① self-hosted runner 注册到评测机(**人工操作**,R4);② 被测算法仓库(manifest + workflow + scenario,可用 examples/slam_algo 打模板);③ 评测机常驻 autotest server + tzcomm daemon(R5/R6);④ Hub 侧 T0/T1(同事推进中,cicd_test 链路已实证) |
| **通路3**(手动预提交) | workflow 已支持 `mannultest/**` 触发约定 | 等 Hub | Hub T4 `POST /hub/manual-check` 端点(同事侧) |
| **通路2**(事件通知) | 无动作(Autotest 不感知) | 等 Hub/PMS | Hub T2 + PMS `/pms/events` |
| **通路4**(交付物查询) | 无动作 | 等 Hub/PMS | Hub T3 + PMS 本地 cid 表 |

**结论:Autotest 主线不阻塞。** 可立即推进:~~A3(tzcomm 仪器化)~~ ✅ → ~~A4(手册)~~ ✅ → v1.1 批次 B(~~M-B0 倒立摆~~ ✅ → ~~M-B1 nav 闭环验证~~ ✅ → ~~M-B2 manip.force 重建~~ ✅ → 暂停单步)→ 部署工程化(R5 runner 包安装、R6 systemd 单元)。
联调项瓶颈在 **runner 注册(人工)+ Hub 端点(同事)+ PMS 端点(协调)**,三方到位前 Autotest 侧先用 mock callback(test_ci.py)与功能测试守住契约面。

## 5. 风险与待决项

| # | 项 | 说明/处置 |
|---|---|---|
| W1 | **PR merged→交付物冻结 事件载体**(D2 遗留) | 方案(a):v1.3 补订新增 `pr_merged` type(推荐);方案(b):PMS 状态机 approve 即冻结。待 PMS 侧确认后回写契约 |
| R2 | PMS 端点排期 | 同团队并行;Hub 侧 outbox mock 兜底不阻塞 |
| R3 | A1 迁移影响面 26 文件 | 协议变化需同步改示例 SUT;测试跟随,一轮迁移到位 |
| R4 | GitHub App 注册 | 组织内注册(Actions:write + webhook 订阅),私钥/App ID 入 hub 配置——需人工操作 |
| R5 | runner 上 autotest 包安装 | workflow 跑 `python3 -m autotest.client`,runner 机需可 import(pip install -e 或 release 包),部署文档补 |
| R6 | tzcomm daemon + Redis 常驻 | 评测机 systemd 单元(Autotest §3.3),部署文档补 |
| R7 | token 分发 | service-token / hub.callback_token / webhook secret 生成与三端配置 |
| R8 | summary 映射 | report.json → 摘要文本规则随 A2 定死 |

## 6. 变更记录

| 日期 | 变更 | 状态 |
|---|---|---|
| 2026-08-19 | v1 建档:五决策 + 四通路阶段计划 | 已批准,执行中 |
| 2026-08-20 | A1 完成:核心去算法化落地(plugins/pipe.slam + nav2d + contract.md;modules/ 与 protocol/data/ 已删除);<br>附带:body 资产补 rear lidar 且 imu 实例名对齐数据集约定;examples/scenarios 全部对齐 v1.1;<br>TzComm 交付交互式 install/setup.sh(central/node/reconfigure/status/uninstall + IP 自动检测);<br>pytest 93 passed 全绿 | 完成,进入 A2/H1 |
| 2026-08-20 | A2 完成:autotest.yml 改 workflow_dispatch 单触发(correlation_id/check_type/commit + 实际 sha 捕获);<br>report.py 重写为 Hub /api/ci/callback 回调(Bearer + 结构化 report{summary, run_url?} + finished_at);<br>mock callback server 载荷验收通过;pytest 97 passed 全绿 | 完成,H1 就绪可启动 |
| 2026-08-20 | 任务重梳:cicd-hub 转同事主导(软链 `__temp__/cicd_hub`,S0✅/T1a✅);<br>本工程新增 Phase A3(tzcomm 可靠性仪器化)/ A4(算法工程师手册);<br>§4.1 四通路就绪度盘点:Autotest 主线不阻塞,联调瓶颈=runner 注册(人工)+Hub/PMS 端点 | 执行中 |
| 2026-08-20 | **A3+A4 完成**:tzcomm 修 EADDRINUSE(_rebind 换端口重试 + 端口池 env 可配 + client 关闭编排),<br>commcheck 预检实跑 PASS + comm_health 双侧留痕入报告 + CI 摘要附告警;<br>《算法测试接入手册》交付(定义链 + 双路径 + 归因速查);<br>tzcomm functional 124/124、atp 112/112、功能三连跑全绿 | 完成,进入 v1.1 批次 B / 部署工程化 |
| 2026-08-21 | **批次 B 启动,M-B0 完成**:新增 Phase B1 追踪;倒立摆闭环插件 ctrl.invp 全套交付(data/sim/checker/contract/场景 + invp_sim 台架本体 + PD 示例 SUT);<br>核心打通:world.closed_loop 标记 + server 闭环会话装配(闭环首通 `client run`)+ manifest 相对路径解析;<br>pytest 121 全绿(新增单元 7 + 功能 2) | 完成,可实操体验;继续 M-B1 |
| 2026-08-21 | **M-B1 完成**:nav2d 对齐 ctrl.invp 模式接入场景装配(from_config 工厂 + register_dataset + nav2d_sim 台架 + 预置场景 + manifest 示例);<br>手册索引补 ctrl.invp 为最简闭环参考;M-B0/M-B1 均 `client run` 实跑验证(双侧零丢包);pytest 122 全绿 | 完成,**算法工程师实操测试已可启动**(推荐从 ctrl.invp 入手);继续 M-B2 |
| 2026-08-21 | **M-B2 完成(D5 收口)**:manip.force 插件按新建插件流程重建(单自由度接触力控,与 invp 镇定/nav2d 到点正交的第三类任务);<br>目标力经 obs 下发新范式(target_force 对齐 nav2d goal 先例);1kHz 闭环双侧 10000/10000 零丢包;<br>pytest 131 全绿;批次 B 插件矩阵成型(3 闭环插件 + 1 开环插件) | 完成,继续 M-B3(暂停/单步) |
| 2026-08-21 | **M-B3 完成,批次 B 收口**:RunControl 帧级闸门(pause 冻结/step 配额放行/resume 清残余),开环 Loader + 闭环 ClosedLoopSession 统一挂载;<br>server 调试命令分支 + job status 暴露 run_state/frames;client pause/step/resume 子命令 + run 打印 job_id;<br>手册补 §4.2 暂停/单步(顺带修 §3 陈旧引用);pytest 140 全绿(单元 6 + 功能 3) | 完成,**v1.1 批次 B 全部收口**;后续视算法实操反馈排新批次 |
