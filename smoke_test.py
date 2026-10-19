"""
冒烟测试 v3：完整链路 + 故障降级 + Manager 决策链路。
完全离线：不需要 LLM key（运行时自动移除）、不需要 langgraph。

用法（项目根目录）：python smoke_test.py
"""

import os
from pathlib import Path

from app import final, llm_client, manager
from app.agents import analysis_agent, creative_agent, customer_agent, marketing_agent
from app.agents._mocks import customer_agent as customer_mock
from app.guard import guard
from app.state import validate_state

ROOT = Path(__file__).resolve().parent

# 离线化：本测试在任意环境（含已配置 key 的机器）都必须不触网——
# 先触发 .env 一次性加载，再移除 key，后续所有 has_api_key() 均返回 False。
llm_client.has_api_key()
os.environ.pop("DEEPSEEK_API_KEY", None)

# (节点名, 模块, 负责字段) —— 顺序即流水线顺序
PIPELINE = [
    ("customer", customer_agent, "customer_info"),
    ("analysis", analysis_agent, "analysis"),
    ("marketing", marketing_agent, "strategy"),
    ("creative", creative_agent, "images"),
    ("final", final, "report"),
]


def run_chain(overrides: dict | None = None) -> dict:
    """手动串联全链路（模拟 LangGraph 语义：字段合并 + trace 追加）。

    overrides：{"节点名": 替换函数}，用于故障注入演练。
    """
    overrides = overrides or {}
    # 离线保障：customer 的真实实现会调用外部数据源（JustOneAPI）——
    # 冒烟测试默认改用 mock 版，保证"不触网、不消耗外部额度"。
    if "customer" not in overrides:
        overrides = {**overrides, "customer": customer_mock.run}
    state: dict = {
        "user_input": "寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广",
        "trace": [],
    }
    for node_name, module, field in PIPELINE:
        fn = overrides.get(node_name, module.run)
        wrapped = guard(node_name, fn, field, module.FALLBACK)
        result = wrapped(state)

        extra_keys = [k for k in result if k not in (field, "trace")]
        assert not extra_keys, f"{node_name} 越界写了别人的字段：{extra_keys}"

        for key, value in result.items():
            if key == "trace":
                state["trace"].extend(value)  # 模拟 reducer 的追加语义
            else:
                state[key] = value
    return state


def test_happy_path() -> bool:
    print("① 正常链路（五节点 + Final 报告）")
    state = run_chain()

    problems = validate_state(state)
    if problems:
        print("  契约校验失败：")
        for p in problems:
            print("   -", p)
        return False

    # 离线环境预期：真实节点（analysis 需要 LLM key）会走降级；
    # 断言改为"每个节点都留下完成/降级记录，且链路与契约完整"。
    statuses = [e["status"] for e in state["trace"] if e.get("status") in ("done", "degraded")]
    if len(statuses) != len(PIPELINE) or any(s not in ("done", "degraded") for s in statuses):
        print("  trace 状态异常：", [e.get("status") for e in state["trace"]])
        return False

    done = statuses.count("done")
    degraded = statuses.count("degraded")
    for node_name, module, field in PIPELINE:
        print(f"  ✓ {field:<14} ← {module.__name__}")
    print(f"  报告标题：{state['report']['title']}")
    print(f"  执行痕迹：{len(statuses)} 个节点（done {done} / degraded {degraded}）")

    # 海报环节：图必须真的落地（这是"交付物"，不能只是路径字符串）
    images = state.get("images") or {}
    poster = str(images.get("poster") or "")
    if not poster:
        print("  ✗ 海报未生成：images.poster 为空")
        return False
    if not (ROOT / poster).is_file():
        print(f"  ✗ 海报文件不存在：{poster}")
        return False
    print(
        f"  ✓ 海报产出：{poster}（{images.get('size') or '?'} · {images.get('motif') or '?'} "
        f"· spec {images.get('spec_id') or '?'}）"
    )
    report = state.get("report") or {}
    if not (report.get("poster") or {}).get("path"):
        print("  ✗ 报告里没有海报摘要（report.poster）")
        return False
    print(f"  ✓ 报告海报摘要：主标语「{(report.get('poster') or {}).get('slogan', '')}」")
    return True


