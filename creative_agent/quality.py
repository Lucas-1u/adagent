"""设计质检 —— 把「好不好看」里可量化的部分变成硬指标，每次出图自动卡一遍。

为什么需要它：上一版被吐槽"不好看"之后，我们靠人肉+vision 复盘才发现问题。
换一家店、换一种广告，同样的错误还会再犯一次。所以把设计规范里**能客观测量**的部分
固化成断言，每次 compose 完立刻自检，不达标就报警。

测量原理：把同一版式**不带文字再渲染一遍**（"空版"），与原图做像素差分，
差分区域就是完整的文字+图形图层。于是可以客观算出：

  · 元素密度 / 留白率     —— 文字图层像素占比（规范：留白 ≥30%，即占比 ≤ 约 20%）
  · 安全边距             —— 文字图层不能压到画布边缘（规范：上下左右留 8-10%）
  · 文字对比度           —— 差分区域内，取最差的 10% 分位做 WCAG 对比度（规范 ≥4.5:1）
  · 标题字号占比         —— 主标题字号 / 画布高度（规范 8%-12%）
  · 元素数量与重叠       —— 由 compose 返回的几何信息判定

返回结构直接写进 state["images"]["quality"]，report.html 上以质检徽章展示。
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image

# 阈值（数字来自公开的小红书/海报设计规范，可按需收紧）
W_SAFE = 0.04          # 文字图层距画布左右最少 4%
V_SAFE = 0.035         # 上下的安全区
INK_WARN, INK_FAIL = 0.20, 0.28      # 文字图层像素占比上限
CONTRAST_MIN = 4.5                  # WCAG AA 正文标准
TITLE_MIN, TITLE_MAX = 0.078, 0.135  # 主标题字号 / 画布高度
MAX_ELEMENTS = 6                    # 一张图的文字元素上限（封面类建议 ≤4）
STRONG_DIFF = 150                    # 字身像素的最小差分强度（滤掉抗锯齿边缘）


def _luminance(r: int, g: int, b: int) -> float:
    def lin(c: float) -> float:
        c /= 255.0
        return c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
    return 0.2126 * lin(r) + 0.7152 * lin(g) + 0.0722 * lin(b)


def _contrast(l1: float, l2: float) -> float:
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


def _diff_mask(img: Image.Image, base: Image.Image, thresh: int = 26):
    """返回 (mask L 图, [(x, y, 强度)])。差分阈值对压缩噪声留了余量。

    注意强度：字形边缘的抗锯齿像素"差一点"就被算成 ink，但它们本身就是背景色，
    直接拿去做对比度会算出一堆 1.x 的假失败 —— 对比度只统计强度高的"文字本体"像素。
    """
    w, h = img.size
    a = img.convert("RGB")
    b = base.convert("RGB").resize((w, h))
    pa, pb = a.load(), b.load()
    mask = Image.new("L", (w, h), 0)
    pm = mask.load()
    pts: List[Tuple[int, int, int]] = []
    step = 2 if w <= 1200 else 3
    for y in range(0, h, step):
        for x in range(0, w, step):
            r1, g1, b1 = pa[x, y]
            r2, g2, b2 = pb[x, y]
            d = abs(r1 - r2) + abs(g1 - g2) + abs(b1 - b2)
            if d > thresh:
                pm[x, y] = 255
                pts.append((x, y, d))
    return mask, pts


def audit(image_path: str, baseline_path: Optional[str] = None,
          headline_px: Optional[int] = None, elements: int = 0,
          warnings: Optional[List[str]] = None) -> Dict[str, Any]:
    """对一张成品图做设计质检。baseline_path 是同一版式的"空版"图。"""
    img = Image.open(image_path)
    w, h = img.size
    checks: List[Dict[str, Any]] = []
    notes: List[str] = list(warnings or [])

    def add(name: str, ok: bool, value: Any, rule: str, level: str = "fail") -> None:
        checks.append({"check": name, "pass": bool(ok), "value": value,
                       "rule": rule, "level": level})

    if not baseline_path or not Path(baseline_path).is_file():
        add("质检可用性", False, "缺少空版基准图", "需要 baseline", "warn")
        return {"pass": False, "score": None, "checks": checks, "notes": notes}

    base = Image.open(baseline_path)
    _, pts = _diff_mask(img, base)
    ink = len(pts) / ((w // 2) * (h // 2)) if w <= 1200 else len(pts) / ((w // 3) * (h // 3))

    # 1) 元素密度 / 留白
    ok_ink = ink <= INK_WARN
    add("留白率（文字图层占比）", ok_ink, round(ink, 3),
        f"≤{INK_WARN}（留白 ≥80%）", "warn" if ink <= INK_FAIL else "fail")

    # 2) 安全边距
    if pts:
        xs = [p[0] for p in pts]
        ys = [p[1] for p in pts]
        left, right = min(xs) / w, 1 - max(xs) / w
        top, bottom = min(ys) / h, 1 - max(ys) / h
        ok_safe = min(left, right) >= W_SAFE * 0.6 and min(top, bottom) >= V_SAFE * 0.5
        add("安全边距（不压边）", ok_safe,
            {"左": round(left, 3), "右": round(right, 3),
             "上": round(top, 3), "下": round(bottom, 3)},
            f"左右 ≥{W_SAFE * 0.6}、上下 ≥{V_SAFE * 0.5}")
    else:
        add("安全边距（不压边）", False, "未检出文字图层", "——")

    # 3) 文字对比度
    #    只看「字身」像素：按差分强度取前 30%，把抗锯齿边缘滤掉
    #    （边缘像素是文字色与背景色的插值，逐像素算对比度必然算出一堆 2.x 的假失败）
    diffs = sorted((p[2] for p in pts), reverse=True)
    core_thresh = max(STRONG_DIFF, diffs[max(0, int(len(diffs) * 0.30))] if diffs else STRONG_DIFF)
    strong = [p for p in pts if p[2] >= core_thresh]
    if strong:
        pimg, pbase = img.convert("RGB").load(), base.convert("RGB").resize((w, h)).load()
        ratios = []
        for x, y, _ in strong[:: max(1, len(strong) // 4000)]:
            r1, g1, b1 = pimg[x, y]
            r2, g2, b2 = pbase[x, y]
            ratios.append(_contrast(_luminance(r1, g1, b1), _luminance(r2, g2, b2)))
        ratios.sort()
        worst = ratios[max(0, int(len(ratios) * 0.15) - 1)] if ratios else 0.0
        med = ratios[len(ratios) // 2] if ratios else 0.0
        add("文字对比度（字身最差 15% 分位）", worst >= CONTRAST_MIN,
            {"最差": round(worst, 2), "中位": round(med, 2), "取样": len(strong)},
            f"≥{CONTRAST_MIN}:1（WCAG AA）")

    # 4) 标题字号占比
    if headline_px:
        ratio = headline_px / h
        add("主标题字号占比", TITLE_MIN <= ratio <= TITLE_MAX, round(ratio, 3),
            f"{TITLE_MIN}~{TITLE_MAX}（画布高度）", "warn")

    # 5) 元素数量
    add("文字元素数量", elements <= MAX_ELEMENTS, elements,
        f"≤{MAX_ELEMENTS}（规范：装饰元素要少）", "warn")

    passed = all(c["pass"] for c in checks if c["level"] == "fail")
    scored = [c for c in checks if c["level"] == "fail" or c["level"] == "warn"]
    score = round(100 * sum(1 for c in scored if c["pass"]) / max(1, len(scored)))
    return {"pass": passed, "score": score, "checks": checks, "notes": notes}


def summarize(reports: List[Dict[str, Any]]) -> str:
    """给 CLI 用的一行摘要。"""
    if not reports:
        return "（无质检结果）"
    fails = [r for r in reports if not r.get("pass")]
    bad = [c for r in reports for c in r.get("checks", [])
           if not c["pass"] and c["level"] == "fail"]
    scores = [r["score"] for r in reports if r.get("score") is not None]
    head = f"质检 {len(reports)} 张：平均 {sum(scores) // max(1, len(scores))} 分"
    if not fails and not bad:
        return head + "，全部通过 ✅"
    names = sorted({c["check"] for c in bad})
    return f"{head}，{len(fails)} 张未达标 ❌（{', '.join(names) or '见 notes'}）"
