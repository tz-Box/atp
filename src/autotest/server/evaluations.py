"""Hub 直连评测记录（v1.5 §4.8）：cid 幂等与状态持久化（SQLite，M-E1）。

存储：``artifacts/atp.db``（随 AUTOTEST_ARTIFACTS_DIR），evaluations 表以
correlation_id 为主键——同 cid 重复提交返回原 job_id，不重复执行（Hub 重发/超时空转安全）。
写频率低（每评测两行），每操作独立连接，线程安全从简。
"""
from __future__ import annotations

import sqlite3
import time
from pathlib import Path
from typing import Optional

_SCHEMA = """
CREATE TABLE IF NOT EXISTS evaluations (
  cid           TEXT PRIMARY KEY,
  job_id        TEXT NOT NULL,
  repo          TEXT NOT NULL,
  ref           TEXT,
  sha           TEXT,
  check_type    TEXT NOT NULL DEFAULT 'autotest',
  scenario      TEXT,
  save_baseline INTEGER NOT NULL DEFAULT 0,
  pms_task_id   TEXT,
  status        TEXT NOT NULL DEFAULT 'running',
  summary       TEXT,
  callback_error TEXT,
  created_at    TEXT NOT NULL,
  finished_at   TEXT
)
"""

_TERMINAL = ("success", "failure")


def scenario_outcomes(results: list[dict],
                      expects: Optional[dict[str, str]] = None) -> list[dict]:
    """按场景聚合 testcase 结果，并与期望比对（A11）。

    testcase_id 在多场景时带 `场景id:` 前缀；单场景无前缀，归入 expects 里的唯一场景
    （或 "default"）。返回每场景 {name, expected, actual, met, testcases{passed,failed}}。

    **四态由 expected/actual 两字段推导，不要只看 met**——met=False 掩盖了两种
    性质完全不同的情况：`expected=pass, actual=fail` 是意外失败（真坏了）；
    而 `expected=fail, actual=pass` 是**预期外通过**，它根本不是失败，
    而是**判据坏了**的信号（体检用例失去鉴别力）。
    """
    expects = expects or {}
    default_name = next(iter(expects), "default") if len(expects) == 1 else "default"
    buckets: dict[str, dict] = {}
    for r in results:
        tid = str(r.get("testcase_id", ""))
        name = tid.split(":", 1)[0] if ":" in tid else default_name
        b = buckets.setdefault(name, {"passed": 0, "failed": 0})
        if r.get("passed") is True:
            b["passed"] += 1
        elif r.get("passed") is False:
            b["failed"] += 1

    # ★以**声明的场景清单**为准枚举，而不是以观测到的结果为准。
    # 否则一个「一条 testcase 都没产出」的场景（World.testcases 为空、数据源没给帧）
    # 会从报文里**静默消失**——met/unmet 都不加一，结论照报 success，没人知道它没跑过。
    # 这与消费侧「解析完整性自检」是同一件事的生产侧：**报文要能发现自己漏了东西**。
    names = list(expects) or list(buckets)
    for name in buckets:                      # 结果里出现了但未声明的（异常情形），也列出
        if name not in names:
            names.append(name)
    out = []
    for name in names:
        b = buckets.get(name)
        expected = expects.get(name, "pass")
        if b is None:
            # 声明了却没有任何结果：既非 pass 也非 fail，恒判不符合预期
            out.append({"name": name, "expected": expected, "actual": "none",
                        "met": False, "testcases": {"passed": 0, "failed": 0}})
            continue
        actual = "fail" if b["failed"] > 0 else "pass"
        out.append({"name": name, "expected": expected, "actual": actual,
                    "met": actual == expected, "testcases": b})
    return out


def conclusion_of(error: Optional[str], results: list[dict],
                  expects: Optional[dict[str, str]] = None) -> str:
    """评测终态判定（状态查询 M-E4 与回调 conclusion M-E3 共用）。

    - `error` → failure（ATP 自身失败，与业务结论无关）。
    - **A11**：给出 expects 时按「实际 vs 期望」判定——所有场景符合预期即 success。
      设计成必须失败的场景（expect=fail）确实失败时**不再拖红整次评测**：
      此前 `any(passed is False) → failure` 一行，会让四个消费者同时被喂错误事实
      （check-run ❌ / PMS「失败必通知」推飞书 / Hub 概览失败数 / 通路4 交付物冻结）。
    - 未给 expects（本机通路、存量调用）→ 沿用原语义，行为不变。
    - passed=None（数据流验证，不打分）两种路径下都不判失败。
    """
    if error:
        return "failure"
    if expects:
        return "success" if all(s["met"] for s in scenario_outcomes(results, expects)) else "failure"
    if any(r.get("passed") is False for r in results):
        return "failure"
    return "success"


class EvaluationStore:
    """evaluations 表的最小访问面：create / get / 终态回写 / 回调异常留痕。"""

    def __init__(self, db_path: Path | str) -> None:
        self._db = Path(db_path)
        self._db.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as conn:
            conn.execute(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db, timeout=10.0)
        conn.row_factory = sqlite3.Row
        return conn

    def create(self, *, cid: str, job_id: str, repo: str, ref: Optional[str],
               sha: Optional[str], check_type: str = "autotest",
               scenario: Optional[str] = None, save_baseline: bool = False,
               pms_task_id: Optional[str] = None) -> bool:
        """登记新评测；cid 已存在（并发 race）返回 False，调用方重查取原记录。"""
        try:
            with self._connect() as conn:
                conn.execute(
                    "INSERT INTO evaluations (cid, job_id, repo, ref, sha, check_type,"
                    " scenario, save_baseline, pms_task_id, status, created_at)"
                    " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                    (cid, job_id, repo, ref, sha, check_type, scenario,
                     1 if save_baseline else 0, pms_task_id, "running", _now()),
                )
            return True
        except sqlite3.IntegrityError:
            return False

    def get_by_cid(self, cid: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evaluations WHERE cid=?", (cid,)).fetchone()
        return dict(row) if row else None

    def get_by_job_id(self, job_id: str) -> Optional[dict]:
        with self._connect() as conn:
            row = conn.execute("SELECT * FROM evaluations WHERE job_id=?", (job_id,)).fetchone()
        return dict(row) if row else None

    def list_recent(self, limit: int = 50) -> list[dict]:
        """最近评测列表（created_at 倒序；M-E10 控制台/运维列表端点）。"""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT * FROM evaluations ORDER BY created_at DESC, rowid DESC LIMIT ?",
                (limit,)).fetchall()
        return [dict(r) for r in rows]

    def update_terminal(self, cid: str, *, status: str, finished_at: str,
                        summary: Optional[str] = None, sha: Optional[str] = None) -> None:
        """评测结束回写终态（success/failure + summary，供回调与状态查询复用）。"""
        assert status in _TERMINAL, f"非法终态: {status!r}"
        with self._connect() as conn:
            conn.execute(
                "UPDATE evaluations SET status=?, finished_at=?,"
                " summary=COALESCE(?, summary), sha=COALESCE(?, sha) WHERE cid=?",
                (status, finished_at, summary, sha, cid),
            )

    def set_callback_error(self, cid: str, message: str) -> None:
        """回调最终失败留痕（结果不丢：Hub 轮询兜底可拿回，M-E3/M-E4）。"""
        with self._connect() as conn:
            conn.execute("UPDATE evaluations SET callback_error=? WHERE cid=?",
                         (message, cid))


def _now() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")
