"""客户发现 Agent（DeepSeek 网页搜索版）
==========================================

链路：一句话需求 → DeepSeek Search 抓公开网页 → LLM 只从证据里挑一家真实门店，
产出符合契约的 `customer_info`（店名 / 品类 / 来源平台 / 地址 / 营业时间 / 特点 / 潜在需求）。

纪律：
- 只允许使用搜索结果里的信息，不补全、不猜测；找不到可核查门店就明确 no_match；
- 每次搜索 0.005 元，默认最多 2 次搜索 + 1 次 LLM 结构化；
- 结果完全可追溯：`extra.evidence` 记 [{index, quote}]，`extra.source_urls` 记原始链接。

使用 `DISCOVERY_PROVIDER=search`（默认）走本模块；`justone` 走 `customer_discovery/` 旧链路。
"""

import json
import re
from typing import Any

from app import llm_client, search_client
from app.agents._normalize import normalize_keys

FIELD = "customer_info"

#: 模型偶尔用中文键名，这里做一次键名归一（只换键，不改内容）
SEARCH_ALIASES: dict[str, str] = {
    "找到": "found",
    "是否找到": "found",
    "店名": "shop",
    "门店": "shop",
    "门店名称": "shop",
    "店铺名称": "shop",
    "类型": "type",
    "品类": "type",
    "平台": "platform",
    "来源": "platform",
    "来源平台": "platform",
    "地址": "address",
    "位置": "address",
    "营业时间": "hours",
    "时间": "hours",
    "特点": "highlights",
    "亮点": "highlights",
    "具体事实": "highlights",
    "潜在需求": "potential_need",
    "营销需求": "potential_need",
    "证据": "evidence",
    "置信度": "confidence",
    "原因": "reason",
    "理由": "reason",
}

MAX_SEARCHES = 2
SEARCH_COUNT = 8

SYSTEM = """你是市场调研助手，任务是从给定的网页搜索结果里，找出一家真实存在、可核查的门店，
并整理成结构化数据。

「可核查」的定义（很重要，不要自己加码）：
- 至少能在证据里看到 **门店名称**；
- 再加上任一可定位/可联系信息即可：区域或街道名、商圈、平台账号/主页链接、点评页、探店笔记标题；
- 门牌号、营业时间、电话**可以缺失**，缺失就留空，不影响判定。

硬性规则：
1. 只能使用搜索结果中出现的信息；没写的一律留空，不要根据常识补全。
2. 同一家店的信息可以跨多条结果合并，但每条结论都要有原文出处（写进 evidence）。
3. 有多家候选时，挑最符合用户需求、信息最完整的一家。
4. 确实没有任何门店名称时 found=false，并在 reason 里说明缺什么。
5. highlights 写 2-5 条具体事实（地理位置、店面风格、招牌产品、主理人、营业时段等），
   不要形容词堆砌，不要写营销口号。
6. 只输出 JSON，不要任何解释文字。"""

PROMPT = """用户需求：{user_input}

搜索结果（每条含标题、站点、链接、摘要、正文节选）：
{digest}

请输出 JSON：
{{
  "found": true 或 false,
  "shop": "门店名称（与证据一致）",
  "type": "coffee / bakery / flowers / cake / handmade / restaurant / retail / other",
  "platform": "信息主要来源（小红书 / 大众点评 / 抖音 / 微博 / 商户官网 / 新闻网页）",
  "address": "地址或所在区域原文，找不到留空",
  "hours": "营业时间原文，找不到留空",
  "highlights": ["2-5 条来自证据的具体事实"],
  "potential_need": "一句话：这家店当前缺什么营销动作（只有能由证据支持才写，否则留空）",
  "evidence": [{{"index": 命中的结果序号, "quote": "支撑上面结论的原文片段"}}],
  "confidence": 0.0-1.0,
  "reason": "found=false 时说明缺什么"
}}"""


def _queries(user_input: str) -> list[str]:
    text = user_input.strip()
    first = text
    # 第二组查询专门要「店名 + 地址/营业时间」这类可核查信息（0.005 元/次）
    second = f"{re.sub(r'[，。；]', ' ', text)} 店名 地址 营业时间"
    return [first, second][:MAX_SEARCHES]


