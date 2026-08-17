"""评测任务（Job）：一次评测的运行状态与结果。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from ..manifest import AlgorithmManifest
from ..scenario import Scenario


@dataclass
class Job:
    job_id: str
    manifest: AlgorithmManifest
    scenario: Scenario
    session_id: str
    clock_rate: Optional[float] = 1.0  # 1.0=实时复现原始帧率；>1 加速；0/None=全速
    done: threading.Event = field(default_factory=threading.Event)
    results: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    lock: threading.Lock = field(default_factory=threading.Lock)


def result_to_dict(result: Any) -> dict:
    return {
        "testcase_id": result.testcase_id,
        "metrics": result.score.metrics if result.score else None,
        "passed": result.score.passed if result.score else None,
        "n_records": len(result.records),
    }
