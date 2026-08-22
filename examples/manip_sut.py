"""示例闭环 SUT：单自由度接触力控（PI 力控 + 速度阻尼 + 抗积分饱和）。

目标接触力随帧由 obs.target_force 下发（无需场景先验）；
增益经 manifest hyperparams 覆盖（kp/ki/kd/i_limit）。
"""
from autotest.protocol import messages as msg
from autotest.protocol.schema import decode_observation, encode_action
from autotest.registry import load_plugin
from autotest.sdk import SutBase

mforce = load_plugin("manip.force")  # 注册 manip.force schema 编解码器


class ForcePI(SutBase):
    module = "manip.force"

    def on_init(self, config):
        p = config.get("hyperparams", {})
        self._kp = float(p.get("kp", 0.4))
        self._ki = float(p.get("ki", 2.0))
        self._kd = float(p.get("kd", 1.0))
        self._i_limit = float(p.get("i_limit", 5.0))  # 积分限幅（抗饱和；ki·i_limit 须 ≥ 目标力量级）
        self._int_err = 0.0
        self._last_ts = None

    def on_reset(self, testcase_meta):
        self._int_err = 0.0  # 清运行状态（积分/时钟），无模型需保留
        self._last_ts = None

    def on_step(self, observation):
        o = decode_observation(observation["data"])  # ManipObs
        ts = float(observation["timestamp"])
        dt = ts - self._last_ts if self._last_ts is not None else 0.0
        self._last_ts = ts

        err = o.target_force - o.f_contact
        self._int_err = max(-self._i_limit, min(self._i_limit, self._int_err + err * dt))
        force = self._kp * err + self._ki * self._int_err - self._kd * o.x_dot
        return msg.Action(
            "manip.force",
            encode_action("manip.force.ManipAction", mforce.ManipAction(force=force)),
        )


def main() -> None:
    sut = ForcePI("force-pi")
    try:
        sut.spin()
    finally:
        sut.close()


if __name__ == "__main__":
    main()
