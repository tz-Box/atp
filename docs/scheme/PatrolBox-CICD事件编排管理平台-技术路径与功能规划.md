# Patrol Box · CI/CD 事件编排管理平台(CICD Hub)· 技术路径与功能规划

> 定位:一个独立的、轻量的 CI/CD **事件编排与管理平台**(内部代号 **CICD Hub**),作为 GitHub 与内部系统之间的中枢。
> 架构基线:**方案 D(独立平台作为事件中枢)+ 方案 C 的优势(可靠队列/异步解耦)**。
> 执行模型:**选项甲(编排者)**——平台只做"收事件→编排→调度→收集→汇总→上报",实际测试/构建仍跑在 **GitHub Actions + self-hosted runner**;平台是大脑,runner 是手脚,不重造 CI 引擎。

---

## 0. 为什么是独立平台(领域边界)

三个系统,三个清晰的领域(bounded context),不互相污染:

| 系统 | 领域 | 关心什么 | 不该承载什么 |
|---|---|---|---|
| **PMS**(已有) | 人与任务 | 谁在做什么、进度、任务卡、飞书提醒 | 不该塞满 commit/CI/构建这类流水线细节(否则变缝合怪) |
| **CICD Hub**(新建) | 代码与流水线 | 事件路由、检查编排、CI 触发与结果汇总、repo↔检查配置 | 不自己跑测试(委托 Actions/runner);不管人和任务(那是 PMS) |
| **autotest service + 其他检查**(已有/扩展) | 具体执行 | 跑测试/lint/构建/benchmark,产出报告 | 不直接对接 GitHub webhook(经 Hub 统一编排) |

**核心价值**:CICD Hub 把"监控/CI-CD 域"从 PMS 里剥离,统一管理"有哪些检查、每个 repo 触发哪些、结果如何汇总",并与 PMS 双向联动做配置。这是 PMS 和单个 autotest 都给不了的能力。

---

## 1. 总体架构

```
┌──────────────────────────────────────────────────────────────────┐
│  GitHub 组织(pbox-* 多仓)                                        │
│   组织级 Webhook(单一出口)                                       │
│   GitHub Actions + self-hosted runner(执行层,已有)              │
└───────────────┬──────────────────────────────▲───────────────────┘
                │ webhook 事件                   │ 触发 workflow / 拿回结果
                ▼                                │
┌──────────────────────────────────────────────┴───────────────────┐
│  CICD Hub(新建,编排者)                                          │
│                                                                    │
│  ┌──────────┐   ┌──────────────┐   ┌─────────────────────────┐    │
│  │ 事件接入 │──▶│ 事件路由/规则 │──▶│ 检查编排器              │    │
│  │ (验签/幂等)│   │ (按repo/事件) │   │ (决定触发哪些check)     │    │
│  └──────────┘   └──────┬───────┘   └───────────┬─────────────┘    │
│                        │                        │                  │
│         协作类事件      │            代码类事件   ▼                  │
│                        │              ┌──────────────────┐         │
│                        │              │ 任务队列(方案C)  │         │
│                        │              │ Redis Stream      │         │
│                        │              └────────┬─────────┘         │
│                        │                       │ 触发                │
│                        │                       ▼                    │
│                        │            ┌────────────────────────┐      │
│                        │            │ Actions 调度适配器      │──────┼──▶ GitHub Actions
│                        │            │ (workflow_dispatch/API)│      │    (self-hosted runner
│                        │            └────────────────────────┘      │     跑 autotest/lint/…)
│                        │                       ▲                    │
│                        │            ┌──────────┴─────────┐          │
│                        │            │ 结果收集器          │◀─────────┼── CI 结果/报告回传
│                        │            │ (汇总check状态+报告) │          │
│                        │            └──────────┬─────────┘          │
│                        │                       │                    │
│                        ▼                       ▼                    │
│                  ┌──────────────────────────────────┐              │
│                  │ PMS 联动适配器(双向)             │              │
│                  │ ·拉PMS项目列表↔repo配置           │              │
│                  │ ·推协作事件/CI结果→PMS(关联任务) │              │
│                  └──────────────┬───────────────────┘              │
│                                 │                                  │
│                  ┌──────────────▼───────────────────┐              │
│                  │ 管理界面(配置/监控/审计)         │              │
│                  └──────────────────────────────────┘              │
└──────────────────────────────┬───────────────────────────────────┘
                               │
                    ┌──────────▼──────────┐         ┌──────────────┐
                    │  PMS(人与任务)     │────────▶│  飞书(通知)  │
                    └─────────────────────┘         └──────────────┘
```

