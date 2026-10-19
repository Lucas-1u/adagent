"""设计版式引擎 v2 —— 按真实小红书 / 海报设计规范重写排版层。

v1 的问题（对照设计规范逐条自查出来的）：
  · 文字元素太多：主标题 + 副标题 + 3 个胶囊卖点 + CTA + 通栏白信息条 = 页面上 6 个元素，
    违反「装饰/标签元素 ≤2」「切忌把整段文案搬上封面」；
  · 只有一种套路：照片 + 横向暗带 + 胶囊标签，等价于把小网页压在照片上，
    没有留白（规范要求 ≥30%）、没有网格、没有字重对比；
  · 横向暗带边界可见，像贴片；白底通栏信息条是最典型的「廉价感」来源；
  · 字体只有系统黑体/宋体，几乎没有字重对比。

v2 的做法：
  1. 文案做减法：一张图上最多 4 个文字元素，留白 ≥30%；
  2. 四种经典版式（居中对称 / 上下分区 / 大图压字 / 左右分割），逐个对应渠道；
  3. 遮罩从「横向暗带」改为「文字区局部羽化虚化 + 轻度压暗」，边界不可见；
  4. 配色从底图自动提取主色，自动生成「主色 + 中性色 + 点缀色」的协调三色；
  5. 字体换成可调字重的开源字体（思源黑体/思源宋体可变字体 + 得意黑），字重对比做足。

字体文件不随仓库分发（太大），由 scripts/fetch_fonts.py 下载；
下载失败会自动回落到系统字体，仓库在任何机器上都仍能渲染。
"""
from __future__ import annotations

import colorsys
import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

ASSETS = Path(__file__).resolve().parent.parent / "assets" / "fonts"

# --------------------------------------------------------------------------- 字体

# family -> 候选文件（按顺序取第一个存在的）
FONT_FILES: Dict[str, List[str]] = {
    "display": [str(ASSETS / "SmileySans-Oblique.ttf")],          # 得意黑，标题专用
    "sans": [str(ASSETS / "NotoSansSC.ttf"),                       # 思源黑体（可变字重）
             "/System/Library/Fonts/Hiragino Sans GB.ttc",
             "/System/Library/Fonts/STHeiti Medium.ttc",
             "C:/Windows/Fonts/msyh.ttc",
             "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"],
    "serif": [str(ASSETS / "NotoSerifSC.ttf"),                     # 思源宋体（可变字重）
              "/System/Library/Fonts/Supplemental/Songti.ttc",
              "C:/Windows/Fonts/simsun.ttc",
              "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc"],
}
# ttc 的 face index（仅系统字体需要）
TTC_INDEX = {
    "/System/Library/Fonts/Hiragino Sans GB.ttc": 1,
    "/System/Library/Fonts/Supplemental/Songti.ttc": 1,
}

WEIGHTS = ("Thin", "ExtraLight", "Light", "Regular", "Medium", "SemiBold",
           "Bold", "ExtraBold", "Black")

_cache: Dict[Tuple[str, str, int], ImageFont.FreeTypeFont] = {}


def _font_path(family: str) -> Optional[str]:
    for p in FONT_FILES.get(family, FONT_FILES["sans"]):
        if os.path.exists(p):
            return p
    return None


def font(family: str = "sans", weight: str = "Regular", size: int = 40):
    """取字体。开源可变字体走字重轴；系统字体只做粗/常规两档映射。"""
    size = max(8, int(size))
    key = (family, weight, size)
    if key in _cache:
        return _cache[key]
    path = _font_path(family)
    if path is None:
        f = ImageFont.load_default()
        _cache[key] = f
        return f
    index = TTC_INDEX.get(path, 0)
    try:
        f = ImageFont.truetype(path, size, index=index)
    except Exception:
        f = ImageFont.load_default()
        _cache[key] = f
        return f
    # 可变字体：按名字设字重
    # 注意：PIL 的 get_variation_names() 返回的是 bytes（b'Black'），直接和 str 比较永远不成立 ——
    # 这个 bug 会让标题一直停在默认字重（细体），是"怎么看都廉价"的隐形元凶。
    if weight in WEIGHTS:
        try:
            names = {n.decode("utf-8", "ignore") if isinstance(n, (bytes, bytearray)) else str(n)
                     for n in f.get_variation_names()}
            if weight in names:
                f.set_variation_by_name(weight)
        except Exception:
            pass
    _cache[key] = f
    return f


# --------------------------------------------------------------------------- 配色

def _hsv(h: float, s: float, v: float) -> Tuple[int, int, int]:
    r, g, b = colorsys.hsv_to_rgb(h % 1.0, max(0.0, min(1.0, s)), max(0.0, min(1.0, v)))
    return int(r * 255), int(g * 255), int(b * 255)


