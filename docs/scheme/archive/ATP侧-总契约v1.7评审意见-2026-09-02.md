# ATP 侧 · 三平台总契约 v1.7 评审意见（2026-09-02）

> 评审对象：《PatrolBox 三平台通信与接口总契约 v1.7（起草，待 M 批准）》+ ARS 仓
> （`README.md` / `docs/ARS-功能设计与技术路径.md` v0.1 / `contracts/ARS接口契约-v0.1.md` / P0 代码）
> 评审人：ATP owner。立场：ATP 是 v1.7 里**唯一不需要改动**的系统，因此本意见侧重
> ①作为"另一个执行体"的同构性对照，②四方并行推进的体系性缺口。
> 分级：**必改** = 不改会上线后返工或出线上问题；**建议补充** = 缺了会在第三个执行体接入时付出代价；
> **措辞/自洽** = 不影响实现但影响后续决策的自洽性。

---

## 0. 总体结论

**方向正确，可以按 (a) 系列拍板推进。** 把 AI 审查抽象成 `check_type=aireview` 的第四类 check、
另起 ARS 而不并入 ATP、接口面与 §4.8 同构——这三条决策我完全支持，理由在契约里已经论证充分，
不再重复。ATP 侧确认：**v1.7 对 autotest 语义一字未改，ATP 无需任何改动**。

以下 9 条为评审发现。其中 **#1 / #2 / #3 是必改**，尤其 **#2（merge-base 漂移）契约完全未覆盖**。

---

## 1. 【必改】`details_url` 的签名短链有效期与 GitHub 链接的永久性冲突

**问题**：§4.9.5 定 HMAC 短链有效期 **30 分钟**，而 §4.3c 把这个 URL 写进 GitHub check-run 的
`details_url`。**GitHub 上的 check-run 是永久记录**——人通常不会在审查完成后 30 分钟内点它，
更常见的是第二天回顾 PR、或者三个月后追溯"当时 AI 报过什么"时点开。

**后果**：PR 页面上那个链接**在绝大多数被点击的时刻都是坏的**（410）。
契约里写的降级"提示回 Hub console 重新进入"，等于要求用户手动做一次本该自动完成的跳转——
这会让 aireview 的可见性设计（§4.3c "可见性由 summary 达成即可"）实际退化为"只有 summary 可用"。

**建议**：`details_url` 指向 **Hub 的跳板端点**，而不是 ARS 的签名链接：

```
GitHub check-run.details_url = {hub_base}/hub/checks/{cid}/review     ← 永久有效
        │ 人点击（Hub 侧已有飞书 OAuth 会话）
        ▼
Hub: 校验人身份 → 当场签发 30 分钟短链 → 302 跳转
        ▼
ARS: GET /ars/ui/{review_id}?exp=&sig=
```

**顺带三个收益**：①GitHub 上的链接永久有效；②短链只活在跳转的一瞬间，**安全性更强**；
③访问控制的真正锚点回到 Hub 的飞书 OAuth（有真实人身份），而不是"谁拿到 URL 谁能看"——
现在的设计里短链在 30 分钟内被转发出去就失控了，而审查页含全量 diff 与 findings。

同一处理适用于 §4.4 透传给 PMS 的 `run_url`（任务卡上的链接同样是长期记录）。

---

## 2. 【必改】merge-base 漂移：aireview 独有、契约完全未覆盖的结果失效场景

**问题**：autotest 的结果绑定 `head_sha`，sha 不变结果就有效——这个假设对 aireview **不成立**。

aireview 的结论依赖 `merge_base_sha`。PR 开着的这几天里，**base 分支上合入了别人的改动 →
merge base 前移 → 审查结论的前提已经变了**，但：

- `head_sha` 没变；
- `pull_request:synchronize` **只在本 PR 自己 push 时触发**，base 分支的 push 不会触发本 PR 重审；
- 缓存键 `(repo, merge_base_sha, head_sha, ...)` 确实会变，但**没有任何事件去命中它**。

**后果**：PR 页面上挂着一份**基于三天前 merge base 的审查结论**，且没有任何迹象表明它已陈旧。
"意图偏差判定""影响面分析""是否与既有约定冲突"这几类结论恰恰是**最依赖 base 状态**的，
也正是 ARS 宣称的核心价值（设计文档 §2 ①②）。**结论陈旧会直接损伤这个价值锚点。**

