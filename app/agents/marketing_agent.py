"""营销策划 Agent（队友 3）

职责：读取商家分析结果，生成结构化、可执行的营销策略。
遵守团队接口契约：只读取上游字段，只返回自己的 strategy 字段。

在项目根目录运行：
    python -m app.agents.marketing_agent
"""

from __future__ import annotations

import json
from typing import Any

from app import llm_client
from app.state import Strategy


SYSTEM_PROMPT = """你是一名务实的本地商家营销策划顾问，负责把商家分析结论转化为可以执行的营销方案。
要求：
1. 只根据输入中提供的信息策划，不得编造门店价格、库存、销量、真实评价、优惠金额或已发生的事实。
2. 如果价格、预算、日期、优惠力度等信息没有提供，将其写成“需商家确认”或提出可选建议，不得当成事实。
3. 方案要具体、低成本、可执行，符合商家品类和目标人群；避免空话。
4. 渠道以 2-3 个为宜，说明每个渠道发什么内容、怎样引导用户行动。
5. 文案自然、有吸引力，不夸大宣传，不承诺无法验证的效果。
6. 只返回合法 JSON 对象，不要 Markdown 代码围栏或额外解释。
"""


def _as_dict(value: Any) -> dict[str, Any]:
    """兼容 dict、Pydantic 模型和空值。"""
    if isinstance(value, dict):
        return value
    if hasattr(value, "model_dump"):
        try:
            result = value.model_dump()
            return result if isinstance(result, dict) else {}
        except Exception:
            return {}
    return {}


def _as_list(value: Any, limit: int = 5) -> list[str]:
    """把上游字段整理成字符串列表，防止 None 等异常值进入提示词。"""
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    result = []
    for item in value:
        if item is None:
            continue
        text = str(item).strip()
        if text:
            result.append(text)
        if len(result) >= limit:
            break
    return result


def _string_list(value: Any, default: list[str], limit: int = 5) -> list[str]:
    """清洗模型返回的列表字段；模型偶尔返回字符串时也尽量兼容。"""
    values = _as_list(value, limit=limit)
    return values or default.copy()


