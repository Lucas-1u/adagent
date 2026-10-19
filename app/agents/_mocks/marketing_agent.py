"""
营销策划 Agent —— 队友 3
==========================

职责：根据商家情况生成营销主题、推广策略与文案方案（连接"分析"与"生成"的桥梁）。
读取：analysis（也可参考 customer_info）
写入：strategy（只写这一个字段，别碰别人的字段）

自测：python -m app.agents.marketing_agent
"""

from app.state import Strategy

FIELD = "strategy"

# 降级兜底数据（演示保险：节点异常时由保护壳 app/guard.py 顶替输出）
FALLBACK: dict = {
    "theme": "七夕 DIY 拼豆体验",
    "channels": ["小红书", "抖音"],
    "keywords": ["治愈", "情侣", "手作"],
    "copywriting": "这个七夕，和 TA 一起拼出专属回忆。",
}


def run(state: dict) -> dict:
    """节点入口：输入整个 state，返回要写入的字段。"""
    analysis = state.get("analysis", {})
    customer = state.get("customer_info", {})

    # TODO（队友 3）：把下面的模拟数据替换为真实实现。
    data = dict(FALLBACK)

    Strategy.model_validate(data)  # 自检
    return {FIELD: data}


if __name__ == "__main__":
    import json

    demo = {
        "customer_info": {"shop": "XX 拼豆体验馆", "type": "handmade"},
        "analysis": {"user": "18-25 岁年轻女性", "problems": ["缺少节日营销活动"]},
    }
    print(json.dumps(run(demo), ensure_ascii=False, indent=2))
