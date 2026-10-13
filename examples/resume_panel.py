"""AI 简历评审面板 —— 示例应用（当前为离线剧本版）。

Roadmap 中的示例应用骨架：三位专家并行评审一份简历，
聚合器合成最终报告。当前用 ScriptedAgent 演示链路，
接入真实 LLM 只需替换 agent 的 execute 实现（见 README 上手段落）。

运行：PYTHONPATH=src python examples/resume_panel.py
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from conclave import Engine  # noqa: E402


class LlmBackedAgent:
    """接真实模型的适配器骨架：把 execute 指向任意 LLM API 即可。"""

    def __init__(self, role: str, goal: str):
        self.role = role
        self.goal = goal

    def execute(self, prompt: str) -> str:
        # TODO: 替换为真实调用，例如：
        #   from openai import OpenAI
        #   client = OpenAI(api_key=...)
        #   resp = client.chat.completions.create(
        #       model="deepseek-chat",
        #       messages=[{"role": "user", "content": prompt}],
        #   )
        #   return resp.choices[0].message.content
        return f"[{self.role}] (剧本占位回复) 结构完整，建议补充量化结果。"


def chief_aggregator(prompt: str, reports: list[str]) -> str:
    """首席评审：融合各专家意见产出终审报告（生产版应为一次 LLM 调用）。"""
    sections = "\n\n".join(f"### {r.splitlines()[0]}\n{r}" for r in reports)
    return (
        f"# 简历终审报告\n\n"
        f"评审维度：技术深度 / 项目价值 / 表达与量化\n\n{sections}\n\n"
        f"## 综合结论\n融合以上 {len(reports)} 位专家意见后给出终审建议。"
    )


def main() -> None:
    panel = [
        LlmBackedAgent("TechDepth", "评估技术深度：语言、架构与工程判断"),
        LlmBackedAgent("ValueFocus", "评估项目价值：业务影响与量化结果"),
        LlmBackedAgent("Clarity", "评估表达：结构、措辞与可信度"),
    ]
    engine = Engine(max_workers=4, per_call_timeout=30.0)
    result = engine.run(panel, "请评审这份后端开发工程师简历", aggregator=chief_aggregator)

    print(result.content)
    print("\n" + "=" * 60)
    print("审计轨迹：")
    for t in result.traces:
        mark = "OK " if t.status == "success" else "ERR"
        print(f"  [{mark}] {t.role:<12} {t.duration_ms:7.1f} ms")
    print(f"  总耗时 {result.duration_ms:.1f} ms")


if __name__ == "__main__":
    main()
