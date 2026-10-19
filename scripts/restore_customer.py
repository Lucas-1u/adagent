"""从客户发现的本地数据库恢复商家线索，并重跑 LLM 研究评估（不调用 JustOneAPI）。

背景：JustOneAPI 余额不足（业务码 601）时，客户发现节点无法再发起外部搜索；
但数据库中已保存了此前成功采集的真实商家线索（runs.payload）。
本脚本复用这些真实线索，只重跑 LLM 研究评估（QualificationAgent），
产出可用于下游节点的 customer_info。

用法（项目根目录）：
    python scripts/restore_customer.py                 # 自动选 leads 最多的一次运行
    python scripts/restore_customer.py --run-id <id>   # 指定 run
输出：
    data/recovered_customer.json（customer_info 结构，含追溯信息）
"""

import argparse
import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import llm_client  # noqa: E402
from customer_discovery.agent import QualificationAgent  # noqa: E402
from customer_discovery.integration import SharedLLMHTTP, project_customer_info  # noqa: E402
from customer_discovery.models import Lead, Run  # noqa: E402


def load_runs(db_path: Path) -> list[tuple[str, dict]]:
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute("select id, payload from runs order by started_at").fetchall()
    finally:
        con.close()
    return [(rid, json.loads(payload)) for rid, payload in rows]


def build_researcher() -> QualificationAgent:
    """复用总控的 app.llm_client（统一模型、统一配置）。"""
    llm_http = SharedLLMHTTP(llm_client)
    return QualificationAgent(
        llm_http, "https://shared-llm.invalid", "managed-by-app", llm_client.default_model()
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="从数据库恢复客户发现线索并重跑研究评估")
    parser.add_argument("--run-id", default=None, help="指定 run id（默认选 leads 最多的那次）")
    parser.add_argument("--db", default=str(ROOT / "data" / "discovery.sqlite3"))
    parser.add_argument(
        "--min-desc",
        type=int,
        default=60,
        help="演示优先：仅保留商家描述长度 >= 该值的线索（0 = 不过滤）",
    )
    args = parser.parse_args()

    db_path = Path(args.db)
    if not db_path.exists():
        print(f"数据库不存在：{db_path}")
        return 1

    candidates = [(rid, d) for rid, d in load_runs(db_path) if d.get("leads")]
    if not candidates:
        print("数据库里没有含商家线索的运行记录")
        return 1
    if args.run_id:
        candidates = [(rid, d) for rid, d in candidates if rid == args.run_id]
        if not candidates:
            print(f"未找到指定 run：{args.run_id}")
            return 1

    run_id, payload = max(candidates, key=lambda item: len(item[1].get("leads") or []))
    run = Run.model_validate(payload)
    print(f"恢复运行 {run.id}：{len(run.leads)} 条真实商家线索（api_calls={run.api_calls}）")

    target = run.request.target
    bounds = (run.request.taobao_min_sales, run.request.taobao_max_sales)
    researcher = build_researcher()

    # 演示优先：投影会优先拿 merchant.description 当"店铺特点"，
    # 只在描述有实质内容的线索里选，避免 highlights 退化成账号 ID。
    if args.min_desc > 0:
        kept = [
            lead for lead in run.leads
            if len((lead.merchant.description or "").strip()) >= args.min_desc
        ]
        if kept:
            print(f"演示筛选：{len(kept)}/{len(run.leads)} 条线索的商家描述完整（>= {args.min_desc} 字）")
            run.leads = kept
        else:
            print(f"警告：没有描述完整的线索，回退为全部 {len(run.leads)} 条")

    assessed: list[Lead] = []
    for index, lead in enumerate(run.leads, 1):
        merchant = lead.merchant
        name = merchant.name or f"{merchant.platform} {merchant.platform_id}"
        if merchant.identity_kind == "unresolved_product":
            print(f"  [{index}/{len(run.leads)}] 跳过商品级线索：{name}")
            continue
        try:
            assessment = researcher.analyze(
                merchant, target, bounds if merchant.platform == "taobao" else None
            )
        except Exception as exc:  # noqa: BLE001
            print(f"  [{index}/{len(run.leads)}] 评估失败：{name} -> {type(exc).__name__}: {exc}")
            assessed.append(Lead(merchant=merchant, assessment=None, analysis_error=str(exc)))
            continue
        print(
            f"  [{index}/{len(run.leads)}] {name} -> {assessment.decision} "
            f"(score={assessment.score}, confidence={assessment.confidence})"
        )
        assessed.append(Lead(merchant=merchant, assessment=assessment))

    run.leads = assessed
    run.status = "completed"
    customer_info = project_customer_info(run)

    out = ROOT / "data" / "recovered_customer.json"
    out.write_text(json.dumps(customer_info, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n恢复完成：")
    print(json.dumps(customer_info, ensure_ascii=False, indent=2))
    print(f"\n已写入：{out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
