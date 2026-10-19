"""
参考示例：一个"标准姿势"的 Agent 节点
=======================================

本文件是写法参考，不参与流程运行（不会被挂进 app/graph.py）。
以"商家分析"为例，展示从读契约 → 调 LLM → 校验 → 写回契约的完整流程。
队友 2 可以直接在此基础上改造成正式的 analysis_agent.py；
其他队友请对照本文件的四步结构写自己的节点。

关键四步：
    ① 读上游字段（state.get，永远做兜底）
    ② 构造 prompt（把上游信息 json.dumps 后喂进去）
    ③ 调 llm_client.chat_json 拿结构化输出
    ④ 用契约模型校验后返回 {"字段名": 数据}

自测（需要先在 .env 配好 key）：python -m app.agents.example_agent
"""

import json

from app import llm_client
from app.state import Analysis

FIELD = "analysis"

SYSTEM_PROMPT = "你是一名资深本地生活营销分析师。你只输出 JSON，不要输出任何多余文字。"

USER_PROMPT_TEMPLATE = """请分析以下商家的营销现状，输出 JSON。

商家信息：
{customer_json}

输出 JSON 结构（字段名不可改）：
{{
  "user": "目标用户画像（一句话）",
  "problems": ["当前营销问题，1-3 条"],
  "suggestions": ["改进建议，1-3 条"]
}}
"""


def run(state: dict) -> dict:
    customer = state.get("customer_info", {})
    if not customer:
        raise ValueError("缺少 customer_info：analysis 节点的上游没有产出，检查流程顺序")

    # 第 ② 步：构造 prompt
    prompt = USER_PROMPT_TEMPLATE.format(
        customer_json=json.dumps(customer, ensure_ascii=False, indent=2)
    )

    # 第 ③ 步：调用统一 LLM 封装
    data = llm_client.chat_json(prompt, system=SYSTEM_PROMPT)

    # 第 ④ 步：契约校验（不合格说明 prompt 或输出需要调整）
    Analysis.model_validate(data)
    return {FIELD: data}


if __name__ == "__main__":
    demo_state = {
        "customer_info": {
            "shop": "XX 拼豆体验馆",
            "type": "handmade",
            "platform": "小红书",
            "highlights": "面向年轻女性的手作体验店",
            "potential_need": "缺少活动推广素材",
        }
    }
    result = run(demo_state)
    print(json.dumps(result, ensure_ascii=False, indent=2))
