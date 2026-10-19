"""下载开源中文字体到 assets/fonts/。

为什么需要这一步：v1 只用 macOS 系统字体（黑体/宋体各一档），没有字重对比，
排版怎么调都显得"廉价"。v2 换用三款**免费商用**的可变/设计字体：

  · Noto Sans SC（思源黑体）   —— OFL，9 档字重，正文/副标题
  · Noto Serif SC（思源宋体）  —— OFL，9 档字重，文艺调性
  · Smiley Sans（得意黑）      —— OFL，标题专用，倾斜切角很有设计感

字体文件共约 45MB，不适合塞进代码仓库，所以写成脚本按需拉取。
拉取失败不影响运行：design.py 会自动回落到系统字体。

用法：  python scripts/fetch_fonts.py
"""
from __future__ import annotations

import sys
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEST = ROOT / "assets" / "fonts"

SOURCES = {
    "NotoSansSC.ttf":
        "https://github.com/google/fonts/raw/main/ofl/notosanssc/NotoSansSC%5Bwght%5D.ttf",
    "NotoSerifSC.ttf":
        "https://github.com/google/fonts/raw/main/ofl/notoserifsc/NotoSerifSC%5Bwght%5D.ttf",
    "SmileySans-Oblique.ttf":
        "https://github.com/atelier-anchor/smiley-sans/releases/download/v2.0.1/"
        "smiley-sans-v2.0.1.zip",
}
# 得意黑只有 zip 包，单独处理
ZIP_MEMBER = "SmileySans-Oblique.ttf"


def _download(url: str, dest: Path) -> None:
    print(f"  ↓ {url.split('/')[-1][:52]} ...", end="", flush=True)
    with urllib.request.urlopen(url, timeout=180) as resp:
        data = resp.read()
    dest.write_bytes(data)
    print(f" {len(data) / 1024 / 1024:.1f}MB")


def main() -> int:
    DEST.mkdir(parents=True, exist_ok=True)
    print(f"字体目录：{DEST}")
    missing = [n for n in SOURCES if not (DEST / n).is_file()]
    if not missing:
        print("三款字体都已就位 ✅")
        return 0

    for name in missing:
        url = SOURCES[name]
        target = DEST / name
        try:
            if url.endswith(".zip"):
                tmp = DEST / "_smiley.zip"
                _download(url, tmp)
                import zipfile
                with zipfile.ZipFile(tmp) as zf:
                    member = next((m for m in zf.namelist() if m.endswith(ZIP_MEMBER)), None)
                    if member is None:
                        raise RuntimeError("压缩包里没找到 SmileySans-Oblique.ttf")
                    (DEST / ZIP_MEMBER).write_bytes(zf.read(member))
                    print(f"    解出 {ZIP_MEMBER}")
                tmp.unlink(missing_ok=True)
            else:
                _download(url, target)
        except Exception as exc:                       # noqa: BLE001
            print(f"    失败：{exc}（将回落到系统字体）")

    ok = [n for n in SOURCES if (DEST / n).is_file()]
    print(f"\n可用字体：{ok if ok else '无 —— 将使用系统字体'}")
    print("提示：字体均为 OFL 开源许可，可商用；随仓库分发请保留其 LICENSE。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
