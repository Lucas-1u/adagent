"""
Manager 决策层 —— 总控的大脑（阶段 2）
======================================

两个角色：
    intake(state)      入口裁剪：读懂用户需求 → 制定执行计划（plan）
    supervisor(state)  节点质检：检查刚完成节点的输出 → 决定下一步

决策原则（LLM 混合式，稳中带智能）：
    - 规则质检：契约结构检查（零成本，每次都做）
    - 广告素材额外规则：海报文件必须真实存在 + 配文不为空 → 否则算不达标（重做 / 超限接受）
    - LLM 质检：仅关键节点（LLM_CHECK_NODES）判断"质量够不够支撑下游"
    - 无 key / LLM 失败 → 自动降级为"全跑 + 不拦截"（仅规则判定）

动作白名单（封闭，不允许自由发挥）：
    start / continue / retry（重做自身）/ rollback（回退上游）/ accept（超限接受）

决策留痕：每次决策写入 trace（status="decision"），前端"执行流"直接展示。

说明：
    - 本模块是"零异常"节点：内部兜底完善，绝不抛异常中断整条链。
    - 测试开关：环境变量 MANAGER_DISABLE_LLM=1 强制走规则路径（供冒烟测试使用）。
"""

import json
import os
import time
from pathlib import Path

from app import llm_client
from app.state import validate_state

PROJECT_ROOT = Path(__file__).resolve().parents[1]

# ---- 流水线定义（全项目唯一的执行顺序） ----
PIPELINE = [
    ("customer", "customer_info"),
    ("analysis", "analysis"),
    ("marketing", "strategy"),
    ("creative", "images"),
]
PIPELINE_NODES = [node for node, _ in PIPELINE]
NODE_FIELDS = dict(PIPELINE)
NODE_LABELS = {
    "customer": "客户发现",
    "analysis": "商家分析",
    "marketing": "营销策划",
    "creative": "广告素材",
}

# 做 LLM 质检的节点（其余节点只做规则质检，控制成本与延迟）
LLM_CHECK_NODES = {"analysis"}

# 循环上限（防死循环 / 控成本 / 控演示延迟）
MAX_RETRY_PER_NODE = 1  # 单节点最多重试次数
MAX_TOTAL_RETRY = 2  # 全流程最多重试次数

# 下游环节对上游产出的需求（喂给 LLM 质检参考）
DOWNSTREAM_NEED = {
    "analysis": "营销策划环节需要：明确的目标用户画像、具体的营销问题（1-3 条）、可执行的改进建议（1-3 条）",
    "marketing": "广告生成环节需要：明确的营销主题、推广渠道列表、宣传关键词",
    "creative": "最终汇报需要：海报素材路径、配套文案",
}

INTAKE_PROMPT = """你是 AI 营销团队的总控，负责决定这次任务要执行哪些环节。

可选环节（必须按此顺序执行，依赖链不能断）：
1. customer —— 发现客户（找到目标商家）
2. analysis —— 商家分析（目标用户 / 营销问题 / 改进建议）
3. marketing —— 营销策划（主题 / 渠道 / 关键词）
4. creative —— 广告素材（海报 / 文案 / 视频脚本）

用户需求：
{user_input}

裁剪规则：
- 默认执行全部四个环节；
- 仅当用户明确表示"不需要某类产出"时，才可以从尾部跳过环节
  （例：只要分析报告 → 只跑前两个；不要素材 → 只跑前三个）；
- 不允许跳过中间环节（会破坏依赖链）。

只输出 JSON：{{"run": ["customer", "analysis", "marketing", "creative"], "reason": "一句话说明执行范围"}}
"""

CHECK_PROMPT = """你是营销项目总监，负责质量把关。

刚完成的环节：{node_label}（{node_name}）
产出内容：
{payload}

下游环节的需求：
{need}

请判断该产出是否足够支撑下游继续推进。

只输出 JSON：
{{"pass": true 或 false, "target": "需要重做的环节名（可选）", "reason": "一句话说明"}}

判定要点：
- pass=true：内容具体、完整、可用；
- pass=false：内容空洞、残缺或明显跑题。若判断问题根因在上游输入（customer_info 信息不足导致），
  把 target 设为 "customer"；否则 target 设为 "{node_name}"（重做本环节）。
"""


# ---------------------------------------------------------------- 入口裁剪


