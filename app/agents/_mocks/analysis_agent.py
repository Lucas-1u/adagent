"""
商家分析 Agent —— 队友 2
==========================

职责：分析目标商家的产品特点、用户群体、内容质量与营销问题（全项目核心节点）。
读取：customer_info
写入：analysis（只写这一个字段，别碰别人的字段）

开发提示：
    - 推荐用 llm_client.chat_json 产出 JSON，再对照 app.state.Analysis 校验
    - 完整写法参考见 app/agents/example_agent.py（就是本节点的完整示例）

自测：python -m app.agents.analysis_agent
"""

from app.state import Analysis

FIELD = "analysis"

# 降级兜底数据（演示保险：节点异常时由保护壳 app/guard.py 顶替输出）
FALLBACK: dict = {
    "user": "18-25 岁年轻女性、学生群体",
    "problems": ["宣传素材质量普通", "缺少节日营销活动", "社交媒体内容更新不足"],
    "suggestions": ["制作年轻化宣传方案", "策划七夕情侣主题活动"],
}


def run(state: dict) -> dict:
    """节点入口：输入整个 state，返回要写入的字段。"""
    customer = state.get("customer_info", {})

    # TODO（队友 2）：把下面的模拟数据替换为真实实现。
    #   真实实现思路：把 customer 转成 prompt → llm_client.chat_json → 校验。
    data = dict(FALLBACK)

    Analysis.model_validate(data)  # 自检
    return {FIELD: data}


if __name__ == "__main__":
    import json

    demo = {
        "customer_info": {
            "shop": "XX 拼豆体验馆",
            "type": "handmade",
            "platform": "小红书",
            "highlights": "面向年轻女性的手作体验店",
            "potential_need": "缺少活动推广素材",
        }
    }
    print(json.dumps(run(demo), ensure_ascii=False, indent=2))
