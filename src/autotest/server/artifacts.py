"""评测留痕：artifacts/{job_id}/ 下的 report.json 与 session.log。

"本机记录（类似 pytorch 训练过程）"：每次评测把配置、逐 testcase 结果、
打分与错误落盘，供事后回溯与回归报告复用。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any


def artifacts_root() -> Path:
    """评测产物根目录：环境变量 AUTOTEST_ARTIFACTS_DIR 可覆盖。"""
    return Path(os.environ.get("AUTOTEST_ARTIFACTS_DIR", "artifacts"))


class ArtifactRecorder:
    """一个 job 的评测产物记录器（线程安全）。"""

    def __init__(self, root: Path | str, job_id: str) -> None:
        self._dir = Path(root) / job_id
        self._dir.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._log_fh = (self._dir / "session.log").open("a", encoding="utf-8")

    @property
    def dir(self) -> Path:
        return self._dir

    def log(self, message: str) -> None:
        with self._lock:
            self._log_fh.write(f"[{time.strftime('%H:%M:%S')}] {message}\n")
            self._log_fh.flush()

    def save_report(self, payload: dict[str, Any]) -> None:
        with self._lock:
            (self._dir / "report.json").write_text(
                json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    def close(self) -> None:
        with self._lock:
            self._log_fh.close()
