"""DeepSeek 官方 Search / Fetch 客户端
=====================================

文档：https://api-docs.deepseek.com/zh-cn/guides/search

- `POST /v1/tools/search`  {query, count, need_content, ori_question} → {results:[...]}
- `POST /v1/tools/fetch`   {url} → {title, content}

计费：每次成功请求约 0.005 元（5 元 / 1000 次），比单纯的采集接口便宜得多；
返回字段：title / url / snippet / summary / content / site_name / date / authority / rerank_score。

与 `app/llm_client.py` 共用 `DEEPSEEK_API_KEY`（同源 Bearer 鉴权）。
"""

import os
from typing import Any

import httpx

from app import llm_client

BASE_URL = os.environ.get("DEEPSEEK_BASE_URL", "https://api.deepseek.com").rstrip("/")

# 演示口径：Search 无结果不计费，但被限流也值得重试一次
RETRIES = 1


class SearchError(RuntimeError):
    """搜索/抓取失败（网络、鉴权、余额、限流）。"""


def _key() -> str:
    llm_client.has_api_key()  # 触发 .env 加载
    key = os.environ.get("DEEPSEEK_API_KEY", "").strip()
    if not key:
        raise SearchError("未配置 DEEPSEEK_API_KEY，无法使用网页搜索")
    return key


def _post(path: str, body: dict[str, Any], timeout: float = 60.0) -> dict:
    headers = {
        "Authorization": f"Bearer {_key()}",
        "Content-Type": "application/json",
    }
    last: Exception | None = None
    for attempt in range(RETRIES + 1):
        try:
            with httpx.Client(timeout=timeout) as client:
                resp = client.post(BASE_URL + path, headers=headers, json=body)
            if resp.status_code in (408, 429) or resp.status_code >= 500:
                last = SearchError(f"搜索服务暂时不可用（HTTP {resp.status_code}）")
                continue
            if resp.status_code == 402:
                raise SearchError("DeepSeek 账户余额不足（HTTP 402），搜索接口不可用")
            if resp.status_code == 401:
                raise SearchError("DEEPSEEK_API_KEY 无效（HTTP 401）")
            if resp.status_code >= 400:
                raise SearchError(f"搜索接口报错（HTTP {resp.status_code}）：{resp.text[:160]}")
            return resp.json()
        except httpx.HTTPError as exc:  # 网络层
            last = SearchError(f"搜索接口连接失败：{type(exc).__name__}")
    raise last or SearchError("搜索接口失败")


def search(query: str, count: int = 8, need_content: bool = True,
           ori_question: str = "") -> list[dict]:
    """按 query 搜索网页，返回结构化结果列表（无结果返回空列表）。"""
    body: dict[str, Any] = {"query": query, "count": max(1, min(50, count)),
                            "need_content": bool(need_content)}
    if ori_question:
        body["ori_question"] = ori_question[:128]
    data = _post("/v1/tools/search", body)
    results = data.get("results") or []
    return [r for r in results if isinstance(r, dict)]


def fetch(url: str, timeout: float = 60.0) -> dict:
    """抓取单个网页正文（Markdown）。"""
    data = _post("/v1/tools/fetch", {"url": url}, timeout=timeout)
    return {"title": data.get("title") or "", "content": data.get("content") or ""}


def digest(results: list[dict], content_limit: int = 700) -> str:
    """把结果压成给 LLM 读的紧凑证据块（带序号，方便模型引用）。"""
    chunks = []
    for index, item in enumerate(results, start=1):
        summary = (item.get("summary") or "").strip()
        content = (item.get("content") or "").strip()
        body = content[:content_limit] if content else summary[:content_limit]
        chunks.append(
            f"[{index}] {item.get('title') or ''}\n"
            f"    站点: {item.get('site_name') or '-'} | 日期: {(item.get('date') or '-')[:10]} | "
            f"权威: {item.get('authority')}\n"
            f"    链接: {item.get('url') or ''}\n"
            f"    摘要: {item.get('snippet') or ''}\n"
            f"    正文: {body}"
        )
    return "\n\n".join(chunks)
