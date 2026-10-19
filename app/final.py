"""
Final 整合层 —— 演示的最终画面
==============================

职责：把四字段组装成结构化的《营销方案报告》（写入 report）。
原则：只组装与浓缩，不新创内容 —— 报告中每条信息都能追溯到上游字段。

v0.5：新增 report.poster（海报成品摘要：路径 / 主标语 / 尺寸 / 母题 / 规格指纹），
      前端「交付成果」栏首屏直接读它，不再自己从 strategy 里猜文案。

两层实现（自动选择）：
    基础组装版：纯规则拼接，零外部依赖、永远可用（也是降级路径）
    综述增强版：有 key 时调 LLM 把要点浓缩成更自然的综述；失败自动退回基础版

自测（不需要 key）：python -m app.final
"""

import json
from datetime import datetime

from app import llm_client
from app.state import FinalReport

FIELD = "report"

# 降级兜底（保护壳在 Final 异常时使用；组装逻辑本身无外部依赖，几乎用不上）
FALLBACK = {
    "title": "营销方案（应急版）",
    "summary": "报告组装环节异常，以下为各环节原始产出，供人工核对。",
    "generated_at": "",
}


def run(state: dict) -> dict:
    """节点入口：四字段 → 营销方案报告。"""
    report = _assemble(state)
    # 本轮没有可核查商家 → 不做综述润色（避免把"资料不足"润色成看起来像方案的话）
    if report.get("outcome") == "blocked":
        return {FIELD: report}
    if llm_client.has_api_key():
        try:
            report["summary"] = _polish_summary(state, report["summary"])
        except Exception as exc:  # LLM 失败 → 保留基础版，不影响主流程
            report["note"] = f"综述增强失败（{type(exc).__name__}），已使用基础组装版"
    return {FIELD: report}


def _blockers(customer: dict, images: dict) -> list:
    """blocked 原因清单：每条都来自上游字段，不新创结论。"""
    extra = customer.get("extra") or {}
    errors = [str(e) for e in (extra.get("errors") or [])]
    out = []
    if extra.get("no_match"):
        quota_hit = any("601" in e for e in errors) or "api_quota_exhausted" in errors
        if quota_hit:
            out.append({
                "code": "api_quota",
                "text": "数据接口余额/额度不足（JustOneAPI 601），本轮没有取到任何商家",
                "hint": "充值后重试；演示可直接用「加速回放」看完整案例",
            })
        else:
            out.append({
                "code": "no_match",
                "text": "按当前关键词没有搜到可核查的商家",
                "hint": "换一种说法重试，例如「武汉 江汉路 拼豆手作」",
            })
    if not (images.get("poster") or ""):
        out.append({
            "code": "no_poster",
            "text": "缺少可核查的商家信息，海报没有生成",
            "hint": "",
        })
    return out


def _assemble(state: dict) -> dict:
    """基础组装版：纯规则拼接。"""
    customer = state.get("customer_info") or {}
    analysis = state.get("analysis") or {}
    strategy = state.get("strategy") or {}
    images = state.get("images") or {}

    shop = customer.get("shop") or ""
    why = customer.get("extra") or {}
    blocked = bool(why.get("no_match")) or not shop

    if blocked:
        report = {
            "title": "本轮未找到可核查商家",
            "summary": "本次没有取到可核查的商家信息，因此没有产出营销方案与海报。"
                       "下面列出卡住的原因；重试或改用演示案例都可以。",
            # 无商家时上游仍会给"待确认模板"，这里不当作交付物往上抛
            "customer": customer,
            "analysis": analysis,
            "strategy": {},
            "images": {},
            "poster": {},
            "outcome": "blocked",
            "blockers": _blockers(customer, images),
            "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        }
        FinalReport.model_validate(report)
        return report

    user = analysis.get("user") or "目标用户"
    theme = strategy.get("theme") or "营销活动"
    channels = "、".join(strategy.get("channels") or []) or "待定渠道"
    keywords = "、".join(strategy.get("keywords") or []) or "待定关键词"

    if strategy:
        summary = (
            f"面向{user}，以「{theme}」为主题，"
            f"通过{channels}进行推广，重点传递「{keywords}」。"
        )
    else:
        # 按需裁剪场景：未生成策划与素材部分
        summary = f"面向{user}，本次按需输出客户分析与营销建议（未包含方案策划与素材部分）。"

    report = {
        "title": f"{shop} · 营销方案",
        "summary": summary,
        "customer": customer,
        "analysis": analysis,
        "strategy": strategy,
        "images": images,
        "poster": _poster_block(images),
        "outcome": "delivered",
        "blockers": [],
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
    }
    FinalReport.model_validate(report)  # 自检：报告结构必须符合契约
    return report


def _poster_block(images: dict) -> dict:
    """海报成品摘要：把"交付物"从素材字段里摘出来，供前端首屏直接展示。

    只做搬运，不加新信息 —— slogan/subline 来自 poster_agent 的 spec，其余来自 images。
    """
    images = images or {}
    spec = (images.get("extra") or {}).get("spec") or {}
    return {
        "path": images.get("poster") or "",
        "copywriting": images.get("copywriting") or "",
        "slogan": spec.get("headline") or "",
        "subline": spec.get("subline") or "",
        "size": images.get("size") or spec.get("size") or "",
        "motif": images.get("motif") or "",
        "layout": spec.get("layout") or "",
        "scene_cues": list(spec.get("cues") or []),
        "spec_id": images.get("spec_id") or "",
    }


def _polish_summary(state: dict, base_summary: str) -> str:
    """综述增强版：LLM 浓缩（只做表达优化，不新增内容）。"""
    payload = {
        "customer": state.get("customer_info") or {},
        "analysis": state.get("analysis") or {},
        "strategy": state.get("strategy") or {},
    }
    prompt = (
        "请把以下营销方案要点浓缩为 1-2 句自然、简洁的中文综述（用作方案汇报的开头）。\n"
        "要求：只使用给定数据中的信息，不要新增任何内容。\n"
        '只输出 JSON：{"summary": "..."}\n\n'
        f"数据：{json.dumps(payload, ensure_ascii=False)}"
    )
    data = llm_client.chat_json(
        prompt, system="你是营销方案汇报助手，只输出 JSON。", purpose="summary"
    )
    summary = str(data.get("summary", "")).strip()
    return summary or base_summary


if __name__ == "__main__":
    demo_state = {
        "customer_info": {"shop": "XX 拼豆体验馆", "type": "handmade"},
        "analysis": {"user": "18-25 岁年轻女性、学生群体", "problems": ["缺少节日营销活动"]},
        "strategy": {
            "theme": "七夕 DIY 拼豆体验",
            "channels": ["小红书", "抖音"],
            "keywords": ["治愈", "情侣", "手作"],
        },
        "images": {"poster": "outputs/poster_qiqi_mock.png", "copywriting": "七夕限定"},
    }
    out = run(demo_state)
    print(json.dumps(out, ensure_ascii=False, indent=2))
