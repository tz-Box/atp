# Patrol Box · 三平台打通推进计划(v1 · 2026-08-19)

> **基准契约**:《三平台通信与接口总契约 **v1.5**》(2026-08-24 M 批准生效,最高基准;**唯一事实源在 PMS 仓 `schema/`**,本仓不留副本)、《Autotest Service 接口契约 v1.1》、《Hub 技术路径规划》。
> v1.5 关键增量:**通路1/3 的 autotest 执行载体由 workflow_dispatch 切换为 Hub 直连 ATP HTTP 面**(§4.8)——
> self-hosted Runner 从评测通路移除(R4 勾销);ATP 评测完成主动回调 Hub(报文不变);Hub 归位后 check-runs
> 回写 PR 可见性;多 ATP 池化(repo↔ATP 一对多路由归 Hub 配置);service-token 轮换拍板勾销(飞书 OAuth)。
> **目标**:三平台(PMS / cicd-hub / Autotest)全链路打通,v1.5 §3 **四条通路**端到端可测。
> 前期不求功能全面,但架构、解耦性、模块扩展性必须清晰;扩展性在打通后补齐。
> 本文档为任务追踪事实源:状态列随开发更新,调整走 §6 变更记录。

## 1. 决策记录(2026-08-19,五项,已拍板)

| # | 议题 | 结论 |
|---|---|---|
| D1 | 打通范围 | v1.3 §3 **四条通路全部**打通(非两条) |
| D2 | D4 中继 | **废弃**。PMS 全部按 v1.3 新协议改造,不留中间版本;协作事件一律走 §4.4 events。**遗留**:PR merged→交付物冻结 无事件载体 → 待决 W1 |
| D3 | autotest 分支 push 触发 | **废弃**。一切触发经 Hub(必带 cid);~~workflow `on` 只留 `workflow_dispatch`~~(**v1.5 更新**:autotest 执行载体改 Hub 直连 ATP HTTP 面,GHA workflow 降级为算法仓自测备选路径);mannultest 分支仅作代码载体,不自动触发,防绕过 cid |
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
    与 tzcomm 面共享 Jobs 池;**v1.5 升级注记**:该 HTTP 面正是批次 E 直连通路的承载面,§4.8 端点在其上扩展)
  - `deploy/autotest.service` 用户级常驻(2333=PMS/2334=cicd-hub 先例;daemon 未就绪靠
    Restart=on-failure 追上)
  - **验收**:8 单测(TestClient)+ pytest 148 全绿;冒烟 `curl /health` ✓、HTTP submit
    倒立摆 passed ✓、`client run` 2/2 passed ✓(systemd 实服务)
- [x] **M-D2 cicd_test 倒立摆接入(真实算法仓)**:`cicd_test@feat/invp-pd`(aadb1ac)——
  根 manifest `scenario.yaml` + `invp_sut.py`(PD)+ `scenarios/sim_basic.yaml` +
  `ci/report.py` + workflow 真实执行版(client run --json + 回调);
  本地等效 E2E(clone→评测→mock Hub 回调载荷校验)通过
- [x] **M-D3 baseline 接入 CI(PMS 建议 A2)**(2026-08-23):
  - ATP 侧(本仓):`run --json` 输出携带 job_id(供后续 step 定位 Service 侧产物);
    `report --json` 机读回归对比(has_baseline/changes 计数/rows);CI 模板
    `examples/ci/autotest.yml` 增 "Regression vs baseline" step + `save_baseline`
    input(先对比后滚动);`examples/ci/report.py` 支持第二参 regression.json,
    回调摘要追加 vs_baseline 变化计数
  - cicd_test 同步(2145ec8):workflow + ci/report.py 同源更新;本地等效 E2E 两轮——
    首轮无基线全 new 并落 baseline.json,次轮 vs_baseline: improved=2,回调摘要携带
  - **给 Hub 的蓝本改动需求(workflows/autotest.yml,建议照 cicd_test 现行版合并分发)**:
    ① 新增 workflow_dispatch input `save_baseline`(bool 默认 false;主干/发版评测置 true);
    ② Run evaluation 后插 "Regression vs baseline" step(env `vars.AUTOTEST_ARTIFACTS_DIR`
    =评测机 Service 产物目录);③ Callback step 改 `python3 ci/report.py report.json
    regression.json`;④ 算法仓新增变量 `AUTOTEST_ARTIFACTS_DIR`(随 R5 部署文档)
  - 验收:pytest 158 全绿(+7 新增:CLI 4 + ci/report.py 3)
- [ ] ~~M-D4 真实算法仓通路1 联调(runner 形态)~~ **随 v1.5 改道,见批次 E**——
  2026-08-24 M 拍板路径 A(Hub 直连 ATP):runner 依赖取消,原"runner 注册→GHA 派单→真评测首跑"
  联调形态废止;已完成的 M-D2/D3 资产(cicd_test manifest/SUT/基线机制)全部保留复用

### 批次 E · v1.5 直连改造(2026-08-24 立项,M 拍板路径 A;2026-08-25 细化;2026-08-25 第一批+M-E2 收口)

