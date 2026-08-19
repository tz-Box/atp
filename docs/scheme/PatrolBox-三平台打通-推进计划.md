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
- [ ] protocol:data 信封 `{schema, v, enc, blob}`;`register_data(kind, schema_id, decoder)`;
      未知 schema **拒收记 error**(废静默透传);删除 SLAM/NAV/MANIP 常量
- [ ] registry:`register_body` + 三件套键命名空间化;场景装配 `produces ⊇ consumes` fail fast
- [ ] body:`body/pbox_v1.yaml` + scenario `body` 必填 + INIT 下发 + dataset 标定一致性校验
- [ ] GroundTruth `{schema, v, data}`
- [ ] `modules/slam` → `plugins/pipe.slam/`(data/datasets/checkers/scenarios + contract.md)
- [ ] `modules/nav` → `plugins/nav2d/`;删除 `modules/manip`(D5)
- [ ] 测试/examples/docs 跟随迁移(影响面 26 文件)
- [ ] **验收**:pytest 全绿 + `client run scenarios/synthetic_slam.yaml` 端到端通过 → v1.1 契约提请冻结

### Phase A2 · CI 链路对齐 v1.3(本工程)
- [ ] `autotest.yml`:`on` 只留 `workflow_dispatch`(inputs: correlation_id/check_type/commit);末步回调
- [ ] `report.py` 重写:POST `/api/ci/callback`(Bearer;`{cid, sha, check_type, conclusion, report:{summary, run_url?}, finished_at}`)
- [ ] report.json → summary 摘要文本生成(passed/n_records/metrics 概览)
- [ ] **验收**:mock callback server 校验载荷通过

### Phase H1 · Hub MVP:通路1 主链路(cicd-hub 新工程,可与 A1 并行启动)
- [ ] webhooks 接入(HMAC 验签 / delivery-id 幂等 / 落库)
- [ ] 事件路由(代码类→编排;协作/告警类→events,H3 展开)
- [ ] Redis Stream 队列(单消费组)+ dispatcher(GitHub App + workflow_dispatch + cid + pending 落库)
- [ ] callback 端点(按 cid 归位 / 幂等 / workflow_run 兜底 / 超时标记)
- [ ] pms_adapter:`POST /pms/ci-results`(带 project + 结构化 report;任务关联规则按 §5 通路1);outbox mock 起步
- [ ] **验收(通路1)**:push → Hub → workflow → autotest → 回调 → ci-results → PMS 任务卡 + 飞书

### Phase H2 · 通路3 手动触发
- [ ] `POST /hub/manual-check` + `mannultest/<user>/<ts>` 约定 + 终态清理临时分支
- [ ] 最简触发界面(CLI/表单即可)
- [ ] **验收(通路3)**:推前缀分支 → Hub 触发 → 回调带实际 sha → repo+sha 可查

### Phase H3 · 通路2 + 通路4
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
A0 ─▶ A1 ─▶ A2 ─┐
      (H1 可与 A1 并行启动;回调接口面 v1.3 §4.3 与 autotest 内部协议无关)
                ├─▶ 通路1 联调(需 A2 + H1 + PMS 端点三者齐备)
H1 ─▶ H2 ─▶ H3 ─┘
```

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