def intake(state: dict) -> dict:
    """入口裁剪：用户需求 → 执行计划（plan）。"""
    started = time.time()
    user_input = (state.get("user_input") or "").strip()

    plan = _llm_intake(user_input)
    first = plan["run"][0] if plan["run"] else "final"

    event = {
        "node": "manager",
        "status": "decision",
        "action": "plan",
        "target": first,
        "reason": plan["reason"],
        "detail": {"run": plan["run"]},
        "elapsed": round(time.time() - started, 2),
    }
    return {
        "plan": plan,
        "control": {"next": first, "action": "start", "reason": plan["reason"]},
        "trace": [event],
    }


def _llm_intake(user_input: str) -> dict:
    """入口决策的 LLM 调用；任何异常 / 无 key → 默认全跑。"""
    default = {"run": list(PIPELINE_NODES), "reason": "默认执行全部环节"}
    if not _llm_enabled() or not user_input:
        return default
    try:
        data = llm_client.chat_json(
            INTAKE_PROMPT.format(user_input=user_input),
            system="你是 AI 营销团队的总控，只输出 JSON。",
            purpose="intake",
        )
        run = data.get("run")
        reason = str(data.get("reason", "")).strip() or "（模型未给出理由）"
        if not _is_valid_prefix(run):  # 必须是流水线顺序的连续前缀（保证依赖链）
            return {"run": list(PIPELINE_NODES), "reason": f"计划不合法，已回退全跑：{run}"}
        return {"run": run, "reason": reason}
    except Exception:
        return default


def _is_valid_prefix(run) -> bool:
    """校验执行计划是流水线顺序的连续前缀。"""
    if not isinstance(run, list) or not run:
        return False
    return run == PIPELINE_NODES[: len(run)]


# ---------------------------------------------------------------- 节点质检 + 决策


def supervisor(state: dict) -> dict:
    """质检 + 决策：检查刚完成的业务节点，决定下一步（零异常）。"""
    started = time.time()
    last_node, last_status = _last_completed(state)
    plan_run = _plan_run(state)

    # 1) 规则质检（零成本）
    problems = _rule_check(state, last_node) if last_node else []

    # 2) LLM 质检（关键节点 + 规则通过 + 开关开启；异常视为通过）
    verdict = None
    if (
        last_node
        and not problems
        and last_status == "done"
        and last_node in LLM_CHECK_NODES
        and _llm_enabled()
    ):
        verdict = _llm_check(last_node, state)

    # 3) 决策
    control, counts, stale, event = _decide(
        state, last_node, plan_run, problems, verdict
    )
    event["elapsed"] = round(time.time() - started, 2)

    result = {"control": control, "trace": [event]}
    if counts is not None:
        result["retry_counts"] = counts
    if stale:
        result.update(stale)
    return result


def _decide(state, last_node, plan_run, problems, verdict):
    """核心决策函数。

    返回 (control, retry_counts 或 None, stale_updates, event)。
    """
    counts = dict(state.get("retry_counts") or {})
    total = sum(counts.values())

    # 防御：没有可检查的节点（理论上不会发生）
    if last_node is None:
        nxt = _next_target(state, plan_run)
        reason = "无待检节点，按计划继续"
        return {"next": nxt, "action": "continue", "reason": reason}, None, {}, _event("continue", nxt, reason)

    # 判定是否不达标 + 重做目标
    fail_target = None
    fail_reason = ""
    if problems:
        fail_target = last_node
        fail_reason = f"规则质检未通过：{'；'.join(problems)}"
    elif verdict and not verdict.get("pass", True):
        target = verdict.get("target") or last_node
        if target not in PIPELINE_NODES or _index(target) > _index(last_node):
            target = last_node  # 目标不合法（不能越级向前）→ 重做本环节
        fail_target = target
        fail_reason = f"质量质检未通过：{verdict.get('reason', '')}"

    if fail_target:
        can_retry = (
            counts.get(fail_target, 0) < MAX_RETRY_PER_NODE and total < MAX_TOTAL_RETRY
        )
        if can_retry:
            counts[fail_target] = counts.get(fail_target, 0) + 1
            if fail_target == last_node:
                reason = f"{fail_reason} → 重做【{NODE_LABELS[fail_target]}】（第 {counts[fail_target]} 次）"
                return (
                    {"next": fail_target, "action": "retry", "reason": reason},
                    counts, {}, _event("retry", fail_target, reason),
                )
            reason = f"{fail_reason} → 回退到上游【{NODE_LABELS[fail_target]}】重做（第 {counts[fail_target]} 次）"
            return (
                {"next": fail_target, "action": "rollback", "reason": reason},
                counts, _stale_updates(fail_target), _event("rollback", fail_target, reason),
            )
        # 已达上限 → 接受现状，继续推进（留痕）
        nxt = _next_target(state, plan_run, after=last_node)
        reason = (
            f"{fail_reason} → 已达重试上限（单节点 {MAX_RETRY_PER_NODE} 次 / 全流程 {MAX_TOTAL_RETRY} 次），"
            "接受当前产出继续"
        )
        return {"next": nxt, "action": "accept", "reason": reason}, None, {}, _event("accept", nxt, reason)

    # 全部通过 → 继续下一站
    nxt = _next_target(state, plan_run, after=last_node)
    reason = f"【{NODE_LABELS[last_node]}】质检通过，进入下一环节"
    return {"next": nxt, "action": "continue", "reason": reason}, None, {}, _event("continue", nxt, reason)


