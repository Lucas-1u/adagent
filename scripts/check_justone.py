"""JustOneAPI 余额自检
====================

不打 LLM、不落盘，只发一次最便宜的搜索请求，看接口返回码：

    code=0    → 余额与 token 都正常
    code=601  → INSUFFICIENT BALANCE（余额不足，要去控制台充值）
    code=100/303/400 → token 无效或参数不对

用法：
    python scripts/check_justone.py
    python scripts/check_justone.py 武汉 复古咖啡店
"""

import json
import os
import sys
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]

MEANING = {
    0: "正常（余额充足）",
    100: "token 无效",
    303: "参数不合法",
    400: "请求被拒绝",
    600: "接口错误",
    601: "INSUFFICIENT BALANCE —— 余额不足，去 justoneapi.com 控制台充值",
    602: "权限不足（当前套餐不支持该接口）",
}


def load_env() -> None:
    env_path = ROOT / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip())
    # 也接受真实进程环境变量（优先级更高）


def check(keyword: str) -> int:
    token = os.environ.get("JUSTONEAPI_TOKEN", "").strip()
    if not token:
        print("✗ 没有 JUSTONEAPI_TOKEN：请在 .env 里填，或设置环境变量")
        return 2

    masked = token[:4] + "…" + token[-2:] if len(token) > 8 else "***"
    print(f"token {masked}（长度 {len(token)}）· 探测关键词「{keyword}」")

    try:
        with httpx.Client(timeout=30) as client:
            resp = client.get(
                "https://api.justoneapi.com/api/xiaohongshu/search-user/v2",
                params={"token": token, "keyword": keyword, "page": 1},
            )
    except Exception as exc:  # noqa: BLE001
        print(f"✗ 连不上接口：{type(exc).__name__}: {exc}")
        return 2

    try:
        data = resp.json()
    except ValueError:
        print(f"✗ 返回不是 JSON（HTTP {resp.status_code}）：{resp.text[:200]}")
        return 2

    code = data.get("code")
    message = data.get("message") or data.get("msg") or ""
    print(f"HTTP {resp.status_code} · code={code} · message={message}")
    print("结论:", MEANING.get(code, "未知返回码，看 message"))

    if code == 0:
        payload = data.get("data")
        count = len(payload) if isinstance(payload, list) else (len(payload or {}) if isinstance(payload, dict) else "?")
        print("数据条数:", count, "→ 余额够跑，可以点「开始执行」")
        return 0
    if code == 601:
        print("→ 现在跑链路一定拿不到商家（报告会进「未交付」态，不会编方案）")
    print("原始返回:", json.dumps(data, ensure_ascii=False)[:300])
    return 1


def main() -> int:
    load_env()
    keyword = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "武汉 复古咖啡店"
    return check(keyword)


if __name__ == "__main__":
    raise SystemExit(main())