def test_degradation() -> bool:
    print("② 故障降级演练（注入 marketing 节点故障）")

    def broken_node(state: dict) -> dict:
        raise RuntimeError("模拟故障：营销策划环节崩溃")

    state = run_chain(overrides={"marketing": broken_node})

    entry = next((e for e in state["trace"] if e["node"] == "marketing"), None)
    if not entry or entry["status"] != "degraded":
        print("  失败：marketing 未被标记为 degraded")
        return False
    if state.get("strategy") != marketing_agent.FALLBACK:
        print("  失败：strategy 未使用兜底数据")
        return False
    if not state.get("report", {}).get("title"):
        print("  失败：链路中断，report 未生成")
        return False
    if validate_state(state):
        print("  失败：降级后契约校验不通过")
        return False

    print("  ✓ strategy 已用兜底数据顶替")
    print(f"  ✓ trace 标记 degraded（{entry['error']}）")
    print(f"  ✓ 链路未中断：report「{state['report']['title']}」正常生成")
    print("  ✓ 兜底数据下的契约校验通过")
    return True


def test_manager_pipeline() -> bool:
    """Manager 组件：无 LLM 降级路径 + 路由 + 重试上限 + 回退。"""
    print("③ Manager 决策链路（无 LLM 降级路径）")
    os.environ["MANAGER_DISABLE_LLM"] = "1"
    try:
        # 3a 入口裁剪降级：默认全跑，首站 customer
        state: dict = {"user_input": "帮武汉的拼豆店做一轮推广", "trace": []}
        state.update(manager.intake(state))
        if state.get("plan", {}).get("run") != list(manager.PIPELINE_NODES):
            print("  失败：intake 降级路径异常：", state.get("plan"))
            return False
        if state["control"]["next"] != "customer":
            print("  失败：intake 首站异常：", state["control"])
            return False
        print("  ✓ intake：无 LLM → 默认全跑，首站 customer")

        # 3b 质检通过 → continue 到下一站
        state["customer_info"] = {
            "shop": "测试店",
            "type": "handmade",
            "platform": "测试",
            "highlights": "测试店铺",
            "potential_need": "测试需求",
        }
        state["trace"].append({"node": "customer", "status": "done", "elapsed": 0})
        out = manager.supervisor(state)
        if out["control"]["action"] != "continue" or out["control"]["next"] != "analysis":
            print("  失败：supervisor continue 决策异常：", out["control"])
            return False
        print("  ✓ supervisor：customer 质检通过 → continue → analysis")

        # 3c 重试上限 → accept 并推进（不回头）
        state2: dict = {
            "user_input": "测试",
            "plan": {"run": list(manager.PIPELINE_NODES)},
            "retry_counts": {"analysis": 99},
            "trace": [
                {"node": "customer", "status": "done", "elapsed": 0},
                {"node": "analysis", "status": "failed", "elapsed": 0},
            ],
        }
        out2 = manager.supervisor(state2)
        if out2["control"]["action"] != "accept" or out2["control"]["next"] != "marketing":
            print("  失败：重试超限未触发 accept 或路由错误：", out2["control"])
            return False
        print("  ✓ supervisor：重试超限 → accept 并推进到 marketing（不回头）")

        # 3d 回退逻辑（注入质检结论：根因在上游 → rollback + 下游字段作废）
        state3: dict = {
            "user_input": "测试",
            "plan": {"run": list(manager.PIPELINE_NODES)},
            "customer_info": {
                "shop": "测试店",
                "type": "handmade",
                "platform": "测试",
                "highlights": "测试店铺",
                "potential_need": "测试需求",
            },
            "analysis": {"user": "略", "problems": [], "suggestions": []},
            "trace": [
                {"node": "customer", "status": "done", "elapsed": 0},
                {"node": "analysis", "status": "done", "elapsed": 0.1},
            ],
        }
        original_check, original_enabled = manager._llm_check, manager._llm_enabled
        manager._llm_enabled = lambda: True
        manager._llm_check = lambda node, st: {
            "pass": False,
            "target": "customer",
            "reason": "测试：上游信息不足",
        }
        try:
            out3 = manager.supervisor(state3)
        finally:
            manager._llm_check, manager._llm_enabled = original_check, original_enabled

        if out3["control"]["action"] != "rollback" or out3["control"]["next"] != "customer":
            print("  失败：回退决策异常：", out3["control"])
            return False
        if out3.get("customer_info") != {} or out3.get("analysis") != {}:
            print("  失败：回退未作废下游字段：", {k: v for k, v in out3.items() if k not in ("trace",)})
            return False
        print("  ✓ supervisor：质检判定根因在上游 → rollback 到 customer + 下游字段作废")

    finally:
        os.environ.pop("MANAGER_DISABLE_LLM", None)
    return True


