# Patrol Box · 三平台通信与接口总契约 v1.3(冻结评审稿)

> 统辖三个系统的通信流程、接口定义、调用逻辑,指导 **PMS / CICD Hub / Autotest Service** 并行开发与对接。
> 本文档是三平台对接的**顶层总契约**,与两份既有子契约的关系:
> - 《途零 · Autotest Service 接口契约 v1.1》——Autotest 内部(tzcomm/World/协议/checker)的权威依据,本文档**不覆盖其内部**,只对接其 §11(CI/CD)与 §10(Client)边界。
> - 《Hub ↔ PMS 接口契约 v1》——本文档**吸收并扩展**它(新增 pull 查询、术语统一)。
>
> **v1.0 三处修正(已拍板)**:①通路3用解法A(复用 `mannultest` 分支,不破坏 tzcomm 同机约束);②术语统一 **CMS = CICD Hub**(下文一律称 Hub);③新增 PMS→Hub 的 pull 查询接口。
>
> **v1.1 修订(2026-08-19,五点对齐,三平台同步)**:
> ①**GitHub 归属分层**:repo 清单 + GitHub↔飞书账号绑定归 **Hub**(直连 GitHub、复用飞书登录);
>   repo↔项目/任务的业务绑定归 **PMS**;PMS 从 Hub **读** GitHub 账号关联(§1.5、§4.5b)。
> ②`POST /pms/ci-results` 补 `project` 字段(PMS 分项目建表,单凭 task_id 定位不到项目,§4.4)。
> ③**cid 落 PMS 本地表**(不回写飞书,避免飞书 schema 变更/回写死信,§6)。
> ④**通路4 降级为可选兜底**:CI 结果正常经通路1 push 到 PMS,交付物提交时直接用本地已存结果;
>   仅当本地无该 commit 结果时 PMS 才 best-effort 出站 pull(§4.6、通路4 时序)。
> ⑤`GET /pms/tasks?repo=` 语义定为 **repo→项目(PMS 绑定)→任务**,每条任务附可点击跳转 URL(§4.5)。
>
> **v1.2 修订(2026-08-19,采纳 Autotest 侧审查四点)**:
> ⑥**通路3 改前缀分支族**:`mannultest` 单分支有并发缺陷(多人手动测试互相覆盖)+ 分支膨胀;
>   改为 `mannultest/<operator>/<ts>` 前缀族,`workflow_dispatch` 用实际分支,配合自动清理;
>   解法A 精神(不跨机、不直连)不变(§3/§4.7/§5 通路3/§10)。
> ⑦**回调必带实际 sha**:通路3 触发时 Hub 落 pending 的 sha 是分支头(甚至空),真实 checkout 的
>   sha 只有 runner 知道;回调补 `sha`+`finished_at`+`check_type`,否则手动测试结果按 repo+sha 永远查不到(§4.3;已入 Autotest §11)。
> ⑧**cid 格式收敛**:直接采纳 Hub D7 `chk_<26位ULID>`,§6 定死、§10 删除该待拍板项。
> ⑨**§10 不再回退已拍板项**:队列=Redis Stream(Hub D2)、触发认证=GitHub App(Hub D3)均已定,
>   §10 引用而非重开。
>
> **v1.2 补订(2026-08-19,Autotest 侧二审六点)**:
> ⑩`/pms/events` type **枚举化**:v1 集合 = `ci_alert` / `deliverable_review`,payload 定死(§4.4);
> ⑪ci-results 的 `project` 改"**repo 已绑定则必填**",未绑定置空照发、PMS 记未归类事件流(§4.4);
> ⑫§4.1 收敛为 GitHub App(D3);通路4 时序改**本地 cid 表优先、出站兜底**(§3/§5);§4.7 删"Hub 代推";
> ⑬手动测试语义写明=**仅预验证、不作交付附件**;report/徽标链接一律指向 **workflow run**(§4.3/§5 通路3);
> ⑭子契约引用同步为 Autotest v1.1 章节号(§7/§8.3)。
>
> **v1.2 二轮补订(2026-08-19,原定"随开发补"三条提前修复,冻结前定死)**:
> ⑮`/hub/ci-results` 的 `latest_status` 改**按 check_type 分组**对象(§4.6);
> ⑯`report` 字段定死为**结构化摘要对象** `{summary, run_url?}`,废弃"URL 或摘要"二义写法与独立 summary 字段(§4.3/§4.4/§4.6);
> ⑰通路1 `pms_task_id` **自动关联规则**定死:repo 未完成任务恰 1 条则关联,0/多条置空由人认领(§5 通路1)。
>
> **v1.3 修订(2026-08-19,采纳 PMS 侧审查六点,冻结候选)**:
> ⑱Autotest 章节引用**全文对齐** v1.1(§4.1/§4.3/§7 结论/§9 阶段表 的 §9→§11;§2 的 §3.4→§3.3;变更纪律承接 §14);
>   完整新旧章节映射表入档为**附录 A**,后续引用以映射表为准一次核对。
> ⑲通路2 叙述与 §4.4 枚举对齐(§3 表/§5,删 `repo_event` 残留,补 `deliverable_review`);
> ⑳`report` 字段在 §4.4/§4.6 **展开为对象**(与 §4.3 一致,消除"字符串占位"对实现方的误导);
> ㉑§1.5 删"或 ci-results 扩展"悬尾,deliverable_review 锁定走 events;
> ㉒ci-results 注明 `conclusion` 为 Hub 内部透传、**PMS 只认 `status`**;
> ㉓**ci-results(failure) 与 ci_alert 职责切分定死**:有 cid/归位目标的失败走 ci-results,
>   无归位目标的基础设施/整体告警走 ci_alert,同一次失败不重复通知(§4.4)。
>
> **v1.3 三轮补订(2026-08-19,PMS 侧冻结评审三点)**:
> ㉔ci_alert 示例删「workflow_run 兜底超时」——兜底判出的 timeout 针对的正是 pending cid、
>   天然有归位目标,按 §4.4 切分恒走 ci-results(status=timeout);ci_alert 只留无归位目标项(§4.4/§5 通路2);
> ㉕ci_alert 触发措辞统一为「无归位目标(可带仅供追溯的 cid)」,消除与 payload `cid?` 的语义打架(§4.4);
> ㉖§5 通路1 时序回调补 `sha`,与 §4.3(v1.2 起必带)对齐。

