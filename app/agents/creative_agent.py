"""广告素材 Agent —— 队友 4 的 creative_agent 接入层（替代原 poster_agent 代码出图）。

真实实现：项目根 `creative_agent/`（队友 4 交付 v0.2）
  · 视觉与文字解耦：底图只出画面（AI 或程序化），中文由 Pillow 设计引擎排版
    → 改价格/地址不用重出图，中文永不糊字
  · 3 个创意 × 5 种版式 = 15 张成品素材（一张底图自动适配多尺寸）
  · 设计质检 + 自动修复（留白率 / 安全边距 / WCAG 对比度 / 标题字号 / 元素数）
  · 三级降级：LLM 失败→规则创意库；文生图失败→本地程序化底图；无任何 key 也能出完整素材包

本文件职责（适配层，不重复实现业务逻辑）：
  1. 把项目 v0.5 的 state 字段映射成 creative_agent 的输入契约（字段名不同）；
  2. 把 CreativeResult 映射回 state["images"]（Creative 契约：poster / copywriting / extra…）；
  3. 保留 FALLBACK 供保护壳降级；异常原样上抛（由 guard 记录 trace）。
"""

from __future__ import annotations

import os
import time
from pathlib import Path

from app.state import Creative

FIELD = "images"

ROOT = Path(__file__).resolve().parent.parent.parent

#: 降级兜底（基本用不到：creative_agent 自身有三级降级，仍保留以符合框架约定）
FALLBACK: dict = {"poster": "", "copywriting": "素材暂不可用"}

#: 主视觉优先版式（小红书封面优先，其次竖版海报/方图/横版）
HERO_LAYOUTS = ("cover_hero", "split_top", "poster_tall", "center_bold", "magazine")


def _rel(path: str) -> str:
    """绝对路径 → 相对项目根（前端走 /outputs 静态服务）。"""
    try:
        return Path(path).resolve().relative_to(ROOT).as_posix()
    except Exception:
        return str(path).replace("\\", "/")


def _usable(state: dict) -> bool:
    """没有可核查商家时不出素材（与海报节点一致：宁可空态，也不印一张「未发现商家」）。"""
    info = state.get("customer_info") or {}
    if (info.get("extra") or {}).get("no_match"):
        return False
    shop = str(info.get("shop") or "")
    return bool(shop) and "未发现" not in shop and "未确定" not in shop


def _split_info(highlights: str) -> tuple[str, str]:
    """从 highlights 里按行抠「地址」「营业时间」（客户发现节点会写成独立行）。"""
    address = hours = ""
    for line in str(highlights or "").splitlines():
        text = line.strip()
        if text.startswith("地址"):
            address = text.split("：", 1)[-1].strip()
        elif text.startswith("营业时间"):
            hours = text.split("：", 1)[-1].strip()
    return address, hours


def _adapt(state: dict) -> dict:
    """v0.5 state → creative_agent 输入契约（字段名做显式映射，不新增事实）。"""
    info = state.get("customer_info") or {}
    analysis = state.get("analysis") or {}
    strategy = state.get("strategy") or {}
    address, hours = _split_info(info.get("highlights"))

    note = "；".join(
        x
        for x in (
            str(info.get("highlights") or "").replace("\n", "；"),
            str(info.get("potential_need") or ""),
        )
        if x
    )

    return {
        "customer_info": {
            "shop": info.get("shop") or "",
            "city": "",
            "category": info.get("type") or "",
            "platform": info.get("platform") or "",
            "note": note[:300],
        },
        "analysis": {
            "target_user": analysis.get("user") or "",
            "problems": list(analysis.get("problems") or []),
            "opportunities": list(analysis.get("suggestions") or []),
        },
        "strategy": {
            "theme": strategy.get("theme") or "",
            "channels": list(strategy.get("channels") or []),
            "keywords": list(strategy.get("keywords") or []),
            # 只用真实提供的信息：不编造价格/优惠（营业时间作为可核实的时间信息）
            "offer": f"营业时间 {hours}" if hours else "",
            "address": address,
            "phone": "",
            "tone": "",
        },
    }