> 通路1/3 执行载体切换为 Hub 直连 ATP HTTP 面(契约 §4.8;子契约 v1.2 §11)。ATP 侧(本仓)任务 = M-E1~M-E7;
> 外部配合(Hub/PMS/运维)清单见下方"批次 E 外部配合清单",随本计划一并传递。
> **开工基线(2026-08-25 盘点)**:可复用资产 = HTTP 运维面 4 端点(/health /api/submit /api/command /api/jobs/{id},
> 与 tzcomm 面共享 Jobs 池)+ baseline/vs_baseline 机制(CLI 层)+ 回调组包逻辑(examples/ci/report.py,语义已对齐 §4.3);
> 全新增 = cid 概念/认证/checkout/主动回调/串行/契约态状态查询。pytest unit 139 全绿。
> **第一批收口(2026-08-25,M 批准 M-E1~M-E4,按建议顺序 M-E5 先行)**:M-E1/M-E5/M-E3/M-E4 完成——
> server/ 新增 evaluations.py(cid 幂等 SQLite)+ checkout.py + callback.py(summarize 内化/退避重试/基线滚动);
> http.py 新增 /atp/evaluations(POST+GET)、/atp/health;server.py Jobs 池改单 worker 队列(HTTP/tzcomm 面共享)。
> **M-E2 收口(2026-08-25,同日续)**:checkout.py 扩 git URL/owner-repo 识别(ATP_GIT_BASE)+ mirror 缓存
> (~/.cache/autotest/repos/<owner>__<repo>.git,ATP_CACHE_ROOT 可配)+ 按 job worktree 隔离(workspaces/<job_id>)
> + 终态清理(ATP_WORKTREE_KEEP=1 保留排查)+ 启动 cleanup_stale 清扫;坐标/manifest 失败即时清理不留现场。
> 验收:pytest 206 全绿(unit +13:URL 识别/缓存键/隔离/复用 fetch/清理/清扫;functional +1:file:// bare 仓 E2E)。
> **本仓 M-E1~M-E5 全部收口**;余 M-E6(待 Hub 调度模块)→ M-E7(文档,贯穿收尾);
> deploy key(O2)为部署配置,就绪后真仓验证随 M-E6 一并覆盖。
> **M-E8+X3 收口(2026-08-25,同日续)**:新增 ATP HTTP client(`python3 -m autotest.client atp health/submit/
> status/wait`,urllib 零新依赖,退出码可接 CI gate;手动触发/联调自验/排障重放);deploy/autotest.service 加
> `EnvironmentFile=-%h/.config/autotest/atp.env` 三件套注入(token 不入库)。**真机部署验证通过**:systemd 常驻
> :2335,health/submit 202/评测 success(summary 含 vs_baseline)/同 cid duplicate 全链路;dev token 自验,正式
> token 待 O3。pytest 219 全绿(unit +13 client)。同步文档已发:Hub 仓 docs/ATP-v1.5-就绪公告与接口事实-2026-08-25.md、
> PMS 仓 docs/plan/ATP-v1.5-就绪同步-2026-08-25.md。
> **Hub 侧进度发现(2026-08-25 读码)**:v1.5 调度模块已基本实现(atp_pool 探活择路/dispatcher 分路含 4xx 判
> failure·5xx 重投·duplicate·manual ref/collector 轮询兜底/save_baseline 事件侧,test_atp_direct 15+ 用例),
> 其 mock 载荷与 ATP 接口逐字段对齐(repo 简写/ref 完整形均可解析)——M-E6 窗口临近,剩余为 checkruns 回写/
> console 面板/超时扫描适配/文档及阻断项 deploy 三文件旧仓名。

- [x] **M-E1 submit 接口**:`POST /atp/evaluations`(server/http.py 扩展)——**已收口(2026-08-25)**
  - 认证:Bearer `atp.service_token`(FastAPI Depends;未配置→503 端点关闭,错/缺→401,hmac.compare_digest 防时序——
    对齐 PMS require_service_token 范式);token 经 systemd Environment 注入
  - 收 `{correlation_id(必填), repo, ref, sha?, check_type(预留,恒 autotest), scenario?, save_baseline, pms_task_id?}`
  - cid 幂等:新增轻量持久化(建议 SQLite `artifacts/atp.db`,evaluations 表:cid PK/job_id/repo/ref/sha/status/created_at);
    同 cid → `200 {ok, duplicate:true, job_id:<原job>}` 不重复执行
  - 响应 `202 {ok, job_id, sha}`(sha 由 checkout 回填,M-E2 前过渡为 null);
    校验失败 4xx `{ok:false, error}`(token/repo·ref 不可达/manifest 缺失)→ Hub 直接判 failure 免等超时
  - **验收**:TestClient 单测(401/503/字段校验/幂等 duplicate/202 骨架),pytest 全绿
- [x] **M-E2 workspace 与 checkout**:按 repo 缓存 git 仓、按 ref/sha worktree 隔离到本机 workspace——**代码已收口(2026-08-25)**;deploy key 部署配置随 O2/M-E7,真仓验证并入 M-E6
  - 布局:`~/.cache/autotest/repos/<owner>__<repo>.git`(mirror 缓存,fetch 更新)+ `workspaces/<job_id>/`
    (`git worktree add` 按 ref/sha 隔离);评测落盘后 worktree 清理(保留期可配,便于排查)
  - 凭证:GitHub 只读 deploy key(ssh;部署细节入 M-E7 文档)【依赖运维 O2】
  - checkout 后定位 manifest(submit 的 `scenario` 或仓根 `scenario.yaml`),拼绝对路径复用 server.submit 业务路径;
    `git rev-parse HEAD` 记实际 sha 回填(M-E1 的 202 响应与 M-E3 回调共用)
  - **验收**:本地等效 E2E(clone cicd_test → checkout 指定 ref → 评测 passed → sha 正确,对齐 M-D2 模式)
- [x] **M-E3 主动回调 Hub**:评测完成(含失败)自动 POST `hub.callback_url`(`/api/ci/callback`)——**已收口(2026-08-25)**
  - 内化 examples/ci/report.py 的 summarize 逻辑(report.json + regression.json → summary 文本含 vs_baseline 计数)
    进 server;conclusion 判定沿用(report 读取失败/有 error/任一 testcase passed=False → failure;passed=None 数据流验证不判)
  - 报文:`{correlation_id, sha(实际 checkout), check_type:"autotest", conclusion, report:{summary, run_url 省略}, finished_at}`;
    Bearer `hub.callback_token`;静态配置经 systemd Environment(`HUB_CALLBACK_URL`/`HUB_CALLBACK_TOKEN`,沿用现 env 名零迁移成本)
  - 失败重试:指数退避(1s/5s/15s 三次),最终失败记 session.log + job 标 callback_error——结果不丢,Hub 轮询兜底(M-E4)可拿回
  - `save_baseline=true` 且 success 时滚动 `artifacts/baseline.json`(先对比后滚动,对齐 M-D3 语义);vs_baseline 计数自动进 summary
  - **验收**:mock Hub server 验载荷(Bearer + 全字段 + vs_baseline + 实际 sha + finished_at,对齐 test_ci.py 模式)+ 重试/失败留痕用例
- [x] **M-E4 状态查询与探活**:`GET /atp/evaluations/{job_id}` + `GET /atp/health` 升级——**已收口(2026-08-25)**
  - 状态映射:内部状态 → `running|success|failure`;终态带 `{sha, report:{summary, run_url:null}, finished_at(ISO8601)}`;
    summary 复用 M-E3 生成器;未知 job → 404
  - health:`{ok, version(包版本单一来源), tzcomm(daemon 可达性快检带超时,复用 commcheck 第 1 级), queue(排队深度,M-E5 后有意义)}`;
    探活必须即时响应,不被串行队列阻塞
  - **验收**:单测覆盖 running/终态/404;health 字段齐 + tzcomm 不可达降级
- [x] **M-E5 单 ATP 串行语义**:评测机资源独占,server 内排队(契约 §4.8 并发语义)——**已收口(2026-08-25)**
  - 现 Jobs 池"每 job 一线程并发"改为单 worker 队列;HTTP submit 与 tzcomm 面 `client run` 共享同一队列(语义最干净);
    `/api/command`(pause/step/resume)与 `/health` 不受队列阻塞
  - 影响:test_service_parallel_jobs 等存量并行语义测试改为验证排队语义
  - **验收**:并发提交 2 个评测 → 串行执行、顺序可证;pytest 全绿
- [x] **M-E6 联调——已勾销(2026-08-26,据 Hub《三系统并行开发规划-2026-08-26》§0 基线)**:
  v1.5 直连迁移收官(Hub v0.7.0,V1~V10 全落,**E2E-1~4 真实通过**);Hub callback_token 已配通(通路1 回调归位实测真实通过)。
  原验收项:cicd_test 真实 push → Hub 编排 → 直连 ATP → 倒立摆评测 → 回调归位/轮询兜底 → check-runs 回写 → PMS 落卡,
  随 Hub v0.7.0 E2E-1~4 一并真实通过;通路2/3/4 同周期覆盖
- [x] **M-E7 文档**(2026-08-25 收口):部署文档=deploy/autotest.service 头部注释(X3 三件套/workspace/deploy key 已全);
  《使用指南》§8 整节重写为 v1.5 直连版(触发模型/前置条件/部署步骤/流程/HTTP 面端点表含 capabilities+console/
  手动触发排障/GHA 降级 §8.7 自测备选);《算法测试接入手册》§3.8 同步;契约 §14 纪律回写(v1.2 已先行一轮)
- [x] **M-E8 ATP HTTP client**(2026-08-25 增补立项,同日收口):手动触发测试与联调自验工具——
  `python3 -m autotest.client atp health/submit/status/wait`(挂现有 client CLI,与 tzcomm 面 run/matrix 并列)
  - urllib 零新依赖;ATP_BASE_URL(缺省 http://127.0.0.1:2335)/ATP_SERVICE_TOKEN 两 env;
    cid 缺省 chk_manual_<时间戳>(幂等安全重放);submit 默认等终态(--no-wait 可关)
  - 出站连接不监听端口,与 autotest service 同机共存无冲突;退出码 0/1/2 可接 CI gate
  - **验收**:mock ATP server 单测 13 全绿(载荷/认证/轮询/超时/退出码);真机全链路(health/submit/wait/duplicate)
- [ ] **M-E9 test service 注册与发现**(2026-08-25 增补立项,回应 M"repo→ATP 绑定管理"需求;**契约 v1.5 无此章节,属新规划**):
  现状 = Hub 静态 config `atp_pool.{atps,routes}` 人工维护;算法测试特殊性(插件依赖如 ctrl.invp、本体台架
  invp_sim 纯仿真/真机、资源差异 GPU/传感器)决定不同 ATP 能力不同,绑定关系需管理面而非手改配置
  - [x] M-E9a(ATP 侧,本仓)**已收口(2026-08-25)**:`GET /atp/capabilities`(独立端点,无认证)——
    `{version, plugins, bodies, resources:{gpu(nvidia-smi 探测), sensors:[]}, queue}`;
    registry.available_plugins/available_bodies 扫描仓内目录(静态事实,不受按需加载影响);单测 +2,真机验证 4 插件/4 本体
  - M-E9b(Hub 侧,同事):探活时采集 capabilities 入健康快照 + console ATP 面板展示(V9);
    routes 管理 API + console 表单(替代手改 config.json;对齐"配置走界面"纪律)
  - M-E9c(绑定数据源,后续):算法 repo 库登记归 PMS(repo↔项目绑定已有,扩展 check_type/需求标签),
    Hub 从 PMS pull 生成 routes;远期能力自动匹配(manifest consumes/scenario 需求 vs ATP capabilities)
  - 不阻塞 M-E6:链路验证用 L0 静态 routes 即可
- [x] **M-E10 ATP Web 控制台**(2026-08-25 增补立项,同日收口;回应 M"client 可视化"需求;域名 atp.turing-zero.com 已 nginx→2335):
  单页静态 HTML 挂 FastAPI `/console`(零前端构建、零新依赖);页面 token 输入存 localStorage,fetch 带 Bearer
  - 数据面:`/atp/health` + **新增 `GET /atp/evaluations` 列表端点**(evaluations.list_recent,created_at 倒序,
    limit≤200,Bearer 认证) + 现有按 job_id 查询(详情展开)
  - 功能:健康卡(version/tzcomm/queue)、评测列表(状态/sha/summary/finished_at 可展开,含 callback_error 留痕)、
    手动触发表单(repo/ref/scenario/save_baseline,cid 缺省 chk_manual_<ts> 幂等)、5s 自动刷新
  - 边界:ATP console = 单机执行面运维视图(本机排队/结果/手动触发);Hub console(V9 ATP 面板)= 池化调度视图,不重复建设
  - **验收**:单测 +5(认证/倒序字段/limit/页面无认证);真机 health/console 200/列表真实记录/无认证 401
- [x] **M-E11 飞书登录授权**(2026-08-25 增补立项,同日收口;回应 M"console 绕过 service-token 手输,完全按 Hub 现在的方式"):
  通路**复用 Hub/PMS 已验证范式**(server/auth.py,urllib 零新依赖;与 Hub 同路径 /api/auth/*)
  - 人/机分层:读(列表/详情)=Bearer(机器,Hub 不受影响) **或** atp_session(member+);写(手动触发)=Bearer **或** admin 会话;member 写 → 403
  - 白名单 ~/.config/autotest/console_users.json(600,name/oauth_open_id/role;open_id 首登姓名唯一匹配回填,与 Hub 同)
  - 会话 sessions.json(原子写 600,重启不失效),cookie atp_session(HttpOnly/SameSite=Lax/14 天)
  - 配置:atp.env 追加 FEISHU_APP_ID/SECRET(O3 分发,与 Hub/PMS 同一只自建应用);飞书后台登记 /api/auth/feishu/callback(按访问 Host 各自登记)
  - console.html:顶栏改飞书登录/用户角色/登出为主通道,token 输入折叠为"机器 token 备选";login_error 横幅;写档位按钮联动
  - **验收**:单测 14(成员门直中/回填/拒绝/state 失效/会话持久化/双通道三档/503-401 语义);pytest 239 全绿;
    真机 oauth:false 降级/Bearer 不受影响/匿名 401 全通过;待 O3 分发 app_id/secret 后即可真机飞书登录
  - **配置闭环(2026-08-25 晚)**:FEISHU_APP_ID/SECRET 取自 cicd-hub config.json(同一只自建应用)已写入 atp.env;
    console_users.json copy Hub 4 名 admin(Mark open_id 直中,其余首登回填);真机 oauth:true/start 302/域名回调全通,
    M 已实际登录成功

### 3.5 批次 F:scenario 体系 + 评测环境隔离(2026-08-26 立项)

> **依据**:Hub《三系统并行开发规划-2026-08-26》(v1.5 收官后四项需求 R1~R4)。
> ATP 分派:**R2 配合**(scenario.yaml Schema 权威 + scenario 参数生效 + 错误码规范化)+ **R3 主责**(runtime 隔离)。
> 定位:测试定义权威在被测仓内,ATP 是通用执行器——不存在"ATP 端测试目录"接口。
> 排期:`Schema(先行,Hub scenario 勾选依赖) → scenario 生效 → venv(F1) → docker(F2,中期)`。

- [x] **M-F1 scenario.yaml Schema 权威定义**(R2 核心,先行项,**已收口 2026-08-26**):[scenario-schema.md](../../07-附录-scenario-yaml-schema.md)——
  三层模型(manifest→场景文件→执行面);manifest 全字段表 + **scenarios 场景清单**(id ^[a-z0-9_]+$/description/
  scenario 引用/hyperparams·checker_config·dataset_config 深合并覆盖/baseline 仓内参考基线)+ **runtime 声明**
  (host|venv|docker,缺省 host 零迁移,与旧 image 字段关系厘清)+ submit.scenario 语义(null|id|[ids]|路径旧语义过渡)
  + 错误码表(manifest_missing/manifest_invalid/scenario_unknown)+ 双示例(cicd_test 现状兼容/多场景+venv 全量)
- [x] **M-F2 scenario 参数生效**(**已收口 2026-08-26**):`POST /atp/evaluations` scenario 字段激活——
  `null(全跑)|"id"|["a","b"]`;多场景单 job 顺序执行(每场景独立 launch/session,testcase_id 带
  `场景id:` 前缀,report.json 落全场景清单;场景异常记失败条目并继续后续场景);清单项
  hyperparams/checker_config/dataset_config **深合并**覆盖场景文件(覆盖优先级:场景文件 < 清单项 <
  评测方 checker 覆盖);未知 id → 400 scenario_unknown;路径值(含 / 或 .yaml 结尾)保持旧语义平滑过渡。
  测试:unit test_scenario_manifest.py 18 例 + functional 多场景端到端 3 例
- [x] **M-F3 manifest 错误规范化**(**已收口 2026-08-26**):错误响应统一 `{"ok":false,"error","code"}`——
  manifest_missing / manifest_invalid / scenario_unknown 三层贯通(submit → submit_evaluation → HTTP 400),
  对齐 Hub 4xx 判 failure 语义
- [x] **M-F4 venv runtime(F1 期,已收口 2026-08-26)**:`runtime.type: venv` → 仓根 `.atp-venv` 复用/创建
  (`--system-site-packages`)+ `pip install -r requirements.txt`(job 级一次,全场景共享);评测进程
  PATH 前置切 venv 解释器,SDK(autotest/tzcomm)经 PYTHONPATH 透传(算法仓无需重复声明);无
  requirements → 裸 venv + WARNING;失败 → job 级 failure(`<runtime>` 失败条目);`type: docker`
  F1 期明确报错不静默回退。测试:unit test_runtime.py 7 例 + functional venv/docker 端到端 2 例
- [ ] **M-F5 docker runtime(F2 期,中期非阻塞)**:`runtime.type: docker` → 仓根 Dockerfile build 或
  image 拉取,容器内评测,产物卷挂载回传;前置 docker daemon + 评测用户 docker 组(部署文档补)

#### 批次 F 真机联调就绪(2026-08-26,ATP 侧)

- ATP 服务已重启承接新代码(:2335,/atp/health ok);Hub v0.8.0 scenario 真实下发的三态**真机冒烟全过**:
  `unknown → 400 {"ok":false,"code":"scenario_unknown"}(报文含可用清单)`｜`"small_push" → success 1/1
  (单场景无前缀,vs_baseline improved=1)`｜`null 全跑 → success 3/3(testcase_id 带 场景id: 前缀,
  report.json 落 scenarios 全清单 + manifest.runtime)`
- 联调素材:cicd_test 仓 scenario.yaml 已升级 scenarios 清单权威格式(`small_push`/`full`,
  本地 commit 20c6993,**待 push 后 Hub Contents API 可读**)
- 注意:多场景前缀改变 testcase_id 命名空间,存量滚动基线首轮全记 `vs_baseline: new=N`(一次性,
  下次 save_baseline 后以新命名空间为准)

#### 批次 F 跨系统配合(传递 Hub/PMS)

- **Hub 主责(R2)**:manual-check scenario 勾选(GitHub Contents API 读仓内 scenario.yaml,无需 ATP 新接口)、
  rules.repos checks 项 scenario 字段、`_dispatch_atp` 透传 scenario、atp_pool 界面化(∥ N2c)、T1c 改造(等 PMS)
- **PMS 主责(R1+R4)**:task_repos 绑定模型 + `GET /pms/tasks?repo=` 语义变更 + 任务卡 CI 结果区块——ATP 无配合项
- **契约变更(§3.3,已双向确认)**:`POST /atp/evaluations` scenario 字段激活语义 + 4xx code 字段——
  **ATP 侧已就绪(M-F2/M-F3,2026-08-26)**,Hub 可对照 [scenario-schema.md](../../07-附录-scenario-yaml-schema.md) §7/§8 对接

#### 批次 E 建议开工顺序(本仓内,不依赖外部)

**M-E1 → M-E5 → M-E3 → M-E4 → M-E2 →(等 Hub)→ M-E6**;M-E7 贯穿收尾。
理由:M-E1 骨架先行冻结接口字段;M-E5 在真实流量前就位;M-E3 用 mock Hub 即可验收(语义价值最大);
M-E4 复用 M-E3 的 summary 生成器;M-E2 依赖运维 deploy key(O2)放内部项最后;M-E6 硬依赖 Hub 调度模块。

#### 批次 E 外部配合清单(2026-08-25 盘点;传递 Hub/PMS/运维,不阻塞本仓 M-E1~M-E4 开工)

**Hub 侧(同事,契约 §8.2;现状 v0.5.0,v1.5 改造均未开工)**:
1. checks 表迁移:`+atp_id`、`+job_id` 两列(ATP 轮询兜底前提)
2. config 增 `atp` 段:repo↔ATP **一对多**路由 `{base_url, service_token}`(池化)
3. dispatcher 分路:`check_type=autotest` → 择健康 ATP `POST /atp/evaluations`(202 即返回;4xx 直接判 failure;同 cid 幂等);
   非 autotest 检查保留 `workflow_dispatch`(§4.3b)
4. `atp_pool.py`:`GET /atp/health` 定期探活;不可达路由跳过 + `ci_alert`(无归位目标告警,§4.4 切分)
5. collector:autotest 兜底由 workflow_run 改为**轮询 `GET /atp/evaluations/{job_id}`** + 超时扫描(workflow_run 兜底仅对非 autotest 检查保留)
6. `checkruns.py`:归位后 GitHub check-runs 回写(name=check_type、head_sha=回调实际 sha、conclusion、output.summary、
   details_url=Hub console check 详情页);失败不阻塞,重试一次放弃
7. `POST /hub/manual-check` 改走 §4.8(ref 语义:实现=调用方传入、契约写 Hub 生成——差异入 R10 提请追认)
8. 【**阻断**】deploy 三文件路径残留旧仓名 `tz_cms`(目录已改名 tz_cicd_hub):cicd-hub.service WorkingDirectory、
   redis-cicd.service ExecStart、redis-cicd.conf dir——不修则 systemd 部署即失败
9. 文档随实现回写:《self-hosted-runner-对接契约》(v1.3 形态大面积过时)、《部署与运维》(补 atp token + App Checks:write)、
   《阶段计划》(N3 表 runner 对接项随 v1.5 废止)
10. 蓝本 workflows/autotest.yml 标注降级为"算法仓自测备选"(v1.5 主通路不再分发)

**PMS 侧(同事)**:**v1.5 零改动**(报文不变;run_url 语义改指 Hub console,PMS 透传无感)。
- 现状 v0.27.2:契约 11 条要求 9 条完整实现(联动测试 76 用例全绿);两处小偏差不阻塞
  (任务卡 CI 徽标为 cid 表合成;project 空时缺"提示管理员补绑定"通知)。
- 历史遗留 1 项(契约追认,提请 M):总契约 §4.5/§6/§8.1 写 `GET /api/admin/projects` 放行 service-token,
  PMS 实现为等价端点 `GET /pms/projects`——建议按变更纪律③回写契约勾销(或 Hub 消费侧确认用 /pms/projects)。

**M/运维层面**:
- O1:GitHub App 补 **Checks:write** 权限(check-runs 回写前提;现仅 Actions:write)
- O2:ATP 评测机预置 GitHub 只读 deploy key(cicd_test 等算法仓)+ workspace 目录规划(M-E2 依赖)
- O3:token 分发:新增 **`atp.service_token`**(Hub→ATP,每 ATP 一枚);hub.callback_token / service-token / webhook secret 已有
- O4:契约追认 2 项(PMS `/pms/projects` + Hub manual-check ref 语义,见 R10)

### Phase H1 · Hub MVP:通路1 主链路(**cicd-hub 工程,同事主导**)
> 进度以同事仓《docs/阶段计划-双通路端到端联调.md》为准(软链 `__temp__/cicd_hub`,同步开发中,代码常变)。
> **2026-08-25 盘点(Hub v0.5.0)**:v1.3/v1.4 资产完备——webhooks(验签/幂等/路由/中继/翻译)、Redis Stream 队列、
> dispatcher(workflow_dispatch 旧模式)、callback 归位(幂等+sha 回填)、pms_adapter(outbox+matched:false 重驱)、
> 超时扫描、manual-check+分支清理、events 三型翻译、四 pull 端点(缓存语义齐)、飞书 OAuth(C3a)、五 tab 控制台,约 145+ 测试。
> **v1.5 调度模块已基本实现(2026-08-25 读码确认)**:atp_pool(routes 一对多/健康优先择浅/转入不健康 ci_alert 一次)、
> dispatcher 分路(202 即返/4xx 判 failure 推 PMS/5xx 与无健康 ATP 留 pending 重投/duplicate 幂等/manual ref 语义/
> save_baseline 透传)、collector 轮询兜底(running 不动/终态归位推 PMS/回调先到 duplicate 安全/不可达不误判)、
> save_baseline 事件侧(主干 push 滚基线),test_atp_direct 15+ 用例;**剩余**:checkruns 回写(依赖 O1)、console ATP
> 面板、超时扫描对 ATP 的适配、文档回写、阻断项 deploy 三文件旧仓名(清单 Hub 1-10 之 6/8/9/10 等)。
- [x] webhooks / 路由 / Redis Stream 队列 + dispatcher(旧模式)/ callback 归位 / pms_adapter(同事侧,Hub v0.5.0)
- [ ] **验收(通路1,v1.5 形态)**:push → Hub → 直连 ATP → 评测 → 回调/轮询归位 → check-runs 回写 → ci-results → PMS 任务卡 + 飞书(= M-E6,随批次 E)

### Phase H2 · 通路3 手动触发(**cicd-hub 工程,同事主导**)
> **2026-08-25 盘点(Hub v0.5.0)**:`POST /hub/manual-check` + `mannultest/*` 白名单校验 + 终态清理临时分支 +
> 控制台手动触发表单均已实现(旧模式);**v1.5 需改走 §4.8 直连 ATP(外部配合清单 Hub 侧第 7 项)。**
- [x] `POST /hub/manual-check` + `mannultest/<user>/<ts>` 约定 + 终态清理临时分支(Hub v0.5.0,旧模式)
- [x] 最简触发界面(控制台手动触发表单)
- [ ] **验收(通路3,v1.5 形态)**:推前缀分支 → Hub 触发(直连 ATP)→ 回调带实际 sha → repo+sha 可查(随 M-E6)

### Phase H3 · 通路2 + 通路4(**cicd-hub 工程,同事主导**;PMS 侧见下)
> **2026-08-25 盘点**:Hub 侧 events 三型路由(ci_alert/deliverable_review/pr_merged)+ 四 pull 端点
> (/hub/ci-results 分组 latest_status、/hub/repos、/hub/github-users、/hub/repo-refs)均已实现;PMS 侧端点齐(v0.27.2)。**联合 E2E 未跑。**
- [x] events 路由:`ci_alert` / `deliverable_review` / `pr_merged`(Hub v0.5.0)
- [x] `GET /hub/ci-results`(latest_status 按 check_type 分组)、`GET /hub/repos`、`GET /hub/github-users`(+`/hub/repo-refs`,v1.4)
- [ ] **验收(通路2/4)**:告警/评审事件 → PMS → 飞书;交付物本地未命中时 pull 兜底成功(联合 E2E 待排,可并入 M-E6)

### PMS 侧清单(同团队并行,按 v1.3 §8.1,不经本 repo)
> **2026-08-25 盘点(PMS v0.27.2):全部落地**——`POST /pms/ci-results`(只认 status)+ `POST /pms/events`(三型枚举)+
> `GET /pms/tasks?repo=`(深链)+ service-token 认证(8 个 /pms/* 端点)+ cid 本地表(不回写飞书)+ repo↔项目绑定 +
> 出站 hub_client(通路4 本地优先,Hub 不可达不阻塞)+ `/pms/repo-projects` `/pms/lookup` `/pms/members`(v1.4 增补)+
> pr_merged 自动冻结(双幂等,matched:false 留重驱余地);联动测试 76 用例全绿。**v1.5 零改动。**
> 偏差记录(不阻塞,见批次 E 外部配合清单 PMS 项):① `/pms/projects` vs 契约 `/api/admin/projects` 提请 M 追认;
> ② 任务卡 CI 徽标为 cid 表合成、project 空时缺"提示管理员补绑定"通知。

### 后续(不阻塞打通)
lint 多检查编排(Hub Phase 1)、Hub 管理界面深化(ATP 池监控/操作审计)、批次 C(device action 回路)、批次 F(suite 联合测试、enc=pb)

## 4. 并行关系与依赖

```
A0 ─▶ A1 ─▶ A2 ─▶ A3/A4 ─▶ 批次B ─▶ 批次D(均已收口,本工程)
                                        └─▶ 批次E(M-E1~M-E5 ✅ 本仓全收口(2026-08-25);M-E6 待 Hub 调度模块;M-E7 文档贯穿收尾)
Hub v0.5.0(v1.3/v1.4 资产✅)─▶ v1.5 调度模块 6 项(同事)─┘
PMS v0.27.2(✅ 零改动);通路2/4 已具备联调条件,并入 M-E6
```

### 4.1 联调就绪度盘点(2026-08-25,按 v1.5 四通路)

| 通路 | Autotest 侧 | 就绪度 | 阻塞项 / 依赖 |
|---|---|---|---|
| **通路1**(自动闭环) | M-D1 HTTP 面就位(:2335);**批次 E 本仓全收口(M-E1~M-E5 ✅)** | **ATP 就绪,待 Hub** | ① ~~ATP 批次 E~~(本仓收口);② Hub 调度模块 6 项(外部配合清单 Hub 1-7);③ GitHub App 补 Checks:write(O1);④ ATP 机 deploy key(O2,部署配置);⑤ PMS 端点已齐(v0.27.2,零改动) |
| **通路3**(手动预提交) | 同通路1(共享执行路径) | **ATP 就绪,待 Hub** | 同通路1;Hub manual-check 旧模式已实现,待改 §4.8(Hub 第 7 项) |
| **通路2**(事件通知) | 无动作(ATP 不感知) | **就绪待联调** | Hub 三型翻译 + PMS events 均已实现;联合 E2E 未跑(可并入 M-E6) |
| **通路4**(交付物查询) | 无动作 | **就绪待联调** | Hub /hub/ci-results + PMS 本地 cid 表 + 出站兜底均已实现;联合 E2E 未跑(可并入 M-E6) |

**结论**:v1.5 瓶颈 = ~~ATP 批次 E(本仓)~~ **已收口(2026-08-25,M-E1~M-E5+M-E8 ✅,X3 机制就位)** →
Hub 调度模块**亦已基本实现(2026-08-25 读码确认)** → 当前进入 **M-E6 联合 E2E 准备期**,
待办:O1(App Checks:write)/O2(deploy key)/O3(正式 token+HUB_CALLBACK_* 配置)/Hub 阻断项 deploy 三文件;
通路2/4 已具备联合 E2E 条件,建议并入 M-E6 同一 PR 生命周期一并验收。
本仓 M-E1~M-E5 已全部本仓内闭环验收(mock Hub + file:// 模拟远端);
M-E2 的 deploy key(O2)仅部署配置(系统 ssh config),真仓验证并入 M-E6;M-E6 依赖 Hub 调度模块 + O1。

## 5. 风险与待决项

| # | 项 | 说明/处置 |
|---|---|---|
| W1 | ~~PR merged→交付物冻结 事件载体~~ | **已勾销(2026-08-23)**:契约 v1.4 §4.4 新增 `pr_merged` type(M 批准);PMS v0.26 已实现端点、Hub 2026-08-24 完成翻译切变(N1a),联合 E2E 随 Hub N1b |
| R2 | PMS 端点排期 | 同团队并行;Hub 侧 outbox mock 兜底不阻塞 |
| R3 | A1 迁移影响面 26 文件 | 协议变化需同步改示例 SUT;测试跟随,一轮迁移到位 |
| R4 | ~~GitHub App 注册 / runner 注册~~ | **已勾销(2026-08-24,v1.5)**:App 已完成(Hub 侧在用);runner 从评测通路移除(路径 A 拍板),注册动作取消;GitHub App 仅需补 Checks:write(check-runs 回写) |
| R5 | runner 上 autotest 包安装 | **已定案(2026-08-23)**:`pip install --user -e`,发行包名 tz_atp;已写入部署文档 |
| R6 | tzcomm daemon + Redis 常驻 | **已完成(M-D0/M-D1)**:tzcomm-daemon 系统级 + autotest.service 用户级(:2335) |
| R7 | token 分发 | service-token(Hub↔PMS)/ hub.callback_token(ATP→Hub)/ webhook secret 已有;**v1.5 新增 `atp.service_token`(Hub→ATP,每 ATP 一枚,见 O3)** |
| R8 | summary 映射 | report.json → 摘要文本规则随 A2 定死;M-E3 内化进 server 时沿用(vs_baseline 计数随 M-D3 定死) |
| R9 | Hub deploy 路径残留旧仓名 | cicd-hub.service / redis-cicd.service / redis-cicd.conf 三处指向 `tz_cms`(目录已改名 tz_cicd_hub),**systemd 部署阻断**;已入外部配合清单 Hub 第 8 项,随 v1.5 修正 |
| R10 | 契约追认 2 项(提请 M,变更纪律③) | ① 总契约 §4.5 写 `GET /api/admin/projects`,PMS 实现为等价 `GET /pms/projects`;② 总契约 §4.7 写 manual-check 的 ref 由 Hub 生成,Hub 实现为调用方传入 + `mannultest/*` 白名单校验——均建议按实现回写契约勾销 |
| R11 | M-E5 串行改造影响存量测试 | test_service_parallel_jobs 等并行语义用例需改为排队语义;改法:并发提交→断言串行执行顺序 |

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
| 2026-08-24 | **基准契约升 v1.4(M 批准)**:W1 勾销(pr_merged 入 §4.4,PMS/Hub 双侧已实现);<br>人操作认证拍板改飞书 OAuth(Hub C3a 已落地,service-token 仅机器间凭证,轮换=泄露时人工更换);<br>R5 定案 pip install --user -e(tz_atp);ATP 就绪公告转发 Hub/PMS,M-D4 待 R4 runner 人工注册 | 执行中,等 R4 后启动 M-D4(可与 Hub N1b 合并) |
| 2026-08-24 | **基准契约升 v1.5(M 批准路径 A:Hub 直连 ATP)**:契约就地升级(PMS 仓 schema/,唯一事实源,本仓 v1.3 副本删除);<br>**R4 勾销**(runner 从评测通路移除);M-D4 改道为**批次 E**(M-E1 submit/M-E2 workspace+checkout/M-E3 主动回调/M-E4 状态查询+探活/M-E5 串行/M-E6 联调/M-E7 文档);<br>D3 注记更新:workflow_dispatch 对 autotest 废止,GHA workflow 降级自测备选;<br>**三平台推进规划(v1.5)**:<br>· ATP(本仓):批次 E 七项,先行开工 M-E1~M-E5,Hub 就绪后 M-E6 联调;<br>· Hub(同事):① autotest 调度模块(repo↔ATP 一对多路由 + §4.8 submit + 排队);② ATP 池管理(/atp/health 探活 + ci_alert);③ 轮询兜底(GET /atp/evaluations/{job_id})+ 超时语义调整;④ check-runs 回写(App 补 Checks:write,§4.3c);⑤ /hub/manual-check 改走 §4.8;⑥ N3 表 runner 对接项随 v1.5 废止;<br>· PMS(同事):**零改动**(报文不变;run_url 语义改指 Hub console,透传无感);<br>· 联调:M-E6 与 Hub N1b 合并,cicd_test 同一 PR 生命周期全覆盖 | 已批准,批次 E 开工 |
| 2026-08-25 | **四仓现状全量盘点 + 子契约升 v1.2 + 批次 E 细化**:<br>**盘点**(对照总契约 v1.5):ATP 批次 E 未开工(HTTP 面仅运维面 4 端点,unit 139 全绿为基线);<br>Hub v0.5.0 停留 v1.3/v1.4(dispatcher 未分路/无 atp_pool/无 checkruns/checks 表缺 atp_id+job_id/config 无 atp 段;<br>deploy 路径残留 tz_cms 为阻断项 R9);PMS v0.27.2 基本就绪(9/11,余契约追认 + 两小偏差);TzComm 稳定无需改动;<br>**子契约 v1.1→v1.2**(§14-3 以实现为事实源回写,待 M 评审):§3.3 tzcomm 实现现实+v1.5 澄清(HTTP 面不受同机约束)、<br>§5.2 body_profile 键名、§7.1 插件文件名单数、§7.4/§9 删除 module 残留、§10 补调试命令/HTTP 运维面、<br>§11 整节重写 v1.5(§4.8 HTTP 面 + 主动回调)、§13 批次 D 收口+E 改向;文件随版本更名 v1.2;<br>**批次 E 细化**:M-E1~M-E7 补实现要点/依赖/验收;建议开工顺序 M-E1→M-E5→M-E3→M-E4→M-E2→M-E6;<br>**外部配合清单单列**(Hub 10 项/PMS 1 项/运维 O1-O4)待传递;§4.1 就绪度按 v1.5 重盘(通路2/4 就绪待联调);<br>§5 风险表新增 R9(deploy 路径)/R10(契约追认 2 项)/R11(串行改造影响),R7 补 atp.service_token | 待 M 评审子契约 v1.2;批次 E 开工就绪 |