---

## 0. 术语与命名统一(消除歧义)

| 术语 | 指代 | 旧称/别名 | 领域 |
|---|---|---|---|
| **PMS** | 项目管理系统(人与任务、飞书提醒) | — | 人/任务 |
| **Hub** | CICD 事件编排管理平台 | **CMS**(你之前通路描述用词)、CICD Hub | 代码/流水线 |
| **Autotest** | 统一自动化测试服务 | autotest service | 具体执行 |
| **Runner** | GitHub Actions self-hosted runner | — | 执行载体 |
| **cid** | correlation_id,全链路对账锚点 | correlation_id | 贯穿三平台 |

> **重要**:你此前通路描述中的 "CMS" 一律等价于本文档的 **Hub**。团队沟通请统一用 Hub,停用 CMS,避免与 PMS 混淆。

---

## 1. 三平台职责边界(bounded context)

| 平台 | 负责 | 不负责 | 与外部的接口面 |
|---|---|---|---|
| **PMS** | 任务卡/迭代/里程碑;交付物;**飞书通知统一出口**;**repo↔项目/任务的业务绑定**(哪个 repo 属哪个项目/任务) | 不跑测试;不感知 tzcomm;不直连 GitHub;**不维护 repo 清单、不做 GitHub 账号绑定**(读 Hub) | 对 Hub:被推 ci-results/events、被查 tasks(按 repo)、主动读 repos/github-users、按需 pull ci-results |
| **Hub** | GitHub 事件唯一入口;检查编排;触发 workflow;收集结果;push/pull 双向对接 PMS;**GitHub repo 清单 + GitHub↔飞书账号绑定**(直连 GitHub、复用飞书登录) | 不自己跑测试(委托 Runner);不感知 tzcomm;不发飞书(交 PMS);**不做 repo↔项目/任务绑定**(那是 PMS) | 对 GitHub:webhook 入 + API 触发;对 Autotest:仅经 workflow 回调;对 PMS:双向 + 提供 repos/github-users 读 |
| **Autotest** | 在评测宿主机上跑测试(tzcomm/World/checker);产报告 | 不直接对接 GitHub webhook;不感知 Hub 内部;client 与 server 必须同机 | 对 Runner:tzcomm Service(本机);对 Hub:仅 workflow 末步回调 |

**领域事实源分置**:人/任务事实在 PMS,流水线事实在 Hub,评测执行事实在 Autotest;三者经 **cid** 关联,不共享数据库。

### 1.5 GitHub repo / 账号 / 绑定 的三层归属(v1.1 明确)

避免"repo 到底归谁"的反复,按"是否已涉及项目对接"切三层:

