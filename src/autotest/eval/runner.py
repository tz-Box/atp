"""评测编排 Runner：协议握手 + testcase 循环 + checker 打分。

等价于 PyTorch 的"训练/验证主循环"：Loader 只提供数据流，Runner 决定
怎么编排（RESET 复用 / checker 打分 / 进度上报），职责单一。
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

import tzcomm

from ..protocol import messages as msg
from ..protocol import topics
from ..world.base import IWorld
from .checker import IChecker, Score
from .loader import Loader

_SERVICE_TIMEOUT = 10.0


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
        session_id: Optional[str] = None,
        name: str = "autotest-service",
    ) -> None:
        self.session_id = session_id or f"autotest-{uuid.uuid4().hex[:8]}"
        self._world = world
        self._checker = checker
        self._node = tzcomm.Node(name)
        self._loader = Loader(self._node, world, self.session_id)
        self._ctl = self._node.create_service_client(topics.ctl_service(self.session_id))

    def run(
        self,
        testcases: list[str],
        init_config: Optional[dict[str, Any]] = None,
        checker_config: Optional[dict[str, Any]] = None,
        clock_rate: Optional[float] = 1.0,
        progress_cb=None,
    ) -> list[TestcaseResult]:
        """clock_rate：1.0=实时复现原始帧率（默认）；>1 加速；<1 减速；0/None=全速。

        progress_cb(testcase_id, result) 可选：每完成一个 testcase 回调一次
        （供 Service 上报部分结果 / 留痕）。
        """
        self._wait_sut()
        ready = self._call(msg.init(self.session_id, init_config))
        self._check_sensors(init_config, ready)
        results: list[TestcaseResult] = []
        try:
            for testcase_id in testcases:
                result = self._run_testcase(testcase_id, checker_config, clock_rate)
                results.append(result)
                if progress_cb:
                    progress_cb(testcase_id, result)
        finally:
            self._call(msg.terminate(self.session_id, "done"))
        return results

    def _run_testcase(
        self, testcase_id: str, checker_config: Optional[dict], clock_rate: Optional[float]
    ) -> TestcaseResult:
        self._call(msg.reset(self.session_id, {"testcase_id": testcase_id}))
        records = self._loader.load(testcase_id, clock_rate)
        score = self._checker.evaluate(records, self._world.get_ground_truth(), checker_config) if self._checker else None
        return TestcaseResult(testcase_id=testcase_id, records=records, score=score)

    def _wait_sut(self) -> None:
        if not self._ctl.wait_for_server(timeout=_SERVICE_TIMEOUT):
            raise RuntimeError(f"SUT 未注册控制服务: {topics.ctl_service(self.session_id)}")

    def _call(self, message: msg.Message) -> msg.Message:
        response = self._ctl.call(message.to_dict(), timeout=_SERVICE_TIMEOUT)
        return msg.Message.from_dict(response)

    @staticmethod
    def _check_sensors(init_config: Optional[dict], ready: msg.Message) -> None:
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

    def close(self) -> None:
        self._node.close()
