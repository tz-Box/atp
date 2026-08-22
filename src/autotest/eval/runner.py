"""评测编排 Runner：协议握手 + testcase 循环 + checker 打分。

v1.1 §5 冻结：INIT 加 body_profile 下发；握手校验强化。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import tzcomm

from ..body import Body, sensor_config_from_body
from ..commcheck import snapshot_node
from ..protocol import messages as msg
from ..protocol import topics
from ..world.base import IWorld
from .checker import IChecker, Score
from .loader import Loader
from .run_control import RunControl

_SERVICE_TIMEOUT = 10.0


def check_sensors(init_config: Optional[dict], ready: "msg.Message") -> None:
    """校验场景提供的传感器是否覆盖 SUT 在 READY 里声明的 required_sensors。"""
    provided = (init_config or {}).get("sensor_config", {})  # {类型: {实例名: topic}}
    required = ready.payload.get("required_sensors", {})  # {类型: [实例名...]}
    for stype, names in required.items():
        available = list(provided.get(stype, {}).keys())
        missing = [name for name in names if name not in available]
        if missing:
            raise RuntimeError(
                f"SUT 需要传感器 {stype}/{missing}，但场景未提供（可用实例：{available}）"
            )


@dataclass
class TestcaseResult:
    testcase_id: str
    records: list[Any] = field(default_factory=list)
    score: Optional[Score] = None


class Runner:
    """驱动一次评测：握手（INIT/READY/RESET/TERMINATE）+ 逐 testcase 推流 + 打分。"""

    def __init__(
        self,
        world: IWorld,
        checker: Optional[IChecker] = None,
        body: Optional[Body] = None,
        session_id: Optional[str] = None,
        name: str = "autotest-service",
        control: Optional[RunControl] = None,
    ) -> None:
        self.session_id = session_id or f"autotest-{uuid.uuid4().hex[:8]}"
        self._world = world
        self._checker = checker
        self._body = body
        self._control = control  # 调试闸门（暂停/单步）；None = 直通
        self._node = tzcomm.Node(name)
        self._loader = Loader(self._node, world, self.session_id)
        self._ctl = self._node.create_service_client(topics.ctl_service(self.session_id))
        # TERMINATE 响应（final payload，含 SUT 自统计）；SUT 异常时为 None
        self.sut_final: Optional[dict] = None

    def run(
        self,
        testcases: list[str],
        init_config: Optional[dict[str, Any]] = None,
        checker_config: Optional[dict[str, Any]] = None,
        clock_rate: Optional[float] = 1.0,
        progress_cb=None,
    ) -> list[TestcaseResult]:
        """clock_rate：1.0=实时复现原始帧率（默认）；>1 加速；<1 减速；0/None=全速。"""
        self._wait_sut()
        # v1.1 §5：INIT 下发 body_profile（sensor_config 从 body 生成）
        body_profile = None
        if self._body:
            body_profile = {
                "body_name": self._body.name,
                "body_version": self._body.version,
                "sensor_config": sensor_config_from_body(self._body),
            }
        merged_config = dict(init_config) if init_config else {}
        if body_profile:
            merged_config["sensor_config"] = body_profile["sensor_config"]
        ready = self._call(msg.init(self.session_id, merged_config, body_profile=body_profile))
        check_sensors(merged_config, ready)
        results: list[TestcaseResult] = []
        try:
            for testcase_id in testcases:
                result = self._run_testcase(testcase_id, checker_config, clock_rate)
                results.append(result)
                if progress_cb:
                    progress_cb(testcase_id, result)
        finally:
            # final payload 留存（含 SUT 侧 comm 自统计）；调用失败时维持 None，语义不变
            self.sut_final = self._call(msg.terminate(self.session_id, "done")).payload
        return results

    def _run_testcase(
        self, testcase_id: str, checker_config: Optional[dict], clock_rate: Optional[float]
    ) -> TestcaseResult:
        self._call(msg.reset(self.session_id, {"testcase_id": testcase_id}))
        records = self._loader.load(testcase_id, clock_rate, control=self._control)
        score = self._checker.evaluate(records, self._world.get_ground_truth(), checker_config) if self._checker else None
        return TestcaseResult(testcase_id=testcase_id, records=records, score=score)

    def _wait_sut(self) -> None:
        if not self._ctl.wait_for_server(timeout=_SERVICE_TIMEOUT):
            raise RuntimeError(f"SUT 未注册控制服务: {topics.ctl_service(self.session_id)}")

    def _call(self, message: msg.Message) -> msg.Message:
        response = self._ctl.call(message.to_dict(), timeout=_SERVICE_TIMEOUT)
        return msg.Message.from_dict(response)

    def comm_snapshot(self) -> dict:
        """Service 侧通信健康快照（result 接收丢包等；须在 close() 前采集）。"""
        return snapshot_node(self._node, side="service")

    def close(self) -> None:
        self._node.close()