| 数据 | 归属 | 理由 | 对方怎么拿 |
|---|---|---|---|
| **GitHub repo 清单**(orgs 下所有 repo 的存在与元信息) | **Hub** | Hub 直连 GitHub,天然拿到全量;与项目无关 | PMS 读 `GET /hub/repos` 做绑定候选 |
| **GitHub↔飞书账号绑定**(github login ↔ 飞书/成员) | **Hub** | Hub 能读 GitHub orgs 成员,又复用飞书登录,一处做掉 | PMS 读 `GET /hub/github-users` 展示/归属 |
| **repo↔项目/任务的业务绑定**(哪个 repo 服务哪个项目/任务) | **PMS** | 这是人/任务域的业务事实 | Hub 读 `GET /pms/tasks?repo=` 反查任务 |

**推论**:①PMS 侧原 `github_user_map`(D-c 深度版临时放 PMS)**迁往 Hub**;approve→评审确认改由 Hub 解析 login→成员后回调 PMS(走 §4.4 `deliverable_review` 事件,v1.3 锁定,不再保留其他路径)。②PMS 配置"某 repo 归本项目"时,repo 候选来自 Hub 的 `GET /hub/repos`,PMS 只存绑定关系。

---

## 2. 部署拓扑(基于 Autotest §3.3,补全三平台)

```
[机器A · 评测执行面]                      [机器B · 管理面]
 Redis + tzcomm daemon (systemd)          Hub (FastAPI)
 Autotest server (常驻)                   PMS
 self-hosted Runner                       └─→ 飞书
   └ workflow → 本机 autotest.client run
       └ SUT 容器(--network host, docker+bind)

跨机唯一应用层接口:
  GitHub ──webhook──▶ Hub          (机器B 收 GitHub 事件)
  Hub ──GitHub API──▶ 触发 Runner 上 workflow (机器A 执行)
  机器A workflow 末步 ──HTTP 回调──▶ Hub  (§4.3)
  Hub ⇄ PMS  (机器B 内部,§4.4/4.5)
```

**关键约束(源自 Autotest §3.3,不可违反)**:
- Autotest client 与 server **必须同机**(tzcomm UDP 组播 TTL=1、禁跨机直连 Redis、SUT 容器 `--network host`)。
- 故 **Hub 永不直接调 Autotest client**;一切测试触发都经"Hub→GitHub API→Runner 上 workflow→本机 client"这条路。这条纪律是通路3选解法A的根本原因。

---

## 3. 四条通路总览

> 你提的三条通路 + 拆出的 pull 查询,共四条。每条给"触发→流转→终点"一句话,详细时序见 §5。

| # | 通路 | 触发源 | 一句话流程 |
|---|---|---|---|
| **通路1** | 自动测试闭环 | repo push commit / PR | GitHub事件→Hub→触发workflow→Runner跑autotest→报告回Hub→push给PMS→(交付时)PMS附结果 |
| **通路2** | 远端事件通知 | CI 告警(基础设施/整体) / PR approve 评审 | 事件→Hub收并转发→PMS→飞书+系统内提示 |
| **通路3** | 手动预提交测试 | 工程师在Hub手动触发 | Hub手动触发→**推到`mannultest/<user>/<ts>`前缀分支**→触发workflow→同通路1后半段 |
| **通路4** | 交付物按需查询 | PMS提交交付物时 | PMS 先查本地 cid 表→未命中再 best-effort pull Hub(repo/sha)→附到交付物 |

---

## 4. 接口定义(逐个,方法/路径/字段/幂等/认证)

### 4.1 认证总览

| 通道 | 认证 | 说明 |
|---|---|---|
| GitHub → Hub(webhook) | HMAC-SHA256 验签(`X-Hub-Signature-256`) | webhook secret |
| Hub → GitHub(API 触发) | **GitHub App token**(Hub D3,已拍板) | 最小权限:Actions:write |
| Runner workflow → Hub(回调) | Bearer `hub.callback_token` | Autotest §11 已定义 |
| Hub ⇄ PMS | Bearer `service-token` | PMS 无会话认证路径(PMS 契约 §认证) |

### 4.2 GitHub → Hub:事件入口

`POST /api/github/webhook`
- 认证:HMAC 验签;幂等:`X-GitHub-Delivery` 去重。
- 订阅事件:`push`、`pull_request`、`issues`、`pull_request_review`、`workflow_run`。
- Hub 行为:验签→解析 `X-GitHub-Event`→路由(§5 各通路)。

### 4.3 Hub → GitHub → Runner → Hub:触发与回调

**触发(Hub→GitHub API)**:`workflow_dispatch`
```json
{
  "repo": "owner/pbox-xxx",
  "workflow": "autotest.yml",
  "ref": "<分支;通路1=事件分支;通路3=mannultest/<user>/<ts> 前缀分支>",
  "inputs": {
    "correlation_id": "chk_01J...",   // Hub 生成,对账锚点
    "check_type": "autotest",
    "commit": "<sha>"
  }
}
```
触发前 Hub 落库 pending check(cid, repo, sha, check_type, pms_task_id, 超时时刻)。

