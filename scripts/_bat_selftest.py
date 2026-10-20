"""临时自检：把「启动服务/打开浏览器」替换为打印后跑一遍启动脚本，
用于验证 GBK 编码、中文显示、if 块与等待循环的语法（不产生任何副作用）。
跑完自动删除测试副本。
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
text = (ROOT / "启动工作台.bat").read_text(encoding="gbk")
text = text.replace(
    'start "AdAgent Server" /min ".venv\\Scripts\\python.exe" -m uvicorn app.server:app --host 127.0.0.1 --port 8890',
    "echo [TEST] SKIP_START_SERVER",
)
text = text.replace('start "" "http://127.0.0.1:8890/?demo=1"', "echo [TEST] SKIP_OPEN_BROWSER")
text = text.replace("if %tries% lss 20", "if %tries% lss 3")

test = ROOT / "_selftest.bat"
test.write_text(text, encoding="gbk", newline="\r\n")

try:
    p = subprocess.run(
        ["cmd", "/c", "_selftest.bat"],
        cwd=str(ROOT),
        capture_output=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
    )
    print("exit:", p.returncode)
    print("--- output ---")
    print(p.stdout.decode("gbk", errors="replace"))
    err = p.stderr.decode("gbk", errors="replace").strip()
    if err:
        print("--- stderr ---")
        print(err)
finally:
    test.unlink(missing_ok=True)
    print("（测试副本已删除）")
