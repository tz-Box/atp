"""闭环评测编排：INIT/RESET/STEP→ACTION→step 生命周期。

v1.1 §5：INIT 加 body_profile 下发。
会话接口与 Runner 对齐（sut_final / comm_snapshot / progress_cb），
server 按 world.closed_loop 选择本会话或开环 Runner。
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import tzcomm

from ..body import Body, sensor_config_from_body
from ..commcheck import snapshot_node
from ..protocol import messages as msg
from ..protocol import topics
from ..world.base import IWorld
from .checker import IChecker
from .run_control import RunControl
from .runner import TestcaseResult, check_data_sensors, check_sensors

_ACTION_TIMEOUT = 60.0
_SERVICE_TIMEOUT = 10.0


class ClosedLoopSession:
    """驱动一个闭环评测会话：World 喂帧 → SUT 回 Action → World 推进。"""

    def __init__(
        self,
        world: IWorld,
        checker: IChecker,
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
        self._obs_pub = self._node.create_publisher(topics.obs_topic(self.session_id), qos=1)
        self._action_sub = self._node.create_subscription(topics.action_topic(self.session_id))
        self._ctl = self._node.create_service_client(topics.ctl_service(self.session_id))
        # TERMINATE 响应（final payload，含 SUT 自统计）；SUT 异常时为 None
        self.sut_final: Optional[dict] = None

    def run(
        self,
        testcases: list[str],
        init_config: Optional[dict[str, Any]] = None,
        checker_config: Optional[dict[str, Any]] = None,
        clock_rate: Optional[float] = None,  # 闭环自带节奏（每 action 推一步），形参仅为与 Runner 对齐
        progress_cb=None,
    ) -> list[TestcaseResult]:
        self._wait_sut()
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
        check_sensors(merged_config, ready)          # SUT 要什么 vs body 声明什么
        for w in check_data_sensors(self._world, ready):   # vs 数据实际给什么
            print(f"[sensors] WARNING: {w}")
        results: list[TestcaseResult] = []
        try:
            for testcase_id in testcases:
                result = self._run_testcase(testcase_id, checker_config)
                results.append(result)
                if progress_cb:
                    progress_cb(testcase_id, result)
        finally:
            self.sut_final = self._call(msg.terminate(self.session_id, "done")).payload
        return results

    def _run_testcase(self, testcase_id: str, checker_config: Optional[dict]) -> TestcaseResult:
        self._call(msg.reset(self.session_id, {"testcase_id": testcase_id}))
        records = self._interact(testcase_id)
        score = self._checker.evaluate(records, self._world.get_ground_truth(), checker_config)
        return TestcaseResult(testcase_id=testcase_id, records=records, score=score)

    def _interact(self, testcase_id: str) -> list[dict]:
        records: list[dict] = []
        observation = self._world.reset(testcase_id)
        records.append(observation)
        while True:
            if self._control is not None:
                self._control.wait_gate()
            self._obs_pub.publish(msg.step(self.session_id, observation, done=False).to_dict())
            command = msg.parse_action(msg.Message.from_dict(self._action_sub.get(timeout=_ACTION_TIMEOUT)))
            observation, done, _ = self._world.step(command)
            records.append(observation)
            if done:
                break
        return records

    def _wait_sut(self) -> None:
        if not self._ctl.wait_for_server(timeout=_SERVICE_TIMEOUT):
            raise RuntimeError(f"SUT 未注册控制服务: {topics.ctl_service(self.session_id)}")

    def _call(self, message: msg.Message) -> msg.Message:
        response = self._ctl.call(message.to_dict(), timeout=_SERVICE_TIMEOUT)
        return msg.Message.from_dict(response)

    def comm_snapshot(self) -> dict:
        """Service 侧通信健康快照（action 接收丢包等；须在 close() 前采集）。"""
        return snapshot_node(self._node, side="service")

    def close(self) -> None:
        self._node.close()