def _fallback_strategy(state: dict[str, Any], reason: str = "") -> dict[str, Any]:
    """LLM 不可用时的确定性兜底方案，保障黑客松 Demo 能继续运行。"""
    customer = _as_dict(state.get("customer_info"))
    analysis = _as_dict(state.get("analysis"))
    user_input = str(state.get("user_input") or "").strip()

    shop = str(customer.get("shop") or "目标商家").strip()
    shop_type = str(customer.get("type") or "本地生活商家").strip().lower()
    audience = str(analysis.get("user") or "对该品类感兴趣的本地消费者").strip()
    problems = _as_list(analysis.get("problems"), limit=3)
    suggestions = _as_list(analysis.get("suggestions"), limit=3)

    if any(word in shop_type for word in ("handmade", "拼豆", "手作", "diy")):
        theme = f"{shop}｜把喜欢做成作品：手作体验打卡计划"
        keywords = ["手作体验", "DIY", "治愈时刻", "作品分享", "周末去哪儿"]
        hook = "亲手完成一件小作品，把普通的一天变成值得分享的回忆。"
    elif any(word in shop_type for word in ("coffee", "咖啡")):
        theme = f"{shop}｜一杯咖啡的日常打卡计划"
        keywords = ["咖啡日常", "店铺打卡", "下午放松", "城市生活", "周末探店"]
        hook = "给忙碌的日常留一点咖啡时间，也欢迎分享你在店里的小片刻。"
    elif any(word in shop_type for word in ("dessert", "甜品", "蛋糕")):
        theme = f"{shop}｜今日甜蜜体验：甜品打卡计划"
        keywords = ["甜品分享", "探店打卡", "下午茶", "生活仪式感", "本地美食"]
        hook = "用一份甜品给今天加点仪式感，欢迎记录你的甜蜜时刻。"
    else:
        theme = f"{shop}｜真实体验与用户分享主题活动"
        keywords = ["本地探店", "真实体验", "到店打卡", "生活方式", "用户分享"]
        hook = "来店里体验一种新的生活灵感，记录并分享属于你的真实感受。"

    channels = ["小红书", "抖音", "微信社群"]
    copywriting = f"{hook}\n📍{shop}\n把你的体验拍下来、分享出来，也欢迎留言告诉我们你最感兴趣的内容。具体活动与参与方式以门店实际安排为准。"

    extra: dict[str, Any] = {
        "generation_mode": "fallback",
        "fallback_reason": reason or "未调用大模型，使用内置兜底策略",
        "target_audience": audience,
        "campaign_goal": "提升活动曝光、内容互动和到店咨询；实际转化效果需根据运营数据评估",
        "key_pain_points": problems,
        "strategy_logic": "针对上游分析中的用户画像和营销问题，设计可拍摄、可分享、可引导到店的内容活动",
        "channel_plans": [
            {
                "channel": "小红书",
                "content": "发布图文/短视频，展示体验过程、环境或产品细节，并用清晰标题说明适合什么人",
                "cta": "邀请用户收藏、评论感兴趣的体验内容，或私信咨询参与方式",
            },
            {
                "channel": "抖音",
                "content": "制作 15-30 秒短视频，按“体验前—体验过程—完成效果”呈现",
                "cta": "引导用户查看主页信息或咨询到店方式",
            },
            {
                "channel": "微信社群",
                "content": "发布简短活动介绍、预约方式及用户作品/体验反馈（需获得授权）",
                "cta": "引导有兴趣的用户咨询或预约",
            },
        ],
        "execution_steps": [
            "确认本次活动主题、可接待人数、价格、日期和参与规则；未确认前不对外承诺",
            "拍摄 3-5 组真实产品/环境/体验过程素材",
            "按渠道特点制作图文和短视频，先发布一轮预热内容",
            "根据评论和私信问题补充说明，再发布活动提醒",
            "记录曝光、互动、咨询和实际到店数据，复盘哪些内容更有效",
        ],
        "timeline": [
            {"阶段": "准备期", "任务": "确认活动规则并准备真实素材"},
            {"阶段": "预热期", "任务": "发布主题介绍和体验过程内容"},
            {"阶段": "活动期", "任务": "持续发布内容并回应用户咨询"},
            {"阶段": "复盘期", "任务": "统计互动、咨询和到店情况，整理改进点"},
        ],
        "budget_notes": "优先使用门店已有场景和手机拍摄，付费投放预算需根据商家实际情况另行确认。",
        "metrics": ["内容曝光/播放量", "收藏与评论量", "私信/活动咨询量", "预约或到店人数"],
        "risk_controls": [
            "活动日期、价格、优惠、库存和预约规则由商家确认后再发布",
            "使用顾客照片、作品或评价前取得授权",
            "不使用虚假评价、虚构销量或无法验证的效果承诺",
        ],
        "source_user_input": user_input,
        "upstream_suggestions": suggestions,
    }

    return Strategy.model_validate(
        {
            "theme": theme,
            "channels": channels,
            "keywords": keywords,
            "copywriting": copywriting,
            "extra": extra,
        }
    ).model_dump()


#: 模块级兜底（供框架保护壳 guard 降级使用）。
#: 演示场景版本：拼豆体验馆 / 年轻女性 / 七夕；节点自身失败时不返回它——异常上抛，由 guard 决策。
FALLBACK: dict[str, Any] = _fallback_strategy(
    {
        "user_input": "寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广",
        "customer_info": {
            "shop": "七七拼豆体验馆",
            "type": "handmade",
            "platform": "小红书",
            "highlights": "面向年轻女性的手作体验店，以作品展示内容为主",
            "potential_need": "缺少活动推广素材",
        },
        "analysis": {
            "user": "18-25 岁年轻女性、学生群体",
            "problems": ["宣传素材质量普通", "缺少节日营销活动"],
            "suggestions": ["制作年轻化宣传方案", "策划七夕情侣主题活动"],
        },
    }
)


