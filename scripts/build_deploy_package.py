"""构建服务器部署包（只含代码 + .env，不含测试数据 / 缓存 / 产物）。

用法（项目根目录）：
    python scripts/build_deploy_package.py

产物：
    deploy/dist/ai-marketing-<时间戳>.tar.gz
    ├── app/                 业务代码（含 __pycache__ 排除）
    ├── customer_discovery/  JustOneAPI 备用链路（含运行时读取的 .md）
    ├── web/                 前端 + 海报模板 + 字体（不含 .cache）
    ├── docs/                文档
    ├── scripts/             运维脚本
    ├── requirements.txt / smoke_test.py / README.md / DESIGN.md / PRODUCT.md
    └── .env                 ★ 含真实 key，传输时注意保密

不含：data/、outputs/、web/.cache/、.impeccable/、.venv/、队友交付源目录、海报产物。
"""

import tarfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = ROOT / "deploy" / "dist"

# 目录 → 允许的扩展名（白名单）
INCLUDE_DIRS: list[tuple[str, set[str]]] = [
    ("app", {".py"}),
    ("customer_discovery", {".py", ".md"}),
    # 广告素材模块（队友 4）：代码 + 设计引擎字体（约 44MB，出图必需）
    ("creative_agent", {".py"}),
    ("assets", {".ttf", ".woff2", ".woff"}),
    ("web", {".html", ".css", ".js", ".woff2", ".woff", ".ttf"}),
    ("docs", {".md"}),
    ("scripts", {".py", ".sh"}),
]

# 单文件白名单（.env 含 key，按用户要求一并打包）
INCLUDE_FILES = [
    "requirements.txt",
    "smoke_test.py",
    "README.md",
    "DESIGN.md",
    "PRODUCT.md",
    ".env",
]

# 路径中任意一段命中则跳过
EXCLUDE_PARTS = {"__pycache__", ".cache", ".git", ".venv", ".impeccable", "node_modules"}


def main() -> int:
    stamp = time.strftime("%Y%m%d-%H%M")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / f"ai-marketing-{stamp}.tar.gz"

    added: list[str] = []
    with tarfile.open(out, "w:gz") as tar:
        for name, exts in INCLUDE_DIRS:
            base = ROOT / name
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file():
                    continue
                rel = path.relative_to(ROOT)
                if any(part in EXCLUDE_PARTS for part in rel.parts):
                    continue
                if path.suffix.lower() not in exts:
                    continue
                tar.add(path, arcname=rel.as_posix())
                added.append(rel.as_posix())

        for name in INCLUDE_FILES:
            path = ROOT / name
            if path.is_file():
                tar.add(path, arcname=name)
                added.append(name)

    size_kb = out.stat().st_size / 1024
    has_env = ".env" in added
    print(f"打包完成：{out}")
    print(f"大小：{size_kb:.0f} KB ｜ 文件数：{len(added)} ｜ 含 .env：{'是（含 key，注意保密）' if has_env else '否'}")
    print("\n== 包内清单 ==")
    for item in added:
        print("  ", item)

    # 安全自检：不该出现的内容
    bad = [n for n in added if n.startswith(("data/", "outputs/", ".impeccable/", "web/.cache/"))]
    if bad:
        print("\n[警告] 包内出现不该上传的路径：", bad)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
