"""话题与服务命名：跨进程通信的单一来源。"""
from __future__ import annotations

import os


def obs_topic(session_id: str) -> str:
    return f"autotest/{session_id}/obs"


def result_topic(session_id: str) -> str:
    return f"autotest/{session_id}/result"


def action_topic(session_id: str) -> str:
    return f"autotest/{session_id}/action"


def ctl_service(session_id: str) -> str:
    return f"autotest/{session_id}/ctl"


def control_service() -> str:
    """Client 提交评测的固定服务名（可用 AUTOTEST_CONTROL_SERVICE 覆盖，供测试/多实例隔离）。"""
    return os.environ.get("AUTOTEST_CONTROL_SERVICE", "autotest/control")


def job_status_service() -> str:
    """Client 轮询评测进度/结果的固定服务名（可用 AUTOTEST_JOB_STATUS_SERVICE 覆盖）。"""
    return os.environ.get("AUTOTEST_JOB_STATUS_SERVICE", "autotest/job/status")