**建议（一期成本极低）**：

1. **契约层**：§4.9.2 的 `report.metrics` 增 `merge_base_sha` 与 `merge_base_at`（求解时刻）；
   §4.3c 的 check-run `output.summary` 与 ARS 审查页**必须显式标注审查基点**，例如
   「审查基于 merge-base `a1b2c3d`（09-02 14:30），此后 base 分支有 7 个新提交」。
   让人自己判断要不要点重审——**一期只要"不假装新鲜"就够了**。
2. **P2 再做**：Hub 收到 base 分支 push 时，把该分支上所有 open PR 的最新 aireview 标记为
   `stale`（不重跑、不烧钱，只改展示态）。数据 Hub 已有（`checks` 表 v1.7 新增了 `base_ref`）。

> 这一条我认为是本次评审最有价值的发现：它是 **aireview 相对 autotest 的结构性差异**，
> 而契约的整体思路是"ARS 与 ATP 同构"，同构性反而掩盖了这个差异。

---

## 3. 【必改】`aireview` 的 `status=success` 会污染下游"是否全绿"的聚合判断

**问题**：§4.4 已经很好地处理了**展示层**（口径②：文案读 `metrics` 不读 `status`）。
但 `POST /pms/ci-results` 的 `status` 字段仍只有 `success|failure|timeout` 三值，
PMS 落库存的就是 `success`。**任何按"这个 commit 的 checks 是否都 success"做的聚合判断都会把
aireview 算作通过**——而 aireview 的 success 只表示"审查跑成了"。

具体风险点：**通路4（交付物附 CI 结果）**。交付物冻结时若要判断"这个 commit 测过了吗"，
一条报了 3 个 blocker 的 aireview 会贡献一个 `success`。§4.6 的 `/hub/ci-results` 虽然
`latest_status` 按 check_type 分组，但分组不等于消费方会正确区分——**契约应当让消费方
不需要认识 check_type 就能做对**。

**建议**：`POST /pms/ci-results` 与 §4.6 响应各增一个**通用**布尔字段：

```jsonc
"advisory": true    // 缺省 false。true = 本结果为参考性质，不参与任何"是否全绿"的聚合判断
```

`autotest` 恒 `false`，`aireview` 恒 `true`，由 **Hub 按 check_type 填充**（消费方不需要认识 check_type）。

**为什么要通用字段而不是让消费方 `if check_type == "aireview"`**：v1.7 是第二个 check_type，
不会是最后一个。硬编码 check_type 的判断分散在 PMS/Hub 各处，**第五类 check 上线时要三方同步发版**；
`advisory` 是一次性投入、永久受益。这与 #4 是同一个诉求的两面。

---

## 4. 【建议补充】未知 `check_type` 的通用消费规则

§4.3b 写了"新增 check_type 须走变更纪律，不得私自扩展"，§4.4 口径①只要求 PMS
"确认不会因未知 check_type 报错"。**但没有规定消费方遇到未知 check_type 的正确行为。**

**建议在 §4.3b 加一条三平台通用条款**：

> 所有消费方对未知 `check_type` 必须：**存档 + 按 `advisory` 展示 + 不参与聚合判断 + 不报错 + 不丢弃**。
> 消费方不得因 check_type 未知而拒绝报文。

价值：**新增执行体时不再需要三方同步发版**。这是四方并行推进最直接的解耦收益，
也是把 v1.7 这次"新增一类 check"的经验固化下来的最小代价。

---

## 5. 【建议补充】`synchronize` debounce 与 cid / pending check 的交互未定义

契约只说"debounce 120s 只审增量"，但没定义：

| 未定义的点 | 需要明确 |
|---|---|
| 窗口内来了 3 次 push | 生成 1 个 cid（用窗口结束时的 head）还是 3 个？ |
| 上一轮 cid 仍在 running 时来了新 push | 旧 cid 怎么处理？取消 / 跑完丢弃 / 照常归位？ |
| check-run 写几条 | 若每个 cid 都写，PR 页面会闪一串 neutral check |

