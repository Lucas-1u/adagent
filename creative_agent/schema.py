"""数据契约：上游 Agent → Creative Agent → state["images"]。

这是防止「5 个模块都能跑但组合不起来」的关键：本 Agent 只读上游字段、只写 images 字段。
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

# ---------------------------------------------------------------- 输入

@dataclass
class CustomerInfo:
    """来自「客户发现 Agent」，写入 state["customer_info"]。"""
    shop: str = ""
    city: str = ""
    category: str = ""          # 咖啡 / 手作 / 宠物 / 美甲 ...
    platform: str = ""          # 小红书 / 抖音 / 到店
    note: str = ""

    def describe(self) -> str:
        parts = [f"{self.city}{self.shop}" if self.city else self.shop]
        if self.category:
            parts.append(f"业态：{self.category}")
        if self.platform:
            parts.append(f"主要平台：{self.platform}")
        if self.note:
            parts.append(f"备注：{self.note}")
        return "；".join(p for p in parts if p)


@dataclass
class Analysis:
    """来自「商家分析 Agent」，写入 state["analysis"]。"""
    target_user: str = ""
    problems: List[str] = field(default_factory=list)
    opportunities: List[str] = field(default_factory=list)

    def describe(self) -> str:
        s = [f"目标客群：{self.target_user}"] if self.target_user else []
        if self.problems:
            s.append("当前问题：" + " / ".join(self.problems))
        if self.opportunities:
            s.append("营销机会：" + " / ".join(self.opportunities))
        return "；".join(s)


@dataclass
class Strategy:
    """来自「营销策划 Agent」，写入 state["strategy"]。"""
    theme: str = ""
    channels: List[str] = field(default_factory=list)
    keywords: List[str] = field(default_factory=list)
    offer: str = ""             # 活动优惠 / 价格 / 时间
    address: str = ""           # 门店地址（有则上海报，无则不编造）
    phone: str = ""
    tone: str = ""              # 调性：治愈 / 年轻 / 高级 / 热闹

    def describe(self) -> str:
        s = []
        if self.theme:
            s.append(f"营销主题：{self.theme}")
        if self.offer:
            s.append(f"活动信息：{self.offer}")
        if self.channels:
            s.append("推广渠道：" + "、".join(self.channels))
        if self.keywords:
            s.append("宣传关键词：" + "、".join(self.keywords))
        if self.tone:
            s.append(f"内容调性：{self.tone}")
        if self.address:
            s.append(f"门店地址：{self.address}")
        if self.phone:
            s.append(f"联系电话：{self.phone}")
        return "；".join(s)


def _as(klass, value: Any):
    """上游可能给 dict / 字符串 / None，统一成 dataclass。"""
    if value is None:
        return klass()
    if isinstance(value, klass):
        return value
    if isinstance(value, dict):
        keep = {k: v for k, v in value.items() if k in klass.__dataclass_fields__}
        return klass(**keep)
    if isinstance(value, str):
        first = list(klass.__dataclass_fields__)[0]
        return klass(**{first: value})
    return klass()


def normalize_state(state: Dict[str, Any]) -> Dict[str, Any]:
    """把任意形态的 state 归一化成 (customer, analysis, strategy) 三元组。"""
    return {
        "customer": _as(CustomerInfo, state.get("customer_info")),
        "analysis": _as(Analysis, state.get("analysis")),
        "strategy": _as(Strategy, state.get("strategy")),
    }


# ---------------------------------------------------------------- 输出

@dataclass
class CreativeResult:
    """Creative Agent 的完整产出，序列化后写入 state["images"]。"""
    theme: str = ""
    palette: str = ""
    font_style: str = "sans"
    concepts: List[Dict[str, Any]] = field(default_factory=list)   # LLM 的 3 个创意方向
    posters: List[Dict[str, Any]] = field(default_factory=list)    # 实际落盘的图片素材
    copy: Dict[str, Any] = field(default_factory=dict)             # 配套文案
    video_script: Dict[str, Any] = field(default_factory=dict)     # 短视频脚本
    publish_advice: List[Dict[str, Any]] = field(default_factory=list)
    meta: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)

    def summary(self) -> str:
        lines = [f"主题：{self.theme}（配色 {self.palette}）"]
        for p in self.posters:
            lines.append(f"  · {p['channel']:<14} {p['w']}x{p['h']}  {p['file']}")
        if self.copy.get("title"):
            lines.append(f"文案标题：{self.copy['title']}")
        if self.video_script.get("shots"):
            lines.append(f"视频脚本：{self.video_script.get('duration_s')}s / "
                         f"{len(self.video_script['shots'])} 个镜头")
        lines.append(f"素材引擎：{self.meta.get('image_backend')}"
                     + ("（兜底模式）" if self.meta.get("fallback_used") else ""))
        return "\n".join(lines)


def stamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def load_brief(path: str) -> Dict[str, Any]:
    """读取一个 demo brief（本质就是一份 state 片段）。"""
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def save_json(obj: Any, path: str) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)
