"""
LangGraph 编排（总控维护）—— v0.3
==================================

v0.3 变化：
    - 新增 Manager 决策层：intake（入口裁剪）+ supervisor（质检决策）
    - 条件路由：每个节点后由 control.next 决定下一站（支持裁剪 / 重试 / 回退）
    - 质检回路：supervisor 检查产出质量，不合格 → 重做或回退（带原因）

v0.4 变化：
    - 广告素材节点改由 poster_agent 承担：不再调用生图模型，改为
      「state → 海报规格 spec → 本地模板渲染（无头浏览器截图）→ outputs/poster_<hash>.png」
    - 海报文案（images.copywriting）同样由同一份 state 派生，保证图文一致

    用户需求 → intake → [业务节点 ⇄ supervisor] → final → 输出（report + trace）

运行：python -m app.graph
    - 有 key：启用 LLM 决策（入口裁剪 + 关键节点质检）
    - 无 key：自动降级为"全跑 + 不拦截"（仅规则质检）
"""

import json

from langgraph.graph import END, START, StateGraph

from app import final, llm_client, manager
from app.agents import analysis_agent, creative_agent, customer_agent, marketing_agent
from app.guard import guard
from app.state import MarketingState, validate_state

# 路由映射：control.next 的取值 → 目标节点
ROUTE_MAP = {
    "customer": "customer",
    "analysis": "analysis",
    "marketing": "marketing",
    "creative": "creative",
    "final": "final",
}

BUSINESS_NODES = ("customer", "analysis", "marketing", "creative")


def _with_llm_calls(node_name: str, fn):
    """节点壳：把节点执行期间产生的 LLM 调用记录并入 trace（status="llm"）。"""
    def wrapped(state):
        with llm_client.collect_calls(node_name) as calls:
            result = dict(fn(state))
        if calls:
            result["trace"] = [*(result.get("trace") or []), *calls]
        return result
    return wrapped


def _route(state: dict) -> str:
    """条件路由：读取 Manager 的指令（control.next）决定下一站。"""
    nxt = (state.get("control") or {}).get("next", "final")
    return nxt if nxt in ROUTE_MAP else "final"


def build_graph():
    """构建并编译流程图：Manager 决策层 + 业务节点 + 质检回路。"""
    builder = StateGraph(MarketingState)

    # Manager 决策层（内部自带兜底，无需保护壳；会收集 LLM 调用记录）
    builder.add_node("intake", _with_llm_calls("manager", manager.intake))
    builder.add_node("supervisor", _with_llm_calls("manager", manager.supervisor))

    # 业务节点（保护壳包装）+ Final
    builder.add_node(
        "customer",
        guard("customer", customer_agent.run, "customer_info", customer_agent.FALLBACK),
    )
    builder.add_node(
        "analysis",
        guard("analysis", analysis_agent.run, "analysis", analysis_agent.FALLBACK),
    )
    builder.add_node(
        "marketing",
        guard("marketing", marketing_agent.run, "strategy", marketing_agent.FALLBACK),
    )
    builder.add_node(
        "creative",
        guard("creative", creative_agent.run, "images", creative_agent.FALLBACK),
    )
    builder.add_node("final", guard("final", final.run, "report", final.FALLBACK))

    # 入口 → 按计划进入第一个节点
    builder.add_edge(START, "intake")
    builder.add_conditional_edges("intake", _route, ROUTE_MAP)

    # 业务节点跑完 → 质检 → 按决策走（继续 / 重做 / 回退）
    for node in BUSINESS_NODES:
        builder.add_edge(node, "supervisor")
    builder.add_conditional_edges("supervisor", _route, ROUTE_MAP)

    builder.add_edge("final", END)

    return builder.compile()


def run_pipeline(user_input: str) -> dict:
    """端到端跑一次，并对结果做契约校验。"""
    graph = build_graph()
    state = graph.invoke(
        {"user_input": user_input, "trace": []},
        config={"recursion_limit": 50},
    )

    problems = validate_state(state)
    if problems:
        print("契约校验发现问题：")
        for p in problems:
            print("  -", p)
    return state


def trace_summary(state: dict) -> list[str]:
    """把 trace 转成人类可读的摘要行（演示 / 调试用）。"""
    lines = []
    for event in state.get("trace") or []:
        node = event.get("node", "?")
        if event.get("status") == "decision":
            lines.append(
                f"[Manager] {event.get('action')} → {event.get('target')}：{event.get('reason')}"
            )
        else:
            detail = f"（{event.get('error')}）" if event.get("error") else ""
            lines.append(f"[{node}] {event.get('status')} {event.get('elapsed')}s{detail}")
    return lines


if __name__ == "__main__":
    result = run_pipeline("寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广")
    print(json.dumps(result, ensure_ascii=False, indent=2))