**数据流两条主线**:
- **协作类事件**(PR opened、issue、review):Hub 接入→路由判定为协作类→**原样中继转发给 PMS**(生 body + 原签名,D4)→PMS 关联任务卡/交付物业务→提醒飞书。Hub 只透传+审计,不做业务。
- **代码类事件**(push commit、tag):Hub 接入→编排器决定触发哪些检查→丢进任务队列→Actions 调度适配器触发对应 workflow(在 self-hosted runner 上跑)→结果收集器拿回报告→PMS 联动适配器合并进 PMS(按关联 ID 归位)→提醒飞书。

---

## 2. 选项甲的关键链路:Hub 如何触发 Actions 又如何拿回结果

这是整个"编排者"模型的命门,单独讲清楚。

### 2.1 触发:Hub → GitHub Actions

两种触发机制,择一或混用:

| 机制 | 说明 | 适用 |
|---|---|---|
| **`workflow_dispatch`(推荐)** | Hub 通过 GitHub API 主动触发指定 workflow,可传入参数(commit、检查类型、Hub 侧关联 ID) | Hub 完全掌控"触发哪个检查、传什么参数",编排能力最强 |
| **`repository_dispatch`** | Hub 发一个自定义事件类型到 repo,workflow 监听该事件类型触发 | 适合"一个事件触发一类 workflow"的解耦场景 |

**关键设计:触发时把 Hub 侧生成的"关联 ID"作为参数传进 workflow**。这个 ID 贯穿"触发→执行→报告回传→PMS 归位"全链路,是对账的锚点。

```
Hub 触发(伪代码):
  correlation_id = generate_id()                  # Hub 生成,贯穿全链路
  db.save_pending_check(correlation_id, repo, commit, check_type, pms_task_id)
  github_api.workflow_dispatch(
      repo, workflow="autotest.yml",
      inputs={ "correlation_id": correlation_id, "commit": sha, "check_type": "autotest" }
  )
  # 发出即返回,不等结果(异步投递语义)
```

### 2.2 拿回结果:GitHub Actions → Hub

两条回传通道,建议主用回调、辅以状态兜底:

| 通道 | 说明 | 角色 |
|---|---|---|
| **workflow 内主动回调(推荐主用)** | autotest 跑完,在 workflow 最后一步 `curl` 把报告 + `correlation_id` POST 回 Hub 的回调端点 | 携带完整报告 + 关联 ID,语义最完整 |
| **Hub 订阅 `workflow_run` webhook(兜底)** | GitHub 在 workflow 完成时发 `workflow_run` 事件给 Hub,Hub 据此知道成功/失败 | 兜底"回调没发出来"(如 runner 崩溃),保证 Hub 不会永远等 |

```
workflow 末步(autotest.yml 内):
  - name: Report back to Hub
    if: always()                                   # 成功失败都回传
    run: |
      curl -X POST "$HUB_CALLBACK_URL" \
        -H "Authorization: Bearer $HUB_TOKEN" \
        -d @report.json                            # report.json 含 correlation_id + 结果 + 报告
```

```
Hub 回调端点:
  POST /api/ci/callback:
    verify_token()
    report = parse()
    cid = report.correlation_id
    db.update_check_result(cid, report)            # 按关联 ID 归位
    pms_adapter.merge_report(cid, report)          # 合并进 PMS
    if report.failed: feishu.notify(...)           # 失败提醒
```

**幂等 + 超时**:同一 `correlation_id` 的回调可能重复(回调 + workflow_run 兜底都到),按 cid 幂等。为每个 pending check 设超时,超时未回结果的标记为 timeout 并告警(防止 runner 崩了永远挂起)。

---

## 3. 模块划分(CICD Hub 内部)