def _build_prompt(state: dict[str, Any]) -> str:
    customer = _as_dict(state.get("customer_info"))
    analysis = _as_dict(state.get("analysis"))
    user_input = str(state.get("user_input") or "").strip()
    control = _as_dict(state.get("control"))
    feedback = str(control.get("reason") or "").strip()

    context = {
        "用户原始需求": user_input,
        "商家信息": customer or "上游未提供商家信息",
        "商家分析结果": analysis or "上游尚未提供分析结果，请在信息不足处标注待确认",
        "上次质检意见": feedback or "无",
    }

    return f"""请根据下面的上下文，制定一份适合该商家的营销策划方案。

上下文：
{json.dumps(context, ensure_ascii=False, indent=2)}

请严格返回 JSON，字段结构如下：
{{
  "theme": "一句明确、有记忆点的营销主题",
  "channels": ["2-3个最适合的推广渠道"],
  "keywords": ["3-6个宣传关键词"],
  "copywriting": "一段可直接修改后发布的营销文案",
  "extra": {{
    "generation_mode": "llm",
    "target_audience": "目标用户画像",
    "campaign_goal": "本次活动目标",
    "key_message": "核心传播信息：固定 3 条，用「；」分隔，每条写成「短标签，一句话说明」，标签不超过 10 个字（例：一张图五种烫法，同一款豆子换成五种表面效果）",
    "core_offer": "参与价值或活动机制；未确认的优惠要写明需商家确认",
    "channel_plans": [
      {{"channel": "渠道名", "content_format": "图文/短视频等", "content_idea": "具体内容", "cta": "用户下一步行动"}}
    ],
    "execution_steps": ["按先后顺序排列的具体执行步骤"],
    "timeline": [{{"phase": "阶段", "task": "任务"}}],
    "budget_notes": "预算建议；不要虚构金额",
    "metrics": ["可观测的效果指标"],
    "risk_controls": ["执行风险与注意事项"]
  }}
}}

策划要求：
- 用 analysis.user 理解目标人群；结合 analysis.problems 和 analysis.suggestions 对症设计。
- 不能只给主题和口号，要让广告素材生成 Agent 能根据主题、渠道和关键词继续制作素材。
- 至少包含“发什么内容、发到哪里、引导用户做什么、如何复盘”。
- 不知道的信息标注“需商家确认”，不要编造具体价格、日期、优惠额度、销量或用户评价。
- extra 必须是 JSON 对象；所有列表字段都返回数组。
"""


def run(state: dict[str, Any]) -> dict[str, Any]:
    """执行营销策划节点，只返回 strategy 字段。

    总控接入说明（2026-10-18）：
      - 不做上下文兜底：LLM 失败 / 返回不合法时异常原样上抛，
        由框架保护壳（app/guard.py）降级并在 trace 留痕——
        避免"看起来成功、实际是兜底数据"在演示时最难被发现。
      - 无 API Key 的离线环境仍走短路兜底（供冒烟测试与本地演示使用）。
    """
    state = state or {}

    # 离线环境（无 key）：直接给确定性方案，避免冒烟测试里无谓的重试等待。
    if not llm_client.has_api_key():
        return {"strategy": _fallback_strategy(state, "未配置 DEEPSEEK_API_KEY")}

    prompt = _build_prompt(state)
    raw = llm_client.chat_json(
        prompt,
        system=SYSTEM_PROMPT,
        temperature=0.7,
        purpose="marketing_strategy",
    )
    if not isinstance(raw, dict):
        raise ValueError("模型返回结果不是 JSON 对象")

    # 对模型输出做轻量清洗，避免列表/文案字段的格式波动破坏契约。
    extra = raw.get("extra")
    if not isinstance(extra, dict):
        extra = {}
    extra["generation_mode"] = "llm"
    extra.setdefault("target_audience", _as_dict(state.get("analysis")).get("user", "待确认"))
    extra.setdefault("metrics", ["内容曝光/播放量", "收藏与评论量", "咨询量", "预约或到店人数"])
    extra.setdefault("risk_controls", ["价格、日期、优惠及参与规则需商家确认后再发布"])

    payload = {
        "theme": str(raw.get("theme") or "真实体验主题营销活动").strip(),
        "channels": _string_list(raw.get("channels"), ["小红书", "抖音"], limit=3),
        "keywords": _string_list(raw.get("keywords"), ["真实体验", "用户分享", "本地探店"], limit=6),
        "copywriting": str(raw.get("copywriting") or "欢迎到店体验，具体活动信息请咨询商家。\n").strip(),
        "extra": extra,
    }

    strategy = Strategy.model_validate(payload).model_dump()
    return {"strategy": strategy}


def _demo_state() -> dict[str, Any]:
    """本地自测样例；真实流程运行时由编排层传入 state。"""
    return {
        "user_input": "帮一家面向年轻人的拼豆手作店策划营销活动",
        "customer_info": {
            "shop": "XX 拼豆体验馆",
            "type": "handmade",
            "platform": "模拟数据",
            "highlights": "面向年轻用户的手作体验店",
            "potential_need": "需要活动推广素材",
        },
        "analysis": {
            "user": "18-25 岁学生和年轻女性，喜欢 DIY、拍照和分享体验",
            "problems": ["缺少有记忆点的主题活动", "线上内容对体验过程展示不足"],
            "suggestions": ["设计可拍照分享的手作体验主题", "用短视频展示从材料到成品的过程"],
        },
    }


if __name__ == "__main__":
    result = run(_demo_state())
    print(json.dumps(result, ensure_ascii=False, indent=2))