def extract_palette(bg_path: str) -> Dict[str, Tuple[int, int, int]]:
    """从底图里提取主色，合成一套「主色 + 中性色 + 点缀色」的协调配色。

    做的是真实设计师的工作：不硬塞配色方案，而是让画面颜色自成一体。
    """
    try:
        im = Image.open(bg_path).convert("RGB").resize((80, 80), Image.Resampling.BILINEAR)
    except Exception:
        return fallback_palette("治愈奶油")
    # 取饱和度与明度都在中间段的像素，按色相直方图找主色
    bins: Dict[int, float] = {}
    for r, g, b in im.getdata():
        h, s, v = colorsys.rgb_to_hsv(r / 255, g / 255, b / 255)
        if s < 0.12 or v < 0.15 or v > 0.94:
            continue
        bucket = int(h * 24)
        # 权重偏向中高饱和、中亮度的区域（画面里的"彩色主体"）
        bins[bucket] = bins.get(bucket, 0.0) + (0.4 + s) * (1.0 - abs(v - 0.55))
    if not bins:
        return fallback_palette("治愈奶油")
    top = max(bins.items(), key=lambda kv: kv[1])[0]
    hue = (top + 0.5) / 24.0
    cold = hue in (0.33, 0.42, 0.5, 0.58, 0.67) or (0.45 <= hue <= 0.75)
    accent = _hsv(hue, 0.55 if not cold else 0.45, 0.62)
    accent_dark = _hsv(hue, 0.72, 0.42)
    accent_soft = _hsv(hue, 0.22, 0.93)
    return {
        "accent": accent,
        "accent_dark": accent_dark,
        "accent_soft": accent_soft,
        "ink": _hsv(hue, 0.18, 0.13),        # 近黑的同色系墨色
        "paper": _hsv(hue, 0.10, 0.97),      # 米白纸感（不用纯白）
        "muted": _hsv(hue, 0.16, 0.33),
    }


# 手动指定配色时的兜底方案（名字与 v1 保持一致，方便 LLM 继续用）
MANUAL_PALETTES: Dict[str, str] = {
    "七夕粉": "#d94a78", "秋日焦糖": "#b8763c", "治愈奶油": "#d9a878",
    "清新薄荷": "#4aa8a0", "高级墨金": "#b08a44", "活力橙红": "#d9551e", "深林绿": "#4a8a5c",
}


def fallback_palette(name: str = "治愈奶油") -> Dict[str, Tuple[int, int, int]]:
    hexv = MANUAL_PALETTES.get(name, "#b8763c")
    r = int(hexv[1:3], 16) / 255
    g = int(hexv[3:5], 16) / 255
    b = int(hexv[5:7], 16) / 255
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    return {
        "accent": _hsv(h, min(1.0, s * 0.72), max(0.35, v * 0.95)),
        "accent_dark": _hsv(h, min(1.0, s * 0.95), max(0.25, v * 0.62)),
        "accent_soft": _hsv(h, s * 0.25, 0.94),
        "ink": _hsv(h, 0.18, 0.14),
        "paper": _hsv(h, 0.10, 0.97),
        "muted": _hsv(h, 0.18, 0.34),
    }


# --------------------------------------------------------------------------- 版式清单

DESIGNS: Dict[str, Dict] = {
    "cover_hero": {"w": 1080, "h": 1440, "channel": "小红书封面（大图压字）", "ratio": "3:4",
                   "kind": "hero"},
    "split_top": {"w": 1080, "h": 1440, "channel": "小红书封面（上下分区）", "ratio": "3:4",
                  "kind": "split"},
    "center_bold": {"w": 1080, "h": 1080, "channel": "朋友圈 / 社群图", "ratio": "1:1",
                    "kind": "center"},
    "poster_tall": {"w": 1080, "h": 1620, "channel": "门店海报 / 易拉宝", "ratio": "2:3",
                    "kind": "hero"},
    "magazine": {"w": 1600, "h": 900, "channel": "横版 banner / 视频封面", "ratio": "16:9",
                 "kind": "magazine"},
}
DEFAULT_DESIGNS = ["cover_hero", "split_top", "center_bold", "poster_tall", "magazine"]


# --------------------------------------------------------------------------- 空版渲染开关

# 生成"空版"（只有画面、没有文字）时置为 False：所有文字/色块绘制调用被跳过，
# 但排版几何完全不变 —— 这样两张图做像素差分，得到的就是纯文字图层，供质检使用。
_TEXT_ON = True