**回调(Runner workflow 末步→Hub)**:`POST /api/ci/callback`(Autotest §11 已定义,本文档沿用)
```
Authorization: Bearer <hub.callback_token>
```
```json
{
  "correlation_id": "chk_01J...",
  "sha": "<实际 checkout 的 commit sha;v1.2 必带>",
  "check_type": "autotest",
  "conclusion": "success | failure",
  "report": { "summary": "<结构化摘要文本:passed/n_records/metrics 概览>",
              "run_url": "<workflow run URL,可选>" },
  "finished_at": "<完成时刻 ISO8601;v1.2 新增>"
}
```
**`sha` 为何必带(v1.2)**:通路1 Hub 从 push 事件已知 sha;但**通路3(手动,ref=分支)Hub 落 pending 时只有分支头甚至空**,真实 checkout 的 commit sha 只有 runner 知道。回调不带回 sha,则 §4.6 按 `repo+sha` 的查询(通路4)对手动测试**永远查不到**。故回调以 runner 侧实际 sha 为准回填 Hub 的 pending 记录。(已同步入 Autotest 契约 §11。)
- **report 格式(v1.2 二轮补订,定死)**:结构化对象 `{summary, run_url?}`,取代旧"URL 或摘要"二义写法(独立的 `summary` 字段同步删除)。`run_url` 一律指向 **workflow run**(run 日志独立于分支存活),禁止指向分支——通路3 临时分支终态后会被清理(§4.7),指向分支的链接将失效。报告全文留 runner artifacts,Hub Phase 2 展示层再解决拉取。
- 幂等:同 cid 去重(`{"ok":true,"duplicate":true}`);未知 cid → 404。
- 兜底:Hub 另订阅 `workflow_run` webhook,回调未达时据此判成功/失败;超时未回 → 标 timeout 告警。

### 4.4 Hub → PMS:写(push,吸收自 PMS 契约,术语统一)

**`POST /pms/ci-results`** — CI 结果归位(一个 cid 终态调一次;幂等按 cid)
```json
{
  "correlation_id": "chk_01J...",
  "project": "<项目 slug;repo 已绑定则必填;未绑定置空(null)>",
  "repo": "owner/pbox-xxx",
  "sha": "abc123...",
  "check_type": "autotest",
  "status": "success | failure | timeout",
  "conclusion": "success",
  "report": { "summary": "<结构化摘要文本>", "run_url": "<workflow run URL,可选>" },  // 同 §4.3,Hub 透传
  "pms_task_id": "<可空;非空则直接落对应任务卡>"
}
```
**`project` 语义(v1.1 引入,v1.2 补订放宽)**:PMS 分项目建表(表键 `{slug}:tasks`),单凭 `pms_task_id` 无法定位属哪个项目。Hub 触发时已知 repo→项目(经 §4.5 `GET /pms/tasks?repo=` 或其缓存的绑定),故顺手带上 `project` 最省事,PMS 不必反查。**repo 未绑定任何项目时 `project` 置空照发**(不阻塞结果归位)。
PMS 行为:`project` 非空→按 `project` 定位表;`pms_task_id` 非空→落任务卡+更新CI徽标+飞书(失败必通知),为空→记事件流不关联;`timeout` 视同 failure 告警。**`project` 为空(repo 未绑定)→记未归类事件流 + 提示管理员补 repo↔项目绑定,不报错、不丢弃。cid 存 PMS 本地映射表(§6),不回写飞书。**
响应:`200 {"ok":true}`(重复 `{"ok":true,"duplicate":true}`)。
**字段注记(v1.3)**:`conclusion` 仅为 Hub 内部透传保留,**PMS 只认 `status`**——success/failure/timeout 已覆盖 PMS 全部判定(落卡/徽标/告警),不读 conclusion。

**`POST /pms/events`** — 协作/系统事件(通路2,Phase 1 起用)
```json
{ "type": "ci_alert | deliverable_review", "repo": "owner/pbox-xxx", "payload": { "...": "按 type 定死,见下" } }
```
**v1 事件集合(枚举,冻结;新增 type 走变更纪律,不得私自扩展)**:

| type | 触发 | payload(定死) | PMS 行为 |
|---|---|---|---|
| `ci_alert` | **无归位目标**的基础设施/整体告警(runner 掉线、队列积压、Hub 自身故障、无对应 pending cid 的野生 workflow_run 事件等;可带一个仅供追溯的 cid) | `{cid?, check_type?, status, summary, operator?}` | 系统内提示 + 按规则推飞书告警 |
| `deliverable_review` | PR approve(承接 §1.5:Hub 解析 reviewer login→成员后下发) | `{pr_url, pr_number, action:"approved", reviewer_login, reviewer:{name, feishu_open_id?}, sha}` | 触发对应交付物的评审确认流程 + 飞书通知 |