**建议明确**：窗口结束时**只生成一个 cid**；若存在仍在 running 的上一轮 cid，
将其标记为 `superseded`——**不写 check-run、不推 PMS、但保留在 Hub 库中供追溯**（不是删除）。
ARS 侧对已 superseded 的 review 可提前中止以省预算（Hub 需提供一个取消语义，或 ARS 自行轮询）。

> ATP 侧无此问题（autotest 由 push 触发、单机串行、天然排队），所以这是 aireview 引入的新语义，
> 值得在契约里写死而不是留给实现。

---

## 6. 【建议补充】送入 LLM 的内容范围应成为契约条款

§4.9.1 的 `context` 携带 PMS 任务卡的 `title` / `status` / `url` 与交付物信息，
这些**会被送进第三方 LLM**。§8.1 的安全段覆盖了"仓内内容视为数据非指令"与"密钥打码"，
但**没有覆盖 `context` 本身**，也没有把"哪些内容可以出境"写成条款。

现有设计其实已经相当克制（任务只传 `record_id/title/status/url`，不传 body/备注），
**但契约没把"为什么只传这些"写成纪律，后人很容易顺手加字段**——比如"把任务描述也带上，
意图判定会更准"，这个动机非常自然，而它会把项目内部描述发到外部供应商。

**建议**：
1. §4.9.1 明确 `context` 是**字段白名单**，扩充字段须走变更纪律（与新增 check_type 同级）；
2. "数据非指令"纪律**显式覆盖 `context`**（任务标题同样可能被注入 prompt injection）；
3. 契约里写一句 **"送入 LLM 的内容范围变更须 M 批准"**——这是数据出境问题，不是实现细节。

---

## 7. 【措辞/自洽】安全红线的论证基础应从 "fork PR 不可信" 改为 "永不执行任何被审代码"

契约里"永不执行被审代码"的论证反复挂在 **fork PR 不可信** 上（㊺、§4.9.1、§8.4），
但同时 ㊽ 又定"**fork PR 一期不触发**"。这形成一个自洽性漏洞：

> 如果一期根本不触发 fork PR，那"fork PR 不可信"就不是当前的威胁模型，
> 安全红线的论证基础在 A4 拍成 (a) 的那一刻就被抽掉了。

**真正的理由与来源无关**：内部仓的 PR 一样可能引入有问题的构建脚本或依赖，
而审查机上跑一次 `pip install` 就足以造成损害。**只读是审查这个行为的本质属性，不是对 fork 的防御措施。**

**建议措辞**：「ARS **永不执行任何被审代码，与代码来源无关**——只读是审查行为的本质属性；
fork PR 只是使该风险更直观的一个场景。」

改完之后，A4 无论拍 (a) 还是 (b)，安全论证都不需要重写。

---

## 8. 【措辞/自洽】`conclusion` 语义按 check_type 分化：应显式记为技术债

我**支持 A1 拍 (a)**，但要指出：这是契约里**第一次出现同一字段在不同 check_type 下含义不同**，
消费方从此必须先看 `check_type` 才能解释 `conclusion`。这是真实的设计债，值得显式记录而不是默认接受。

**顺带指出一个既有问题**：`conclusion=failure` 在 **autotest 侧本来就是双关的**——
"测试没通过"（业务失败）和"ATP 自己崩了 / checkout 失败"（基础设施失败）**现在共用 failure**。
ARS 只是把这个混淆暴露了出来，不是引入的。理想的字段设计是拆两个：

| 字段 | 语义 | autotest | aireview |
|---|---|---|---|
| `outcome` | 执行体是否正常跑完 | ok / error | ok / error |
| `verdict` | 业务结论 | pass / fail | 恒 neutral |

**但我不建议 v1.7 做这个改动**——影响面覆盖 Hub/PMS/ATP 三方，为一个尚未造成实际损害的
语义洁癖付出跨系统改动的代价不划算。

**建议改为两条低成本条款**：
1. §4.3 显式声明「`conclusion` 的语义按 check_type 分化，属已知技术债，
   将来若拆分为 `outcome`/`verdict` 走独立版本」；
2. **§4.3b 的 check_type 表增一列「conclusion 语义」，新增 check_type 必须填写**，
   未声明者默认按 autotest 语义。

