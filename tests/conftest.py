"""共享测试替身（test doubles）。

设计决策 D6/D8 的受益者：Agent 是鸭子类型 + 构造注入行为，
测试替身零成本；不需要 mock 库，纯 stdlib。
"""
from __future__ import annotations

import time


class ScriptedAgent:
    """按剧本回应的假 Agent；记录每次收到的 prompt 用于断言。"""

    def __init__(self, role: str, goal: str, responses: list[str]):
        self.role = role
        self.goal = goal
        self.responses = list(responses)
        self.received_prompts: list[str] = []

    def execute(self, prompt: str) -> str:
        self.received_prompts.append(prompt)
        idx = min(len(self.received_prompts) - 1, len(self.responses) - 1)
        return self.responses[idx]


class ExplodingAgent:
    """永远抛异常的 Agent——故障注入实验的主角。

    模拟真实世界的：API 超时、限流 429、返回体解析失败……
    对引擎而言它们都只是 Exception，统一走收割路径。
    """

    def __init__(self, role: str, goal: str = "boom", error: Exception | None = None):
        self.role = role
        self.goal = goal
        self.error = error or RuntimeError("simulated API outage")
        self.calls = 0

    def execute(self, prompt: str) -> str:
        self.calls += 1
        raise self.error


class SleepingAgent:
    """慢 Agent——用于验证 per-batch 超时路径（D4）。"""

    def __init__(self, role: str, goal: str, seconds: float, output: str = "late"):
        self.role = role
        self.goal = goal
        self.seconds = seconds
        self.output = output

    def execute(self, prompt: str) -> str:
        time.sleep(self.seconds)
        return self.output


class FakeClock:
    """假时钟——零等待推进断路器冷却期（D8）。"""

    def __init__(self, start: float = 1000.0):
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds
