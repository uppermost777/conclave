"""AI 简历评审面板 —— Conclave 的端到端示例。

三位专家并行、彼此隔离地评审同一份简历，聚合器（可选真实 LLM）合成终审报告。
这个场景是框架设计动机的活体演示：**独立性靠机制而非 prompt 保证**——
三位专家只拿到「简历全文 + 自己的角色设定」，看不到彼此，从根上杜绝 Groupthink。

用法：
    # 离线剧本模式（零网络、零依赖，CI 与本地默认走这条）
    python examples/resume_panel.py

    # 真实模型模式（需 CONCLAVE_LLM_API_KEY 等环境变量，见 llm_backend.py）
    python examples/resume_panel.py --live

    # 指定简历文件 + 故意让一位专家崩溃，观察 D2「异常即数据」的部分降级
    python examples/resume_panel.py --live --resume my_resume.md --inject-failure Clarity

解析优先级：命令行参数 > 环境变量 > 内置默认。
"""
from __future__ import annotations

import argparse
import atexit
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

# 在 GBK 控制台上也要能无损打印中文与框线字符
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    except (AttributeError, ValueError):
        pass

from conclave import Engine  # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
DEFAULT_RESUME = os.path.join(HERE, "sample_resume.md")


class _Tee:
    """把 stdout 同时写进终端和文件 —— 落盘的副本始终是干净的 UTF-8，方便做演示截图。"""

    def __init__(self, *streams):
        self._streams = streams

    def write(self, text: str) -> int:
        for stream in self._streams:
            stream.write(text)
        return len(text)

    def flush(self) -> None:
        for stream in self._streams:
            try:
                stream.flush()
            except Exception:  # noqa: BLE001
                pass


# --------------------------------------------------------------------------- #
# Agents
# --------------------------------------------------------------------------- #
class LlmBackedAgent:
    """真实模型适配器：execute 里只做一次 chat 调用。

    注意它**没有**任何"同伴是谁"的信息 —— 这正是 D1 零互知在适配层的体现：
    独立性不是靠 prompt 里写"不要受他人影响"，而是靠根本拿不到他人输出。
    """

    def __init__(self, role: str, goal: str, *, live: bool, resume: str, llm_kwargs=None):
        self.role = role
        self.goal = goal
        self._live = live
        self._resume = resume
        self._llm_kwargs = llm_kwargs or {}
        self.last_prompt: str | None = None

    def execute(self, prompt: str) -> str:
        task = f"{prompt}\n\n{self._resume}"
        self.last_prompt = task

        if not self._live:
            return _scripted(self.role)

        from llm_backend import chat  # noqa: PLC0415  (仅 live 模式才需要)

        return chat(
            task,
            system=(
                f"你是一位资深简历评审专家，角色是「{self.role}」。\n"
                f"你的评审视角与唯一职责：{self.goal}\n"
                "要求：\n"
                "1. 只从你的视角评审，不要面面俱到；\n"
                "2. 给出 3 条具体、可执行的修改建议，每条必须引用简历原文作为依据；\n"
                "3. 直接输出建议条目，不要写任何客套话、开场白或总结语；\n"
                "4. 总字数控制在 250 字以内。"
            ),
            **self._llm_kwargs,
        )


class CrashingAgent:
    """会崩溃的专家 —— 用来演示 D2：异常被收割成 error Trace，不炸整批。

    它不调用任何模型，直接抛异常。真实世界里对应的场景是 API 超时 / 限流 / 401。
    """

    def __init__(self, role: str, goal: str):
        self.role = role
        self.goal = goal

    def execute(self, prompt: str) -> str:  # noqa: ARG002
        raise RuntimeError(f"[{self.role}] simulated upstream failure (HTTP 503 / rate limit)")


_SCRIPTS = {
    "TechDepth": (
        "[技术深度] 简历中「BM25 / FTS5 / 向量三通道 + RRF 融合」写清了检索链路，"
        "但未说明为何选 RRF 而非加权求和：建议补一句『RRF 只融合名次不融合分值，"
        "避免不同通道分数量纲不可比』，这能体现你的取舍依据。"
    ),
    "ValueFocus": (
        "[项目价值] 「故障注入覆盖五条链路」是强结果，但缺少量化。"
        "建议补充『16 项离线测试 0.28s 跑完』这类可验证指标，"
        "把『做得全』翻译成『做得可证明』。"
    ),
    "Clarity": (
        "[表达] 项目条目动词高度统一（设计/实现/拆解），结构清晰。"
        "唯一问题是技术栈行堆叠了 5 个关键词，建议把最关键的一项前置并加粗。"
    ),
}


def _scripted(role: str) -> str:
    return _SCRIPTS.get(role, f"[{role}] (剧本占位回复) 建议补充量化结果。")


