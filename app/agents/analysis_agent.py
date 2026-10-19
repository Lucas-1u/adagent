"""商家分析 Agent —— 契约 v0.4 交付版。

对应《队友协作指南 v0.4》第 5 节的"四步走"：
    ① 读上游字段 customer_info（缺失字段用「未提供」兜底）
    ② 构造 prompt
    ③ llm_client.chat_json(..., purpose="analysis")
    ④ Analysis.model_validate(data) 校验类型，再按契约业务规则查条数与空内容

【本文件刻意不做的事，以及为什么】

1. 不自己实现模型客户端、重试、退避、JSON 解析。
   → 全部交给总控的 app/llm_client.py（铁律 3）。它内部已有重试与 JSON 容错，
     我在外面再包一层只会让调用次数翻倍、延迟变长，还会绕过 v0.4 的 LLM 调用记录。

2. 不 try/except 吞掉异常。
   → 节点保护壳负责降级与执行记录。如果我在 run() 里把失败悄悄换成空结果，
     框架就记不到"这次真的失败了"，演示时会出现"看起来成功、其实全是空字段"，
     那比直接报错危险得多。所以这里让异常原样向上抛。

3. 不读 state 里除 customer_info 以外的任何字段。
   → 铁律 1：只写自己的 analysis，也只读自己该读的 customer_info。

4. 保留模块级 FALLBACK，但 run() 永远不返回它。
   → 它是演示场景（拼豆体验馆 / 年轻女性 / 七夕）的示例结构，
     给框架保护壳和演示脚本用，同时充当"契约输出长什么样"的活文档。
     用它兜底就等于把失败伪装成成功，所以 run() 里没有它的位置。
"""

from __future__ import annotations

from typing import Any

from .. import llm_client
from ..state import Analysis

from ._normalize import normalize_keys
from .prompts import (
    DEFAULT_FIELD_VALUE,
    FIELD_LABELS,
    FIELD_ORDER,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_prompt,
)

#: 模型偶尔用中文键名，这里做一次键名归一（只换键，不改内容）
ANALYSIS_ALIASES: dict[str, str] = {
    "用户": "user",
    "用户画像": "user",
    "目标用户": "user",
    "核心用户": "user",
    "问题": "problems",
    "问题点": "problems",
    "痛点": "problems",
    "关键问题": "problems",
    "分析问题": "problems",
    "建议": "suggestions",
    "优化建议": "suggestions",
    "改进建议": "suggestions",
    "营销建议": "suggestions",
    "行动建议": "suggestions",
    "扩展": "extra",
    "其他": "extra",
}

#: 本 Agent 的版本号（与契约版本 v0.4 对应）
AGENT_VERSION = "0.4.0"

#: problems / suggestions 的条数要求（契约 v0.4：1-3 条）
MIN_LIST_ITEMS = 1
MAX_LIST_ITEMS = 3


class AnalysisRuleError(ValueError):
    """模型输出通过了 Pydantic 类型校验，但违反了业务规则。

    典型情况：problems 返回 0 条或 4 条、某一条是纯空白字符串。

    继承 ValueError 的原因：上层既可以只 catch 这个专用类型做精细化处理，
    也可以和其他"参数/数据不合法"的情况一起按 ValueError 统一兜住。
    """


def check_business_rules(analysis: Analysis) -> None:
    """校验"类型对、但内容不合规"的部分（契约 v0.4 的业务规则）。

    为什么 Pydantic 之后还要单独查一遍？
        Analysis 只保证 user 是 str、problems / suggestions 是 list[str]。
        它不管条数，也不管某一条是不是空白字符串。于是会出现两种
        "能过契约校验、但下游根本没法用"的结果：
            problems = []          向下游策划 Agent 提不出任何可回应的问题
            problems = ["   "]     条数够，内容却是空的
        这两种情况如果放过去，下游会拿到一份"看起来有结构、实际没信息"的分析，
        比直接报错更难查。

    为什么抛异常而不是自动截断 / 补空 / 返回 FALLBACK？
        静默修复会让"模型这次输出不合格"这件事从执行记录里消失。
        框架的保护壳需要看见真实失败，才能做降级和记录；
        用 FALLBACK 顶替则等于把失败伪装成成功，演示时最难被发现。

    参数用已校验的 Analysis 对象（而不是原始 dict）：
        items 的 str 类型已由 Pydantic 保证，这里只关心"条数"和"是否空白"。
    """
    errors: list[str] = []

    for field_name, label in (("problems", "营销问题"), ("suggestions", "改进建议")):
        items = getattr(analysis, field_name)
        count = len(items)

        if not (MIN_LIST_ITEMS <= count <= MAX_LIST_ITEMS):
            errors.append(
                f"{field_name}（{label}）应有 {MIN_LIST_ITEMS}-{MAX_LIST_ITEMS} 条，实际 {count} 条"
            )

        for index, item in enumerate(items):
            if not item.strip():
                errors.append(
                    f"{field_name}[{index}]（{label}第 {index + 1} 条）是空白字符串，没有实际内容"
                )

    if errors:
        raise AnalysisRuleError(
            "模型返回的 analysis 不符合业务规则：\n"
            + "\n".join(f"  - {msg}" for msg in errors)
            + "\n注意：这不是类型错误（类型已由 Analysis 校验通过），而是内容不合格。"
            "本节点不做静默修复，也不返回 FALLBACK，请由框架保护壳按失败处理。"
        )


