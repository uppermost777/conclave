"""conclave.engine —— 并行隔离执行引擎（核心）。

命名典故：教皇选举（conclave）——枢机们彼此隔离、并行独立投票，
选票聚合后才揭晓结果；黑烟 = 门 1（零有效票），白烟 = 聚合完成。

═══════════════════════════════════════════════════════════════════════
设计决策（D1–D6，每条都是一个被追问时能展开讲的取舍）
═══════════════════════════════════════════════════════════════════════

D1 零互知（zero mutual knowledge）
    Agent 只拿到「任务 + 自己的角色设定」，看不到任何同伴的存在。
    独立性靠机制保证，而不是靠 prompt 恳求模型"请独立思考"——
    同一上下文里互相看到的 Agent 会趋同（Groupthink）并陷入
    同一个思维死角（认知隧道）。隔离是唯一可靠的手段。

D2 异常即数据（gate 0：收割）
    子任务异常不向上传播，被收割为 status="error" 的 Trace。
    理由：审计完整性优先于执行成功率——崩溃的 Agent 也是一次
    可观测的事件，而不是应该让整批任务陪葬的事故。

D3 两道可配置的门
    门 1（永远开启）：零成功 -> NoValidReportsError。
        空集合上做"合成"只会产出幻觉，快速失败。
    门 2（require_all=True 时开启）：批次不完整 -> IncompleteBatchError。
        完整性场景（对账/合规/多签）下部分结果比没有结果更危险，
        它看起来可用、实则缺角。默认不开，避免一个免费额度抖动
        就拖垮整批任务。

D4 手动管理 executor（不用 with 语句）
    ThreadPoolExecutor.__exit__ 会阻塞等待所有 future 完成或取消，
    per-batch 超时语义形同虚设。因此显式 submit +
    as_completed(timeout=...)，超时后 shutdown(wait=False,
    cancel_futures=True) 立即放弃收割——超时必须是超时。

D5 time.monotonic() 而非 time.time()
    墙上时钟会被 NTP 回拨，耗时可能出现负数或倒流；单调时钟只
    保证递增，是测量耗时的唯一正确选择。

D6 Aggregator 是 callable，不是基类
    聚合策略千差万别（拼接/投票/再调一次 LLM 合成），用 Protocol
    鸭子类型只要 (prompt, reports) -> str 就能接入，框架不强迫
    继承——降低接入成本，也让测试替身零成本。
"""
from __future__ import annotations

import time
from concurrent.futures import (
    ThreadPoolExecutor,
    TimeoutError as FutureTimeoutError,
    as_completed,
)
from typing import Callable, Iterable, Protocol, runtime_checkable

from .exceptions import IncompleteBatchError, NoValidReportsError
from .schema import STATUS_ERROR, STATUS_SUCCESS, SynthesisResult, Trace

__all__ = ["Agent", "Engine", "default_aggregator"]


@runtime_checkable
class Agent(Protocol):
    """Agent 契约（鸭子类型）：有 role/goal、能 execute 即可接入。"""

    role: str
    goal: str

    def execute(self, prompt: str) -> str: ...


def default_aggregator(prompt: str, reports: list[str]) -> str:
    """默认聚合：拼接。没有聚合器时也能给出可读结果，
    引擎永不因"没配聚合器"而拒绝工作。"""
    parts = [f"# Synthesis of {len(reports)} report(s)\n"]
    parts += [f"## Report {i + 1}\n{r}\n" for i, r in enumerate(reports)]
    return "\n".join(parts)


class Engine:
    """并行隔离执行引擎。

    用法::

        engine = Engine(max_workers=8, per_call_timeout=30.0)
        result = engine.run(agents, task, aggregator=my_aggregator)
        # result.traces —— 全量审计轨迹（成败/耗时/错误）
        # result.content —— 聚合产物
    """

    def __init__(
        self,
        max_workers: int = 8,
        per_call_timeout: float | None = None,
    ) -> None:
        self.max_workers = max_workers
        # None = 不限时；设值则整批有硬超时（D4 的另一半）
        self.per_call_timeout = per_call_timeout

    # ------------------------------------------------------------------ run

    def run(
        self,
        agents: Iterable[Agent],
        prompt: str,
        aggregator: Callable[[str, list[str]], str] | None = None,
        require_all: bool = False,
    ) -> SynthesisResult:
        started = time.monotonic()
        agents = list(agents)

        # D4：手动管理 executor，见模块 docstring
        executor = ThreadPoolExecutor(max_workers=self.max_workers)
        future_map = {
            executor.submit(self._timed_execute, agent, prompt): agent
            for agent in agents
        }
        traces: list[Trace] = []
        harvested: set = set()
        try:
            try:
                for fut in as_completed(future_map, timeout=self.per_call_timeout):
                    traces.append(fut.result())
                    harvested.add(fut)
            except FutureTimeoutError:
                # 批次超时：已完成的补收，未完成的记为 timeout 错误（仍是 Trace，D2）
                for fut, agent in future_map.items():
                    if fut in harvested:
                        continue
                    if fut.done() and not fut.cancelled():
                        traces.append(fut.result())
                    else:
                        traces.append(
                            Trace(
                                role=agent.role,
                                status=STATUS_ERROR,
                                error="pending at batch timeout (abandoned)",
                            )
                        )
        finally:
            # D4：立即放弃收割仍在执行的任务
            executor.shutdown(wait=False, cancel_futures=True)

        # ---------------- 门 1：零成功（永远开启） ----------------
        ok = [t for t in traces if t.status == STATUS_SUCCESS]
        if not ok:
            raise NoValidReportsError(traces)

        # ---------------- 门 2：完整性校验（按需开启） ----------------
        if require_all and len(ok) < len(agents):
            raise IncompleteBatchError(traces)

        # ---------------- 聚合 ----------------
        agg = aggregator or default_aggregator
        content = agg(prompt, [t.output or "" for t in ok])
        return SynthesisResult(
            content=content,
            traces=traces,
            duration_ms=(time.monotonic() - started) * 1000.0,
        )

    # ------------------------------------------------------------- internals

    def _timed_execute(self, agent: Agent, prompt: str) -> Trace:
        """单 Agent 执行 + 计时 + 收割（D2/D5）。

        永不抛出：任何异常都转化为 error Trace 交给收割层，
        保证 as_completed 循环里 fut.result() 一定能拿到 Trace。
        """
        t0 = time.monotonic()  # D5：单调时钟
        built = self._build_prompt(agent, prompt)
        try:
            output = agent.execute(built)
        except Exception as exc:  # D2：异常即数据
            return Trace(
                role=agent.role,
                status=STATUS_ERROR,
                error=f"{type(exc).__name__}: {exc}",
                duration_ms=(time.monotonic() - t0) * 1000.0,
            )
        return Trace(
            role=agent.role,
            status=STATUS_SUCCESS,
            output=output,
            duration_ms=(time.monotonic() - t0) * 1000.0,
        )

    @staticmethod
    def _build_prompt(agent: Agent, prompt: str) -> str:
        """D1 零互知：Agent 收到的只有任务与自身设定。

        注意这里刻意不注入任何同伴信息——这不是遗漏，
        是框架的第一设计原则。
        """
        return (
            f"You are the {agent.role}.\n"
            f"Your mandate: {agent.goal}\n"
            f"You operate in isolation: you have NO knowledge of other agents.\n"
            f"\nTASK:\n{prompt}"
        )
