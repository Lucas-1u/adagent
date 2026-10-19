"""
AI Marketing Employee —— 数据契约 State v0.5
=============================================

全项目唯一的权威契约文件。四个 Agent 之间的信息流转，全部以本文件为准。

【三条铁律】
1. 字段名锁死：customer_info / analysis / strategy / images —— 任何人不得改名、不得缺省。
2. 各写各的：每个 Agent 只写自己的字段，绝不碰别人的字段。
3. 变更走流程：契约要改，先找总控；由总控改本文件并同步全组（版本号会升）。

【字段归属】
    user_input    ← 入口写入（用户需求原文），全链路只读
    customer_info ← 客户发现 Agent（队友 1）写
    analysis      ← 商家分析 Agent（队友 2）写
    strategy      ← 营销策划 Agent（队友 3）写
    images        ← 广告素材节点写（v0.5 起由 poster_agent 代码出图：spec → SVG 模板 → PNG）
    report        ← Final 整合层写（v0.2 新增 · 营销方案报告）
    trace         ← 框架层追加（v0.2 新增；v0.4 起含 LLM 调用记录事件——
                    事件类型：node / decision / llm，前端执行流数据源）
    plan          ← Manager 写（v0.3 新增 · 入口裁剪计划）
    control       ← Manager 写（v0.3 新增 · 路由指令）
    retry_counts  ← Manager 写（v0.3 新增 · 重试计数，防死循环）

【用法】
    # 队友：校验自己造的数据是否合格
    from app.state import Analysis
    Analysis.model_validate(my_data)

    # 总控：校验整条链路的状态
    from app.state import validate_state
    errs = validate_state(state)
"""

import operator
from typing import Annotated, Any, TypedDict

from pydantic import BaseModel, ConfigDict, Field

# ---------------------------------------------------------------- 各 Agent 的输出结构


class CustomerInfo(BaseModel):
    """客户发现 Agent 的输出（写入 customer_info）。"""

    model_config = ConfigDict(extra="allow")  # 核心字段之外，允许自由扩展

    shop: str = Field(description="店铺名称，例：XX 拼豆体验馆")
    type: str = Field(description="品类，例：handmade / coffee / dessert")
    platform: str = Field(description="来源平台，例：小红书 / 抖音 / 模拟数据")
    highlights: str = Field(description="店铺特点，一句话")
    potential_need: str = Field(description="潜在营销需求，例：缺少活动推广素材")
    extra: dict[str, Any] = Field(default_factory=dict, description="自由扩展位（可选）")


class Analysis(BaseModel):
    """商家分析 Agent 的输出（写入 analysis）。"""

    model_config = ConfigDict(extra="allow")

    user: str = Field(description="目标用户画像，例：18-25 岁学生群体")
    problems: list[str] = Field(description="当前营销问题，1-3 条")
    suggestions: list[str] = Field(description="改进建议，1-3 条")
    extra: dict[str, Any] = Field(default_factory=dict)


class Strategy(BaseModel):
    """营销策划 Agent 的输出（写入 strategy）。"""

    model_config = ConfigDict(extra="allow")

    theme: str = Field(description="营销主题，例：七夕 DIY 拼豆体验")
    channels: list[str] = Field(description="推广渠道，例：['小红书', '抖音']")
    keywords: list[str] = Field(description="宣传关键词，例：['治愈', '情侣', '手作']")
    copywriting: str = Field(default="", description="配套文案（可选）")
    extra: dict[str, Any] = Field(default_factory=dict)


class Creative(BaseModel):
    """广告生成 Agent 的输出（写入 images）。

    v0.5：素材节点改由 poster_agent 用代码出图，因此补三个可选字段描述"这张图是怎么来的"，
    便于 Manager 质检、Final 报告与前端展示；缺失时（如降级兜底）留空即可。
    """

    model_config = ConfigDict(extra="allow")

    poster: str = Field(description="海报文件路径或 URL")
    copywriting: str = Field(description="海报配套文案")
    video_script: str = Field(default="", description="视频脚本（可选）")
    spec_id: str = Field(default="", description="海报规格指纹（同 spec 必出同一张图）")
    size: str = Field(default="", description="海报尺寸，例：1024x1536")
    motif: str = Field(default="", description="视觉母题，例：beads-heart / latte / wheat")
    extra: dict[str, Any] = Field(default_factory=dict)


class FinalReport(BaseModel):
    """Final 整合层的输出（写入 report）—— Demo 的最终画面。"""

    model_config = ConfigDict(extra="allow")

    title: str = Field(description="报告标题，例：XX 拼豆体验馆 · 营销方案")
    summary: str = Field(description="一句话综述（浓缩全案要点）")
    customer: dict = Field(default_factory=dict, description="客户概况（来自 customer_info）")
    analysis: dict = Field(default_factory=dict, description="分析结论（来自 analysis）")
    strategy: dict = Field(default_factory=dict, description="方案要点（来自 strategy）")
    images: dict = Field(default_factory=dict, description="素材信息（来自 images）")
    poster: dict = Field(default_factory=dict, description="海报成品摘要（路径/主标语/尺寸/母题/规格指纹）")
    outcome: str = Field(default="delivered", description="delivered=有交付物；blocked=本轮无交付物（如无商家）")
    blockers: list = Field(default_factory=list, description="blocked 时的原因清单：[{code,text,hint}]")
    generated_at: str = Field(default="", description="生成时间")


# ---------------------------------------------------------------- 全局状态（LangGraph 使用）


class MarketingState(TypedDict, total=False):
    """LangGraph 全局状态。四个业务字段从空开始，各自被对应 Agent 填充。"""

    user_input: str  # 入口写入，全链路只读
    customer_info: dict  # ← 队友 1 写
    analysis: dict  # ← 队友 2 写
    strategy: dict  # ← 队友 3 写
    images: dict  # ← 队友 4 写
    report: dict  # ← Final 整合层写（v0.2 新增）
    trace: Annotated[list[dict], operator.add]  # ← 框架层追加（v0.2；v0.4 起含 status="llm" 调用记录；reducer 追加）
    plan: dict  # ← Manager 写（v0.3 新增；{"run": [...], "reason": "..."}）
    control: dict  # ← Manager 写（v0.3 新增；{"next": ..., "action": ..., "reason": ...}）
    retry_counts: dict  # ← Manager 写（v0.3 新增；{"analysis": 1}，防死循环）


# ---------------------------------------------------------------- 校验工具

# 字段 → 归属（用于校验与文档）
FIELD_OWNERS: dict[str, str] = {
    "customer_info": "客户发现 Agent（队友 1）",
    "analysis": "商家分析 Agent（队友 2）",
    "strategy": "营销策划 Agent（队友 3）",
    "images": "广告素材节点（poster_agent 代码出图）",
    "report": "Final 整合层（总控）",
}

# 字段 → 对应模型
_FIELD_MODELS = {
    "customer_info": CustomerInfo,
    "analysis": Analysis,
    "strategy": Strategy,
    "images": Creative,
    "report": FinalReport,
}


def validate_state(state: dict) -> list[str]:
    """校验 state 中已填充的字段。返回问题列表（空列表 = 全部合法）。

    空字符串 / 空 dict / None 视为"该节点还没跑"，直接跳过。
    """
    problems: list[str] = []
    for field_name, model in _FIELD_MODELS.items():
        value = state.get(field_name)
        if not value:
            continue
        try:
            model.model_validate(value)
        except Exception as exc:
            problems.append(f"[{field_name}] 不符合契约：{exc}")
    return problems