class _NullDraw:
    """文字关掉时的空绘制代理，吞掉所有绘图调用，保持代码路径不变。"""

    def __getattr__(self, _name):
        return lambda *a, **k: None


def _d(img):
    return ImageDraw.Draw(img, "RGBA") if _TEXT_ON else _NullDraw()


# --------------------------------------------------------------------------- 基础工具

_NO_START = "，。、！？；：》）】}%,.!?;:)]}"


def wrap(text: str, f: ImageFont.FreeTypeFont, max_w: float,
         max_lines: Optional[int] = None) -> List[str]:
    text = (text or "").strip()
    if not text:
        return []
    lines: List[str] = []
    cur = ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        if f.getlength(cur + ch) <= max_w or not cur:
            cur += ch
            continue
        if ch in _NO_START:
            cur += ch
            continue
        lines.append(cur)
        cur = ch
        if max_lines and len(lines) >= max_lines:
            break
    if cur:
        lines.append(cur)
    if max_lines and len(lines) > max_lines:
        lines = lines[:max_lines]
        lines[-1] = lines[-1][:-1] + "…"
    return lines


def fit_fixed(lines: Sequence[str], family: str, weight: str, max_w: float,
              size_max: int, size_min: int) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    """按外部（LLM）给定的断行排版 —— 中文断行靠字典，程序均分必然拆词，交给 LLM 最稳。"""
    size = int(size_max)
    f = font(family, weight, size)
    while size > size_min and max(f.getlength(l) for l in lines) > max_w:
        size -= max(2, int(size_max * 0.045))
        f = font(family, weight, size)
    return f, list(lines)