# ----------------------------------------------------------------------
# 演示场景常量
# ----------------------------------------------------------------------
#: 与团队演示场景一致的示例结果：拼豆体验店 / 年轻女性 / 七夕。
#:
#: 用途：演示脚本、契约示例、框架保护壳的展示素材。
#: 严禁在 run() 里用它做异常兜底——那会把"模型调用失败"伪装成"分析成功"。
FALLBACK: dict[str, Any] = {
    "user": "18-25 岁年轻女性、学生群体（依据：店铺特点写明「面向年轻女性的手作体验店」）",
    "problems": [
        "宣传内容以作品展示为主，缺少到店体验的场景感（依据：店铺特点为「以作品展示内容为主」）",
        "缺少节日营销活动，潜在需求只提到缺素材（依据：潜在营销需求为「缺少活动推广素材」）",
    ],
    "suggestions": [
        "在小红书按周发布 3 条顾客到店体验的短图文，标题突出「零基础也能完成」以降低尝试门槛（对应问题 1）",
        "围绕七夕策划一次双人拼豆体验主题活动，同步产出 3-5 张可复用的宣传素材（对应问题 2）",
    ],
    "extra": {
        "positioning": "面向年轻女性的拼豆手作体验店，主打到店体验与作品分享",
        "known_facts": [
            "品类为 handmade（手作）",
            "来源平台为小红书",
            "店铺特点：面向年轻女性的手作体验店，以作品展示内容为主",
            "潜在营销需求：缺少活动推广素材",
        ],
        "inferences": [
            "以作品展示内容为主 → 内容偏向成品呈现，到店体验的过程感可能不足",
            "面向年轻女性 → 小红书是与之匹配的内容平台，但缺少节点活动承接流量",
        ],
        "unknowns": [
            "客单价与套餐结构未知，无法判断价格是否影响转化",
            "历史活动效果未知，无法判断哪类主题更受欢迎",
            "粉丝量与互动率未知，无法评估现有内容基线",
        ],
        "data_gaps": [
            "近 3 个月的小红书笔记数据（曝光、互动、收藏）",
            "现有套餐与客单价",
            "以往节日活动的参与人数与转化情况",
        ],
    },
}


# ----------------------------------------------------------------------
# 对外入口
# ----------------------------------------------------------------------
def run(state: dict) -> dict:
    """执行一次商家分析。

    参数:
        state: LangGraph 全局状态，至少包含 customer_info

    返回:
        {"analysis": {...}}  —— 只有这一个根字段，不碰别人的字段

    异常:
        模型调用失败、返回的不是合法 JSON、输出不符合 Analysis 类型契约，
        或通过类型校验但违反业务规则（problems / suggestions 不是 1-3 条、
        或某条为空白字符串），都会原样向上抛，由框架的保护壳负责降级与记录。
        本函数不做任何捕获，也不会在失败时返回 FALLBACK。
    """
    # ---------- ① 读上游字段 ----------
    customer_info, missing_fields = read_customer_info(state)

    # ---------- ② 构造 prompt ----------
    prompt = build_user_prompt(customer_info, missing_fields)

    # ---------- ③ 调模型（统一入口，带 purpose 以出现在工作台执行流里） ----------
    data = llm_client.chat_json(prompt, system=SYSTEM_PROMPT, purpose="analysis")

    # ---------- ④ 契约校验 ----------
    # 模型偶尔把英文字段名写成中文（{"用户": ...}），先把键名归一，再交给 Pydantic；
    # 只换键名、不动内容，标准键优先，不凭空补字段。
    if isinstance(data, dict):
        data = normalize_keys(data, ANALYSIS_ALIASES)
    analysis = Analysis.model_validate(data)
    # 类型过关之后，再查一遍契约的业务规则（条数 1-3、每条非空白）。
    # 放在 model_validate 之后：类型不对时先由 Pydantic 报清楚，不在这里混淆两种错误。
    check_business_rules(analysis)
    result = analysis.model_dump()

    # extra 是契约允许的自由扩展位。这里写入两个"我们自己才知道"的事实，
    # 不覆盖模型给出的任何内容：
    #   prompt_version —— 这次用的是哪版提示词，方便对比效果
    #   missing_fields —— 上游缺了哪些字段，下游据此判断结论的可信度
    extra = result["extra"]
    extra["prompt_version"] = PROMPT_VERSION
    extra["missing_fields"] = list(missing_fields)

    return {"analysis": result}