| 模块 | 职责 | 备注 |
|---|---|---|
| **事件接入** | 收 GitHub 组织级 webhook;HMAC 验签;delivery-id 幂等 | 唯一入口,安全底线 |
| **事件路由/规则引擎** | 按 repo + 事件类型,判定"协作类→PMS"或"代码类→编排" | 规则可配置(见管理界面) |
| **检查编排器** | 决定某代码事件要触发哪些检查(autotest/lint/build/benchmark) | 编排核心,支持一事件多检查 |
| **任务队列(方案C)** | 代码类检查任务入队,削峰 + 可靠投递 + 时间解耦 | Redis Stream(已拍板,D2),慢消费者友好 |
| **Actions 调度适配器** | 经 GitHub API 触发 workflow_dispatch,传 correlation_id | 触发层 |
| **结果收集器** | 收 workflow 回调 + workflow_run 兜底;汇总多检查状态 | 拿回结果,按 cid 归位 |
| **PMS 联动适配器** | 双向:拉 PMS 项目列表做配置;推事件/结果给 PMS 关联任务 | 与 PMS 的唯一接口面 |
| **管理界面** | 配置(repo↔检查↔PMS项目映射)、监控(事件流/队列/检查状态)、审计(事件全生命周期追溯) | 平台价值的呈现层 |

---

## 4. 与 PMS / autotest 的接口契约(草案)

### 4.1 Hub ↔ GitHub
- 入:组织级 webhook(push/tag/PR/issue/review/workflow_run),HMAC 验签。
- 出:GitHub API `workflow_dispatch`(触发)。

### 4.2 Hub ↔ autotest(及其他检查)
- 触发:经 Actions workflow,Hub 不直接调 autotest(autotest 是 workflow 里的一步)。传入 `correlation_id`。
- 回传:workflow 末步 POST `report.json`(含 correlation_id + 结果 + 报告 URL/内容)到 Hub 回调端点。

### 4.3 Hub ↔ PMS(双向,关键)
| 方向 | 接口 | 用途 |
|---|---|---|
| Hub → PMS(读) | `GET /api/admin/projects`(复用 PMS 既有端点,service-token 放行,D9)、`GET /pms/tasks?repo=`(待 PMS 新增) | 拉项目/任务列表,供 repo↔项目配置、事件关联 |
| Hub → PMS(写) | `POST /pms/events`(协作事件)、`POST /pms/ci-results`(CI 结果,带 correlation_id 对应的 task) | 推事件/结果,PMS 据此更新任务卡 + 提醒飞书 |

**对账锚点**:Hub 生成的 `correlation_id` 在触发时关联到 `pms_task_id`;结果回传时 Hub 据 cid 找到 task,再写给 PMS。PMS 不需自己对账。

**飞书归属**:飞书通知**由 PMS 统一发出**(PMS 是人/任务域,提醒也属该域),Hub 不直接发飞书,保持领域干净。(若某些纯 CI 告警想让 Hub 直发运维群,可作例外,但默认走 PMS。)

---

## 5. 分阶段功能规划

> 与 Patrol Box 6+2+4 节奏错峰:Hub 是**支撑性基础设施**,MVP 要赶在算法收口需要自动化测试之前就位(即 Patrol Box 阶段一末/阶段二初)。

### Phase 0 · 骨架(与 Patrol Box 阶段一并行,第 1–3 周)
**目标**:最小可用——webhook 单一入口 + 直触发 autotest + 结果回 PMS(mock),先把主链路打通。PMS 与 Hub 同属可控范围,本阶段即收编 webhook 入口(D4);队列本阶段即采用 Redis Stream(D2,单消费组,不展开编排)。
- 事件接入(验签/幂等)
- 事件路由 + **中继**(D4):GitHub 组织级 webhook 唯一指向 Hub;协作类事件(PR/review/issue)生 body + 原 `X-Hub-Signature-256` 原样转发 PMS `/api/webhooks/github`;PMS 侧仅改配置(`github_webhook_secret` 与 App secret 统一),业务零改动
- 任务队列(Redis Stream,XADD 入队 + 单消费组,本阶段即就位)
- Actions 调度适配器(GitHub App 认证 + workflow_dispatch + correlation_id)
- 结果收集器(回调 + workflow_run 兜底)
- PMS 联动适配器(契约 v1 + mock 实现:推送先落本地 outbox 可查;PMS 侧端点由同一团队并行开发,见 §8.1)
- **验收**:① 一个 pbox 仓 push commit → Hub 触发 autotest workflow → 报告回 Hub → mock PMS 适配器收到合并结果,全链路跑通,correlation_id 对账正确;② PR 合并事件经 Hub 中继到 PMS,交付物自动冻结/评审确认链路不回归。
- **产出物**:Hub MVP 服务 + 三方接口契约 v1 + 主链路 demo。

