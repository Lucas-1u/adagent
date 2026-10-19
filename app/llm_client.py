"""
统一 LLM 封装（DeepSeek）
=========================

全组唯一的大模型调用入口 —— 不要各自写 API 请求代码。
（key、超时、重试、模型名、JSON 解析，全组只维护这一处。）

配置（二选一）：
    A. 项目根目录建 .env 文件（推荐）：从 .env.example 复制一份改名，填入 key。
    B. 直接设环境变量 DEEPSEEK_API_KEY。

可选环境变量：
    DEEPSEEK_BASE_URL   默认 https://api.deepseek.com（兼容 OpenAI 协议的其他平台可改）
    DEEPSEEK_MODEL      默认 deepseek-flash
    DEEPSEEK_TIMEOUT    单次请求超时秒数，默认 60

用法：
    from app import llm_client

    text = llm_client.chat("帮我写一句咖啡店的七夕文案")
    data = llm_client.chat_json("分析这家店……，输出 JSON")

调用记录（工作台"LLM 记录"数据源）：
    在 collect_calls("节点名") 作用域内发生的每次调用都会自动被记录为
    {node, purpose, model, system, prompt, reasoning, response, elapsed}，
    由框架层并入 trace（status="llm"）。调用时可传 purpose="用途" 便于区分；
    不在作用域内的调用不做记录（零开销）。

模型说明（以 DeepSeek 官方文档为准）：
    - deepseek-flash    即 DeepSeek-V4.1-Flash，快、便宜、支持 JSON 输出，默认用它
    - deepseek-v4-pro   V4-Pro（逐步退役中，请求会被路由到 V4.1-Flash）
    提示：deepseek-flash 默认开启 thinking 模式，如需调整请参考官方 Thinking Mode 文档。
"""

import contextvars
import json
import os
import time
from contextlib import contextmanager

from openai import OpenAI

_env_loaded = False
_client = None

# ---------------------------------------------------------------- 调用记录

# 当前节点名 / 记录收集器（contextvar：随执行上下文传播，互不串扰）
_current_node: contextvars.ContextVar[str] = contextvars.ContextVar("am_current_node", default="")
_call_sink: contextvars.ContextVar = contextvars.ContextVar("am_call_sink", default=None)

_RECORD_LIMIT = 2000  # 单字段截断长度（字符）


@contextmanager
def collect_calls(node_name: str = ""):
    """在作用域内收集 LLM 调用记录。

    用法（框架层）：
        with llm_client.collect_calls("final") as calls:
            result = fn(state)
        # calls -> [{node, status:"llm", purpose, model, system, prompt, reasoning, response, elapsed}, ...]

    不在该作用域内的调用不做记录（零开销、零副作用）。
    """
    sink: list[dict] = []
    tok_node = _current_node.set(node_name)
    tok_sink = _call_sink.set(sink)
    try:
        yield sink
    finally:
        _call_sink.reset(tok_sink)
        _current_node.reset(tok_node)


def _cut(text: str | None, limit: int = _RECORD_LIMIT) -> str:
    text = text or ""
    return text if len(text) <= limit else text[:limit] + "…（已截断）"


def _record_call(purpose: str, system: str, prompt: str, response: str,
                 reasoning: str | None, elapsed: float, model: str) -> None:
    """把一次调用写入当前收集器（不在收集作用域内则静默跳过）。"""
    sink = _call_sink.get()
    if sink is None:
        return
    item: dict = {
        "node": _current_node.get() or "unknown",
        "status": "llm",
        "purpose": purpose,
        "model": model,
        "system": _cut(system),
        "prompt": _cut(prompt),
        "response": _cut(response),
        "elapsed": elapsed,
    }
    if reasoning:
        item["reasoning"] = _cut(reasoning)
    sink.append(item)


def _ensure_env() -> None:
    """首次调用时自动加载项目根目录 .env（未安装 python-dotenv 也不影响）。"""
    global _env_loaded
    if _env_loaded:
        return
    _env_loaded = True
    try:
        from dotenv import load_dotenv

        load_dotenv()
    except ImportError:
        pass


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _ensure_env()
        api_key = os.environ.get("DEEPSEEK_API_KEY", "")
        if not api_key:
            raise RuntimeError(
                "未找到 DEEPSEEK_API_KEY。\n"
                "请复制 .env.example 为 .env 并填入 key（见 docs/队友协作指南.md 第 4 节）。"
            )
        _client = OpenAI(
            api_key=api_key,
            base_url=os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            timeout=float(os.environ.get("DEEPSEEK_TIMEOUT", "60")),
        )
    return _client


def has_api_key() -> bool:
    """是否已配置 API key（供 Final 等模块判断能否走 LLM 增强）。"""
    _ensure_env()
    return bool(os.environ.get("DEEPSEEK_API_KEY", "").strip())


def default_model() -> str:
    """当前默认模型（可用环境变量 DEEPSEEK_MODEL 覆盖）。"""
    _ensure_env()
    return os.environ.get("DEEPSEEK_MODEL", "deepseek-flash")


def chat(
    prompt: str,
    system: str = "",
    model: str | None = None,
    temperature: float = 1.0,
    json_mode: bool = False,
    max_retries: int = 2,
    purpose: str = "",
) -> str:
    """普通对话调用，返回文本；带自动重试（网络抖动容错）。

    purpose：调用用途（如 "intake" / "check" / "summary"），写入调用记录供工作台展示。
    """
    messages: list[dict] = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    extra: dict = {}
    if json_mode:
        extra["response_format"] = {"type": "json_object"}

    last_err: Exception | None = None
    for attempt in range(max_retries + 1):
        try:
            model_used = model or default_model()
            t0 = time.time()
            resp = _get_client().chat.completions.create(
                model=model_used,
                messages=messages,
                temperature=temperature,
                **extra,
            )
            message = resp.choices[0].message
            content = message.content or ""
            _record_call(
                purpose=purpose or ("chat_json" if json_mode else "chat"),
                system=system,
                prompt=prompt,
                response=content,
                reasoning=getattr(message, "reasoning_content", None),
                elapsed=round(time.time() - t0, 2),
                model=model_used,
            )
            return content
        except Exception as exc:
            last_err = exc
            if attempt < max_retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"LLM 调用失败（已重试 {max_retries} 次）：{last_err}")


def chat_json(
    prompt: str,
    system: str = "",
    model: str | None = None,
    temperature: float = 1.0,
    purpose: str = "",
) -> dict:
    """调用并解析 JSON —— 各 Agent 产出契约结构的推荐入口。"""
    text = chat(prompt, system=system, model=model, temperature=temperature,
                json_mode=True, purpose=purpose)
    text = text.strip()
    if text.startswith("```"):  # 容错：清掉可能出现的 markdown 围栏
        text = text.strip("`").removeprefix("json").strip()
    return json.loads(text)
