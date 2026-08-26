"""评测任务（Job）：一次评测的运行状态与结果。"""
from __future__ import annotations

import threading
from dataclasses import dataclass, field
from typing import Any, Optional

from ..eval.run_control import RunControl
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
    started: threading.Event = field(default_factory=threading.Event)  # worker 取出后置位（M-E5 串行队列）
    results: list[dict] = field(default_factory=list)
    error: Optional[str] = None
    comm_health: dict = field(default_factory=dict)  # 通信健康（双侧丢包 + 告警），评测结束时采集
    control: RunControl = field(default_factory=RunControl)  # 调试闸门（暂停/单步），随 Job 创建
    lock: threading.Lock = field(default_factory=threading.Lock)
    # v1.5 §4.8（M-E1）：Hub 直连评测上下文（cid/repo/ref/sha/save_baseline/pms_task_id）；
    # 非 None 时评测结束回写 EvaluationStore 终态并触发主动回调（M-E3）。tzcomm 面提交为 None。
    eval_ctx: Optional[dict] = None
    # M-F2 场景清单：[(entry_id, Scenario)]，逐场景顺序执行（每场景独立 launch/session）；
    # 空 = 旧式单场景（执行面以 [("default", scenario)] 兜底，全兼容）
    scenario_entries: list[tuple[str, Scenario]] = field(default_factory=list)


def result_to_dict(result: Any) -> dict:
    return {
        "testcase_id": result.testcase_id,
        "metrics": result.score.metrics if result.score else None,
        "passed": result.score.passed if result.score else None,
        "n_records": len(result.records),
    }
