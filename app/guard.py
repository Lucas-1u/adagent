"""
节点保护壳 —— 稳定性工程
========================

把任意节点函数包一层保护：统一异常捕获 + 降级兜底 + trace 记录。
任一节点故障时，流程不中断（阶段 1 验收标准），且执行痕迹留档。

用法（graph.py 装配时）：
    builder.add_node(
        "analysis",
        guard("analysis", analysis_agent.run, "analysis", analysis_agent.FALLBACK),
    )

trace 事件结构（前端"执行流"的直接数据源）：
    {"node": "analysis", "status": "done" | "degraded" | "failed",
     "elapsed": 1.23, "error": "（仅 degraded / failed）",
     "detail": {...}}          # 可选：节点自报的细节（如海报的母题 / 尺寸 / 渲染耗时）

节点细节上报约定：节点返回 {"_trace_detail": {...}} 即可，保护壳会把它塞进该节点的
trace 事件并把这个私有 key 从状态更新里剔除（不污染契约字段）。
"""

import sys
import time

from app import llm_client

DETAIL_KEY = "_trace_detail"


def guard(node_name: str, fn, field: str, fallback=None):
    """包装节点函数。

    参数：
        node_name: 节点名（写入 trace）
        fn:        节点实现 run(state: dict) -> {field: {...}}
        field:     该节点负责写入的字段名
        fallback:  降级兜底数据（节点异常时顶替，None 则只记录失败）
    """

    def wrapped(state: dict) -> dict:
        started = time.time()
        try:
            with llm_client.collect_calls(node_name) as calls:
                result = dict(fn(state))
            detail = result.pop(DETAIL_KEY, None)
            event = {
                "node": node_name,
                "status": "done",
                "elapsed": round(time.time() - started, 2),
            }
            if detail:
                event["detail"] = detail
            result["trace"] = [*(result.get("trace") or []), event, *calls]
            return result
        except Exception as exc:
            elapsed = round(time.time() - started, 2)
            message = f"{type(exc).__name__}: {exc}"[:200]
            if fallback is not None:
                print(
                    f"[guard] 节点 {node_name} 异常，已降级兜底：{message}",
                    file=sys.stderr,
                )
                event = {
                    "node": node_name,
                    "status": "degraded",
                    "elapsed": elapsed,
                    "error": message,
                }
                return {field: fallback, "trace": [event]}
            print(
                f"[guard] 节点 {node_name} 异常，未配置兜底：{message}",
                file=sys.stderr,
            )
            event = {
                "node": node_name,
                "status": "failed",
                "elapsed": elapsed,
                "error": message,
            }
            return {"trace": [event]}

    return wrapped
