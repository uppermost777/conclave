"""Conclave —— 并行隔离多智能体执行引擎。

教皇选举（conclave）式的多智能体协作：Agent 彼此隔离、并行独立
执行，产出聚合后才揭晓；零运行时依赖（纯 stdlib）。
"""
from .breaker import CLOSED, HALF_OPEN, OPEN, CircuitBreaker
from .engine import Agent, Engine, default_aggregator
from .exceptions import ConclaveError, IncompleteBatchError, NoValidReportsError
from .schema import SynthesisResult, Trace

__version__ = "0.1.0"

__all__ = [
    "Agent",
    "Engine",
    "CircuitBreaker",
    "Trace",
    "SynthesisResult",
    "NoValidReportsError",
    "IncompleteBatchError",
    "ConclaveError",
    "default_aggregator",
    "CLOSED",
    "OPEN",
    "HALF_OPEN",
    "__version__",
]