### Phase 1 · 编排与汇总(Patrol Box 阶段二,第 4–6 周)
**目标**:从"单检查直触发"升级到"多检查编排 + 结果汇总"(队列已在 Phase 0 就位,本阶段重点是编排器与汇总语义)。
- 检查编排器(一事件触发多检查:**autotest + lint 优先**,build 视仓库成熟度跟进)
- 队列展开为多消费者组(按检查类型分消费组,慢消费者互不影响;削峰 + 可靠投递兑现)
- 结果收集器汇总多检查状态(全绿才算 check 通过)
- 超时/重试/告警机制(pending check 超时标记 + 告警)
- PMS 真实对接(同一团队并行开发的 PMS 侧端点就绪后,mock 平滑切换)
- **验收**:一个 commit 触发 autotest + lint 并发跑,结果汇总后统一回 PMS;队列削峰(高频 commit 不打爆 autotest);autotest 临时下线任务不丢。
- **产出物**:编排器 + 多检查汇总 + 可靠投递 + PMS 真实联动。

### Phase 2 · 管理界面与 PMS 深度联动(Patrol Box 生态阶段,第 7–12 周)
**目标**:平台化——可配置、可监控、可审计,与 PMS 双向联动。
- 管理界面:
  - **配置**:repo ↔ 检查类型 ↔ PMS 项目 的映射(从 PMS 拉项目列表做关联)
  - **监控**:实时事件流、队列深度、各 repo 检查状态看板
  - **审计**:单个事件/commit 的全生命周期追溯(触发了哪些检查、各自结果、何时回 PMS)
- PMS 深度联动:双向配置同步、CI 结果直接落到对应任务卡
- **功能迁移收口**(§8.2):GitHub 侧 PMS 直连 webhook 配置退役(唯一入口已在 Phase 0 收编到 Hub,此处删除 PMS 侧冗余入口配置);PMS 的 `/api/webhooks/github` 退化为仅接受 Hub 中继的内部端点,远期可重构为类型化内部事件 API(验签→内网认证),PMS 中"代码/流水线域"的残留逻辑同步剔除
- **验收**:非命令行方式即可为新 repo 配置检查流水线并关联 PMS 项目;可视化监控所有在跑检查;任一事件可审计全链路。
- **产出物**:Hub 管理控制台 + PMS 双向联动 + 审计能力。

### Phase 3+ · 扩展(后续,方向性)
- 更多检查类型接入(安全扫描、benchmark 回归作为一种 check、覆盖率)
- 检查结果趋势分析(呼应巡检产品的 benchmark 历史基线理念)
- 多组织/工业线复用(新增前缀仓零改动接入)
- 检查编排的 DAG 化(检查间依赖:build 通过才跑 test)

---

## 6. 与既有架构原则的一致性

| 原则(前序方案) | Hub 的落地 |
|---|---|
| 单一事实源 | GitHub 事件唯一入口在 Hub;但**领域事实源分置**:人/任务事实在 PMS,流水线事实在 Hub。二者经 correlation_id 关联,不重叠 |
| 领域边界清晰 | PMS(人/任务)/ Hub(代码/流水线)/ 执行层(Actions+runner)三分,不缝合 |
| 异步投递语义 | 触发即返回,结果回调;队列 Phase 0 即就位(Redis Stream),Phase 1 展开多消费组 |
| 幂等/验签 | webhook 验签 + delivery-id 幂等;回调按 correlation_id 幂等 |
| 飞书为通知末端 | 由 PMS 统一发飞书,Hub 不直发(领域干净) |
| 执行层复用 | 选项甲:不重造 CI,复用 Actions + self-hosted runner |

---

## 7. 风险与提示