**ci-results 与 ci_alert 的职责切分(v1.3 定死,防重复通知)**:同一次 CI 失败**只走一条路**——
有归位目标(pending cid,可定位到 repo/sha/任务卡)的失败或超时 → 走 `POST /pms/ci-results`(status=failure/timeout,PMS 失败必通知),**不再发 ci_alert**。
**含 §4.3 workflow_run 兜底判出的 timeout**——它检测的正是 Hub 已触发的 pending cid,天然有归位目标,恒走 ci-results。
无归位目标的基础设施/整体告警 → 走 `POST /pms/events`(type=ci_alert;payload 的 `cid?` 仅供追溯,不构成归位目标)。
判定责任在 Hub(只有 Hub 知道是否有 pending cid);PMS 侧不做去重。

### 4.5 Hub → PMS:读(pull)

- **`GET /api/admin/projects`**(PMS 既有,对 service-token 放行):拉项目列表(slug/name/status),供 repo↔项目映射。
- **`GET /pms/tasks?repo=<full_name>`**(PMS 新增,Phase 1):**语义(v1.1 明确)= repo →(PMS 的 repo↔项目绑定)→ 该项目任务**,供 Hub 把某 repo 的事件关联到任务、并给用户可点击入口。返回:
```json
{ "project": "<slug>",
  "tasks": [ { "record_id": "...", "标题": "...", "状态": "...",
              "url": "<PMS 深链,如 https://pms.../p/{slug}#t={record_id}>" } ] }
```
  - repo 未绑定任何项目 → `{"project":null,"tasks":[]}`。可选 `&open_only=1` 只返回未完成任务。
  - 注:PMS **不**提供"查某任务的 repo"(无此需求);方向恒为 repo→任务(§5 通路说明)。

### 4.5b PMS → Hub:读(pull,v1.1 新增,承接 §1.5 归属)

PMS 作为客户端读 Hub 的 GitHub 主数据(service-token,与 §4.6 同向出站):
- **`GET /hub/repos`**:Hub 返回可绑定的 GitHub repo 清单 `{"repos":[{"full_name","name","url","default_branch"}]}`,供 PMS 配置"repo↔项目"时做候选。
- **`GET /hub/github-users`**:Hub 返回 GitHub↔飞书/成员账号绑定 `{"map":{"<github_login>":{"name","feishu_open_id?"}}}`,供 PMS 展示 CI 操作人的真实身份/归属。**取代** PMS 侧原 `github_user_map`。

### 4.6 PMS → Hub:pull 查询(★本版新增,支撑通路4)

**`GET /hub/ci-results?repo=<full_name>&sha=<sha>`** — 交付物按需查询最新 CI 结果
- 认证:Bearer service-token(与 4.4/4.5 同 token,反向调用)。
- 用途(v1.1 降级为**可选兜底**):**正常路径**是通路1——push/PR 时测试已跑完并经 `POST /pms/ci-results` 落到 PMS,交付物提交时结果**早已在 PMS 本地**,直接取用即可,无需出站。**仅当** PMS 本地查不到该 commit 的结果(如测试时未带 `pms_task_id`、或跨项目补录)时,才 best-effort 调本接口补拉;**Hub 不可达/超时不得阻塞或失败交付物创建**(降级为"暂无测试结果"提示)。
- 返回:
```json
{
  "repo": "owner/pbox-xxx",
  "sha": "abc123...",
  "results": [
    { "correlation_id":"chk_...", "check_type":"autotest",
      "status":"success",
      "report": { "summary":"<结构化摘要文本>", "run_url":"<workflow run URL,可选>" },
      "finished_at":"<ts ISO8601>" }
  ],
  "latest_status": { "autotest": "success", "lint": "failure" }
}
```
- **`latest_status` 按 check_type 分组**(v1.2 二轮补订):每类检查各自的最新状态(success/failure/timeout),聚合判定交给 PMS 按分组自行处理——单一标量在多检查混合时语义不明(如 autotest 绿/lint 红),已废弃。
- 无结果:`results:[]`,`latest_status:{}`(空对象即"none";PMS 据此提示"该交付物暂无测试结果")。
- 可选参数:`&check_type=autotest` 只查某类检查(此时 latest_status 为单键);`&task_id=` 按任务查。

**对称性说明**:至此 Hub↔PMS 为**双向**——Hub 主动 push 结果(4.4)+ PMS 主动 pull 查询(4.6),覆盖"实时通知"与"交付时按需附加"两种模式。

### 4.7 Hub 手动触发端点(★支撑通路3,解法A)

