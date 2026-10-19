"""验证海报 agent 已接入 LangGraph 全流程（mock 客户发现，其余真实）。

用法：python scripts/verify_pipeline.py
      （有 key 时 analysis / marketing 会真实调 LLM，约 30 秒；无 key 自动降级）
"""

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

os.environ.setdefault("MANAGER_DISABLE_LLM", "1")

from app.agents import customer_agent
from app.agents._mocks import customer_agent as customer_mock

# LangGraph 在 build 时绑定节点函数，先替换再 build
customer_agent.run = customer_mock.run

from app.graph import build_graph

graph = build_graph()
state = graph.invoke(
    {"user_input": "寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广", "trace": []},
    config={"recursion_limit": 50},
)

print("=== 决策 / 执行流 ===")
for event in state.get("trace") or []:
    if event.get("status") == "decision":
        print(f"  [manager] {event.get('action'):8} → {event.get('target'):10} {event.get('reason')}")
    elif event.get("status") in ("done", "degraded", "failed"):
        detail = event.get("detail")
        extra = f"  detail={json.dumps(detail, ensure_ascii=False)}" if detail else ""
        print(f"  [{event.get('node'):9}] {event.get('status'):9} {event.get('elapsed')}s{extra}")

images = state.get("images") or {}
poster = state.get("report", {}).get("poster") or {}
print("\n=== 海报 ===")
print("  images.poster :", images.get("poster"))
print("  images.motif  :", images.get("motif"), "| size:", images.get("size"), "| spec:", images.get("spec_id"))
print("  report.poster :", json.dumps(poster, ensure_ascii=False))
print("  文件存在      :", os.path.isfile(poster.get("path", "")))