# ---------------------------------------------------------------- 辅助函数


def _last_completed(state: dict):
    """从 trace 里找最近一次业务节点事件，返回 (节点名, 状态)。"""
    for event in reversed(state.get("trace") or []):
        node = event.get("node")
        if node in PIPELINE_NODES and event.get("status") in ("done", "degraded", "failed"):
            return node, event["status"]
    return None, None


def _plan_run(state: dict) -> list:
    """当前执行计划（缺失 / 非法 → 默认全跑）。"""
    plan = state.get("plan") or {}
    run = plan.get("run")
    return run if _is_valid_prefix(run) else list(PIPELINE_NODES)


def _next_target(state: dict, plan_run: list, after: str | None = None) -> str:
    """按计划顺序找 after 之后第一个"还没产出"的节点；没有 → final。

    从"最近完成的节点"往后找（而不是从头扫描），保证：
    - 被接受的问题产出不会导致回头重跑；
    - 回退后的重跑顺序自然衔接。
    """
    started = after is None
    for node, field in PIPELINE:
        if node == after:
            started = True
            continue
        if not started or node not in plan_run:
            continue
        if not state.get(field):
            return node
    return "final"


def _rule_check(state: dict, node: str) -> list[str]:
    """规则质检：该节点负责字段的契约结构检查（零成本）。"""
    field = NODE_FIELDS[node]
    if not state.get(field):
        return [f"{field} 为空（节点未产出）"]
    problems = [p for p in validate_state(state) if p.startswith(f"[{field}]")]
    if node == "creative":
        problems.extend(_creative_problems(state))
    return problems


def _creative_problems(state: dict) -> list[str]:
    """海报环节的额外规则：图真的存在、配文不为空。

    poster_agent 出图后会返回相对路径（outputs/poster_<hash>.png）；这里要防止
    "路径写了但文件没落地"或"只有图没有配文"这种看起来成功、其实交不出货的情况。
    """
    images = state.get("images") or {}
    poster = str(images.get("poster") or "").strip()
    if not poster:
        return ["images.poster 为空（海报未生成）"]
    if not (PROJECT_ROOT / poster).is_file():
        return [f"images.poster 指向的文件不存在：{poster}"]
    if not str(images.get("copywriting") or "").strip():
        return ["images.copywriting 为空（缺少海报配文）"]
    return []


def _stale_updates(target_node: str) -> dict:
    """回退：目标节点及其下游字段作废（置空），路由会自然重跑。"""
    updates = {}
    active = False
    for node, field in PIPELINE:
        if node == target_node:
            active = True
        if active:
            updates[field] = {}
    return updates


def _llm_check(node: str, state: dict) -> dict | None:
    """对指定节点做 LLM 质量质检；任何异常 → None（视为通过）。"""
    try:
        payload = state.get(NODE_FIELDS[node]) or {}
        prompt = CHECK_PROMPT.format(
            node_label=NODE_LABELS[node],
            node_name=node,
            payload=json.dumps(payload, ensure_ascii=False),
            need=DOWNSTREAM_NEED.get(node, "（未定义）"),
        )
        data = llm_client.chat_json(
            prompt, system="你是严格的营销项目质量总监，只输出 JSON。", purpose="check"
        )
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _llm_enabled() -> bool:
    """Manager 的 LLM 决策开关（MANAGER_DISABLE_LLM=1 可强制关闭，供测试）。"""
    if os.environ.get("MANAGER_DISABLE_LLM") == "1":
        return False
    return llm_client.has_api_key()


def _index(node: str) -> int:
    return PIPELINE_NODES.index(node)


def _event(action: str, target: str, reason: str) -> dict:
    return {
        "node": "manager",
        "status": "decision",
        "action": action,
        "target": target,
        "reason": reason,
    }