**`POST /hub/manual-check`** — 工程师经 Hub 界面手动发起测试
```json
{
  "repo": "owner/pbox-xxx",
  "ref": "mannultest/<operator>/<ts>",   // v1.2:前缀分支族,每次手动测试一条独立分支
  "check_type": "autotest",
  "pms_task_id": "<可空,关联任务>",
  "operator": "<发起人>"
}
```
- Hub 行为:生成 cid→落 pending→走 §4.3 的 workflow_dispatch(ref=该前缀分支)。**与自动触发共用同一条执行路径**,仅触发源不同。
- **前缀分支族(v1.2,解决单分支并发缺陷)**:多人同时对同一 repo 手动测试若共用单一 `mannultest` 分支会互相覆盖/排队。改约定 **`mannultest/<operator>/<ts>`**:工程师把待测代码**自行**推到该唯一分支(Hub 不参与代码推送;如需代建分支,Hub 仅从指定既有 ref 创建——预提交代码在工程师本地,Hub 无物可推),`workflow_dispatch` 的 `ref` 用该实际分支;Autotest workflow 触发匹配 `mannultest/**`(已入 Autotest §11)。**自动清理**:测试终态后 Hub 删除该临时分支(或定期清理 `mannultest/*`),避免分支膨胀。
- 前置约定不变:Hub 不直连内网 git、不跨机调 autotest client(守 §2 约束);解法A 精神仅从"单分支"变"前缀族"。

---

## 5. 四条通路端到端时序

### 通路1 · 自动测试闭环(push/PR → 报告 → PMS)
```
repo push commit / PR opened
  └─▶ GitHub webhook ─▶ Hub /api/github/webhook (验签/幂等)
        └─ 路由:代码类事件 ─▶ 编排器决定 check 集
              └─ 生成 cid,落 pending;pms_task_id 关联规则(v1.2 二轮补订,定死):
                   §4.5 查该 repo 未完成任务(open_only=1)恰 1 条 → 关联之;
                   0 条或多条 → 置空(仍带 project 落事件流,人在 PMS 经深链认领)
              └─ [Phase1: 入队] ─▶ workflow_dispatch(cid, ref=事件分支)
                    └─▶ Runner: autotest.client run --json (本机 tzcomm 调 server)
                          └─ SUT 容器跑评测 ─▶ report.json
                    └─ workflow 末步 ─▶ Hub /api/ci/callback (cid, sha, conclusion, report)
              └─ Hub 结果收集(按 cid 归位)─▶ PMS /pms/ci-results (push)
                    └─ PMS: 落任务卡 + CI 徽标 + 飞书(失败必通知)
  [非实时] 工程师在 PMS 提交交付物时 ─▶ 见通路4(pull 附加)
```

### 通路2 · 远端事件通知(事件 → Hub 转发 → PMS → 飞书)
```
远端事件(ci_alert:无归位目标的基础设施/整体告警,如 runner 掉线、队列积压、Hub 自身故障、
        野生 workflow_run 事件(无对应 pending cid);
        deliverable_review:PR approve,Hub 已解析 reviewer login→成员)
  └─▶ Hub 收(webhook 或内部产生)
        └─ 路由:协作/告警类 ─▶ PMS /pms/events (type=ci_alert / deliverable_review,§4.4)
              └─ PMS: 系统内提示 + 按规则推飞书
  [注:有归位目标(pending cid)的 CI 失败/超时不走本通路,走通路1 的 ci-results;见 §4.4 职责切分]
```

### 通路3 · 手动预提交测试(解法A,前缀分支族 mannultest/<user>/<ts>)
```
工程师:推代码到 mannultest/<user>/<ts> 唯一分支(commit 之前的验证,多人互不干扰)
  └─▶ 在 Hub 界面点"手动测试" ─▶ Hub /hub/manual-check (ref=该前缀分支, cid)
        └─ Hub: workflow_dispatch(ref=该分支) ─▶ 同通路1后半段
              └─ Runner 跑 autotest ─▶ 回调 Hub(带实际 sha)─▶ (可选)push PMS 或仅 Hub 展示
              └─ 终态后 Hub 清理该临时分支(防膨胀)
  说明:不新增跨机接口,不违反 tzcomm 同机约束;
       "局域网 git/预提交"需求 = 推 mannultest/<user>/<ts> 分支承载;
       回调必带实际 checkout 的 sha,手动测试结果方可按 repo+sha 被查到;
       **手动测试语义 = 仅预验证、不作交付附件**:其 sha 属临时分支,与最终交付的
       正式 commit 不同 sha,通路4 按交付 sha 查到的恒为通路1 正式结果。
```

