"""
海报渲染 Agent —— 物料环节（替代 AI 生图）
==========================================

职责：把 strategy / customer_info / analysis 里的结构化信息，编排成一张
      可直接投放的宣传海报（PNG），图片本身由前端代码（SVG/CSS）绘制，
      不依赖任何文生图模型。

读取：customer_info / analysis / strategy
写入：images（只写这一个字段，别碰别人的字段）

工作方式（三段式）：
    1. build_spec(state)   → 视觉规格 spec：品类母题 / 配色 / 主标语 / 三条卖点 /
                             门店信息 / 行动指引 / seed（同一家店结果稳定可复现）
    2. render_png(spec)    → 用 headless 浏览器加载 web/poster.html?spec=...，
                             截屏成 outputs/poster_<hash>.png（2x = 2048x3072）
    3. run(state)          → 返回 {"images": {"poster": ..., "copywriting": ...,
                                              "extra": {"spec": ..., "render": ...}}}

降级策略：找不到浏览器 / 渲染失败时 poster 置空字符串（前端会显示"素材待接入"
占位），流程不中断；spec 仍随结果返回，前端可以实时渲染预览。

自测：
    python -m app.agents.poster_agent          # 打印三品类 spec
    python -m app.agents.poster_agent render   # 顺带渲染成 PNG
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from app.state import Creative

FIELD = "images"

ROOT = Path(__file__).resolve().parents[2]
POSTER_PAGE = ROOT / "web" / "poster.html"
OUT_DIR = ROOT / "outputs"

# 渲染模板版本：改 web/poster.* 后 +1，旧缓存自动失效
RENDER_VERSION = 9

# 画布尺寸（--force-device-scale-factor=2 截图 → 实际输出 2048x3072）
CANVAS_W, CANVAS_H = 1024, 1536

# 降级兜底（保护壳在节点异常时顶替；空 poster 让前端显示占位而不是错图）
FALLBACK: dict = {"poster": "", "copywriting": "海报素材暂不可用"}

# ---------------------------------------------------------------- 品类 → 母题 / 配色

CATEGORY_STYLES: dict[str, dict] = {
    "beads": {
        "label": "手工体验", "tag": "到店做成品",
        "motif": "beads",
        "shape": "heart",
        "palette": {
            "bg": "#101A3C", "surface": "#16224A", "ink": "#F2F4F8",
            "dim": "#98A5CD", "accent": "#F0A322",
            "beads": ["#E7A9C0", "#C6B2E8", "#F3D9A6", "#A9D8CC", "#F6EFE6", "#E9C0D8"],
        },
    },
    "coffee": {
        "label": "手冲咖啡", "tag": "到店喝一杯",
        "motif": "latte",
        "palette": {
            "bg": "#1A100A", "surface": "#241608", "ink": "#F7EEDF",
            "dim": "#C8A987", "accent": "#C98A4B",
        },
    },
    "bakery": {
        "label": "手工烘焙", "tag": "每天现烤",
        "motif": "wheat",
        "palette": {
            "bg": "#20150B", "surface": "#2A1C0F", "ink": "#FBF0DC",
            "dim": "#D6B48A", "accent": "#E0A94F",
        },
    },
    "flowers": {
        "label": "花艺", "tag": "到店带走",
        "motif": "petals",
        "palette": {
            "bg": "#141022", "surface": "#1C1730", "ink": "#F6EFF8",
            "dim": "#C6B4D8", "accent": "#E08FB0",
        },
    },
    "cake": {
        "label": "甜品蛋糕", "tag": "现做现取",
        "motif": "swirl",
        "palette": {
            "bg": "#1C1017", "surface": "#26151D", "ink": "#FBEDF2",
            "dim": "#D3A9B8", "accent": "#E77FA0",
        },
    },
    "generic": {
        "label": "本地小店",
        "motif": "grid",
        "palette": {
            "bg": "#101A3C", "surface": "#16224A", "ink": "#F2F4F8",
            "dim": "#98A5CD", "accent": "#2E6FD8",
        },
    },
}

# 关键词 → 品类（按顺序命中，越靠前越优先）
CATEGORY_RULES: list[tuple[str, tuple[str, ...]]] = [
    ("coffee", ("咖啡", "coffee", "拿铁", "美式", "手冲", "espresso", "饮品", "茶饮", "奶茶")),
    ("bakery", ("面包", "烘焙", "bakery", "吐司", "可颂", "欧包", "baker")),
    ("cake", ("蛋糕", "甜品", "dessert", "cake", "生日", "慕斯")),
    ("flowers", ("花店", "花艺", "flowers", "鲜花", "花束")),
    ("beads", ("拼豆", "handmade", "diy", "手作", "手工", "串珠", "钥匙扣", "冰箱贴")),
]

# ---------------------------------------------------------------- 文本工具


def _sp(text: str) -> str:
    """中英数混排：数字与汉字之间补半角空格（221色 → 221 色，24小时 → 24 小时）。"""
    text = re.sub(r"(\d)\s*([\u4e00-\u9fa5])", r"\1 \2", str(text or ""))
    text = re.sub(r"([\u4e00-\u9fa5])\s*(\d)", r"\1 \2", text)
    return re.sub(r"\s{2,}", " ", text).strip()


def _pick_line(text: str, label: str) -> str:
    """从抖音简介式的 highlights 里抠出「地址：…」「营业时间：…」这类行。"""
    match = re.search(label + r"[^：:]*[：:]\s*([^\n]+)", str(text or ""))
    if not match:
        return ""
    cleaned = re.sub(r"[\u2700-\u27BF\uD83C-\uDBFF\uDC00-\uDFFF\uFE0F\u200d]", " ", match.group(1))
    return _sp(cleaned)


def _split_theme(theme: str) -> tuple[str, str]:
    """主题 → (主标语, 副标语)，按第一个冒号切。"""
    text = str(theme or "").strip()
    for sep in ("：", ":"):
        if sep in text:
            head, sub = text.split(sep, 1)
            return head.strip(), sub.strip()
    return text, ""


def _find_desc(key: str, copy: str) -> str:
    """在成品文案里找与卖点同句的说明，用于补齐卖点细节。"""
    probe = re.sub(r"\s", "", key)[:5]
    if not probe:
        return ""
    for sentence in re.split(r"[\n。]", str(copy or "")):
        flat = re.sub(r"\s", "", sentence)
        at = flat.find(probe)
        if at >= 0:
            comma = flat.find("，", at)
            tail = flat[comma + 1:] if comma >= 0 else ""
            return tail if 4 < len(tail) < 48 else ""
    return ""


_DROP_TAIL = re.compile(r"[，,]?(就在|位于)[^，。]*[。.]?$")
_DROP_LEAD = re.compile(r"^(能做成|可以做成|做成|加上|还有|以及|并且)")
_TRIM_PUNCT = re.compile(r"[。.；;，,、\s]+$")
_PARENS = re.compile(r"[（(][^）)]{0,80}[）)]")


def _strip_parens(text: str) -> str:
    """去掉括号补充说明——海报上放的是勾子，不是脚注。"""
    return re.sub(r"\s{2,}", " ", _PARENS.sub("", str(text or ""))).strip()


def _clean_headline(text: str, limit: int = 18) -> str:
    """主标语必须短：去括号、去副标题、只留首个短句，超长按标点截断。"""
    text = _strip_parens(text)
    text = re.split(r"[|｜/]", text)[0].strip()
    text = re.split(r"[。！!？?]", text)[0].strip()
    text = _TRIM_PUNCT.sub("", text)
    if len(text) <= limit:
        return text
    for sep in ("，", "、", "·", " ", "—"):
        head = text.split(sep)[0].strip()
        if 6 <= len(head) <= limit:
            return head
    return text[:limit].rstrip() + "…"



def _split_selling(key_message: str, copy: str) -> list[dict[str, str]]:
    """卖点：key_message 按「；」切分，每条按第一个「，」拆成 标题 / 说明。"""
    parts = [p.strip() for p in re.split(r"[；;]", str(key_message or "")) if p.strip()]
    if parts and re.search(r"[：:]", parts[0]):
        parts[0] = re.split(r"[：:]", parts[0], maxsplit=1)[1].strip()

    points: list[dict[str, str]] = []
    for raw in parts[:3]:
        body = _TRIM_PUNCT.sub("", _DROP_TAIL.sub("", raw))
        if "，" in body:
            key, value = body.split("，", 1)
            value = _TRIM_PUNCT.sub("", _DROP_LEAD.sub("", value).strip())
        else:
            key, value = body, ""
        key = _sp(key)
        value = _sp(value) or _sp(_find_desc(key, copy))
        points.append({"k": _TRIM_PUNCT.sub("", key), "v": _TRIM_PUNCT.sub("", value)})
    return points


def _detect_category(state: dict) -> str:
    """品类识别：客户类型 / 店名 / 特点 / 关键词 / 主题，谁先命中算谁。"""
    customer = state.get("customer_info") or {}
    analysis = state.get("analysis") or {}
    strategy = state.get("strategy") or {}
    haystack = " ".join([
        str(customer.get("type") or ""),
        str(customer.get("shop") or ""),
        str(customer.get("highlights") or ""),
        " ".join(str(k) for k in (strategy.get("keywords") or [])),
        str(strategy.get("theme") or ""),
        str(analysis.get("user") or ""),
    ]).lower()
    for category, words in CATEGORY_RULES:
        if any(word.lower() in haystack for word in words):
            return category
    return "generic"


def _seed_of(*parts: str) -> int:
    """同一家店 + 同一个主题 → 同一个 seed（图案与配色可复现）。"""
    digest = hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()
    return int(digest[:8], 16)


# ---------------------------------------------------------------- 内容线索（画什么，由这家店自己决定）

#: 数据里命中哪个词，海报上半部分就多画一件对应的东西
CUE_PATTERNS: dict[str, str] = {
    "cat": r"猫|喵|撸猫",
    "dog": r"狗|柴犬|柯基|宠物犬",
    "oldhouse": r"老洋房|洋房|老房子|老宅|民国|壁炉|木地板|红砖|保护建筑|老建筑",
    "watercolor": r"水彩|画室|画廊|手绘|画画",
    "coffee": r"咖啡|拿铁|手冲|美式|拉花|冷萃|咖啡豆",
    "tea": r"茶室|点茶|奶茶|茶饮",
    "bead": r"拼豆|豆片|熨烫|拼贴|拼图",
    "bread": r"面包|欧包|吐司|可颂|贝果|烘焙",
    "flower": r"花店|鲜花|干花|花艺",
    "plant": r"绿植|植物|多肉|庭院",
    "window": r"窗|天窗|落地窗",
    "night": r"夜|宵夜|深夜|24\s*小时",
    "street": r"巷|街|里弄|弄堂|街区|路口",
    "lamp": r"台灯|暖光|灯",
    "music": r"音乐|黑胶|唱片|演出|live",
    "book": r"书店|阅读|书房",
}


def _visual_cues(*texts: str) -> list[str]:
    """从真实字段里挑出"这家店有的东西"，作为画面元素（没命中就不画）。"""
    blob = " ".join(t for t in texts if t)
    return [name for name, pattern in CUE_PATTERNS.items() if re.search(pattern, blob)]


def _badges(highlights: str) -> list[dict[str, str]]:
    """真实数字做成图里的贴纸：人均 / 评分 / 评价数 / 营业时间（有才画）。"""
    badges: list[dict[str, str]] = []
    match = re.search(r"人均[^0-9]{0,4}(\d+)\s*元", highlights) or re.search(r"人均\s*(\d+)", highlights)
    if match:
        badges.append({"k": "人均", "v": f"{match.group(1)} 元"})
    match = re.search(r"评?分\s*([45]\.\d)", highlights)
    if match:
        badges.append({"k": "评分", "v": match.group(1)})
    match = re.search(r"(\d+)\s*条(?:评价|评论)", highlights)
    if match:
        badges.append({"k": "评价", "v": f"{match.group(1)} 条"})
    match = re.search(r"(\d{1,2}:\d{2})\s*[-–—~至]\s*(\d{1,2}:\d{2})", highlights)
    if match:
        badges.append({"k": "营业", "v": f"{match.group(1)}–{match.group(2)}"})
    return badges[:3]


def _numero(highlights: str) -> str:
    """门牌号：做成背景里的幽灵数字（一眼就知道是哪家店）。"""
    match = re.search(r"(\d{1,4})\s*号", highlights)
    return match.group(1) if match else ""


def _glyph(shop: str) -> str:
    """店名首字：拼豆店会把它拼成珠子画。"""
    for ch in shop or "":
        if "\u4e00" <= ch <= "\u9fff":
            return ch
    return (shop or "店")[:1]


def _pick_layout(seed: int, category: str, cues: list[str]) -> str:
    """版式也随店变：同一品类不同店，画面结构不同。"""
    if category == "beads" or "bead" in cues:
        return "bead"
    if "oldhouse" in cues:
        # 老洋房/老宅：拱窗内景最能说明"这是什么地方"
        return ["arch", "arch", "street"][seed % 3]
    if "window" in cues:
        return "arch" if seed % 2 == 0 else "still"
    if "coffee" in cues or "tea" in cues:
        options = ["still", "arch", "street"]
    elif "bread" in cues:
        options = ["still", "street"]
    else:
        options = ["street", "arch", "still"]
    return options[seed % len(options)]


# ---------------------------------------------------------------- 规格编排


def build_spec(state: dict) -> dict:
    """把 state 编排成海报视觉规格（纯函数，不渲染、不写盘）。"""
    customer = state.get("customer_info") or {}
    strategy = state.get("strategy") or {}
    extra = strategy.get("extra") or {}

    category = _detect_category(state)
    style = CATEGORY_STYLES[category]
    palette = dict(style["palette"])

    headline, subline = _split_theme(strategy.get("theme") or "")
    headline = _clean_headline(headline)
    subline = _strip_parens(subline)
    points = _split_selling(extra.get("key_message") or "", strategy.get("copywriting") or "")
    for point in points:
        point["v"] = _strip_parens(point["v"])

    highlights = str(customer.get("highlights") or "")
    copy = str(strategy.get("copywriting") or "")

    # 角标：平台 / 24 小时 / 免费打孔（都来自真实字段，命中才显示）
    channels = strategy.get("channels") or []
    platform = re.split(r"[（(]", str(channels[0] if channels else customer.get("platform") or "抖音"))[0].strip()
    chips = [platform] if platform else []
    if re.search(r"24\s*小时", highlights):
        chips.append("24 小时")
    if "免费打孔" in highlights + copy:
        chips.append("免费打孔")

    address = _pick_line(highlights, "地址")
    hours = re.sub(r"[（(][^）)]*[）)]$", "", _pick_line(highlights, "营业时间"))
    shop = str(customer.get("shop") or "")
    hook = "看五种烫法对比" if "五种烫法" in str(strategy.get("theme") or "") else "看成品与到店指引"

    seed = _seed_of(shop, str(strategy.get("theme") or ""))
    cues = _visual_cues(shop, highlights, copy, address, str(extra.get("key_message") or ""),
                        str(customer.get("potential_need") or ""))
    layout = _pick_layout(seed, category, cues)

    spec = {
        "category": category,
        "motif": style["motif"],
        "shape": style.get("shape", ""),
        "label": style["label"],
        "seed": seed,
        # 内容驱动：画什么由这家店真实查出的事实决定（cues / badges / numero / glyph）
        "scene": True,
        "layout": layout,
        "cues": cues,
        "badges": _badges(highlights),
        "numero": _numero(highlights),
        "glyph": _glyph(shop),
        "palette": palette,
        "eyebrow": f'{style["label"]} · {style.get("tag", "到店体验")}',
        "headline": headline or "这一次，来店里做点自己的东西",
        "subline": _sp(subline),
        "points": points,
        "chips": chips[:3],
        "keywords": [str(k) for k in (strategy.get("keywords") or [])][:4],
        "shop": shop,
        "meta": " · ".join(x for x in (address, hours) if x),
        "cta": f'{platform}搜索「{shop}」· {hook}' if shop else "到店体验",
        "fine": "具体价格、耗材与可选时段请以店内或私信确认为准。",
        "copywriting": headline or FALLBACK["copywriting"],
        "size": f"{CANVAS_W}x{CANVAS_H}",
    }
    return spec


# ---------------------------------------------------------------- 渲染（无头浏览器截图）

BROWSER_CANDIDATES = (
    os.environ.get("POSTER_BROWSER", ""),
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Google\Chrome\Application\chrome.exe",
    r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
)


def find_browser() -> str | None:
    """找可用的 Chromium 内核浏览器（Edge / Chrome / Chromium）。"""
    for candidate in BROWSER_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    for name in ("msedge", "chrome", "chromium", "chromium-browser", "google-chrome"):
        found = shutil.which(name)
        if found:
            return found
    return None


def spec_key(spec: dict) -> str:
    """spec 指纹：同样的输入不必重复渲染（改模板时提升 RENDER_VERSION 让旧图失效）。"""
    payload = json.dumps({"v": RENDER_VERSION, "spec": spec}, ensure_ascii=False, sort_keys=True)
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:12]


def _spec_url(spec: dict) -> str:
    payload = base64.urlsafe_b64encode(
        json.dumps(spec, ensure_ascii=False).encode("utf-8")
    ).decode("ascii")
    return f"{POSTER_PAGE.resolve().as_uri()}?spec={payload}&static=1"


def render_png(spec: dict, force: bool = False) -> tuple[str, dict]:
    """把 spec 渲染成 PNG，返回 (相对路径, 渲染信息)。失败时路径为空字符串。"""
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    key = spec_key(spec)
    target = OUT_DIR / f"poster_{key}.png"
    relative = f"outputs/{target.name}"

    if target.exists() and target.stat().st_size > 4096 and not force:
        return relative, {"status": "cached", "path": relative, "spec_id": key}

    browser = find_browser()
    if not browser:
        return "", {"status": "failed", "reason": "未找到 Chromium 内核浏览器（Edge/Chrome）"}

    started = time.time()
    profile = Path(tempfile.mkdtemp(prefix="poster-profile-"))
    argv = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",              # Linux 以 root 运行时必需的沙箱参数
        "--disable-dev-shm-usage",   # 低内存 / 容器环境防崩溃
        "--hide-scrollbars",
        "--no-first-run",
        "--no-default-browser-check",
        "--mute-audio",
        "--force-device-scale-factor=2",
        f"--window-size={CANVAS_W},{CANVAS_H}",
        "--virtual-time-budget=5000",
        f"--user-data-dir={profile}",
        f"--screenshot={target}",
        _spec_url(spec),
    ]
    try:
        subprocess.run(
            argv,
            check=False,
            timeout=60,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as exc:  # 浏览器崩溃 / 超时 / 被沙箱拦截
        return "", {"status": "failed", "reason": f"{type(exc).__name__}: {exc}"[:160]}
    finally:
        shutil.rmtree(profile, ignore_errors=True)

    if not target.exists() or target.stat().st_size < 4096:
        return "", {"status": "failed", "reason": "截图未生成（浏览器可能被当前环境拦截）"}

    elapsed = round(time.time() - started, 2)
    return relative, {"status": "rendered", "elapsed": elapsed, "path": relative, "spec_id": key}


# ---------------------------------------------------------------- 节点入口


def _usable_shop(state: dict) -> bool:
    """没有可核查商家时不出图——宁可留空态，也不印一张「未发现可核查商家」的海报。"""
    info = state.get("customer_info") or {}
    if (info.get("extra") or {}).get("no_match"):
        return False
    shop = str(info.get("shop") or "")
    return bool(shop) and "未发现" not in shop and "未确定" not in shop


def run(state: dict) -> dict:
    """节点入口：state → 海报 PNG + 配文（只写 images 字段）。"""
    if not _usable_shop(state):
        raise ValueError("缺少可核查商家信息，本轮跳过海报生成")

    spec = build_spec(state)
    poster, render_info = render_png(spec)
    if not poster:
        # 渲染失败也走降级（保护壳兜底 + Manager 规则质检会看到空 poster）
        raise RuntimeError(f'海报渲染失败：{render_info.get("reason") or "未知原因"}')

    data = {
        "poster": poster,
        "copywriting": spec["copywriting"],
        "spec_id": render_info.get("spec_id") or spec_key(spec),
        "size": spec.get("size", ""),
        "motif": "-".join(x for x in (spec.get("motif"), spec.get("shape")) if x),
        "extra": {"spec": spec, "render": render_info},
    }
    Creative.model_validate(data)  # 自检

    # 把渲染细节写进本节点的 trace 事件（前端执行流据此显示 BRAND / SIZE）
    detail = {
        "motif": data["motif"],
        "size": data["size"],
        "spec_id": data["spec_id"],
        "render": render_info.get("status"),
        "render_elapsed": render_info.get("elapsed"),
        "headline": spec.get("headline", ""),
    }
    return {FIELD: data, "_trace_detail": detail}


# ---------------------------------------------------------------- 自测

_DEMO_STATES = {
    "beads": {
        "customer_info": {
            "shop": "有家拼豆店手工DIY学府店",
            "type": "handmade",
            "platform": "抖音",
            "highlights": "地址📍：学府广场一楼、鸡柳大人后门\n营业时间⏰：24小时（智能拼豆板）\n本店还提供免费打孔服务",
        },
        "strategy": {
            "theme": "一张图，五种烫法：在大学城24小时拼豆，做一枚只属于你的周边",
            "channels": ["抖音（主阵地）"],
            "keywords": ["拼豆DIY", "特殊烫法对比"],
            "copywriting": "店里的智能拼豆板24小时可用，下课晚了也能来坐一会儿，慢慢拼。",
            "extra": {
                "key_message": "这不是普通的拼豆店：221色融合豆，加上澡巾烫、格利特烫等多种特殊烫法；烫好免费打孔，能做成钥匙扣、冰箱贴、明星周边、3D摆件；智能拼豆板24小时可用，就在学府广场一楼。"
            },
        },
    },
    "coffee": {
        "customer_info": {
            "shop": "写在溪边的咖啡馆",
            "type": "coffee",
            "platform": "小红书",
            "highlights": "地址📍：大学城西门外沿河路 12 号\n营业时间⏰：08:00-22:00\n主推手冲与冷萃",
        },
        "strategy": {
            "theme": "一杯手冲的时间：把下午三点还给自己",
            "channels": ["小红书（主阵地）"],
            "keywords": ["手冲咖啡", "自习", "安静"],
            "copywriting": "午后最安静的一小时，从一杯手冲开始。",
            "extra": {
                "key_message": "手冲豆单每周更新：埃塞日晒、云南小粒、哥伦比亚水洗；吧台可以看见完整的冲煮过程；靠窗有插座和长桌，适合坐一下午。"
            },
        },
    },
    "bakery": {
        "customer_info": {
            "shop": "早起面包房",
            "type": "bakery",
            "platform": "抖音",
            "highlights": "地址📍：学府路 8 号\n营业时间⏰：07:00-19:00\n每天凌晨现烤",
        },
        "strategy": {
            "theme": "凌晨四点开烤：一天只做两炉的欧包",
            "channels": ["抖音"],
            "keywords": ["现烤面包", "欧包", "手工烘焙"],
            "copywriting": "凌晨四点开始揉面，第一炉出炉时天刚亮。",
            "extra": {
                "key_message": "每天只做两炉：第一炉 07:30 出炉，第二炉 15:30 出炉；主推原味欧包、可颂与全麦吐司；面团低温发酵 18 小时，只在店里卖。"
            },
        },
    },
}


def _main(argv: list[str]) -> None:
    do_render = len(argv) > 1 and argv[1] == "render"
    for name, demo in _DEMO_STATES.items():
        spec = build_spec(demo)
        print(f"\n=== {name} · {spec['motif']} · seed={spec['seed']} ===")
        print(json.dumps(spec, ensure_ascii=False, indent=1))
        if do_render:
            path, info = render_png(spec)
            print("[render]", path or "(失败)", info)


if __name__ == "__main__":
    _main(sys.argv)