def _collect(user_input: str) -> tuple[list[dict], list[str]]:
    """两轮搜索合并去重（合计不超过 MAX_SEARCHES 次，约 0.01 元）。"""
    seen: set[str] = set()
    merged: list[dict] = []
    used: list[str] = []
    for query in _queries(user_input):
        results = search_client.search(query, count=SEARCH_COUNT, need_content=True,
                                       ori_question=user_input)
        used.append(query)
        for item in results:
            url = str(item.get("url") or "")
            if url and url in seen:
                continue
            if url:
                seen.add(url)
            merged.append(item)
    return merged, used


def _highlights_text(highlights: list[str], address: str, hours: str) -> str:
    """把结构化字段拼回契约的 highlights：地址/营业时间各自成行，poster 会按行抠。"""
    lines: list[str] = []
    if address:
        lines.append(f"地址：{address}")
    if hours:
        lines.append(f"营业时间：{hours}")
    lines.extend(str(h).strip() for h in highlights if str(h).strip())
    return "\n".join(lines)


def _no_match(reason: str, extra_base: dict) -> dict:
    extra = dict(extra_base)
    extra.update({"status": "failed", "no_match": True, "errors": [reason]})
    return {
        FIELD: {
            "shop": "未发现可核查商家",
            "type": "other",
            "platform": "网页搜索",
            "highlights": reason,
            "potential_need": "",
            "extra": extra,
        }
    }


def run(state: dict) -> dict:
    """节点入口：state 里的 user_input → customer_info。"""
    user_input = str(state.get("user_input") or "").strip()
    if len(user_input) < 3:
        raise ValueError("user_input 至少需要 3 个字")

    results, queries = _collect(user_input)
    extra_base: dict[str, Any] = {
        "provider": "deepseek_search",
        "searches": len(queries),
        "results": len(results),
        "source_urls": [str(r.get("url") or "") for r in results][:8],
        "queries": queries,
    }

    if not results:
        return _no_match("search_no_results", extra_base)

    prompt = PROMPT.format(user_input=user_input, digest=search_client.digest(results))
    try:
        data = llm_client.chat_json(prompt, system=SYSTEM, purpose="customer_search")
    except Exception as exc:  # noqa: BLE001 —— LLM 失败也算"没找到"，但不能静默
        return _no_match(f"llm_failed:{type(exc).__name__}", extra_base)

    if not isinstance(data, dict):
        return _no_match("llm_invalid_json", extra_base)

    # 中文键名（{"店名": ...}）先归一，再按契约字段读
    data = normalize_keys(data, SEARCH_ALIASES)

    found = bool(data.get("found"))
    shop = str(data.get("shop") or "").strip()
    if not found or not shop:
        return _no_match(str(data.get("reason") or "not_found_in_evidence"), extra_base)

    address = str(data.get("address") or "").strip()
    hours = str(data.get("hours") or "").strip()
    highlights = [str(h).strip() for h in (data.get("highlights") or []) if str(h).strip()]
    evidence = [
        {"index": item.get("index"), "quote": str(item.get("quote") or "")[:160]}
        for item in (data.get("evidence") or [])
        if isinstance(item, dict)
    ]

    extra = dict(extra_base)
    extra.update({
        "status": "ok",
        "no_match": False,
        "source": "deepseek_search",
        "confidence": data.get("confidence"),
        "evidence": evidence,
        "address": address,
        "hours": hours,
        "raw": json.dumps(data, ensure_ascii=False)[:2000],
    })

    return {
        FIELD: {
            "shop": shop,
            "type": str(data.get("type") or "other").strip() or "other",
            "platform": str(data.get("platform") or "网页搜索").strip(),
            "highlights": _highlights_text(highlights, address, hours),
            "potential_need": str(data.get("potential_need") or "").strip(),
            "extra": extra,
        }
    }


if __name__ == "__main__":
    import sys

    demo = {"user_input": " ".join(sys.argv[1:]) or "武汉地区 适合年轻人放松的复古咖啡店"}
    print(json.dumps(run(demo), ensure_ascii=False, indent=2)[:2000])
