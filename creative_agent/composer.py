"""排版合成引擎 —— 本项目的技术差异点。

为什么不直接让 AI 画一张带文字的海报？
  文生图模型渲染中文标题/价格/地址时极易出现错字、糊字、乱码，而且改一个字要重新出图。
本引擎把「视觉」和「文字」彻底解耦：
  AI 只负责没有文字的视觉底图（一次生成，可复用），
  中文标题、副标题、卖点标签、地址/时间信息条、CTA 由 Pillow 精确排版，
  于是一张图可以瞬间适配 4 种渠道尺寸、改文案不用重出图、字永远不糊。

对外只有一个函数：compose(...)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from PIL import Image, ImageDraw, ImageFilter, ImageFont

# --------------------------------------------------------------------------- 字体

# 每项 (字体文件, ttc 内的 face index)；按顺序取第一个存在的文件，保证跨平台可跑
FONT_CANDIDATES: Dict[str, Dict[str, List[Tuple[str, int]]]] = {
    "sans": {
        "bold": [
            ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 1),
            ("C:/Windows/Fonts/msyhbd.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", 0),
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
        ],
        "regular": [
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
            ("/System/Library/Fonts/STHeiti Light.ttc", 0),
            ("C:/Windows/Fonts/msyh.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", 0),
            ("/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc", 0),
        ],
    },
    "serif": {
        "bold": [
            ("/System/Library/Fonts/Supplemental/Songti.ttc", 1),
            ("/System/Library/Fonts/STHeiti Medium.ttc", 0),
            ("C:/Windows/Fonts/simsun.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Bold.ttc", 0),
        ],
        "regular": [
            ("/System/Library/Fonts/Supplemental/Songti.ttc", 0),
            ("/System/Library/Fonts/Hiragino Sans GB.ttc", 0),
            ("C:/Windows/Fonts/simsun.ttc", 0),
            ("/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc", 0),
        ],
    },
}

_font_cache: Dict[Tuple[str, int, int], ImageFont.FreeTypeFont] = {}


def _font(style: str, weight: str, size: int) -> ImageFont.FreeTypeFont:
    size = max(8, int(size))
    key = (style, weight, size)
    if key in _font_cache:
        return _font_cache[key]
    for path, index in FONT_CANDIDATES.get(style, FONT_CANDIDATES["sans"])[weight]:
        if os.path.exists(path):
            try:
                f = ImageFont.truetype(path, size, index=index)
                _font_cache[key] = f
                return f
            except Exception:
                continue
    f = ImageFont.load_default()
    _font_cache[key] = f
    return f


# --------------------------------------------------------------------------- 配色

PALETTES: Dict[str, Dict[str, Tuple[int, int, int]]] = {
    "七夕粉": {"accent": (226, 74, 120), "chip_fg": (176, 32, 84), "bar_fg": (72, 34, 50), "tag_fg": (255, 255, 255)},
    "秋日焦糖": {"accent": (196, 122, 60), "chip_fg": (150, 84, 32), "bar_fg": (66, 45, 30), "tag_fg": (255, 255, 255)},
    "治愈奶油": {"accent": (222, 172, 120), "chip_fg": (150, 108, 62), "bar_fg": (78, 62, 48), "tag_fg": (255, 255, 255)},
    "清新薄荷": {"accent": (74, 178, 160), "chip_fg": (36, 128, 116), "bar_fg": (34, 66, 62), "tag_fg": (255, 255, 255)},
    "高级墨金": {"accent": (198, 162, 90), "chip_fg": (150, 116, 48), "bar_fg": (38, 36, 34), "tag_fg": (255, 255, 255)},
    "活力橙红": {"accent": (238, 104, 62), "chip_fg": (186, 66, 30), "bar_fg": (72, 38, 28), "tag_fg": (255, 255, 255)},
    "深林绿": {"accent": (86, 138, 96), "chip_fg": (52, 106, 66), "bar_fg": (34, 52, 40), "tag_fg": (255, 255, 255)},
}
DEFAULT_PALETTE = "治愈奶油"


def resolve_palette(name: str) -> Dict[str, Tuple[int, int, int]]:
    if name in PALETTES:
        return PALETTES[name]
    for k in PALETTES:
        if k and k[:2] in (name or ""):
            return PALETTES[k]
    return PALETTES[DEFAULT_PALETTE]


# --------------------------------------------------------------------------- 版式

LAYOUTS: Dict[str, Dict] = {
    "poster_2x3": {"w": 1080, "h": 1620, "channel": "门店海报 / 易拉宝", "ratio": "2:3",
                   "style": "bottom", "preview": 0.62},
    "xhs_3x4": {"w": 1080, "h": 1440, "channel": "小红书封面", "ratio": "3:4",
                "style": "bottom", "preview": 0.68},
    "square_1x1": {"w": 1080, "h": 1080, "channel": "朋友圈 / 社群图", "ratio": "1:1",
                   "style": "card", "preview": 0.9},
    "banner_16x9": {"w": 1600, "h": 900, "channel": "短视频封面 / 横版banner", "ratio": "16:9",
                    "style": "left", "preview": 0.55},
}


# --------------------------------------------------------------------------- 文本工具

_NO_LINE_START = "，。、！？；：》）】}%,.!?;:)]}"

# 中文换行宽度按「汉字=1、其他=0.5」估算，用于视觉平衡
def _visual_len(s: str) -> float:
    return sum(1.0 if ord(c) > 0x2E80 else 0.5 for c in s)


def wrap_text(text: str, font: ImageFont.FreeTypeFont, max_w: float,
              max_lines: Optional[int] = None) -> List[str]:
    """按像素宽度折行，对中文友好，并做简易「避头点」处理。"""
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
        probe = cur + ch
        if font.getlength(probe) <= max_w or not cur:
            cur = probe
            continue
        if ch in _NO_LINE_START and cur:
            cur += ch          # 标点不另起一行
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


def fit_headline(text: str, style: str, max_w: float, size_max: int, size_min: int,
                 max_lines: int = 2) -> Tuple[ImageFont.FreeTypeFont, List[str]]:
    """自动缩字号直到标题能塞进 max_lines 行。"""
    size = size_max
    font = _font(style, "bold", size)
    lines: List[str] = []
    while size > size_min:
        font = _font(style, "bold", size)
        lines = wrap_text(text, font, max_w, max_lines)
        if len(lines) <= max_lines and all(font.getlength(l) <= max_w for l in lines):
            return font, lines
        size -= max(2, int(size_max * 0.05))
    return font, lines


def _text(draw: ImageDraw.ImageDraw, xy, text: str, font, fill, *, shadow: bool = True,
          anchor: str = "la", spacing: int = 0, align: str = "left"):
    """在底图上写字。shadow=True 时用「高斯模糊柔光投影」，比硬描边干净得多。

    这里拿到底图对象是为了叠一层模糊阴影图层；调用方传进来的 draw 与 base 共享同一张图。
    """
    base = draw._image          # PIL 私有属性，但 draw/base 本来就成对使用
    if shadow:
        layer = Image.new("L", base.size, 0)
        ImageDraw.Draw(layer).multiline_text(xy, text, font=font, fill=190,
                                             anchor=anchor, spacing=spacing, align=align)
        blur = max(3, int(getattr(font, "size", 24) * 0.085))
        layer = layer.filter(ImageFilter.GaussianBlur(blur))
        shadow_img = Image.new("RGBA", base.size, (0, 0, 0, 0))
        shadow_img.putalpha(layer)
        base.alpha_composite(shadow_img)
        draw = ImageDraw.Draw(base, "RGBA")
    draw.multiline_text(xy, text, font=font, fill=fill, anchor=anchor,
                        spacing=spacing, align=align)


def _chip(draw, xy, text: str, font, fg, bg, pad=(18, 10)) -> Tuple[int, int]:
    """画一个圆角标签，返回 (宽, 高)。"""
    w = font.getlength(text) + pad[0] * 2
    h = font.size + pad[1] * 2
    x, y = xy
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=bg)
    draw.text((x + w / 2, y + h / 2), text, font=font, fill=fg, anchor="mm")
    return int(w), int(h)


def _darken(color: Tuple[int, int, int], factor: float = 0.78) -> Tuple[int, int, int]:
    """白字压在强调色上时，把强调色压深一档，保证小字对比度。"""
    return (int(color[0] * factor), int(color[1] * factor), int(color[2] * factor))


# --------------------------------------------------------------------------- 背景处理

def cover_crop(img: Image.Image, w: int, h: int, focus: float = 0.42) -> Image.Image:
    """按短边铺满 + 居中裁切；focus<0.5 表示偏上取景（保住主体）。"""
    r = max(w / img.width, h / img.height)
    nw, nh = max(w, int(img.width * r + 0.5)), max(h, int(img.height * r + 0.5))
    img = img.resize((nw, nh), Image.Resampling.LANCZOS)
    left = int((nw - w) * 0.5)
    top = int((nh - h) * (focus if nh > h else 0.5))
    return img.crop((left, top, left + w, top + h))


def _scrim(base: Image.Image, style: str, strength: float = 0.9) -> Image.Image:
    """给文字区压一层遮罩，保证白字压在复杂照片上也读得清。"""
    w, h = base.size
    grad = Image.linear_gradient("L")
    if style == "bottom":
        # 从 35% 高度开始逐渐压暗，底部近似实色 —— 文字块正好落在这段里
        mask = grad.resize((w, h)).point(
            lambda v: 0 if v <= 90 else int(min(240, (v - 90) * 240 / 165) * strength))
    elif style == "top":
        mask = grad.transpose(Image.Transpose.ROTATE_180).resize((w, h)).point(
            lambda v: int(min(235, v * 1.05) * strength))
    else:  # left —— 文字压在左半幅
        mask = grad.transpose(Image.Transpose.ROTATE_270).resize((w, h)).point(
            lambda v: min(255, int(v * 2.0 * strength)))
    black = Image.new("RGBA", (w, h), (10, 10, 14, 255))
    return Image.composite(black, base, mask)


# --------------------------------------------------------------------------- 主函数

def compose(bg_path: str, spec: Dict, out_path: str, *, layout: str = "xhs_3x4",
            palette: str = DEFAULT_PALETTE, font_style: str = "sans",
            theme_tag: str = "", info: str = "", shop: str = "",
            offer: str = "", address: str = "", phone: str = "",
            show_qr: bool = False, quality: int = 92) -> Dict:
    """把一张无文字底图 + 一份文案规格，合成一张可用广告图。

    spec 字段：headline / subheadline / bullets[] / cta
    底部信息条：第一行加粗强调 offer（价格/活动），第二行放 address · phone。
    """
    cfg = LAYOUTS.get(layout) or LAYOUTS["xhs_3x4"]
    w, h = cfg["w"], cfg["h"]
    pal = resolve_palette(palette)
    accent = pal["accent"]
    accent_dark = _darken(accent)
    white = (255, 255, 255, 255)

    base = Image.open(bg_path).convert("RGBA")
    base = cover_crop(base, w, h, focus=0.35 if cfg["style"] != "left" else 0.5)
    base = _scrim(base, cfg["style"])
    draw = ImageDraw.Draw(base, "RGBA")

    unit = min(w, h)
    style = cfg["style"]
    max_w = w * (0.52 if style == "left" else 0.84)

    headline = (spec.get("headline") or "").strip()
    sub = (spec.get("subheadline") or "").strip()
    bullets = [b for b in (spec.get("bullets") or []) if b][:3]
    cta = (spec.get("cta") or "").strip()

    size_max = int(unit * (0.11 if style == "left" else 0.095))
    hl_font, hl_lines = fit_headline(headline, font_style, max_w, size_max,
                                     max(30, int(size_max * 0.55)))
    sub_font = _font(font_style, "regular", int(unit * 0.038))
    sub_lines = wrap_text(sub, sub_font, max_w, 3)
    chip_font = _font(font_style, "bold", int(unit * 0.027))
    tag_font = _font(font_style, "bold", int(unit * 0.028))
    bar_font_b = _font(font_style, "bold", int(unit * 0.030))
    bar_font = _font(font_style, "regular", int(unit * 0.026))

    hl_lh = int(hl_font.size * 1.16)
    sub_lh = int(sub_font.size * 1.5)
    gap_hl_sub = int(unit * 0.026)
    block_h = len(hl_lines) * hl_lh + (gap_hl_sub + len(sub_lines) * sub_lh if sub_lines else 0)
    if bullets:
        block_h += int(chip_font.size * 2.6)
    if cta:
        block_h += int(chip_font.size * 3.4)

    offer_l = (offer or "").strip()
    addr_l = " · ".join(p for p in [address.strip(), phone.strip()] if p)
    line2 = info.strip() if (not addr_l and info) else addr_l
    bar_rows = 1 if (not offer_l and not line2) else (2 if (offer_l and line2) else 1)
    bar_h = int(unit * (0.155 if bar_rows == 2 else 0.115)) if (shop or offer_l or line2) else 0
    pad = int(w * 0.075)

    # ---------------- 顶部主题标签（活动角标）
    if theme_tag:
        tw = tag_font.getlength(theme_tag) + 44
        th = tag_font.size + 24
        draw.rounded_rectangle([pad, pad, pad + tw, pad + th], radius=th // 2,
                               fill=accent_dark + (238,))
        draw.text((pad + tw / 2, pad + th / 2), theme_tag, font=tag_font,
                  fill=pal["tag_fg"], anchor="mm")

    # ---------------- 各版式摆放
    if style == "card":
        card_pad = int(unit * 0.075)
        card_w = w - pad * 2
        card_h = block_h + card_pad * 2
        cx0, cy0 = pad, int((h - card_h) / 2)
        mid_x = cx0 + card_w / 2
        draw.rounded_rectangle([cx0, cy0, cx0 + card_w, cy0 + card_h], radius=int(unit * 0.05),
                               fill=(255, 255, 255, 242))
        draw.rounded_rectangle([cx0 + int(card_w * 0.28), cy0, cx0 + int(card_w * 0.72),
                                cy0 + max(4, int(unit * 0.012))],
                               radius=int(unit * 0.006), fill=accent + (255,))
        y = cy0 + card_pad
        for line in hl_lines:
            _text(draw, (mid_x, y), line, hl_font, pal["bar_fg"] + (255,), shadow=False,
                  anchor="ma", align="center")
            y += hl_lh
        if sub_lines:
            y += gap_hl_sub
            for line in sub_lines:
                _text(draw, (mid_x, y), line, sub_font, pal["bar_fg"] + (205,), shadow=False,
                      anchor="ma", align="center")
                y += sub_lh
        y += int(chip_font.size * 0.7)
        _draw_chips(draw, cx0 + card_pad, y, bullets, chip_font, pal, accent,
                    max_x=cx0 + card_w - card_pad, on_dark=False,
                    center_x=mid_x, center_right=cx0 + card_w - card_pad)
        if cta:
            y += int(chip_font.size * 2.6)
            _draw_cta(draw, cx0 + card_pad, y, cta, chip_font, accent_dark, pal["tag_fg"],
                      center_x=mid_x)
    elif style == "left":
        y0 = int((h - block_h) / 2)
        y = y0
        x = pad
        for line in hl_lines:
            _text(draw, (x, y), line, hl_font, white)
            y += hl_lh
        if sub_lines:
            y += gap_hl_sub
            for line in sub_lines:
                _text(draw, (x, y), line, sub_font, (240, 236, 232, 235))
                y += sub_lh
        y += int(chip_font.size * 0.8)
        _draw_chips(draw, x, y, bullets, chip_font, pal, accent, max_x=x + max_w)
        if cta:
            y += int(chip_font.size * 2.8)
            _draw_cta(draw, x, y, cta, chip_font, accent_dark, pal["tag_fg"])
    else:  # bottom
        y0 = h - bar_h - int(unit * 0.05) - block_h
        y = y0
        x = pad
        for line in hl_lines:
            _text(draw, (x, y), line, hl_font, white)
            y += hl_lh
        if sub_lines:
            y += gap_hl_sub
            for line in sub_lines:
                _text(draw, (x, y), line, sub_font, (242, 238, 234, 235))
                y += sub_lh
        if bullets:
            y += int(chip_font.size * 0.9)
            _draw_chips(draw, x, y, bullets, chip_font, pal, accent, max_x=w - pad, on_dark=True)
            y += int(chip_font.size * 2.0)
        if cta:
            y += int(chip_font.size * 0.9)
            _draw_cta(draw, x, y, cta, chip_font, accent_dark, pal["tag_fg"])

    # ---------------- 底部信息条（店名 / 活动信息 / 地址电话 / 二维码位）
    if bar_h:
        draw.rectangle([0, h - bar_h, w, h], fill=(255, 255, 255, 246))
        draw.rectangle([0, h - bar_h, w, h - bar_h + max(3, int(unit * 0.006))],
                       fill=accent + (255,))
        offer_l = (offer or "").strip()
        addr_l = " · ".join(p for p in [address.strip(), phone.strip()] if p)
        line2 = info.strip() if (not addr_l and info) else addr_l
        row1 = shop or offer_l
        y1 = h - bar_h + (int(bar_h * 0.34) if bar_rows == 2 else int(bar_h * 0.32))
        y2 = h - bar_h + int(bar_h * 0.72)
        draw.text((pad, y1), row1, font=bar_font_b, fill=pal["bar_fg"] + (255,), anchor="lm")
        if offer_l and shop:
            ox = pad + bar_font_b.getlength(shop) + 22
            draw.text((ox, y1), offer_l, font=bar_font_b, fill=accent_dark + (255,), anchor="lm")
        elif offer_l and not shop:
            draw.text((pad, y1), offer_l, font=bar_font_b, fill=accent_dark + (255,), anchor="lm")
        ry = y2 if bar_rows == 2 else y1
        if line2:
            draw.text((pad, ry if bar_rows == 2 else y1), line2, font=bar_font,
                      fill=pal["bar_fg"] + (185,), anchor="lm")
        if show_qr:
            qs = int(bar_h * 0.66)
            qx = w - pad - qs
            qy = h - bar_h + (bar_h - qs) // 2
            draw.rounded_rectangle([qx, qy, qx + qs, qy + qs], radius=int(qs * 0.12),
                                   fill=(250, 248, 246, 255), outline=accent + (255,), width=3)
            draw.text((qx + qs / 2, qy + qs / 2), "扫码\n咨询",
                      font=_font(font_style, "regular", int(qs * 0.22)),
                      fill=pal["chip_fg"] + (255,), anchor="mm", align="center")

    # ---------------- 暗角 + 导出
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    final = base.convert("RGB")
    if out.suffix.lower() in (".jpg", ".jpeg"):
        final.save(out, quality=quality, subsampling=0, optimize=True)
    else:
        final.save(out)
    return {"file": str(out.resolve()), "w": w, "h": h, "channel": cfg["channel"],
            "ratio": cfg["ratio"], "layout": layout, "palette": palette}


def _draw_chips(draw, x, y, bullets, font, pal, accent, *, max_x: float,
                on_dark: bool = True, center_x: Optional[float] = None,
                center_right: Optional[float] = None) -> int:
    """一行放不下就换行；深色底上用半透明白胶囊，浅色卡片上用浅色胶囊。

    给了 center_x 就把每一行整体居中（卡片版式用）。
    """
    bg = (255, 255, 255, 228) if on_dark else (accent[0], accent[1], accent[2], 34)
    fg = pal["chip_fg"] + (255,)
    gap = 12
    # 先按宽度分行
    rows: List[List[Tuple[str, int, int]]] = [[]]
    used = 0.0
    for b in bullets:
        w, h = (int(font.getlength(b) + 40), int(font.size + 22))
        if used + w > (max_x - x) and rows[-1]:
            rows.append([])
            used = 0.0
        rows[-1].append((b, w, h))
        used += w + gap
    cy = y
    for row in rows:
        row_w = sum(w for _, w, _ in row) + gap * (len(row) - 1)
        cx = x
        if center_x is not None:
            cx = max(x, center_x - row_w / 2)
            if center_right is not None:
                cx = min(cx, center_right - row_w)
        for b, w, h in row:
            draw.rounded_rectangle([cx, cy, cx + w, cy + h], radius=h // 2, fill=bg)
            draw.text((cx + w / 2, cy + h / 2), b, font=font, fill=fg, anchor="mm")
            cx += w + gap
        cy += (row[0][2] if row else 0) + gap
    return x


def _draw_cta(draw, x, y, cta: str, font, accent, fg, *,
              center_x: Optional[float] = None) -> int:
    w = int(font.getlength(cta) + 56)
    h = int(font.size + 28)
    if center_x is not None:
        x = max(x, center_x - w / 2)
    draw.rounded_rectangle([x, y, x + w, y + h], radius=h // 2, fill=accent + (255,))
    draw.text((x + w / 2, y + h / 2), cta, font=font, fill=fg, anchor="mm")
    return w