### 通路4 · 交付物按需查询(本地优先,出站兜底)
```
PMS: 工程师提交某次交付物(关联 repo + sha/task)
  └─▶ 先查 PMS 本地 cid 表(§6):
        ├─ 命中 → 直接附结果,流程结束(正常路径,不出站)
        └─ 未命中 → best-effort 调 Hub /hub/ci-results?repo=&sha= (service-token)
              ├─ 命中 → 附结果 + 补记本地表
              └─ 未命中 / Hub 不可达 → 提示"暂无测试结果"
                 (不阻塞交付物创建;可引导发起通路3手动测试)
```

---

## 6. cid(correlation_id)全链路对账规范

cid 是三平台唯一共享的对账锚点,规范如下:

- **生成**:Hub 生成,格式 **`chk_<26位ULID>`(据 Hub 文档 D7,v1.2 定死)**;PMS/Autotest 视其为**不透明字符串**,原样携带,不解析内部结构。
- **贯穿**:Hub 触发 workflow 时传入 → workflow 传给 autotest client(经 inputs/env)→ 回调原样带回 → Hub 据 cid 归位 → push PMS 时携带 → PMS 落库时存 cid(供通路4查询对齐)。
- **映射**:Hub 侧维护 `cid → {repo, sha, check_type, pms_task_id, status, report, ts}`;PMS 侧存 `cid → {project, task_id/deliverable_id, status, report, ts}`。
- **PMS 侧存储(v1.1 明确)**:cid 映射落 **PMS 本地表(SQLite,不回写飞书)**——cid 是运维对账元数据非业务字段,回写飞书会引入新列与回写死信风险(与 `形态` 字段同类坑)。任务卡/交付物上只在需要时以 CI 徽标/链接呈现,底层关联查本地 cid 表。
- **幂等**:所有以 cid 为键的写操作(回调、ci-results)按 cid 去重。

---

## 7. 与既有两份契约的兼容性说明