这样把隐性分歧变成显式条款，成本几乎为零。

---

## 9. 【提醒】ARS 仓内文档已与 v1.7 不一致 —— 正好是它自己要抓的那条规则

不是契约问题，是提醒 ARS owner：

| 位置 | 现状 | 与 v1.7 的冲突 |
|---|---|---|
| `README.md` | 「当前状态：**仅有设计文档，代码未开始**」 | `ars/` 下已有 ~1000 行 P0 代码（diffkit / workspace / rules / pipeline / cli） |
| `docs/ARS-功能设计与技术路径.md` §7 | 「页面**落在 Hub console**…ARS 只提供数据接口，**不做自己的前端**」 | v1.7 ㊾ 已拍板 **ARS 自建审查页 + Hub 深链**（A3 已初拍） |
| 同上 §7.4 | 「Hub console：全量、权威」 | 同上 |
| `README.md` 外部权威文档 | 引用总契约 **v1.6** | 已到 v1.7 |

> ARS 设计文档 §4.2 提出的"杀手锏"规则是：**改了跨平台接口但未同步契约文档 → 报 blocker**，
> 并称"这一条是整个功能里 ROI 最高的单点"。**这条规则现在正好命中提出它的那个仓。**
> 这不是挑刺——恰恰说明该规则确实抓的是真问题，而且**人真的会忘**。
> 建议 ARS 把这几处同步掉，然后把这个案例作为 P1 的第一个回归样本。

---

## 10. ATP 侧主动认领：把 `vs_baseline` 从 summary 文本迁进 `report.metrics`

v1.7 ㊼ 新增的可选 `report.metrics` 说「ATP 不发则无该字段，向后兼容」。
**但 ATP 现在正是那个应该发的**：`vs_baseline` 计数（`improved/regressed/worse/new/same`）
和逐场景 passed 计数现在塞在 `summary` **文本**里，下游要展示就得解析文本。

**ATP 侧认领**：在 `POST /api/ci/callback` 增发 `report.metrics`（summary 文本保留不变，纯增量）：

```jsonc
"metrics": {
  "testcases": {"passed": 5, "failed": 1, "total": 6},
  "scenarios": {"run": 2, "failed": 0},
  "vs_baseline": {"improved": 1, "regressed": 0, "worse": 1, "new": 0, "same": 4},
  "comm_health": {"loss_rate_max": 0.0, "warnings": 0}
}
```

收益：PMS 展示层对 autotest 与 aireview **走同一条渲染路径**（都读 metrics），
不需要为 autotest 保留一条正则解析 summary 的旁路。这是 v1.7 顺手能收掉的一笔既有债。

**这条不阻塞 v1.7 批准**，ATP 侧可在 v1.7 生效后独立发版（向后兼容，`metrics` 是可选字段）。

---

## 附：对四个待拍板项的立场

| # | 议题 | 我的立场 | 补充论证 |
|---|---|---|---|
| **A1** | conclusion 语义 | **(a)**，但配 #8 的两条条款 | 契约给的理由是"通知噪声 + 事实门禁"。**更根本的理由是消费方不同**：`conclusion` 的消费方是**机器**（Hub 归位、PMS 通知规则），`metrics` 的消费方是**人**。把"代码有没有问题"塞进机器消费的字段，等于让机器替人下了结论——**而一期的产品定位恰恰是"分配人的注意力"，不是替人下结论**（设计文档 D4）。字段设计要和产品定位一致。 |
| **A2** | PMS 读 metrics | **(a)**，且应配 #3 的 `advisory` 字段 | 只改展示文案是**不够的**：展示对了，但底层聚合判断仍会把 aireview 当"绿"。#3 补上这一半。 |
| **A3** | 审查页归属 | **(a)**，但必须配 #1 的 Hub 跳板端点 | 不配跳板的话，(a) 的代价是"GitHub 上的链接大部分时候是坏的"，会抵消掉 ARS 自建页的全部好处。 |
| **A4** | fork PR | **(a) 起步**，但按 #7 换掉论证基础 | 换完之后 A4 变成纯粹的 Hub 工作量取舍，与安全性脱钩，将来放开时不需要重开安全评审。 |