def run(state: dict) -> dict:
    """节点入口：state → 整套广告素材（多版式图 + 配套文案 + 短视频脚本 + 投放建议）。"""
    if not _usable(state):
        raise ValueError("缺少可核查商家信息，本轮跳过素材生成")

    from creative_agent import CreativeAgent  # 延迟导入：避免拖慢服务启动

    payload = _adapt(state)
    out_dir = ROOT / "outputs" / "creative" / f"run_{time.strftime('%Y%m%d-%H%M%S')}"

    result = CreativeAgent(
        # 默认用本地程序化底图（快、稳、可复现）；配了图像 API 时可用
        # CREATIVE_IMAGE_BACKEND=xai|codex|openai_compat 切到真实文生图
        image_backend=os.environ.get("CREATIVE_IMAGE_BACKEND", "mock").strip().lower()
    ).run(payload, out_dir=str(out_dir))
    data = result.to_dict()

    posters = [p for p in (data.get("posters") or []) if p.get("file")]
    if not posters:
        raise RuntimeError("素材生成未产出任何图片")

    hero = next((p for p in posters if p.get("layout") in HERO_LAYOUTS), posters[0])
    copy = data.get("copy") or {}
    meta = data.get("meta") or {}

    # 相对路径（前端 /outputs 静态服务可直接访问）
    posters_rel = [{**p, "file": _rel(p["file"])} for p in posters]
    hero_rel = _rel(hero["file"])

    extra = {
        "theme": data.get("theme", ""),
        "palette": data.get("palette", ""),
        "font_style": data.get("font_style", ""),
        "concepts": data.get("concepts") or [],
        "posters": posters_rel,
        "copy": copy,
        "video_script": data.get("video_script") or {},
        "publish_advice": data.get("publish_advice") or [],
        "meta": meta,
        # 兼容 Final 报表 / 前端已有的海报摘要结构（slogan / subline / size / layout）
        "spec": {
            "headline": hero.get("headline") or str(copy.get("title") or ""),
            "subline": hero.get("subheadline") or "",
            "size": f'{hero.get("w")}x{hero.get("h")}',
            "layout": hero.get("layout", ""),
            "cues": [],
        },
    }

    data_out = {
        "poster": hero_rel,
        "copywriting": str(copy.get("title") or data.get("theme") or "").strip(),
        "video_script": "",
        "spec_id": out_dir.name,
        "size": extra["spec"]["size"],
        "motif": hero.get("layout", ""),
        "extra": extra,
    }
    Creative.model_validate(data_out)  # 自检

    detail = {
        "materials": len(posters),
        "layouts": len({p.get("layout") for p in posters}),
        "backend": meta.get("image_backend"),
        "brief_source": meta.get("brief_source"),
        "fallback_used": meta.get("fallback_used"),
        "quality": meta.get("quality"),
        "elapsed": meta.get("elapsed_s"),
        "headline": extra["spec"]["headline"],
    }
    return {FIELD: data_out, "_trace_detail": detail}


if __name__ == "__main__":
    import json

    demo = {
        "customer_info": {
            "shop": "七七拼豆体验馆",
            "type": "handmade",
            "platform": "小红书",
            "highlights": "地址：武汉市江汉区江汉路 118 号\n营业时间：10:00-22:00\n面向年轻女性的手作体验店",
            "potential_need": "缺少活动推广素材",
        },
        "analysis": {
            "user": "18-25 岁年轻女性、学生群体",
            "problems": ["宣传素材质量普通", "缺少节日营销活动"],
            "suggestions": ["制作年轻化宣传方案", "策划七夕情侣主题活动"],
        },
        "strategy": {
            "theme": "七夕双人拼豆体验：把喜欢拼成一对",
            "channels": ["小红书", "抖音"],
            "keywords": ["手作体验", "七夕", "情侣"],
        },
    }
    print(json.dumps(run(demo), ensure_ascii=False)[:800])
