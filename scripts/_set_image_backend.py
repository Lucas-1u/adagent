"""幂等地设置图像后端相关的 .env 项（存在则替换、不存在则追加）。

用法：
    ARK_KEY=ark-xxx python scripts/_set_image_backend.py            # 改当前目录 .env
    ARK_KEY=ark-xxx python scripts/_set_image_backend.py /opt/ai-marketing/.env
"""

import os
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
ark_key = os.environ.get("ARK_KEY", "").strip()
if not ark_key:
    raise SystemExit("缺少环境变量 ARK_KEY")

pairs = {
    "CREATIVE_IMAGE_BACKEND": "openai_compat",
    "CREATIVE_IMAGE_BASE_URL": "https://ark.cn-beijing.volces.com/api/v3",
    "CREATIVE_IMAGE_API_KEY": ark_key,
    "CREATIVE_IMAGE_MODEL": "ep-20261019125740-zpg4n",
}

lines = target.read_text(encoding="utf-8").splitlines() if target.exists() else []
out: list[str] = []
seen: set[str] = set()
for line in lines:
    stripped = line.strip()
    key = None
    if "=" in stripped and not stripped.startswith("#"):
        key = stripped.split("=", 1)[0].strip()
    if key in pairs:
        out.append(f"{key}={pairs[key]}")
        seen.add(key)
    else:
        out.append(line)

for key, val in pairs.items():
    if key not in seen:
        out.append(f"{key}={val}")

target.write_text("\n".join(out) + "\n", encoding="utf-8")
print("updated:", target, "| set:", sorted(pairs))