def test_creative_check() -> bool:
    """海报环节的规则质检：图/文不齐 → 重做；超限 → 接受并推进 final。"""
    print("④ 海报环节质检（缺图 → 重做 / 超限 → 接受）")
    os.environ["MANAGER_DISABLE_LLM"] = "1"

    def make_state(images: dict, retry_counts: dict | None = None) -> dict:
        state = {
            "user_input": "测试",
            "plan": {"run": list(manager.PIPELINE_NODES)},
            "images": images,
            "trace": [
                {"node": "customer", "status": "done", "elapsed": 0},
                {"node": "analysis", "status": "done", "elapsed": 0},
                {"node": "marketing", "status": "done", "elapsed": 0},
                {"node": "creative", "status": "done", "elapsed": 0.1},
            ],
        }
        if retry_counts:
            state["retry_counts"] = retry_counts
        return state

    try:
        # a) 路径写了但文件不存在 → 判为不达标 → 重做 creative
        out = manager.supervisor(make_state(
            {"poster": "outputs/__not_exist__.png", "copywriting": "文案"}
        ))
        if out["control"]["action"] != "retry" or out["control"]["next"] != "creative":
            print("  失败：缺图未触发重做：", out["control"])
            return False
        print("  ✓ 海报文件不存在 → retry creative")

        # b) 只有图没有配文 → 同样不达标
        real = next(iter(sorted((ROOT / "outputs").glob("poster_*.png"))), None)
        if real is not None:
            out = manager.supervisor(make_state(
                {"poster": f"outputs/{real.name}", "copywriting": ""}
            ))
            if out["control"]["action"] != "retry":
                print("  失败：缺配文未触发重做：", out["control"])
                return False
            print("  ✓ 缺少配文 → retry creative")

            # c) 图 + 文齐全 → 通过，进入 final
            out = manager.supervisor(make_state(
                {"poster": f"outputs/{real.name}", "copywriting": "一张图，五种烫法"}
            ))
            if out["control"]["action"] != "continue" or out["control"]["next"] != "final":
                print("  失败：合规海报未放行：", out["control"])
                return False
            print("  ✓ 图 + 文齐全 → continue → final")
        else:
            print("  · 跳过 b/c：outputs/ 下还没有海报文件")

        # d) 重试超限 → accept，不卡在素材环节
        out = manager.supervisor(make_state(
            {"poster": "outputs/__not_exist__.png", "copywriting": "文案"},
            retry_counts={"creative": 99},
        ))
        if out["control"]["action"] != "accept" or out["control"]["next"] != "final":
            print("  失败：重试超限未接受：", out["control"])
            return False
        print("  ✓ 重试超限 → accept 并推进到 final")
    finally:
        os.environ.pop("MANAGER_DISABLE_LLM", None)
    return True


def main() -> int:
    ok1 = test_happy_path()
    print()
    ok2 = test_degradation()
    print()
    ok3 = test_manager_pipeline()
    print()
    ok4 = test_creative_check()
    print()

    if ok1 and ok2 and ok3 and ok4:
        print("四部测试全部通过：闭环 + 降级兜底 + Manager 决策 + 海报质检 均正常。")
        return 0
    print("存在失败项，请检查上方输出。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