| 项 | 提示 |
|---|---|
| Hub 成为新单点 | Hub 挂了则检查触发中断;但 Hub 是无状态编排层(状态在队列/DB),可快速重启;关键状态持久化,重启不丢 pending check |
| correlation_id 对账 | 全链路锚点,务必贯穿触发→执行→回传;缺失会导致报告无法归位 |
| workflow_dispatch 权限 | Hub 需 GitHub token 有触发 workflow 权限;已拍板用 GitHub App(权限 Actions:write,最小化授权) |
| 回调丢失 | 主用 workflow 回调 + workflow_run webhook 兜底 + 超时告警,三重保障不永久挂起 |
| 队列运维 | 本机 Redis 7.2.5 已装未跑,需启用为常驻服务(systemd);Hub 重启时消费组 pending 消息自动恢复,注意消费者侧幂等 |
| 免费版 Actions 配额 | Hub 触发的 workflow 仍走 self-hosted runner 规避配额(与前序方案一致) |
| Hub 与 PMS 耦合度 | 经明确接口契约(第4章)通信,不共享数据库;PMS 项目列表经 API 拉取,不直连其库 |
| PMS 侧端点缺口 | /pms/ci-results 等端点不存在,且 PMS 仅有 Cookie 会话认证、无服务间 token;两系统同一团队,并行开发 + mock 兜底,不阻塞 Hub;改造清单见 §8.1 |
| webhook 中继(D4) | Hub 成为 PMS 协作事件链路的依赖:同机部署 + systemd 自动重启缓解;中继必须**逐字节转发原始 body**(禁止 re-serialize JSON),否则 PMS 侧验签失败,须有回归测试兜底 |
| GitHub App 配置 | 需组织内注册 App(权限 Actions:write + metadata + webhook 订阅),私钥与 App ID 入 Hub 配置管理;installation token 1h 过期,适配器内需自动刷新 |

---

## 8. 技术选型与决策记录(ADR)

> 2026-08-17 拍板。综合两版选型方案与负责人输入:**PMS 与 Hub 同属可控范围、协同演进;衔接面统一规划;PMS 中属"代码/流水线域"的功能,先在 Hub 实现、再在 PMS 剔除**(迁移路径见 §8.2)。

| # | 议题 | 决策 | 关键理由 |
|---|---|---|---|
| D1 | 语言/框架 | **Python 3.10 + FastAPI + uvicorn + SQLite**(跟随 PMS 技术栈) | Hub 是低吞吐编排器,性能非瓶颈;同栈复用 PMS 已验证模式(router 分层、本地库运行权威、验签/幂等写法、systemd + nginx 部署),单一团队心智 |
| D2 | 队列 | **Redis Stream,Phase 0 即就位**(单消费组 → Phase 1 多消费组) | CMS 推进快,省去"DB 轮询→队列"二次迁移;XADD/XREADGROUP 天然匹配可靠投递 + 慢消费者友好;本机 Redis 7.2.5 已装,启用为常驻服务即可;不选 NATS/RabbitMQ(新组件运维) |
| D3 | GitHub 认证 | **GitHub App(一次到位)** | 权限最小化(Actions:write)、审计记 App 不记个人、installation token 自动刷新;**副产物**:App webhook secret 与 PMS secret 统一后,D4 中继的 HMAC 天然通过 |
| D4 | webhook 一本化 | **Hub = 唯一入口 + 中继代理,Phase 0 即收编** | 两系统均可控,PMS 仅需配置改动(secret 统一,不动代码);协作类事件生 body + 原 `X-Hub-Signature-256` 原样转发 PMS `/api/webhooks/github`,PMS 既有业务(PR 合并→交付物冻结、approve→评审确认)零改动保留;统一审计从第一天成立 |
| D5 | PMS 对接 | **契约先行 + mock 适配器起步,PMS 端点同团队并行开发** | Hub 不被阻塞;mock 推送落本地 outbox 可查可演示;PMS 侧端点(§8.1)就绪即平滑切真实推送 |
| D6 | 检查接入顺序 | **autotest + lint 优先**,build 视仓库成熟度跟进,benchmark/安全扫描/覆盖率放 Phase 3 | autotest 须在 Patrol Box 阶段二前就位;lint 成本极低,立刻验证多检查编排价值 |
| D7 | correlation_id | **`chk_<26位ULID>`,不透明 + DB join** | ID 不埋语义;对账靠 DB(cid → repo/sha/check_type/pms_task_id/status);PMS 侧不透明、回写原样带回 |
| D8 | 管理界面(Phase 2) | **原生 JS 单页,复用 PMS 视觉风格,无构建步骤** | 开发以 AI 为主,两种范式产能相当;单一范式 + 可直接复用 PMS 组件(抽屉/徽章/泳道样式)是决定性优势,不引入 htmx 造成跨项目前端割裂 |
| D9 | 读 PMS 项目列表 | **复用 PMS 既有 `GET /api/admin/projects`**(service-token 放行) | PMS 侧少实现一个端点;其余必需接口随开发在契约中增补(§8.1 第 5 条) |
| D10 | 部署 | systemd 用户服务 + 端口 **2334** + nginx 反代(与 PMS 同机同模式) | 运维模式复制 PMS,开机自启 |

