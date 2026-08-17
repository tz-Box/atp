"""闭环评测编排：INIT/RESET/STEP→ACTION→step 生命周期。"""
from __future__ import annotations

import uuid
from typing import Any, Optional

import tzcomm

from ..protocol import messages as msg
from ..protocol import topics
from ..protocol.schema import StampedPose
from ..world.base import IWorld
from .checker import IChecker
from .runner import TestcaseResult

_ACTION_TIMEOUT = 60.0
_SERVICE_TIMEOUT = 10.0


class ClosedLoopSession:
    """驱动一个闭环评测会话：World 喂帧 → SUT 回 Action → World 推进。"""

    def __init__(
        self,
        world: IWorld,
        checker: IChecker,
        session_id: Optional[str] = None,
        name: str = "autotest-service",
    ) -> None:
        self.session_id = session_id or f"autotest-{uuid.uuid4().hex[:8]}"
        self._world = world
        self._checker = checker
        self._node = tzcomm.Node(name)
        self._obs_pub = self._node.create_publisher(topics.obs_topic(self.session_id), qos=1)
        self._action_sub = self._node.create_subscription(topics.action_topic(self.session_id))
        self._ctl = self._node.create_service_client(topics.ctl_service(self.session_id))

    def run(
        self,
        testcases: list[str],
        init_config: Optional[dict[str, Any]] = None,
        checker_config: Optional[dict[str, Any]] = None,
    ) -> list[TestcaseResult]:
        self._wait_sut()
        self._call(msg.init(self.session_id, init_config))
        results: list[TestcaseResult] = []
        try:
            for testcase_id in testcases:
                results.append(self._run_testcase(testcase_id, checker_config))
        finally:
            self._call(msg.terminate(self.session_id, "done"))
        return results

    def _run_testcase(self, testcase_id: str, checker_config: Optional[dict]) -> TestcaseResult:
        self._call(msg.reset(self.session_id, {"testcase_id": testcase_id}))
        records = self._interact(testcase_id)
        score = self._checker.evaluate(records, self._world.get_ground_truth(), checker_config)
        return TestcaseResult(testcase_id=testcase_id, records=records, score=score)

    def _interact(self, testcase_id: str) -> list[StampedPose]:
        records: list[StampedPose] = []
        observation = self._world.reset(testcase_id)
        records.append(self._stamped(observation))
        while True:
            self._obs_pub.publish(msg.step(self.session_id, observation, done=False).to_dict())
            command = msg.parse_action(msg.Message.from_dict(self._action_sub.get(timeout=_ACTION_TIMEOUT)))
            observation, done, _ = self._world.step(command.data)
            records.append(self._stamped(observation))
            if done:
                break
        return records

    @staticmethod
    def _stamped(observation) -> StampedPose:
        return StampedPose(timestamp=observation.timestamp, pose=observation.data.robot_pose)

    def _wait_sut(self) -> None:
        if not self._ctl.wait_for_server(timeout=_SERVICE_TIMEOUT):
            raise RuntimeError(f"SUT 未注册控制服务: {topics.ctl_service(self.session_id)}")

    def _call(self, message: msg.Message) -> msg.Message:
        response = self._ctl.call(message.to_dict(), timeout=_SERVICE_TIMEOUT)
        return msg.Message.from_dict(response)

    def close(self) -> None:
        self._node.close()
