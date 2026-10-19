"""客户发现 Agent —— 接入层（两条数据源，按环境变量切换）。

数据源：
- `search`（默认）：DeepSeek 官方 Search / Fetch（`app/search_client.py`），抓公开网页，
  走 `app/agents/search_customer_agent.py`。不需要额外 key，只需 `DEEPSEEK_API_KEY`。
- `justone`：队友 1 的 `customer_discovery/` 包（JustOneAPI 采集 + LLM 三阶段），
  需要 `JUSTONEAPI_TOKEN` 与余额。

切换方式（进程环境优先于 .env）：
    DISCOVERY_PROVIDER=search    # 默认
    DISCOVERY_PROVIDER=justone

本文件仍只做两件事：暴露契约约定的 `run(state)`；保留 `FALLBACK` 供保护壳降级。
"""

import os

from customer_discovery.customer_agent import run as _justone_run

from app.agents import search_customer_agent

FIELD = "customer_info"

DEFAULT_PROVIDER = "search"

# 降级兜底数据（演示保险：节点异常时由保护壳 app/guard.py 顶替输出）
FALLBACK: dict = {
    "shop": "七七拼豆体验馆",
    "type": "handmade",
    "platform": "小红书",
    "highlights": "面向年轻女性的手作体验店，以作品展示内容为主",
    "potential_need": "缺少活动推广素材",
}


def provider() -> str:
    """当前数据源（大小写不敏感，非法值回落到默认）。"""
    value = (os.environ.get("DISCOVERY_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    return value if value in ("search", "justone") else DEFAULT_PROVIDER


def run(state: dict) -> dict:
    """节点入口：按 DISCOVERY_PROVIDER 选数据源，并上报可展示的成本/来源信息。"""
    if provider() == "justone":
        patch = _justone_run(state)
        info = patch.get("customer_info") or {}
        extra = info.get("extra") or {}
        detail = {
            "provider": "justone",
            "api_calls": extra.get("api_calls"),
            "tool_calls": extra.get("tool_calls"),
            "search_pairs": extra.get("search_pairs"),
            "merchants": extra.get("lead_count"),
        }
    else:
        patch = search_customer_agent.run(state)
        info = patch.get("customer_info") or {}
        extra = info.get("extra") or {}
        detail = {
            "provider": "deepseek_search",
            "searches": extra.get("searches"),
            "results": extra.get("results"),
            "merchants": 0 if extra.get("no_match") else 1,
            "confidence": extra.get("confidence"),
        }

    detail = {key: value for key, value in detail.items() if value is not None}
    if detail:
        patch["_trace_detail"] = detail
    return patch


if __name__ == "__main__":
    import json

    demo = {"user_input": " ".join(__import__("sys").argv[1:])
            or "寻找武汉地区适合年轻群体感受小资氛围的复古咖啡店，帮它做一轮推广"}
    print(json.dumps(run(demo), ensure_ascii=False, indent=2))