def read_customer_info(state: dict) -> tuple[dict[str, str], list[str]]:
    """读取并规范化 customer_info。

    返回 (五个字段的字典, 上游缺失的字段名列表)。

    规则：
      · 只读 customer_info，不碰 state 里其他任何字段；
      · 只有"真正的、去掉首尾空白后非空的字符串"才会被采用；
        缺失、None、空字符串、纯空白字符串、以及任何非字符串值（数字 / 列表 /
        字典 / 布尔），一律用「未提供」占位，并记进 missing_fields；
      · state 本身不合法（不是 dict、没有 customer_info、customer_info 不是 dict）
        属于调用方的接口错误，直接抛 ValueError，不静默容忍。
    """
    if not isinstance(state, dict):
        raise ValueError(
            f"run(state) 的 state 必须是 dict，实际收到 {type(state).__name__}。"
            '正确用法：run({"customer_info": {...}})'
        )

    raw_info = state.get("customer_info")
    if raw_info is None:
        raise ValueError(
            "state 里没有 customer_info，无法分析。"
            "该字段由客户发现 Agent（队友 1）写入，本节点只读不写。"
        )

    # 兼容一种常见的集成写错：上游把 CustomerInfo 模型对象直接塞进 state，
    # 而不是 model_dump() 后的字典。这里顺手转一下，不要让整个节点因此崩掉。
    if hasattr(raw_info, "model_dump") and not isinstance(raw_info, dict):
        raw_info = raw_info.model_dump()

    if not isinstance(raw_info, dict):
        raise ValueError(
            f"state['customer_info'] 必须是 dict，实际是 {type(raw_info).__name__}"
        )

    customer_info: dict[str, str] = {}
    missing_fields: list[str] = []

    for name in FIELD_ORDER:
        value = raw_info.get(name)

        # 只接受"真正的、非空白的字符串"。
        #
        # 这里刻意不再用 str(value) 做兜底转换，因为那会把不该当作文本的东西
        # 悄悄变成"看起来合理"的值，然后被模型当成商家的真实描述使用：
        #     123           -> "123"            （凭空造出一个门店编号）
        #     ["小红书"]    -> "['小红书']"      （把列表的 repr 塞进 prompt）
        #     {"name": "x"} -> "{'name': 'x'}"
        #     True          -> "True"
        # 这类值一旦进了 prompt，下游就无法区分"商家说了这句话"和
        # "上游把数据类型写错了"。宁可当作未提供，也不能伪造事实。
        if isinstance(value, str):
            text = value.strip()
            if text:
                customer_info[name] = text
                continue

        customer_info[name] = DEFAULT_FIELD_VALUE
        missing_fields.append(name)

    return customer_info, missing_fields


# ----------------------------------------------------------------------
# 单独运行本文件时的入口（会真实调用模型）
#   python -m app.agents.analysis_agent            # 用 samples 里的示例跑一次
#   python -m app.agents.analysis_agent --fallback # 只看演示场景的示例结构，不调用模型
# ----------------------------------------------------------------------
def _main() -> int:  # pragma: no cover
    import argparse
    import json
    from pathlib import Path

    parser = argparse.ArgumentParser(description="商家分析 Agent 单节点自测")
    parser.add_argument("--fallback", action="store_true", help="打印演示场景示例结构（不调用模型）")
    parser.add_argument("--input", default=None, help="customer_info 的 JSON 文件路径")
    args = parser.parse_args()

    if args.fallback:
        print("演示场景示例（拼豆体验馆 / 年轻女性 / 七夕）")
        print("注意：以下内容是常量 FALLBACK，不是模型输出，也不是真实分析结果。\n")
        print(json.dumps({"analysis": FALLBACK}, ensure_ascii=False, indent=2))
        return 0

    sample = Path(args.input) if args.input else (
        Path(__file__).resolve().parents[2] / "samples" / "sample_customer_info.json"
    )
    customer_info = json.loads(sample.read_text(encoding="utf-8"))

    print(f"输入 customer_info（{sample.name}）：")
    print(json.dumps(customer_info, ensure_ascii=False, indent=2))
    print("\n正在调用真实模型……\n")

    result = run({"customer_info": customer_info})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
