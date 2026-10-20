"""把网页 PPT 打包成真正的单文件（图片 / 中文字体 / 动画库全部内嵌）。

用法：
    python scripts/build_standalone_ppt.py            # 完整字体（默认，队友可自由改文案）
    python scripts/build_standalone_ppt.py --subset   # 只保留当前用到的字（体积最小，改文案需重跑）

输入：ppt/index.html + ppt/images/* + assets/fonts/*.ttf
输出：ppt/standalone.html（可直接发给别人，双击打开即看，无需图片目录与网络）
"""

import base64
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "ppt" / "index.html"
DST = ROOT / "ppt" / "standalone.html"
FONTS = ROOT / "assets" / "fonts"
SKILL = Path(r"C:/Users/11196/.workbuddy/skills/guizang-ppt-skill")

SUBSET = "--subset" in sys.argv

html = SRC.read_text(encoding="utf-8")

# ---------------------------------------------------------------- 1) 字符集（仅子集模式需要）
chars_file = ROOT / "ppt" / ".chars.txt"
if SUBSET:
    plain = re.sub(r"<script[\s\S]*?</script>", "", html)
    plain = re.sub(r"<style[\s\S]*?</style>", "", plain)
    plain = re.sub(r"<[^>]+>", "", plain)
    extra = "，。、；：？！“”‘’（）《》【】—…·0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz%¥$+-–—/|·"
    chars = "".join(sorted(set(plain) | set(extra)))
    chars_file.write_text(chars, encoding="utf-8")
    print(f"字符集：{len(chars)} 个不同字符")


# ---------------------------------------------------------------- 2) 中文字体
def to_woff2(src: Path, out: Path) -> bool:
    """完整字体转 woff2（保留全部字形与可变字重轴）—— 队友改文案也不会缺字。"""
    if not src.exists():
        print(f"  [跳过] 缺少字体 {src}")
        return False
    try:
        from fontTools.ttLib import TTFont

        font = TTFont(str(src))
        font.flavor = "woff2"
        font.save(str(out))
    except Exception as exc:  # noqa: BLE001
        print(f"  [失败] {src.name}: {exc}")
        return False
    print(f"  {src.name} -> {out.name} {out.stat().st_size / 1024 / 1024:.1f} MB（完整）")
    return True


def subset_woff2(src: Path, out: Path) -> bool:
    """只保留字符集里出现的字形（体积最小）。"""
    if not src.exists():
        print(f"  [跳过] 缺少字体 {src}")
        return False
    cmd = [
        sys.executable, "-m", "fontTools.subset", str(src),
        f"--text-file={chars_file}", "--flavor=woff2",
        f"--output-file={out}", "--no-hinting", "--desubroutinize",
    ]
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"  [失败] {src.name}: {res.stderr[:200]}")
        return False
    print(f"  {src.name} -> {out.name} {out.stat().st_size // 1024} KB（子集）")
    return True


conv = subset_woff2 if SUBSET else to_woff2
serif_b64 = sans_b64 = ""
tmp_serif, tmp_sans = ROOT / "ppt" / ".serif.woff2", ROOT / "ppt" / ".sans.woff2"
if conv(FONTS / "NotoSerifSC.ttf", tmp_serif):
    serif_b64 = base64.b64encode(tmp_serif.read_bytes()).decode()
if conv(FONTS / "NotoSansSC.ttf", tmp_sans):
    sans_b64 = base64.b64encode(tmp_sans.read_bytes()).decode()

# ---------------------------------------------------------------- 3) 图片内嵌
img_count = 0
for img in sorted((ROOT / "ppt" / "images").glob("*")):
    rel = f"images/{img.name}"
    if rel not in html:
        continue
    mime = "image/png" if img.suffix.lower() == ".png" else "image/jpeg"
    b64 = base64.b64encode(img.read_bytes()).decode()
    html = html.replace(rel, f"data:{mime};base64,{b64}")
    img_count += 1
print(f"内嵌图片：{img_count} 张")

# ---------------------------------------------------------------- 4) 移除 CDN 依赖
html = re.sub(r'<link rel="preconnect"[^>]*>\s*', "", html)
html = re.sub(r'<link href="https://fonts\.googleapis\.com[^"]*"[^>]*>\s*', "", html)
html = re.sub(
    r'<script src="https://unpkg\.com/lucide[^"]*"></script>\s*<script>lucide\.createIcons\(\);</script>\s*',
    "", html,
)

# ---------------------------------------------------------------- 5) 注入 @font-face + 备用字体栈
fontface = (
    "<style>\n"
    + (
        "@font-face{font-family:'NotoSerifSC-X';src:url(data:font/woff2;base64,"
        + serif_b64
        + ") format('woff2');font-weight:100 900;font-display:swap}\n"
        if serif_b64 else ""
    )
    + (
        "@font-face{font-family:'NotoSansSC-X';src:url(data:font/woff2;base64,"
        + sans_b64
        + ") format('woff2');font-weight:100 900;font-display:swap}\n"
        if sans_b64 else ""
    )
    + "</style>\n"
)
html = html.replace("</head>", fontface + "</head>", 1)

html = html.replace(
    '--serif-zh:"Noto Serif SC",source-han-serif-sc,serif;',
    '--serif-zh:"NotoSerifSC-X","Noto Serif SC","Source Han Serif SC","Microsoft YaHei",serif;',
)
html = html.replace(
    '--sans-zh:"Noto Sans SC",source-han-sans-sc,sans-serif;',
    '--sans-zh:"NotoSansSC-X","Noto Sans SC","Microsoft YaHei","PingFang SC",sans-serif;',
)
html = html.replace(
    '--serif-en:"Playfair Display","Source Serif 4",Georgia,serif;',
    '--serif-en:Georgia,"Times New Roman",serif;',
)
html = html.replace(
    '--mono:"IBM Plex Mono",ui-monospace,monospace;',
    '--mono:ui-monospace,Consolas,"Courier New",monospace;',
)

# ---------------------------------------------------------------- 6) 内嵌动画库（Motion One）
motion_js = SKILL / "assets" / "motion.min.js"
if motion_js.exists():
    mb64 = base64.b64encode(motion_js.read_bytes()).decode()
    html = html.replace(
        "motion = await import('./assets/motion.min.js');",
        f"motion = await import('data:text/javascript;base64,{mb64}');",
    )
    print(f"内嵌动画库：{motion_js.stat().st_size // 1024} KB")

# ---------------------------------------------------------------- 7) 输出
DST.write_text(html, encoding="utf-8")
for tmp in (chars_file, tmp_serif, tmp_sans):
    try:
        tmp.unlink()
    except OSError:
        pass
print(f"\n单文件已生成：{DST}")
print(f"大小：{DST.stat().st_size / 1024 / 1024:.2f} MB（{'子集字体' if SUBSET else '完整字体'}）")
