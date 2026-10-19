"""LLM 客户端：任何 OpenAI 兼容的 chat/completions 端点都能用。

默认读环境变量（也支持 .env 文件）：
    CREATIVE_LLM_BASE_URL   默认 https://api.deepseek.com/v1
    CREATIVE_LLM_API_KEY    默认回落到 DEEPSEEK_API_KEY
    CREATIVE_LLM_MODEL      默认 deepseek-chat

没有可用 key 时自动走 rule_based 兜底 —— 保证 Demo 在任何环境都能跑出完整素材包。
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path
from typing import Any, Dict, Optional

import requests

from . import prompts

DEFAULT_BASE_URL = "https://api.deepseek.com/v1"
DEFAULT_MODEL = "deepseek-chat"


def load_env_file(path: Optional[str] = None) -> Dict[str, str]:
    """读一个 KEY=VALUE 的 .env（不依赖 python-dotenv）。"""
    path = path or os.environ.get("CREATIVE_ENV_FILE", "")
    if not path:
        for cand in (Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env",
                     Path.home() / ".hermes" / ".env"):
            if cand.is_file():
                path = str(cand)
                break
    out: Dict[str, str] = {}
    if path and Path(path).is_file():
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


class LLMClient:
    """极简 OpenAI 兼容客户端，带 JSON 修复与重试。"""

    def __init__(self, base_url: Optional[str] = None, api_key: Optional[str] = None,
                 model: Optional[str] = None, timeout: int = 120):
        env = load_env_file()
        self.base_url = (base_url or os.environ.get("CREATIVE_LLM_BASE_URL")
                         or env.get("CREATIVE_LLM_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")
        self.api_key = (api_key or os.environ.get("CREATIVE_LLM_API_KEY")
                        or env.get("CREATIVE_LLM_API_KEY") or env.get("DEEPSEEK_API_KEY") or "")
        self.model = (model or os.environ.get("CREATIVE_LLM_MODEL")
                      or env.get("CREATIVE_LLM_MODEL") or DEFAULT_MODEL)
        self.timeout = timeout
        self.last_error = ""

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    # -------------------------------------------------- 低层

    def chat(self, messages, temperature: float = 0.8, json_mode: bool = False) -> str:
        body: Dict[str, Any] = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
        }
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        last = None
        for attempt in range(3):
            try:
                resp = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers={"Authorization": f"Bearer {self.api_key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=self.timeout)
                if resp.status_code >= 400:
                    raise RuntimeError(f"HTTP {resp.status_code}: {resp.text[:300]}")
                return resp.json()["choices"][0]["message"]["content"]
            except Exception as exc:      # noqa: BLE001 —— 网络/额度/格式都重试
                last = exc
                self.last_error = str(exc)
                time.sleep(1.5 * (attempt + 1))
        raise RuntimeError(f"LLM 调用失败：{last}")

    def chat_json(self, system: str, user: str, temperature: float = 0.8) -> Dict[str, Any]:
        raw = self.chat([{"role": "system", "content": system},
                         {"role": "user", "content": user}],
                        temperature=temperature, json_mode=True)
        data = _parse_json(raw)
        if data is not None:
            return data
        fixed = self.chat([{"role": "system", "content": prompts.REPAIR_SYSTEM},
                           {"role": "user", "content": raw}], temperature=0.0, json_mode=True)
        data = _parse_json(fixed)
        if data is None:
            raise RuntimeError("LLM 两次都没能返回合法 JSON")
        return data

    # -------------------------------------------------- 高层：创意简报

    def creative_brief(self, customer: str, analysis: str, strategy: str,
                       channels: str) -> Dict[str, Any]:
        return self.chat_json(
            prompts.build_system_prompt(),
            prompts.build_user_prompt(customer, analysis, strategy, channels))


# ---------------------------------------------------------------- 工具

_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def _parse_json(text: str) -> Optional[Dict[str, Any]]:
    if not text:
        return None
    text = text.strip()
    m = _FENCE.search(text)
    if m:
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    start, end = text.find("{"), text.rfind("}")
    if 0 <= start < end:
        chunk = text[start:end + 1]
        try:
            return json.loads(chunk)
        except Exception:
            try:
                return json.loads(chunk.replace("，", ",").replace("：", ":"))
            except Exception:
                return None
    return None