**requirements(Hub)初版**:`fastapi uvicorn httpx pyjwt cryptography redis pytest`。

### 8.1 需 PMS 侧配合的改造清单(同一团队,排期协调)
1. `github_webhook_secret` 改为与 Hub App webhook secret 一致(**配置项**,不动代码)。—— D4
2. 新增 service-token(Bearer)认证路径,放行服务间端点(复用 PMS webhook"无会话 + 校验"既有范式)。—— D5/D9
3. 实现 `POST /pms/ci-results` handler:cid → task 归位(Hub 随结果带回映射)+ 更新任务卡 + 触发飞书。—— D5
4. `GET /api/admin/projects` 对 service-token 放行。—— D9
5. (按需,随 Hub 开发提出)`GET /pms/tasks?repo=` 等补充接口,在契约 v1 中增补。
6. (功能迁移收口,Phase 2)GitHub 侧 PMS 直连 webhook 配置退役;PMS 端点退化为内部中继接收端,远期重构为类型化内部事件 API。
> 其余 PMS 功能(飞书通知、交付物状态机、评审确认)**保持不变**,由 Hub 在其外围编排。

### 8.2 PMS ↔ Hub 功能迁移路径(领域归位)
判断标准:**代码/流水线域 → Hub;人/任务域 → PMS**。
- **迁入 Hub**:GitHub 事件接收/验签/路由/审计(PMS 不再直接挂 GitHub webhook);repo↔检查编排配置;CI 结果汇总与超时治理。
- **留在 PMS**:交付物状态机(冻结/评审确认的业务动作)、任务卡、飞书通知。
- 迁移方式:先在 Hub 实现并经中继跑通(Phase 0–1),PMS 侧原入口再退役剔除(Phase 2),全程无硬切换、可随时回退(GitHub webhook 重新指回 PMS 即可)。

### 8.3 工程拆分(Phase 0 落地清单)

```
tz_cms/
├── hub/
│   ├── server.py            # FastAPI 装配层( lifespan + include_router )
│   ├── core.py              # 共享基础设施(config / store / github app token / redis client)
│   ├── store.py             # SQLite 本地库:events / checks / config / outbox 表
│   ├── routers/
│   │   ├── webhooks.py      # 事件接入+中继:HMAC 验签 + delivery-id 幂等 + 落库 + 协作类原样转发 PMS(D4)
│   │   └── callback.py      # CI 回调:token 校验 + 按 cid 归位 + 幂等
│   ├── router_rules.py      # 事件路由:repo×事件类型 → 协作类/代码类(配置驱动)
│   ├── taskqueue.py         # Redis Stream 封装:XADD / XREADGROUP / XACK / pending 恢复
│   │                        #   (命名避开 stdlib queue,防遮蔽)
│   ├── dispatcher.py        # Actions 调度适配器:GitHub App 认证 + workflow_dispatch
│   ├── collector.py         # 结果收集器:汇总 + 超时扫描 + workflow_run 兜底处理
│   ├── pms_adapter.py       # PMS 联动:契约 v1 客户端 + mock 实现(落 outbox)
│   └── config.json          # 本地配置(App 凭据/webhook secret/redis 地址,不入库)
├── tests/                   # pytest:验签/幂等/路由/编排/回调归位/超时
├── contracts/
│   └── pms-contract-v1.md   # Hub↔PMS 接口契约(供 PMS 侧实现对照)
├── workflows/
│   └── autotest.yml         # 分发到 pbox 仓的 workflow 模板(收 cid、末步回调)
└── deploy/
    ├── cicd-hub.service     # systemd 用户单元
    └── nginx-cicd.conf      # 反代配置
```
