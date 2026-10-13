"""Engine 三道门的回归测试——由故障注入实验固化而来。

每个测试对应一个真实失败路径：
gate 0（收割）/ gate 1（零成功）/ gate 2（require_all）
以及 D1 零互知与 D4 批次超时两个核心机制。
"""
from __future__ import annotations

import pytest

from conclave import Engine, IncompleteBatchError, NoValidReportsError
from conclave.engine import STATUS_ERROR, STATUS_SUCCESS
from conclave.schema import Trace

from conftest import ExplodingAgent, ScriptedAgent, SleepingAgent


def make_panel() -> list:
    """三位专家评审面板（离线剧本版）。"""
    return [
        ScriptedAgent("Researcher", "find facts", ["facts: A, B"]),
        ScriptedAgent("Critic", "find risks", ["risk: X"]),
        ScriptedAgent("Writer", "draft report", ["draft v1"]),
    ]


class TestGate0Harvest:
    """gate 0：异常收割——单个 Agent 崩溃不炸整批。"""

    def test_single_failure_degrades_to_partial(self):
        engine = Engine()
        panel = [
            ScriptedAgent("Researcher", "find facts", ["facts: A, B"]),
            ExplodingAgent("Critic"),  # <- 注入 API 故障
            ScriptedAgent("Writer", "draft report", ["draft v1"]),
        ]
        result = engine.run(panel, "review this design")

        assert len(result.traces) == 3  # 3 条 Trace，一条不少
        by_role = {t.role: t for t in result.traces}
        assert by_role["Critic"].status == STATUS_ERROR
        assert "RuntimeError" in by_role["Critic"].error
        # 幸存者照常工作，聚合产物包含其输出
        assert by_role["Researcher"].status == STATUS_SUCCESS
        assert "facts: A, B" in result.content
        assert len(result.succeeded) == 2

    def test_error_trace_keeps_duration(self):
        """崩溃的 Agent 也要有耗时记录（审计完整性）。"""
        engine = Engine()
        result = engine.run([ExplodingAgent("Critic"), ScriptedAgent("W", "g", ["ok"])], "t")
        err = next(t for t in result.traces if t.status == STATUS_ERROR)
        assert err.duration_ms >= 0.0


class TestGate1ZeroSuccess:
    """gate 1：零成功快速失败。"""

    def test_all_fail_raises_no_valid_reports(self):
        engine = Engine()
        panel = [ExplodingAgent("A"), ExplodingAgent("B")]
        with pytest.raises(NoValidReportsError) as ei:
            engine.run(panel, "task")
        # 异常自带完整审计现场
        assert len(ei.value.traces) == 2
        assert all(t.status == STATUS_ERROR for t in ei.value.traces)
        assert "A" in str(ei.value) and "B" in str(ei.value)

    def test_aggregator_never_sees_empty_batch(self):
        """零成功时聚合器绝不能被调用（防幻觉聚合）。"""
        engine = Engine()
        called = []
        agg = lambda prompt, reports: called.append(reports) or "x"
        with pytest.raises(NoValidReportsError):
            engine.run([ExplodingAgent("A")], "task", aggregator=agg)
        assert called == []


class TestGate2RequireAll:
    """gate 2：完整性校验（require_all=True）。"""

    def test_incomplete_batch_raises_with_roster(self):
        engine = Engine()
        panel = [
            ScriptedAgent("Researcher", "g", ["ok"]),
            ExplodingAgent("Critic"),
            ScriptedAgent("Writer", "g", ["ok"]),
        ]
        with pytest.raises(IncompleteBatchError) as ei:
            engine.run(panel, "task", require_all=True)
        msg = str(ei.value)
        assert "Critic" in msg  # 失败名单
        assert "Researcher" in msg and "Writer" in msg  # 成功名单

    def test_complete_batch_passes_gate2(self):
        engine = Engine()
        result = engine.run(make_panel(), "task", require_all=True)
        assert len(result.succeeded) == 3


class TestZeroMutualKnowledge:
    """D1：零互知——Agent 收到的 prompt 不含任何同伴信息。"""

    def test_prompt_contains_no_peer_knowledge(self):
        engine = Engine()
        panel = make_panel()
        engine.run(panel, "review this design")
        for agent in panel:
            assert len(agent.received_prompts) == 1
            sent = agent.received_prompts[0]
            # 自己的角色与任务必须在场
            assert agent.role in sent and "review this design" in sent
            # 任何同伴的名字/目标不得出现
            for other in panel:
                if other is not agent:
                    assert other.role not in sent
                    assert other.goal not in sent


class TestBatchTimeout:
    """D4：per-batch 超时——超时必须是超时。"""

    def test_slow_agent_harvested_as_timeout(self):
        engine = Engine(per_call_timeout=0.15)
        panel = [
            ScriptedAgent("Fast", "g", ["quick answer"]),
            SleepingAgent("Slow", "g", seconds=5.0, output="late"),
        ]
        result = engine.run(panel, "task")
        by_role = {t.role: t for t in result.traces}
        assert by_role["Fast"].status == STATUS_SUCCESS
        assert by_role["Slow"].status == STATUS_ERROR
        assert "timeout" in by_role["Slow"].error


class TestAggregation:
    """聚合路径与默认聚合器。"""

    def test_custom_aggregator_receives_only_successes(self):
        engine = Engine()
        panel = [ScriptedAgent("A", "g", ["aa"]), ExplodingAgent("B")]
        result = engine.run(panel, "task", aggregator=lambda p, rs: "|".join(rs))
        assert result.content == "aa"

    def test_default_aggregator_never_empty(self):
        engine = Engine()
        result = engine.run(make_panel(), "task")
        assert "3 report(s)" in result.content
        assert "facts: A, B" in result.content
