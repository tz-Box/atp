"""eval 层：评测执行（Loader 薄数据流 + Runner/ClosedLoop 编排 + checker 接口）。"""
from .checker import IChecker, Score
from .closed_loop import ClosedLoopSession
from .loader import Loader
from .runner import Runner, TestcaseResult

__all__ = ["ClosedLoopSession", "IChecker", "Loader", "Runner", "Score", "TestcaseResult"]