| 既有契约条目 | 本总契约的处理 |
|---|---|
| Autotest §11 CI/CD 对接(cid、回调带 sha、workflow_run 兜底、mannultest/** 前缀分支) | **沿用**;v1.2 回调补 sha + 分支改前缀族;通路1/3 建立其上 |
| Autotest §10 Client(run/matrix/report、--json、report.json) | **沿用**;Runner 上 workflow 调 client 的方式不变 |
| Autotest §3.3(tzcomm 同机约束、Hub 不感知 tzcomm) | **奉为硬约束**;通路3 因此选解法A |
| PMS 契约 §认证(service-token) | **沿用**;4.6 新增的反向 pull 复用同 token |
| PMS 契约 §1 `POST /pms/ci-results` | **沿用**,术语 CMS→Hub 统一 |
| PMS 契约 §1 `POST /pms/events` | **沿用** |
| PMS 契约 §2 `GET /api/admin/projects`、`GET /pms/tasks` | **沿用** |
| （新增）`GET /hub/ci-results` | **本版新增**,支撑通路4(PMS pull) |
| （新增）`POST /hub/manual-check` | **本版新增**,支撑通路3(手动触发,ref=`mannultest/<user>/<ts>` 前缀族) |
| 术语 CMS | **废弃**,统一为 Hub |

**结论**:两份既有契约无需推翻,本总契约=吸收 PMS 契约 + 对接 Autotest §10/§11 边界 + 新增两个端点(pull 查询、手动触发)+ 统一术语。三方可在此文档下并行开发。

---

## 8. 三平台开发推进要点(各自的 owner 视角)

### 8.1 PMS 侧(改造清单,v1.1)
- 新增 `POST /pms/ci-results`(**含 `project` 字段;只认 `status`,不读 conclusion**)、`POST /pms/events`(接 Hub push,type 枚举见 §4.4)。
- 新增 `GET /pms/tasks?repo=`(**repo→项目→任务 + 每条附深链 url**;供 Hub 反查关联任务)。
- `GET /api/admin/projects` 对 service-token 放行。
- **cid 落 PMS 本地表(不回写飞书)**;飞书通知统一由 PMS 发出。
- **repo↔项目绑定在 PMS**:配置时 repo 候选读 Hub `GET /hub/repos`,PMS 只存绑定;原 `github_user_map` 迁 Hub,PMS 改读 `GET /hub/github-users`。
- 通路4(可选兜底):本地无该 commit 结果时才 best-effort 调 Hub `GET /hub/ci-results`,不可达不阻塞交付物创建。
- 出站需配置:Hub base URL + 出站 service-token(与入站校验 token 分离)。

### 8.2 Hub 侧(核心新建)
- `POST /api/github/webhook`(验签/幂等/路由)。
- 编排器 + [Phase1]队列 + `workflow_dispatch` 触发(带 cid)。
- `POST /api/ci/callback`(收 workflow 回调,按 cid 归位)+ `workflow_run` 兜底 + 超时告警。
- push:`POST /pms/ci-results`(带 `project`)、`/pms/events`;pull:`GET /api/admin/projects`、`/pms/tasks?repo=`。
- **失败通知路径判定(§4.4 切分)**:有归位目标(pending cid)的失败/超时只发 ci-results(含 workflow_run 兜底判出的 timeout),无归位目标的才发 ci_alert。
- **供 PMS pull(v1.1):`GET /hub/repos`(repo 清单)、`GET /hub/github-users`(账号绑定)、`GET /hub/ci-results`(交付物兜底查询)**。
- **GitHub 账号绑定归 Hub**:读 GitHub orgs 成员 + 复用飞书登录做 login↔飞书/成员映射;approve→评审确认由 Hub 解析后经 `deliverable_review` 事件回调 PMS。
- `POST /hub/manual-check`(手动触发,ref=`mannultest/<user>/<ts>` 前缀族)+ 终态清理临时分支。
- 无状态编排:pending/cid 状态持久化,重启不丢。

### 8.3 Autotest 侧(基本不改,契约边界维持)
- 维持 §10 Client、§11 workflow(Autotest 契约 v1.1 章节);workflow 触发匹配 **`mannultest/**` 前缀族**(v1.2)+ `correlation_id` 入参 + 末步回调 Hub(**回调带实际 sha + finished_at**,§4.3)。
- **不因 Hub/PMS 改动内部**(tzcomm/World/协议/checker);对外只暴露 workflow 回调这一应用层接口。
- 收口 Autotest §13 已知缺口(`protocol/data/` 包),不属本总契约范围但为通路1可用前提。

---

## 9. 分阶段落地(与 Hub Phase 对齐)

| 阶段 | 通路可用性 | 依赖 |
|---|---|---|
| **Hub Phase 0**(骨架) | 通路1(直触发,HTTP 投递)+ 通路4(pull) | Autotest §11 回调就绪;PMS ci-results/pull 端点 |
| **Hub Phase 1**(编排+队列) | 通路1(多检查+队列)+ 通路2(events→飞书) | 队列;PMS events 端点 |
| **Hub Phase 2**(界面+联动) | 通路3(手动触发界面)+ 全通路可视化/审计 | Hub 管理界面;repo↔project 配置 |

---

## 10. 待拍板/填实

- service-token 轮换策略(PMS 契约标注"待定,不阻塞")。
- 通路3 手动测试结果是否默认 push PMS(建议:带 pms_task_id 则 push,否则仅 Hub 展示)。

> **已拍板项(v1.2 从本节移除,避免与已定决策相左)**:
> - cid 格式 = `chk_<26位ULID>`(Hub D7,见 §6)。
> - `mannultest` 改前缀分支族 `mannultest/<user>/<ts>` + 自动清理(§4.7),分支膨胀/并发问题一并解决。
> - 队列 = **Redis Stream**(Hub D2);Hub 触发 GitHub 认证 = **GitHub App**(Hub D3)——本总契约引用,不重开。

---

## 附录 A:Autotest 契约章节映射(v1.0 → v1.1,v1.3 入档)

> 本文档所有 "Autotest §x" 引用一律指 **v1.1** 章节号。凡引用子契约处,按本表一次核对,禁止凭 v1.0 旧号翻阅。

| v1.0 | v1.1 | 说明 |
|---|---|---|
| §3.3 部署约束(硬性) | §3.3 部署约束(硬性) | **未位移**;v1.0 §3.4 部署拓扑并入 v1.1 §3.3 第 4 点 |
| §3.4 部署拓扑 | §3.3(第 4 点) | 并入,不再单列 |
| §8 Client 控制接口 | §10 Client 控制接口 | — |
| §9 CI/CD 对接 | §11 CI/CD 对接 | — |
| §10 交付批次与已知缺口 | §13 交付批次、现状与重构迁移 | — |
| §11 变更纪律 | §14 变更纪律 | — |

> v1.1 新增章节(本总契约未引用,仅备查):§4 数据面 schema 机制、§7 插件规范、§8 本体 profile、§12 联合测试 suite。

---

## 附:变更纪律(三平台统一)

1. 本总契约冻结后,任何跨平台接口变更须提"动机+影响面",经 M 批准,通知三方 owner,版本号递增。
2. 子契约(Autotest/PMS)内部变更若不影响跨平台接口,各自按其变更纪律走;若影响本文档 §4 接口,须同步回本总契约。
3. 接口实现与文档不符时,以实现为事实源回写文档并记变更(承接 Autotest §14 原则)。
