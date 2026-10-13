"""零依赖 LLM 适配层 —— 纯 stdlib 实现 OpenAI 兼容的 chat completions 协议。

为什么不用厂商 SDK：Conclave 的 D0 决策是"编排层不绑定任何厂商"。
把 SDK 挡在 examples/ 之外，用 40 行 urllib 说清接入成本，本身就是文档。

任何暴露 `POST {base_url}/chat/completions` 的服务都能直接接：
    DeepSeek     https://api.deepseek.com/v1              deepseek-chat
    智谱 GLM     https://open.bigmodel.cn/api/paas/v4     glm-4-flash
    Moonshot     https://api.moonshot.cn/v1               moonshot-v1-8k
    DashScope    https://dashscope.aliyuncs.com/compatible-mode/v1   qwen-plus
    SiliconFlow  https://api.siliconflow.cn/v1            Qwen/Qwen2.5-7B-Instruct

配置方式（任选，环境变量优先）：
    export CONCLAVE_LLM_API_KEY=sk-xxx
    export CONCLAVE_LLM_BASE_URL=https://api.deepseek.com/v1
    export CONCLAVE_LLM_MODEL=deepseek-chat
"""
from __future__ import annotations

import json
import os
import ssl
import urllib.error
import urllib.request

# 常见厂商的默认端点，仅用于未显式配置 base_url 时的猜测
_KNOWN_PROVIDERS = {
    "deepseek": ("https://api.deepseek.com/v1", "deepseek-chat"),
    "zhipu": ("https://open.bigmodel.cn/api/paas/v4", "glm-4-flash"),
    "moonshot": ("https://api.moonshot.cn/v1", "moonshot-v1-8k"),
    "dashscope": ("https://dashscope.aliyuncs.com/compatible-mode/v1", "qwen-plus"),
    "siliconflow": ("https://api.siliconflow.cn/v1", "Qwen/Qwen2.5-7B-Instruct"),
}

_DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
_DEFAULT_MODEL = "deepseek-chat"


class LlmError(RuntimeError):
    """LLM 调用失败。Engine 会把它收割成 error Trace —— 这正是 D2 的用武之地。"""


def resolve_config(
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> tuple[str, str, str]:
    """按 参数 > 环境变量 > 默认值 的顺序解析配置。"""
    provider = (os.environ.get("CONCLAVE_LLM_PROVIDER") or "").strip().lower()
    prov_base, prov_model = _KNOWN_PROVIDERS.get(provider, (_DEFAULT_BASE_URL, _DEFAULT_MODEL))

    key = (
        api_key
        or os.environ.get("CONCLAVE_LLM_API_KEY")
        or os.environ.get("DEEPSEEK_API_KEY")
        or os.environ.get("ZHIPUAI_API_KEY")
        or os.environ.get("MOONSHOT_API_KEY")
        or os.environ.get("DASHSCOPE_API_KEY")
        or os.environ.get("SILICONFLOW_API_KEY")
    )
    if not key:
        raise LlmError(
            "未找到 API Key。请设置环境变量 CONCLAVE_LLM_API_KEY"
            "（或 DEEPSEEK_API_KEY / ZHIPUAI_API_KEY 等厂商变量）。"
        )

    base = (base_url or os.environ.get("CONCLAVE_LLM_BASE_URL") or prov_base).rstrip("/")
    mdl = model or os.environ.get("CONCLAVE_LLM_MODEL") or prov_model
    return key, base, mdl


def chat(
    prompt: str,
    *,
    system: str | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
    timeout: float = 90.0,
    temperature: float = 0.3,
    max_tokens: int | None = None,
) -> str:
    """发一次 chat completion，返回纯文本内容。失败一律抛 LlmError。"""
    key, base, mdl = resolve_config(api_key, base_url, model)

    messages: list[dict[str, str]] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    payload: dict[str, object] = {
        "model": mdl,
        "messages": messages,
        "temperature": temperature,
    }
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens

    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        f"{base}/chat/completions",
        data=body,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
            "Accept": "application/json",
        },
    )

    ctx = ssl.create_default_context()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:400]
        raise LlmError(f"HTTP {exc.code} from {base}: {detail}") from exc
    except Exception as exc:  # noqa: BLE001
        raise LlmError(f"{type(exc).__name__} calling {base}: {exc}") from exc

    try:
        data = json.loads(raw)
        return data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
        raise LlmError(f"响应结构异常（{type(exc).__name__}）：{raw[:300]}") from exc


def chat_aggregate(prompt: str, reports: list[str], **kwargs: object) -> str:
    """把「聚合」也做成一次真实 LLM 调用 —— Engine 的 Aggregator 契约就是 (prompt, reports) -> str。

    注意：聚合器只拿到**幸存报告列表**，它无从得知有谁缺席（Engine 不把失败名单喂给它）。
    所以这里显式告知实际份数，避免模型臆测出不存在的专家意见——需要"人数必须齐"的场景，
    应该用 Engine 的 require_all 门在聚合之前就拦下，而不是靠提示词补救。
    """
    count = len(reports)
    expert_block = "\n\n---\n\n".join(reports)
    merged = (
        f"{prompt}\n\n"
        f"以下是 {count} 位相互隔离、彼此不知情的专家独立给出的意见。"
        f"本批次实际只有 {count} 份有效意见，请以实际份数为准，不要臆测未出现的观点或专家。\n\n"
        f"{expert_block}\n\n"
        f"请综合作出最终判断：如专家间有分歧，明确指出并给出你的取舍理由；输出结构化的终审结论。"
    )
    return chat(
        merged,
        system="你是一位首席评审，负责融合多位独立专家的意见并给出终审结论。",
        **kwargs,  # type: ignore[arg-type]
    )