# --------------------------------------------------------------------------- #
# Aggregator
# --------------------------------------------------------------------------- #
def make_aggregator(live: bool, llm_kwargs=None):
    """返回符合 Engine 契约的聚合器：(prompt, reports) -> str。"""
    llm_kwargs = llm_kwargs or {}

    if live:

        def aggregate(prompt: str, reports: list[str]) -> str:
            from llm_backend import chat_aggregate  # noqa: PLC0415

            return chat_aggregate(prompt, reports, **llm_kwargs)

        return aggregate

    def aggregate(prompt: str, reports: list[str]) -> str:
        """离线版：把各专家意见机械拼接，保证无 key 环境也能完整跑通。"""
        block = "\n\n".join(reports)
        return (
            f"# 简历终审报告\n\n"
            f"评审任务：{prompt.splitlines()[0]}\n"
            f"参与专家：{len(reports)} 位（并行独立评审）\n\n"
            f"{block}\n\n"
            f"## 综合结论\n"
            f"融合以上 {len(reports)} 位专家意见后给出终审建议。"
        )

    return aggregate


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Conclave 示例：多专家并行简历评审",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--live", action="store_true", help="调用真实 LLM（需 API Key）")
    parser.add_argument("--resume", default=DEFAULT_RESUME, help="简历文本文件路径")
    parser.add_argument(
        "--inject-failure",
        metavar="ROLE",
        default=None,
        help="让指定角色的专家抛异常，演示部分降级（如 Clarity）",
    )
    parser.add_argument("--provider", default=None, help="厂商名：deepseek/zhipu/moonshot/...")
    parser.add_argument("--model", default=None, help="模型名，覆盖默认值")
    parser.add_argument("--base-url", default=None, help="API base url，覆盖默认值")
    parser.add_argument("--max-workers", type=int, default=4)
    parser.add_argument("--timeout", type=float, default=120.0, help="单个专家的超时秒数")
    parser.add_argument(
        "--save-output",
        default=None,
        metavar="PATH",
        help="把完整输出另存为 UTF-8 文本（终端显示不受影响）",
    )
    return parser


def load_resume(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as fh:
            text = fh.read().strip()
    except OSError as exc:
        raise SystemExit(f"无法读取简历文件 {path}: {exc}") from exc
    if not text:
        raise SystemExit(f"简历文件为空：{path}")
    return text


PANEL = [
    ("TechDepth", "评估技术深度：语言、架构与工程取舍是否说得清"),
    ("ValueFocus", "评估项目价值：业务影响与结果是否可量化"),
    ("Clarity", "评估表达：结构、措辞与可信度"),
]


def main() -> int:
    args = build_parser().parse_args()

    if args.save_output:
        sink = open(args.save_output, "w", encoding="utf-8")
        atexit.register(sink.close)
        sys.stdout = _Tee(sys.stdout, sink)

    resume = load_resume(args.resume)

    llm_kwargs = {}
    if args.provider:
        os.environ["CONCLAVE_LLM_PROVIDER"] = args.provider
    if args.model:
        llm_kwargs["model"] = args.model
    if args.base_url:
        llm_kwargs["base_url"] = args.base_url
    if args.live:
        llm_kwargs["timeout"] = args.timeout

    agents = []
    for role, goal in PANEL:
        if args.inject_failure and role == args.inject_failure:
            agents.append(CrashingAgent(role, goal))
        else:
            agents.append(
                LlmBackedAgent(role, goal, live=args.live, resume=resume, llm_kwargs=llm_kwargs)
            )

    task = "请评审这份 AI Agent 方向实习求职简历，指出问题并给出改进建议。"

    model_label = "offline-script"
    if args.live:
        from llm_backend import resolve_config  # noqa: PLC0415

        _, base, mdl = resolve_config(None, args.base_url, args.model)
        model_label = f"{mdl} @ {base}"

    mode = "真实模型" if args.live else "离线剧本"
    print("=" * 72)
    print("  Conclave · AI 简历评审面板")
    print(f"  {len(agents)} 位专家并行独立评审 → 聚合终审   [{mode}]")
    print("=" * 72)
    print(f"  模型      : {model_label}")
    print(f"  简历来源  : {os.path.relpath(args.resume)}  ({len(resume)} 字符)")
    print(f"  注入故障  : {args.inject_failure or '无'}")
    print("=" * 72)
    print()

    engine = Engine(max_workers=args.max_workers, per_call_timeout=args.timeout)
    result = engine.run(agents, task, aggregator=make_aggregator(args.live, llm_kwargs))

    print(result.content)
    print()
    print("-" * 72)
    print("  审计轨迹（Trace）")
    print("-" * 72)
    for t in result.traces:
        mark = "OK " if t.status == "success" else "ERR"
        detail = ""
        if t.status != "success" and getattr(t, "error", None):
            detail = f"  {t.error}"
        print(f"  [{mark}] {t.role:<12} {t.duration_ms:9.1f} ms{detail}")
    ok = sum(1 for t in result.traces if t.status == "success")
    print("-" * 72)
    print(
        f"  总耗时 {result.duration_ms:.1f} ms  |  "
        f"{ok}/{len(result.traces)} 位专家成功  |  "
        f"聚合产物 {len(result.content)} 字符"
    )
    print("-" * 72)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
