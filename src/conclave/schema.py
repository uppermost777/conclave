"""conclave.schema —— 引擎的数据契约。

设计决策 D7：用 stdlib dataclass 而非 pydantic。
引擎层的数据结构（Trace / SynthesisResult）只是被动记录审计事实，
不需要网络边界的输入校验和容错解析——那是 LLM 适配层的职责。
零依赖的承诺要求核心不引入任何重依赖；等真需要强校验时，
边界单独建层，核心保持纯 stdlib。
"""
from __future__ import annotations

from dataclasses import dataclass, field

STATUS_SUCCESS = "success"
STATUS_ERROR = "error"


@dataclass
class Trace:
    """单个 Agent 的一次执行审计记录（无论成败都留痕——见 engine 的 D2）。"""

    role: str
    status: str
    output: str | None = None
    error: str | None = None
    duration_ms: float = 0.0


@dataclass
class SynthesisResult:
    """一次 Engine.run 的完整结果：聚合产物 + 全量 Trace。

    traces 是审计的关键：成败、耗时、错误信息一字不落，
    排查"哪个专家超时了"不需要翻日志，结构里就有。
    """

    content: str
    traces: list[Trace] = field(default_factory=list)
    duration_ms: float = 0.0

    @property
    def succeeded(self) -> list[Trace]:
        return [t for t in self.traces if t.status == STATUS_SUCCESS]

    @property
    def failed(self) -> list[Trace]:
        return [t for t in self.traces if t.status == STATUS_ERROR]