def _rebalance(lines: List[str]) -> List[str]:
    """避免「末行只剩 1-2 个字」的难看待断行：把整串按行数重新均分。"""
    if len(lines) < 2 or len(lines[-1]) >= 3:
        return lines
    text = "".join(lines)
    n = len(lines)
    per = -(-len(text) // n)                     # 向上取整
    out, i = [], 0
    for k in range(n):
        take = per if k < n - 1 else len(text) - i
        out.append(text[i:i + take])
        i += take
    fixed: List[str] = []
    for ln in out:                                # 避头点：标点不另起一行
        while ln and ln[0] in _NO_START and fixed:
            fixed[-1] += ln[0]
            ln = ln[1:]
        fixed.append(ln)
    return [l for l in fixed if l]


def fit(text: str, family: str, weight: str, max_w: float, max_lines: int,
        size_max: int, size_min: int) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    size = int(size_max)
    f = font(family, weight, size)
    lines = wrap(text, f, max_w, max_lines)
    while size > size_min:
        f = font(family, weight, size)
        lines = wrap(text, f, max_w, max_lines)
        if len(lines) <= max_lines and all(f.getlength(l) <= max_w for l in lines):
            bal = _rebalance(lines)
            if all(f.getlength(l) <= max_w for l in bal):
                lines = bal
            break
        size -= max(2, int(size_max * 0.045))
    return f, lines


def _bottom_scrim_mask(w: int, h: int, start_frac: float, max_alpha: int) -> Image.Image:
    """从 start_frac 高度往下渐深的遮罩。

    踩过的坑：Image.linear_gradient 的 point() 给的是 **0-255 的像素值**，
    不是画布坐标。之前写成 `0 if v > h*0.34 ...` 拿像素值去比高度，条件永远为假，
    于是整张画布都被压了一层灰 —— 上半部的"干净留白"直接没了。
    """
    grad = Image.linear_gradient("L").transpose(Image.Transpose.ROTATE_180).resize((w, h))
    v_start = int(255 * (1.0 - start_frac))     # ROTATE_180 后：顶部 v=255，底部 v=0
    return grad.point(lambda v: 0 if v >= v_start else
                      min(max_alpha, int((v_start - v) * max_alpha / max(1, v_start))))


def _hx(c: Tuple[int, int, int], a: int = 255) -> Tuple[int, int, int, int]:
    return (int(c[0]), int(c[1]), int(c[2]), a)


def text(base: Image.Image, xy, s: str, f, fill, *, anchor: str = "la",
         align: str = "left", shadow: bool = True, spacing: int = 0,
         opacity: float = 0.85) -> None:
    """写字。shadow=True 时叠一层高斯模糊柔光投影（比硬描边干净）。"""
    if not _TEXT_ON:
        return
    if shadow:
        layer = Image.new("L", base.size, 0)
        ImageDraw.Draw(layer).multiline_text(xy, s, font=f, fill=int(215 * opacity),
                                            anchor=anchor, spacing=spacing, align=align)
        blur = max(3, int(getattr(f, "size", 24) * 0.09))
        sh = Image.new("RGBA", base.size, (0, 0, 0, 0))
        sh.putalpha(layer.filter(ImageFilter.GaussianBlur(blur)))
        base.alpha_composite(sh)
    ImageDraw.Draw(base, "RGBA").multiline_text(xy, s, font=f, fill=fill, anchor=anchor,
                                                spacing=spacing, align=align)


def cover(img: Image.Image, w: int, h: int, focus: float = 0.42,
          zoom: float = 1.0) -> Image.Image:
    """铺满 + 裁切；focus<0.5 偏上取景（保住主体）；zoom>1 轻微裁紧。"""
    r = max(w / img.width, h / img.height) * zoom
    nw, nh = max(w, int(img.width * r + 0.5)), max(h, int(img.height * r + 0.5))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = int((nw - w) * 0.5)
    top = int((nh - h) * (focus if nh > h else 0.5))
    return img.crop((left, top, left + w, top + h))


def _feather_mask(size, box, feather: int, strength: float) -> Image.Image:
    m = Image.new("L", size, 0)
    ImageDraw.Draw(m).rectangle(box, fill=255)
    m = m.filter(ImageFilter.GaussianBlur(max(2, feather)))
    return m.point(lambda v: int(v * strength))


def local_soften(base: Image.Image, box, *, blur: int = 30, feather: int = 60,
                 darken: float = 0.30, strength: float = 0.9) -> Image.Image:
    """文字区局部羽化虚化 + 轻度压暗 —— 替代 v1 那条边界可见的横向暗带。

    真实设计师就是这么干的：让文字后面的画面「退到后面去」，而不是盖一条黑带。
    """
    mask = _feather_mask(base.size, box, feather, strength)
    softened = base.filter(ImageFilter.GaussianBlur(blur))
    out = Image.composite(softened, base, mask)
    dark = Image.new("RGBA", base.size, (12, 10, 14, int(255 * darken)))
    dark.putalpha(mask.point(lambda v: int(v * darken)))
    return Image.alpha_composite(out, dark)


def _rule(base: Image.Image, x: float, y: float, w: float, color, width: int = 4,
          dot: bool = False) -> None:
    d = _d(base)
    d.rectangle([x, y, x + w, y + width], fill=color)
    if dot:
        r = width * 2.1
        d.ellipse([x - r, y + width / 2 - r, x + r, y + width / 2 + r], fill=color)


def _badge(base: Image.Image, x: float, y: float, label: str, color, fg,
           fsize: int, family: str = "sans") -> float:
    """极简角标：一条细竖线 + 主题文字（不用胶囊，胶囊是最"网页"的元素）。"""
    f = font(family, "Medium", fsize)
    d = _d(base)
    h = fsize * 1.25
    d.rectangle([x, y, x + max(3, fsize // 9), y + h], fill=color)
    d.text((x + fsize * 0.55, y + h / 2), label, font=f, fill=fg, anchor="lm")
    return x + fsize * 0.55 + f.getlength(label)


# --------------------------------------------------------------------------- 主函数

def compose(bg_path: str, spec: Dict, out_path: str, *, design: str = "cover_hero",
            palette: str = "", font_family: str = "sans",
            theme_tag: str = "", shop: str = "", offer: str = "",
            address: str = "", phone: str = "", quality: int = 92,
            with_text: bool = True, baseline_out: Optional[str] = None,
            contrast_boost: float = 1.0) -> Dict:
    """把无文字底图 + 文案，按指定版式合成一张成品图。

    spec: headline / subheadline / cta / bullets（bullets 只在 magazine 版式里用，最多 2 个）
    palette: 传调色板名走固定配色；留空则从底图自动提取主色。
    baseline_out: 给出路径时，同时导出一张"空版"（几何一致、无文字），供质检做像素差分。
    """
    global _TEXT_ON
    _TEXT_ON = with_text
    cfg = DESIGNS.get(design) or DESIGNS["cover_hero"]
    w, h = cfg["w"], cfg["h"]
    kind = cfg["kind"]
    pal = fallback_palette(palette) if palette else extract_palette(bg_path)
    if palette:
        pal = fallback_palette(palette)
    accent, accent_dark = pal["accent"], pal["accent_dark"]
    accent_soft = pal.get("accent_soft", pal["accent"])
    ink, paper, muted = pal["ink"], pal["paper"], pal["muted"]

    headline = (spec.get("headline") or "").strip()
    sub = (spec.get("subheadline") or "").strip()
    cta = (spec.get("cta") or "").strip()
    bullets = [b for b in (spec.get("bullets") or []) if b][:2]
    hint_items = hint_parts(shop, offer, address, phone)

    hl_fixed = [l.strip() for l in (spec.get("headline_lines") or []) if l and l.strip()][:3]
    sub_fixed = [l.strip() for l in (spec.get("subheadline_lines") or []) if l and l.strip()][:3]

    def _headline(max_w: float, max_lines: int, size_max: int, size_min: int):
        """有 LLM 断行就按语义断行，否则自动折行 + 均衡。"""
        if hl_fixed:
            return fit_fixed(hl_fixed, font_family, "Black", max_w, size_max, size_min)
        return fit(headline, font_family, "Black", max_w, max_lines, size_max, size_min)

    def _subhead(max_w: float, max_lines: int, size_max: int, size_min: int):
        """副标题同理：中文断行靠语义，不靠程序均分（否则会把「一颗颗」拆开）。"""
        if sub_fixed:
            return fit_fixed(sub_fixed, font_family, "Regular", max_w, size_max, size_min)
        return fit(sub, font_family, "Regular", max_w, max_lines, size_max, size_min)

    unit = min(w, h)
    margin = int(w * (0.085 if kind != "magazine" else 0.065))
    base = Image.open(bg_path).convert("RGBA")
    hl_px = 0
    warnings: List[str] = []

    # =======================================================  大图压字式
    if kind == "hero":
        base = cover(base, w, h, focus=0.40, zoom=1.04)
        # 文字区：上部 1/3 偏下；局部虚化让主体退后
        box = (0, int(h * 0.10), w, int(h * 0.46))
        # contrast_boost > 1 用于"质检不过 → 自动加深文字区"的自动修复回路
        base = local_soften(base, box, blur=max(18, unit // 26),
                            feather=int(unit * 0.10),
                            darken=min(0.62, 0.34 * contrast_boost), strength=0.92)
        # 底部一条轻薄渐变，只压住 CTA 与信息行
        gm = _bottom_scrim_mask(w, h, 0.34, int(min(232, 150 * contrast_boost)))
        base = Image.alpha_composite(base, Image.merge("RGBA", (
            Image.new("L", (w, h), 10), Image.new("L", (w, h), 10),
            Image.new("L", (w, h), 14), gm)))
        if theme_tag:
            _badge(base, margin, int(h * 0.085), theme_tag, accent, _hx((255, 255, 255, 240)),
                   int(unit * 0.026), font_family)
        y = int(h * 0.15)
        fh, hl = _headline(w - margin * 2, 2, int(h * 0.115), int(h * 0.055))
        hl_px = fh.size
        lh = int(fh.size * 1.14)
        for line in hl:
            text(base, (margin, y), line, fh, _hx((255, 255, 255)), shadow=True)
            y += lh
        y += int(unit * 0.028)
        _rule(base, margin, y, int(w * 0.13), _hx(accent, 255), width=max(3, unit // 240), dot=True)
        y += int(unit * 0.045)
        if sub:
            fs, sl = _subhead(w - margin * 2, 2, int(fh.size * 0.46), int(fh.size * 0.30))
            for line in sl:
                text(base, (margin, y), line, fs, _hx((255, 255, 255, 232)), shadow=True)
                y += int(fs.size * 1.5)
        text_end = y          # 文字块底部（质检用）
        # 底部两行结构：信息行独占一行在上，CTA 独占一行在下（并排会显得挤压）
        th = int(unit * 0.034) + int(unit * 0.048)
        by = h - max(int(unit * 0.095), int(h * 0.085)) - th
        cy_info = by - int(unit * 0.065)
        _rule(base, margin, cy_info - int(unit * 0.055), w - margin * 2,
              _hx((255, 255, 255, 70)), width=max(2, unit // 400))
        if hint_items:
            _hint_render(base, margin, cy_info, hint_items, font_family,
                         int(unit * 0.030), accent_soft,
                         max_right=w - margin, align="left")
        if by < text_end + int(unit * 0.015):
            warnings.append("CTA 与文字块间距不足，可能视觉粘连")
        if cta:
            fc = font(font_family, "Bold", int(unit * 0.034))
            tw = fc.getlength(cta) + unit * 0.08
            d = _d(base)
            d.rounded_rectangle([margin, by, margin + tw, by + th],
                                radius=th // 2, fill=_hx(accent_dark, 250))
            d.text((margin + tw / 2, by + th / 2), cta, font=fc, fill=_hx((255, 255, 255)),
                   anchor="mm")

    # =======================================================  上下分区式
    elif kind == "split":
        top_h = int(h * 0.36)
        img_h = h - top_h
        base = cover(Image.open(bg_path).convert("RGBA"), w, img_h, focus=0.26, zoom=1.04)
        canvas = Image.new("RGBA", (w, h), _hx(paper))
        # 圆角图片卡贴在下部
        mask = Image.new("L", (w, img_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, int(unit * 0.045), w, img_h + int(unit * 0.06)],
                                               radius=int(unit * 0.055), fill=255)
        canvas.paste(base, (0, top_h), mask)
        y = int(top_h * 0.20)
        if theme_tag:
            _badge(canvas, margin, y, theme_tag, accent, _hx(accent_dark),
                   int(unit * 0.025), font_family)
            y += int(unit * 0.055)
        fh, hl = _headline(w - margin * 2, 2, int(h * 0.085), int(h * 0.040))
        hl_px = fh.size
        lh = int(fh.size * 1.16)
        for line in hl:
            text(canvas, (margin, y), line, fh, _hx(ink), shadow=False)
            y += lh
        y += int(unit * 0.018)
        _rule(canvas, margin, y, int(w * 0.11), _hx(accent), width=max(3, unit // 240), dot=True)
        y += int(unit * 0.032)
        if sub:
            fs, sl = _subhead(w - margin * 2, 2, int(fh.size * 0.50), int(fh.size * 0.34))
            for line in sl:
                text(canvas, (margin, y), line, fs, _hx(muted), shadow=False)
                y += int(fs.size * 1.5)
        text_end = y          # 质检用
        # 图片上：信息行一行 + CTA 一行（各自独立，不并排）
        fc = font(font_family, "Bold", int(unit * 0.033))
        th = fc.size + unit * 0.044
        by = h - max(int(unit * 0.095), int(h * 0.085)) - th
        iy = by - int(unit * 0.065)
        if iy < text_end + int(unit * 0.010):
            warnings.append("信息行与文字块间距不足")
        gm = _bottom_scrim_mask(w, h, 0.34, int(min(245, 215 * contrast_boost)))
        canvas = Image.alpha_composite(canvas, Image.merge("RGBA", (
            Image.new("L", (w, h), 10), Image.new("L", (w, h), 10),
            Image.new("L", (w, h), 14), gm)))
        if hint_items:
            _hint_render(canvas, margin, iy, hint_items, font_family,
                         int(unit * 0.029), accent_soft,
                         max_right=w - margin, align="left")
        if cta:
            tw = fc.getlength(cta) + unit * 0.076
            d = _d(canvas)
            d.rounded_rectangle([margin, by, margin + tw, by + th],
                                radius=th // 2, fill=_hx(accent_dark, 250))
            d.text((margin + tw / 2, by + th / 2), cta, font=fc, fill=_hx((255, 255, 255)),
                   anchor="mm")
        base = canvas

    # =======================================================  居中对称式
    elif kind == "center":
        canvas = Image.new("RGBA", (w, h), _hx(paper))
        # 先算好底部两行（信息行 + CTA）的占位，再决定上方图片卡能占多高 ——
        # 否则内容一多，CTA 按钮会直接压到主标题上（v2 第一轮就踩了这个坑）。
        _fc = font(font_family, "Bold", int(unit * 0.032))
        _th = _fc.size + int(unit * 0.044)
        cta_top = h - int(h * 0.115) - _th
        cy_info = cta_top - int(unit * 0.062)
        text_limit = cy_info - int(unit * 0.080)
        top = int(h * 0.085)
        # 先量文字块高度，再决定图片卡能占多高（否则 CTA 会压到主标题上）
        _fh_t, _hl_t = _headline(w - margin * 2, 2, int(h * 0.088), int(h * 0.05))
        _blk = len(_hl_t) * int(_fh_t.size * 1.18) + int(unit * 0.032)
        if sub:
            _fs_t, _sl_t = _subhead(w - margin * 2, 2, int(_fh_t.size * 0.44),
                                    int(_fh_t.size * 0.30))
            _blk += int(unit * 0.028) + len(_sl_t) * int(_fs_t.size * 1.5)
        _blk += int(unit * 0.075) if theme_tag else 0
        _min_img = int(h * 0.20)
        _avail = text_limit - top - int(unit * 0.075)
        _hint_items = list(hint_items)
        if _avail - _blk < _min_img + int(h * 0.05):
            # 内容塞不下时先牺牲「信息行」（辅助信息），
            # 底线是主标题、副标题、按钮之间永不重叠。
            _hint_items = []
            text_limit = cta_top - int(unit * 0.080)
            _avail = text_limit - top - int(unit * 0.075)
        img_h = int(h * 0.42)
        if _avail - _blk < img_h:
            img_h = max(_min_img, _avail - _blk)
        art = cover(Image.open(bg_path).convert("RGBA"), w - margin * 2, img_h,
                    focus=0.40, zoom=1.05)
        mask = Image.new("L", (w - margin * 2, img_h), 0)
        ImageDraw.Draw(mask).rounded_rectangle([0, 0, w - margin * 2, img_h],
                                               radius=int(unit * 0.045), fill=255)
        canvas.paste(art, (margin, top), mask)
        y = top + img_h + int(unit * 0.075)
        cx = w / 2
        if theme_tag:
            fb = font(font_family, "Medium", int(unit * 0.024))
            tw = fb.getlength(theme_tag)
            d = _d(canvas)
            d.ellipse([cx - tw / 2 - unit * 0.055, y + unit * 0.012,
                       cx - tw / 2 - unit * 0.028, y + unit * 0.039], fill=_hx(accent))
            d.text((cx, y + unit * 0.026), theme_tag, font=fb, fill=_hx(muted), anchor="mm")
            y += int(unit * 0.075)
        fh, hl = _headline(w - margin * 2, 2, int(h * 0.088), int(h * 0.05))
        hl_px = fh.size
        lh = int(fh.size * 1.18)
        for line in hl:
            text(canvas, (cx, y), line, fh, _hx(ink), anchor="ma", align="center", shadow=False)
            y += lh
        y += int(unit * 0.028)
        if sub:
            fs, sl = _subhead(w - margin * 2, 2, int(fh.size * 0.44), int(fh.size * 0.30))
            for line in sl:
                text(canvas, (cx, y), line, fs, _hx(muted), anchor="ma",
                     align="center", shadow=False)
                y += int(fs.size * 1.5)
        y += int(unit * 0.032)
        d = _d(canvas)
        d.rectangle([cx - int(w * 0.05), y, cx + int(w * 0.05), y + max(3, unit // 250)],
                    fill=_hx(accent))
        text_end = y          # 质检用
        if cta_top < text_end + int(unit * 0.015):
            warnings.append("CTA 与文字块间距不足（居中版式）")
        # 信息行与 CTA 各自成行（居中对称：两行都居中）
        fc, th, by = _fc, _th, cta_top
        if _hint_items:
            _hint_render(canvas, cx, cy_info, _hint_items, font_family,
                         int(unit * 0.029), accent_dark, max_right=cx + w * 0.42,
                         on_dark=False, align="center")
        if cta:
            tw = fc.getlength(cta) + int(unit * 0.09)
            d.rounded_rectangle([cx - tw / 2, by, cx + tw / 2, by + th], radius=th // 2,
                                fill=_hx(accent_dark, 252))
            d.text((cx, by + th / 2), cta, font=fc, fill=_hx((255, 255, 255)), anchor="mm")
        base = canvas

    # =======================================================  左右分割（杂志式）
    else:
        left_w = int(w * 0.40)
        canvas = Image.new("RGBA", (w, h), _hx(paper))
        art = cover(Image.open(bg_path).convert("RGBA"), w - left_w, h, focus=0.45, zoom=1.05)
        canvas.paste(art, (left_w, 0))
        d = _d(canvas)
        # 竖向强调线：不顶到画布上下边缘（否则质检的安全边距会判它压边，设计上也更透气）
        d.rectangle([left_w - max(4, unit // 200), int(h * 0.13), left_w, int(h * 0.87)],
                    fill=_hx(accent))
        x = margin
        y = int(h * 0.17)
        if theme_tag:
            _badge(canvas, x, y, theme_tag, accent, _hx(accent_dark), int(min(w, h) * 0.026),
                   font_family)
            y += int(min(w, h) * 0.058)
        fh, hl = _headline(left_w - margin * 1.55, 3, int(h * 0.128), int(h * 0.05))
        hl_px = fh.size
        lh = int(fh.size * 1.16)
        for line in hl:
            text(canvas, (x, y), line, fh, _hx(ink), shadow=False)
            y += lh
        y += int(min(w, h) * 0.022)
        _rule(canvas, x, y, int(left_w * 0.22), _hx(accent), width=max(3, unit // 240), dot=True)
        y += int(min(w, h) * 0.045)
        if sub:
            fs, sl = _subhead(left_w - margin * 1.55, 3, int(fh.size * 0.36), int(fh.size * 0.24))
            for line in sl:
                text(canvas, (x, y), line, fs, _hx(muted), shadow=False)
                y += int(fs.size * 1.5)
        for b in bullets:
            if y > h - margin - int(h * 0.20):
                break
            y += int(min(w, h) * 0.016)
            d.ellipse([x, y + min(w, h) * 0.008, x + min(w, h) * 0.012,
                       y + min(w, h) * 0.020], fill=_hx(accent))
            text(canvas, (x + min(w, h) * 0.030, y), b,
                 font(font_family, "Regular", int(min(w, h) * 0.024)), _hx(muted),
                 shadow=False)
            y += int(min(w, h) * 0.030)
        text_end = y          # 质检用
        # 底部：信息行一行 + CTA 一行，各自独立
        fc = font(font_family, "Bold", int(h * 0.036))
        th = fc.size + int(h * 0.030)
        by = h - margin - th
        if hint_items:
            _hint_render(canvas, x, by - int(h * 0.058), hint_items, font_family,
                         int(h * 0.030), accent_dark, max_right=left_w - margin * 0.5,
                         on_dark=False, align="left")
        if cta:
            tw = fc.getlength(cta) + int(h * 0.075)
            d.rounded_rectangle([x, by, x + tw, by + th], radius=th // 2,
                                fill=_hx(accent_dark, 252))
            d.text((x + tw / 2, by + th / 2), cta, font=fc, fill=_hx((255, 255, 255)),
                   anchor="mm")
        base = canvas

    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    final = base.convert("RGB")
    if out.suffix.lower() in (".jpg", ".jpeg"):
        final.save(out, quality=quality, subsampling=0, optimize=True)
    else:
        final.save(out)

    # 顺便导出一张几何一致的"空版"，供质检做像素差分（只算文字图层，成本极低）
    if baseline_out and with_text:
        try:
            compose(bg_path, spec, baseline_out, design=design, palette=palette,
                    font_family=font_family, theme_tag=theme_tag, shop=shop, offer=offer,
                    address=address, phone=phone, quality=quality, with_text=False,
                    contrast_boost=contrast_boost)   # 必须与成品同参数，否则差分出来的是背景
        finally:
            _TEXT_ON = True

    n_elems = sum(bool(x) for x in (theme_tag, headline, sub, cta, hint_items))
    if bullets and kind == "magazine":
        n_elems += 1
    return {"file": str(out.resolve()), "w": w, "h": h, "channel": cfg["channel"],
            "ratio": cfg["ratio"], "layout": design, "palette": palette or "auto",
            "headline_px": hl_px, "elements": n_elems, "warnings": warnings,
            "baseline": str(Path(baseline_out).resolve()) if baseline_out else None}


def hint_parts(shop: str, offer: str, address: str, phone: str) -> List[str]:
    """底部信息行：只保留能放进一行的关键信息。"""
    parts = [p.strip() for p in (shop, offer, address, phone) if p and p.strip()]
    return parts[:3]


def fit_hint(items: Sequence[str], f, max_w: float) -> str:
    """按可用宽度拼信息行，放不下就整段丢弃（绝不允许压边或与 CTA 重叠）。"""
    out: List[str] = []
    for it in items:
        trial = " · ".join(out + [it])
        if f.getlength(trial) <= max_w:
            out.append(it)
    return " · ".join(out)


def _hint_render(base: Image.Image, anchor_x: float, cy: float, items: Sequence[str],
                 family: str, size: int, accent_light,
                 max_left: float = 0.0, max_right: float = 1e9,
                 on_dark: bool = True, align: str = "right") -> None:
    """绘制信息行，按可用宽度自动丢弃放不下的段，**价格单独加粗变色**。

    第 2 段（offer，也就是价格/活动）是转化信息，用强调色加粗；其余用常规字重，
    这样"价格不突出"的问题一并解决。
    align="right" 时 anchor_x 是右边界；"left" 时 anchor_x 是左边界。
    """
    if not items or not _TEXT_ON:
        return
    fs = font(family, "Regular", size)
    fb = font(family, "Bold", size)
    sep = "  ·  "
    fg_dim = _hx((255, 255, 255, 246)) if on_dark else _hx((112, 102, 96))
    fg_acc = _hx(accent_light)
    segs = []
    for i, it in enumerate(items):
        segs.append((it, fb if i == 1 else fs,
                     fg_acc if i == 1 else fg_dim))

    kept, total = [], 0.0
    for seg in segs:
        add = seg[1].getlength(seg[0]) + (fs.getlength(sep) if kept else 0)
        if align == "right":
            fits = anchor_x - (total + add) >= max_left
        else:
            fits = anchor_x + total + add <= max_right
        if not fits:
            break
        kept.append(seg)
        total += add
    if not kept:
        return
    d = _d(base)
    if align == "right":
        x = anchor_x - total
    elif align == "center":
        x = anchor_x - total / 2
    else:
        x = anchor_x
    for i, (txt, f, col) in enumerate(kept):
        if i:
            d.text((x, cy), sep, font=fs,
                   fill=_hx((255, 255, 255, 150)) if on_dark else _hx((176, 166, 158)),
                   anchor="lm")
            x += fs.getlength(sep)
        d.text((x, cy), txt, font=f, fill=col, anchor="lm")
        x += f.getlength(txt)
