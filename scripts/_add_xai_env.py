"""幂等地向 .env 追加 xAI 图像后端配置（XAI_API_KEY + CREATIVE_IMAGE_BACKEND=xai）。

用法：
    XAI_KEY=xai-xxx python scripts/_add_xai_env.py            # 改当前目录 .env
    XAI_KEY=xai-xxx python scripts/_add_xai_env.py /opt/ai-marketing/.env
"""

import os
import sys
from pathlib import Path

target = Path(sys.argv[1] if len(sys.argv) > 1 else ".env")
key = os.environ.get("XAI_KEY", "").strip()
if not key:
    raise SystemExit("缺少环境变量 XAI_KEY")

text = target.read_text(encoding="utf-8") if target.exists() else ""
add: list[str] = []
if "CREATIVE_IMAGE_BACKEND" not in text:
    add.append("CREATIVE_IMAGE_BACKEND=xai")
if "XAI_API_KEY" not in text:
    add.append(f"XAI_API_KEY={key}")

if not add:
    print("already configured:", target)
else:
    if text and not text.endswith("\n"):
        text += "\n"
    text += "\n".join(add) + "\n"
    target.write_text(text, encoding="utf-8")
    print("updated:", target, "->", [line.split("=")[0] for line in add])
