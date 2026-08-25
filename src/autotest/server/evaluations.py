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


def conclusion_of(error: Optional[str], results: list[dict]) -> str:
    """评测终态判定（状态查询 M-E4 与回调 conclusion M-E3 共用）：
    error 或任一 testcase passed=False → failure；passed=None（数据流验证）不判失败。
    """
    if error:
        return "failure"
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
