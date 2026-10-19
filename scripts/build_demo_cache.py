"""用恢复的真实商家数据构建演示缓存。

customer 节点复用 data/recovered_customer.json（真实采集 + LLM 研究评估的产物），
不发起外部搜索；其余节点（analysis / marketing / creative / final）照常真实运行。

用法（项目根目录）：
    python scripts/build_demo_cache.py
产物：
    web/.cache/last_run.json（工作台"加速回放"数据源）
"""

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents import customer_agent  # noqa: E402
from app.graph import build_graph  # noqa: E402

DEMO_INPUT = "寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广"
CACHE_PATH = ROOT / "web" / ".cache" / "last_run.json"


def main() -> int:
    ci_path = ROOT / "data" / "recovered_customer.json"
    if not ci_path.exists():
        print(f"缺少恢复数据：{ci_path}（请先运行 scripts/restore_customer.py）")
        return 1
    customer_info = json.loads(ci_path.read_text(encoding="utf-8"))

    # customer 节点复用真实已采集数据（JustOneAPI 余额受限，不再发起外部搜索）
    customer_agent.run = lambda state: {"customer_info": dict(customer_info)}  # type: ignore[assignment]

    graph = build_graph()
    t0 = time.time()
    state = graph.invoke({"user_input": DEMO_INPUT, "trace": []}, config={"recursion_limit": 50})
    duration = round(time.time() - t0, 1)

    payload = {
        "input": DEMO_INPUT,
        "trace": state.get("trace", []),
        "state": state,
        "duration": duration,
    }
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    CACHE_PATH.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    print(f"演示缓存已写入：{CACHE_PATH}（本次耗时 {duration}s）")
    print("trace 事件：", [e.get("status") for e in state.get("trace", [])])
    print("report.title  :", state.get("report", {}).get("title"))
    print("strategy.theme:", state.get("strategy", {}).get("theme"))
    print("analysis.user :", str(state.get("analysis", {}).get("user"))[:120])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
