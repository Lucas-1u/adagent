"""
广告生成 Agent —— 队友 4
==========================

职责：利用多模态 AI 生成宣传海报、活动图片与视频脚本（Demo 最大视觉冲击来源）。
读取：strategy（也可参考 customer_info / analysis）
写入：images（只写这一个字段，别碰别人的字段）

开发提示：
    - v0.1 先用占位路径把流程跑通，图片真实生成能力后续接入
    - 生成的海报文件建议统一放到项目根目录 outputs/ 下

自测：python -m app.agents.creative_agent
"""

from app.state import Creative

FIELD = "images"

# 降级兜底数据（演示保险：节点异常时由保护壳 app/guard.py 顶替输出）
FALLBACK: dict = {
    "poster": "outputs/poster_qiqi.png",
    "copywriting": "一张图，五种烫法",
}


def _poster_caption(strategy: dict) -> str:
    """海报配文：取方案主题冒号前的短句，与策略保持一致。

    只做"取主题里已经写明的那句话"，不新造卖点、不写金额与力度
    （与 strategy.extra.core_offer 的约束一致：优惠需商家确认后才能对外）。
    """
    theme = str((strategy or {}).get("theme") or "").strip()
    head = theme.split("：")[0].split(":")[0].strip()
    if 4 <= len(head) <= 18:
        return head
    extra = (strategy or {}).get("extra") or {}
    key_message = str(extra.get("key_message") or "").strip()
    if key_message:
        return key_message.split("；")[0].split("。")[0].strip()[:24]
    return FALLBACK["copywriting"]


def run(state: dict) -> dict:
    """节点入口：输入整个 state，返回要写入的字段。"""
    strategy = state.get("strategy", {})

    # TODO（队友 4）：把下面的模拟数据替换为真实实现（真实图片生成待接入）。
    data = dict(FALLBACK)
    data["copywriting"] = _poster_caption(strategy)

    Creative.model_validate(data)  # 自检
    return {FIELD: data}


if __name__ == "__main__":
    import json

    demo = {"strategy": {"theme": "七夕 DIY 拼豆体验", "channels": ["小红书"]}}
    print(json.dumps(run(demo), ensure_ascii=False, indent=2))
