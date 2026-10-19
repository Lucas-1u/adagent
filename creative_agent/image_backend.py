"""图像生成后端适配器 —— 换后端不改上层代码。

支持三种后端，按需切换（环境变量 CREATIVE_IMAGE_BACKEND）：

  codex          ChatGPT/Codex OAuth（gpt-image-2），无需 API Key，画质最好、无平台水印。
                 读 ~/.codex/auth.json，走官方 images/generations 原生接口。
  openai_compat  任意 OpenAI 兼容的图片接口（智谱 CogView-4 / 通义万相 / SiliconFlow / OpenAI ...）
                 注意：国内厂商按法规会在成图上加「AI生成」水印，正式投放前需让用户确认。
  mock           本地程序化生成占位底图（渐变 + 几何图形），零依赖、离线可用，
                 用于测试与「演示现场断网」兜底。

统一接口：backend.generate(prompt, ratio) -> 本地图片绝对路径
ratio 取值：square / portrait / landscape
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import time
import uuid
from pathlib import Path
from typing import Optional, Tuple

import requests

from .llm import load_env_file

# --------------------------------------------------------------------------- 尺寸表

RATIO_SIZES = {
    "codex": {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"},
    "openai_compat": {"square": "1024x1024", "portrait": "768x1344", "landscape": "1440x720"},
    "mock": {"square": "1024x1024", "portrait": "1024x1536", "landscape": "1536x1024"},
}


class ImageBackendError(RuntimeError):
    pass


class BaseBackend:
    name = "base"

    def __init__(self, out_dir: Path, quality: str = "medium"):
        self.out_dir = Path(out_dir)
        self.out_dir.mkdir(parents=True, exist_ok=True)
        self.quality = quality

    def generate(self, prompt: str, ratio: str = "portrait", tag: str = "img") -> str:
        raise NotImplementedError

    # 工具：保存字节
    def _save(self, raw: bytes, tag: str) -> str:
        digest = hashlib.sha1(raw).hexdigest()[:8]
        # 按文件头判断真实格式：Seedream 4.0 固定输出 jpeg，其余多为 png
        ext = ".jpg" if raw[:3] == b"\xff\xd8\xff" else ".png"
        path = self.out_dir / f"{int(time.time())}_{tag}_{digest}{ext}"
        path.write_bytes(raw)
        return str(path.resolve())


# --------------------------------------------------------------------------- Codex

class CodexOAuthBackend(BaseBackend):
    """gpt-image-2 via ChatGPT/Codex OAuth —— 直接用订阅额度，不需要 OPENAI_API_KEY。"""

    name = "codex"
    DEFAULT_BASE = "https://chatgpt.com/backend-api/codex"

    def __init__(self, out_dir: Path, quality: str = "medium",
                 auth_path: Optional[str] = None, base_url: Optional[str] = None,
                 model: Optional[str] = None):
        super().__init__(out_dir, quality)
        env = load_env_file()
        self.auth_path = auth_path or env.get(
            "CREATIVE_CODEX_AUTH", str(Path.home() / ".codex" / "auth.json"))
        self.base_url = (base_url or os.environ.get("CREATIVE_CODEX_BASE_URL")
                         or env.get("CREATIVE_CODEX_BASE_URL") or self.DEFAULT_BASE).rstrip("/")
        self.model = (model or os.environ.get("CREATIVE_IMAGE_MODEL")
                      or env.get("CREATIVE_IMAGE_MODEL") or "gpt-image-2-medium")

    # -- 凭据 --------------------------------------------------------------
    def _token(self) -> Optional[str]:
        try:
            data = json.loads(Path(self.auth_path).read_text(encoding="utf-8"))
        except Exception:
            return None
        tok = (data.get("tokens") or {}).get("access_token")
        return tok.strip() if isinstance(tok, str) and tok.strip() else None

    @staticmethod
    def _account_headers(token: str) -> dict:
        """Codex 后端从 OAuth JWT 派生工作区头，缺失会 401。"""
        import base64 as _b64
        out = {}
        try:
            payload = token.split(".")[1]
            payload += "=" * (-len(payload) % 4)
            auth = json.loads(_b64.urlsafe_b64decode(payload)).get("https://api.openai.com/auth", {})
            if auth.get("chatgpt_account_id"):
                out["ChatGPT-Account-ID"] = auth["chatgpt_account_id"]
            residency = auth.get("chatgpt_data_residency") or auth.get("chatgpt_compute_residency")
            if residency:
                out["x-openai-internal-codex-residency"] = residency
        except Exception:
            pass
        return out

    @property
    def available(self) -> bool:
        return bool(self._token())

    # -- 生成 --------------------------------------------------------------
    def generate(self, prompt: str, ratio: str = "portrait", tag: str = "img") -> str:
        token = self._token()
        if not token:
            raise ImageBackendError(
                f"未找到 Codex 凭据（{self.auth_path}）。请先 `hermes auth add openai-codex`。")
        size = RATIO_SIZES["codex"].get(ratio, "1024x1536")
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "User-Agent": "AIMarketingEmployee/0.1 (Creative Agent)",
            "originator": "hermes-agent",
            "x-codex-image-turn-id": str(uuid.uuid4()),
        }
        headers.update(self._account_headers(token))
        body = {"prompt": prompt, "model": self.model, "n": 1,
                "quality": self.quality, "size": size, "background": "opaque"}
        resp = requests.post(f"{self.base_url}/images/generations",
                             headers=headers, json=body, timeout=300)
        if resp.status_code >= 400:
            raise ImageBackendError(f"Codex 图片接口 HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        data = payload.get("data") or []
        b64 = data[0].get("b64_json") if data and isinstance(data[0], dict) else None
        if b64:
            return self._save(base64.b64decode(b64), tag)
        url = data[0].get("url") if data else None
        if url:
            return self._save(requests.get(url, timeout=180).content, tag)
        raise ImageBackendError("Codex 图片接口返回体里没有图片数据")


# --------------------------------------------------------------------------- OpenAI 兼容

class OpenAICompatBackend(BaseBackend):
    """任意 OpenAI 兼容图片接口。默认智谱 CogView-4（国内可直连、便宜）。"""

    name = "openai_compat"
    DEFAULT_BASE = "https://open.bigmodel.cn/api/paas/v4"

    def __init__(self, out_dir: Path, quality: str = "medium"):
        super().__init__(out_dir, quality)
        env = load_env_file()
        self.base_url = (os.environ.get("CREATIVE_IMAGE_BASE_URL")
                         or env.get("CREATIVE_IMAGE_BASE_URL") or self.DEFAULT_BASE).rstrip("/")
        self.api_key = (os.environ.get("CREATIVE_IMAGE_API_KEY")
                        or env.get("CREATIVE_IMAGE_API_KEY") or env.get("GLM_API_KEY") or "")
        self.model = (os.environ.get("CREATIVE_IMAGE_MODEL")
                      or env.get("CREATIVE_IMAGE_MODEL") or "cogview-4")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, ratio: str = "portrait", tag: str = "img") -> str:
        if not self.api_key:
            raise ImageBackendError("未配置 CREATIVE_IMAGE_API_KEY / GLM_API_KEY")
        size = RATIO_SIZES["openai_compat"].get(ratio, "768x1344")
        body = {"model": self.model, "prompt": prompt, "size": size}
        if "volces.com" in self.base_url:      # 火山方舟 Seedream：watermark 为布尔开关
            body["watermark"] = False
        else:                                  # 智谱等 OpenAI 兼容服务：watermark_enabled
            body["watermark_enabled"] = False
        resp = requests.post(f"{self.base_url}/images/generations",
                             headers={"Authorization": f"Bearer {self.api_key}",
                                      "Content-Type": "application/json"},
                             json=body, timeout=300)
        if resp.status_code >= 400:
            raise ImageBackendError(f"图片接口 HTTP {resp.status_code}: {resp.text[:300]}")
        payload = resp.json()
        data = payload.get("data") or []
        if data:
            if isinstance(data[0], dict) and data[0].get("url"):
                return self._save(requests.get(data[0]["url"], timeout=180).content, tag)
            if isinstance(data[0], dict) and data[0].get("b64_json"):
                return self._save(base64.b64decode(data[0]["b64_json"]), tag)
        raise ImageBackendError("图片接口返回体里没有图片数据")


# --------------------------------------------------------------------------- xAI / Grok

class XAIBackend(BaseBackend):
    """Grok Imagine（xAI）—— 用 XAI_API_KEY，按张计费（约 $0.01-0.02/张）。

    模型可选：
      grok-imagine-image-2.0  最强、对排版/小字更友好（推荐用于带文字的场景）
      grok-imagine-image      默认款，出图快（~5-10s）
      grok-imagine-image-quality  细节更好，速度较慢
    注意：本项目默认不在图里生成文字（文字由 composer 排版），所以文字能力只算加分项。
    """

    name = "xai"
    DEFAULT_BASE = "https://api.x.ai/v1"
    DEFAULT_MODEL = "grok-imagine-image-2.0"

    ASPECT = {"square": "1:1", "portrait": "2:3", "portrait_tall": "9:16", "landscape": "16:9"}

    def __init__(self, out_dir: Path, quality: str = "medium"):
        super().__init__(out_dir, quality)
        env = load_env_file()
        self.base_url = (os.environ.get("CREATIVE_XAI_BASE_URL")
                         or env.get("CREATIVE_XAI_BASE_URL") or self.DEFAULT_BASE).rstrip("/")
        self.api_key = (os.environ.get("XAI_API_KEY") or env.get("XAI_API_KEY")
                        or env.get("CREATIVE_IMAGE_API_KEY") or "")
        self.model = (os.environ.get("CREATIVE_XAI_IMAGE_MODEL")
                      or env.get("CREATIVE_XAI_IMAGE_MODEL")
                      or os.environ.get("XAI_IMAGE_MODEL") or env.get("XAI_IMAGE_MODEL")
                      or self.DEFAULT_MODEL)
        self.resolution = (os.environ.get("CREATIVE_XAI_RESOLUTION")
                           or env.get("CREATIVE_XAI_RESOLUTION") or "1k")

    @property
    def available(self) -> bool:
        return bool(self.api_key)

    def generate(self, prompt: str, ratio: str = "portrait", tag: str = "img") -> str:
        if not self.api_key:
            raise ImageBackendError("未配置 XAI_API_KEY")
        body = {"model": self.model, "prompt": prompt,
                "aspect_ratio": self.ASPECT.get(ratio, "2:3"),
                "resolution": self.resolution}
        resp = requests.post(f"{self.base_url}/images/generations",
                             headers={"Authorization": f"Bearer {self.api_key}",
                                      "Content-Type": "application/json",
                                      "User-Agent": "AIMarketingEmployee/0.1 (Creative Agent)"},
                             json=body, timeout=180)
        if resp.status_code >= 400:
            raise ImageBackendError(f"xAI 图片接口 HTTP {resp.status_code}: {resp.text[:300]}")
        data = (resp.json() or {}).get("data") or []
        if not data or not isinstance(data[0], dict):
            raise ImageBackendError("xAI 图片接口返回体里没有图片数据")
        if data[0].get("b64_json"):
            return self._save(base64.b64decode(data[0]["b64_json"]), tag)
        if data[0].get("url"):
            return self._save(requests.get(data[0]["url"], timeout=180).content, tag)
        raise ImageBackendError("xAI 图片接口返回体里没有图片数据")


# --------------------------------------------------------------------------- Mock

class MockBackend(BaseBackend):
    """程序化占位底图：不联网也能产出可交付的排版素材。"""

    name = "mock"
    _THEMES = {
        "warm": [(247, 226, 200), (226, 168, 120), (150, 90, 60)],
        "rose": [(252, 226, 232), (238, 150, 175), (160, 60, 95)],
        "cool": [(224, 240, 236), (140, 195, 190), (60, 120, 125)],
        "dark": [(60, 62, 74), (120, 110, 100), (30, 30, 40)],
    }

    @property
    def available(self) -> bool:
        return True

    def generate(self, prompt: str, ratio: str = "portrait", tag: str = "img") -> str:
        from PIL import Image, ImageDraw, ImageFilter

        w, h = (1024, 1024) if ratio == "square" else (
            (1024, 1536) if ratio == "portrait" else (1536, 1024))
        key = hashlib.md5((prompt or tag).encode("utf-8")).hexdigest()
        idx = int(key[:2], 16) % 4
        c0, c1, c2 = self._THEMES[list(self._THEMES)[idx]]
        img = Image.new("RGB", (w, h), c0)
        draw = ImageDraw.Draw(img, "RGBA")
        # 垂直渐变
        for y in range(h):
            t = y / max(1, h - 1)
            col = tuple(int(c0[i] * (1 - t) + c1[i] * t) for i in range(3))
            draw.line([(0, y), (w, y)], fill=col)
        # 若干柔和圆形，模拟手作/咖啡馆的散点构图
        blob = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        bd = ImageDraw.Draw(blob, "RGBA")
        for i in range(14):
            cx = (int(key[i * 2:i * 2 + 2], 16) / 255) * w
            cy = (int(key[i * 2 + 2:i * 2 + 4] or "80", 16) / 255) * h
            r = int(min(w, h) * (0.05 + (i % 5) * 0.03))
            bd.ellipse([cx - r, cy - r, cx + r, cy + r],
                       fill=(c2[0], c2[1], c2[2], 40 + (i % 6) * 18))
        blob = blob.filter(ImageFilter.GaussianBlur(min(w, h) // 26))
        img = Image.alpha_composite(img.convert("RGBA"), blob).convert("RGB")
        buf = self.out_dir / f"mock_{tag}.png"
        img.save(buf, quality=92)
        return str(buf.resolve())


# --------------------------------------------------------------------------- 工厂

def get_backend(name: Optional[str] = None, out_dir: Path = Path("output/_cache"),
                quality: str = "medium") -> BaseBackend:
    """解析要用的后端；explicit name > 环境变量 > 自动探测（xai > codex > openai_compat > mock）。"""
    env = load_env_file()
    name = (name or os.environ.get("CREATIVE_IMAGE_BACKEND")
            or env.get("CREATIVE_IMAGE_BACKEND") or "auto").lower()

    if name in ("auto", ""):
        for candidate in (XAIBackend(out_dir, quality),
                          CodexOAuthBackend(out_dir, quality),
                          OpenAICompatBackend(out_dir, quality)):
            if candidate.available:
                return candidate
        return MockBackend(out_dir, quality)

    table = {"codex": CodexOAuthBackend, "xai": XAIBackend,
             "openai_compat": OpenAICompatBackend, "mock": MockBackend}
    if name not in table:
        raise ValueError(f"未知图像后端：{name}（可选 {', '.join(table)}）")
    return table[name](out_dir, quality)


def describe_backends(out_dir: Path = Path("output/_cache")) -> Tuple[str, list]:
    """给 CLI/演示页看的后端可用性清单。"""
    rows = []
    for cls in (XAIBackend, CodexOAuthBackend, OpenAICompatBackend, MockBackend):
        b = cls(out_dir)
        rows.append({"name": b.name, "available": bool(b.available),
                     "model": getattr(b, "model", "-")})
    return "ok", rows
