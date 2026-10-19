"""DeepSeek 搜索冒烟：一次搜索，打印条数与样例（演示前自检用）。

用法：
    python scripts/check_search.py
    python scripts/check_search.py 武汉 复古咖啡店
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from app import search_client  # noqa: E402


def main() -> int:
    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "武汉 复古咖啡店 探店"
    print(f"搜索「{query}」…（一次约 0.005 元）")
    try:
        results = search_client.search(query, count=5, need_content=False)
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 搜索失败：{type(exc).__name__}: {exc}")
        print("  → 检查 DEEPSEEK_API_KEY 与账户余额")
        return 1

    print(f"✓ 拿到 {len(results)} 条结果")
    for item in results[:5]:
        print(f"  - {item.get('title')} | {item.get('site_name')} | {(item.get('date') or '')[:10]}")
        print(f"    {item.get('url')}")
    return 0 if results else 1


if __name__ == "__main__":
    raise SystemExit(main())
