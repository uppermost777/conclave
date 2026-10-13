"""conclave.exceptions —— 失败语义即 API。

设计决策：异常不是"出错"，是引擎对外暴露失败语义的正式接口。
两个异常携带完整 traces，调用方 catch 到的那一刻就拥有全部审计现场，
不需要二次查询——调试分布式并行任务时，这一点价值千金。
"""
from __future__ import annotations

from .schema import STATUS_ERROR, Trace


class ConclaveError(RuntimeError):
    """所有引擎异常的基类。"""


class NoValidReportsError(ConclaveError):
    """门 1：整批零成功。

    聚合无从谈起（对一个空集合做"合成"只会产出幻觉），
    因此快速失败并把失败现场原样交还调用方。
    """

    def __init__(self, traces: list[Trace]):
        self.traces = traces
        failed = [t.role for t in traces if t.status == STATUS_ERROR]
        super().__init__(
            f"all {len(traces)} agent(s) failed; nothing to aggregate. "
            f"failed: {failed}"
        )


class IncompleteBatchError(ConclaveError):
    """门 2：require_all=True 但批次不完整。

    与门 1 的区别：这里有部分成功，但完整性场景（对账、合规、
    多签）下"部分结果"比"没有结果"更危险——它看起来可用，
    实则缺了一角。所以宁可整体失败，也不交付残缺品。
    """

    def __init__(self, traces: list[Trace]):
        self.traces = traces
        ok = [t.role for t in traces if t.status != STATUS_ERROR]
        bad = [t.role for t in traces if t.status == STATUS_ERROR]
        super().__init__(
            f"require_all=True but batch incomplete. "
            f"succeeded: {ok}; failed/missing: {bad}"
        )
