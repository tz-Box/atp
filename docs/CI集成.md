# CI 集成：Hub 自动触发与日常使用

> 读者：已接好算法的算法工程师（[快速上手](快速上手.md) / [算法接入手册](算法接入手册.md) 之后）。
> 目标：理解"push / 手动勾选 → ATP 评测 → 红绿回传"的日常链路，会看结果、会管基线、会排障。

## 1. 链路拓扑（你只需维护仓内 scenario.yaml）

```
你 push 代码（或在 Hub 手动勾选场景触发）
        │
        ▼
CICD Hub ──按坐标(repo+ref+scenario)──▶ ATP 评测机（checkout → 逐场景评测）
        │                                    │
        │◀──终态主动回调（conclusion+summary）─┘
        ▼
GitHub check-run 红绿 + PMS 任务卡 CI 区块
```

- **算法仓零 workflow、零 Secret**：评测逻辑全在仓内 `scenario.yaml`（场景清单 + runtime 声明）。
- 唯一前置：ATP 评测机预置你仓的**只读 deploy key**（找评测负责人配置一次）。
- 提交幂等：Hub 对每次评测发 `correlation_id`，ATP 同 cid 不重复执行（重发/超时空转安全）。

## 2. 场景勾选（R2 多场景）

Hub 手动触发页会列出你仓 `scenario.yaml` 的 `scenarios:` 清单（`{id, description}`），勾选后下发：

| Hub 下发 | ATP 行为 |
|---|---|
| `scenario: null`（不选） | 清单**全部场景**顺序执行 |
| `scenario: "smoke"` | 只跑该 id |
| `scenario: ["a","b"]` | 按清单顺序跑这两个 |

- 多场景 = **单 job 逐场景顺序执行**，每场景独立拉起你的进程（场景间无状态共享）。
- 报告里 `testcase_id` 带场景前缀（如 `full:tc-1`；单场景无前缀），按此前缀归组看结果。
- 单场景异常不阻塞后续场景（记 `场景id:<scenario>` 失败条目），CI 能拿到完整的多场景结果矩阵。
- 改清单后 push 即生效（Hub 经 Contents API 读仓内文件，无需通知 ATP）。

## 3. 基线与回归语义（M-D3）

ATP 在评测机维护**滚动基线**（`artifacts/baseline.json`），每次评测自动对比：

```json
"vs_baseline": { "new": 1, "improved": 2, "regressed": 0, "unchanged": 5 }
```

| 计数 | 含义 | 你该做什么 |
|---|---|---|
| `regressed` | 指标比基线差（超阈值） | **关注**：代码改动引入回归，或基线需更新 |
| `improved` | 指标比基线好 | 可经 `save_baseline` 滚动为新基线 |
| `new` | 基线中无此 testcase | 首次/新场景，正常现象 |
| `unchanged` | 与基线一致 | — |

- **滚动基线**：Hub 在主干/发版评测置 `save_baseline=true` → ATP 先对比、后把本次结果滚为新基线。
  手动触发也可勾选（console 表单 / `atp submit --save-baseline`）。
- **仓内参考基线**（可选）：清单项 `baseline: baselines/x.json` 作为该场景**首次评测**（ATP 尚无
  滚动基线）时的对比种子；之后以滚动基线为准。格式 = `client report --json` 的 results 数组。
- **一次性现象**：多场景前缀改变 testcase_id 命名空间（`smoke:tc-1` ≠ `tc-1`），启用清单后首轮
  全记 `new=N`，下次 `save_baseline` 后恢复正常对比。

## 4. 触发即失败（4xx，Hub 直接判红，不等超时）

提交期错误 ATP 返回 4xx + 机读 `code`（Hub 收到直接判 failure，不重投）：

| code | 含义 | 处理 |
|---|---|---|
| `manifest_missing` | 仓内找不到 scenario.yaml | 确认 manifest 在仓根并已 push |
| `manifest_invalid` | manifest 解析/校验失败 | 按报文修（缺 launch、id 非法/重复、清单项缺 scenario 等） |
| `scenario_unknown` | 勾选的 id 不在清单中 | 报文含可用清单；改选或同步清单 |

执行期失败（非 4xx）看 check-run summary 与评测机产物（§5）。

## 5. 看结果与排障

| 入口 | 能看到什么 |
|---|---|
| GitHub check-run | 红绿 + summary（逐场景 passed/metrics/vs_baseline） |
| ATP console（`<ATP_BASE_URL>/console`） | 评测列表（展开详情）、手动触发、健康/队列 |
| `atp status <job_id>` / `atp wait` | 命令行同款终态 JSON |
| 评测机 `artifacts/<job_id>/` | `report.json`（逐 testcase 明细 + comm_health + scenarios 清单）、`session.log`（执行留痕） |

排障速查（框架 / tzcomm / 算法 / 配置归因表）见 [算法接入手册](算法接入手册.md) §4.4。

## 6. 手动预验证（联调/算法调试）

不走 Hub，直接打 ATP（与 Hub 同款接口）。先一次登录（`atp login` 落盘配置，免 export，
详见 [快速上手](快速上手.md) §1），随后：

```bash
python3 -m autotest.client atp submit --repo <owner/repo> --ref <分支> --scenario smoke
# 默认阻塞等终态并输出 summary；--no-wait 仅受理即返；同 --cid 幂等安全重放
# CI/脚本注入场景仍可用环境变量 ATP_BASE_URL / ATP_SERVICE_TOKEN（优先于配置文件）
```

console 表单等价（飞书登录，admin 可触发，member 只读）。

---

附：GHA 自测备选（非主通路）——`examples/ci/autotest.yml` + `report.py` 保留给算法工程师在自有
runner 上自测（不依赖 Hub 编排），含 `--save-baseline` 与回归对比步骤。
