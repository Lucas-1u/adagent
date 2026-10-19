"""
客户发现 Agent —— 队友 1
==========================

职责：寻找具有潜在营销需求的商家。
读取：user_input（用户需求原文，如"寻找武汉年轻消费相关店铺"）
写入：customer_info（只写这一个字段，别碰别人的字段）

数据源方向：小红书 / 抖音 / 搜索引擎 / 第三方数据 API。
Demo 阶段允许先用模拟数据把流程跑通，再逐步接入真实数据源。

参考：完整写法示例见 app/agents/example_agent.py
自测：python -m app.agents.customer_agent
"""

from app.state import CustomerInfo

FIELD = "customer_info"

# 降级兜底数据（演示保险：节点异常时由保护壳 app/guard.py 顶替输出）
FALLBACK: dict = {
    "shop": "七七拼豆体验馆",
    "type": "handmade",
    "platform": "小红书",
    "highlights": "面向年轻女性的手作体验店，以作品展示内容为主",
    "potential_need": "缺少活动推广素材",
}


def run(state: dict) -> dict:
    """节点入口：输入整个 state，返回要写入的字段。"""
    user_input = state.get("user_input", "")

    # TODO（队友 1）：把下面的模拟数据替换为真实实现。
    #   真实实现思路：根据 user_input 调搜索/数据接口 → 整理成契约结构。
    data = dict(FALLBACK)

    CustomerInfo.model_validate(data)  # 自检：不合格立刻报错，早发现早改
    return {FIELD: data}


if __name__ == "__main__":
    import json

    out = run({"user_input": "寻找武汉地区适合年轻女性消费的小店"})
    print(json.dumps(out, ensure_ascii=False, indent=2))
