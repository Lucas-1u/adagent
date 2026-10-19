"""演示案例切换器
=================

把 `web/.cache/cases/<name>.json` 复制成 `web/.cache/last_run.json`（服务读的那份），
这样前端首屏「交付成果」和加速回放都会变成这套结果。

用法：
    python scripts/use_case.py              # 列出已有案例
    python scripts/use_case.py pindou       # 切成拼豆案例
    python scripts/use_case.py coffee       # 切成咖啡案例
    python scripts/use_case.py --save 新名字  # 把当前 last_run.json 存成一个新案例
"""

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "web" / ".cache"
CASES = CACHE / "cases"
LIVE = CACHE / "last_run.json"


def describe(path: Path) -> str:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        return f"（读取失败：{exc}）"
    state = data.get("state") or {}
    report = state.get("report") or {}
    shop = (state.get("customer_info") or {}).get("shop") or "?"
    duration = data.get("duration")
    poster = (report.get("poster") or {}).get("path") or "-"
    return f"{shop} · {report.get('title') or '-'} · {duration}s · {poster}"


def main(argv: list[str]) -> int:
    CASES.mkdir(parents=True, exist_ok=True)

    if len(argv) >= 3 and argv[1] == "--save":
        name = argv[2].strip()
        if not LIVE.exists():
            print("当前没有 last_run.json 可保存")
            return 1
        target = CASES / f"{name}.json"
        shutil.copyfile(LIVE, target)
        print(f"已保存案例 {name} → {target.relative_to(ROOT)}")
        return 0

    if len(argv) >= 2:
        name = argv[1].strip()
        source = CASES / f"{name}.json"
        if not source.exists():
            print(f"没有案例 {name}，现有：{[p.stem for p in CASES.glob('*.json')]}")
            return 1
        shutil.copyfile(source, LIVE)
        print(f"已切换到 {name}：{describe(LIVE)}")
        return 0

    print("现有案例：")
    for path in sorted(CASES.glob("*.json")):
        print(f"  {path.stem:12s} {describe(path)}")
    if LIVE.exists():
        print(f"\n当前生效：{describe(LIVE)}")
    print("\n用法：python scripts/use_case.py <案例名>")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
