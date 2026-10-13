"""conclave.breaker —— 跨批次失败记忆（断路器状态机）。

Engine 的三道门管的是“一批任务内”的失败隔离；熔断器管的是
“跨批次”的失败记忆——上游 LLM API 持续故障时，与其让每一批
都付出完整的超时成本，不如快速失败（OPEN）、冷却后小流量探测
（HALF_OPEN）、确认恢复再放量（CLOSED）。

状态机::

    CLOSED --(连续失败 >= failure_threshold)--> OPEN
    OPEN   --(冷却 reset_timeout 到期)-------> HALF_OPEN
    HALF_OPEN --(探测成功)--> CLOSED          # 恢复放量
    HALF_OPEN --(探测失败)--> OPEN            # 二次熔断

设计决策 D8：时钟可注入。
构造参数 clock 默认 time.monotonic，测试注入假时钟即可
零等待地推进“冷却 30 秒”——确定性测试不依赖真实睡眠。
这也是整个 repo 的测试能又快又稳的原因之一。
"""
from __future__ import annotations

import time
from typing import Callable

__all__ = ["CircuitBreaker", "CLOSED", "OPEN", "HALF_OPEN"]

CLOSED = "closed"
OPEN = "open"
HALF_OPEN = "half_open"


class CircuitBreaker:
    def __init__(
        self,
        failure_threshold: int = 3,
        reset_timeout: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.failure_threshold = failure_threshold
        self.reset_timeout = reset_timeout
        self._clock = clock
        self._state = CLOSED
        self._consecutive_failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        return self._state

    def allow(self) -> bool:
        """是否放行本次调用。OPEN 且未到期时拒绝（快速失败）。"""
        if self._state == OPEN:
            elapsed = self._clock() - (self._opened_at or 0.0)
            if elapsed >= self.reset_timeout:
                self._state = HALF_OPEN  # 冷却到期，转入探测
                return True
            return False
        return True  # CLOSED 放行；HALF_OPEN 放行（作为探测流量）

    def record_success(self) -> None:
        """任意成功都会清零失败计数并回到 CLOSED。"""
        self._consecutive_failures = 0
        self._state = CLOSED

    def record_failure(self) -> None:
        """记录一次失败；HALF_OPEN 下失败立即二次熔断。"""
        if self._state == HALF_OPEN:
            self._trip()  # 探测失败：不再给机会，直接回 OPEN 重新冷却
            return
        self._consecutive_failures += 1
        if self._consecutive_failures >= self.failure_threshold:
            self._trip()

    def _trip(self) -> None:
        self._state = OPEN
        self._opened_at = self._clock()
        self._consecutive_failures = 0
