"""断路器状态机回归测试——全部使用假时钟，零真实等待。"""
from __future__ import annotations

from conclave import CLOSED, HALF_OPEN, OPEN, CircuitBreaker

from conftest import FakeClock


class TestClosedToOpen:
    def test_closed_allows_everything(self):
        cb = CircuitBreaker()
        assert cb.state == CLOSED
        assert cb.allow() is True

    def test_trips_after_consecutive_threshold(self):
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        assert cb.state == CLOSED  # 未到阈值，保持闭合
        cb.record_failure()
        assert cb.state == OPEN
        assert cb.allow() is False  # OPEN：快速失败

    def test_intermittent_failures_do_not_trip(self):
        """设计意图：熔断看『连续』失败，偶发抖动不该误伤。"""
        cb = CircuitBreaker(failure_threshold=3)
        cb.record_failure()
        cb.record_failure()
        cb.record_success()  # 清零计数
        cb.record_failure()
        assert cb.state == CLOSED


class TestCooldownAndHalfOpen:
    def test_open_blocks_until_cooldown(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=30.0, clock=clock)
        cb.record_failure()
        assert cb.state == OPEN
        assert cb.allow() is False

        clock.advance(29.9)
        assert cb.allow() is False  # 差一秒都不行

        clock.advance(0.1)
        assert cb.allow() is True  # 冷却到期，转入探测
        assert cb.state == HALF_OPEN

    def test_half_open_recovers_on_success(self):
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=30.0, clock=clock)
        cb.record_failure()
        clock.advance(30.0)
        assert cb.allow() is True  # HALF_OPEN 探测流量
        cb.record_success()  # 探测成功
        assert cb.state == CLOSED

    def test_half_open_retrips_on_failure(self):
        """探测失败立即二次熔断——恢复期不重复踩坑。"""
        clock = FakeClock()
        cb = CircuitBreaker(failure_threshold=1, reset_timeout=30.0, clock=clock)
        cb.record_failure()
        clock.advance(30.0)
        cb.allow()
        cb.record_failure()  # 探测失败
        assert cb.state == OPEN
        clock.advance(30.0)
        assert cb.allow() is True  # 重新冷却后再次给机会
