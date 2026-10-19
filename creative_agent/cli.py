"""Creative Agent 命令行入口。

常用：
  # 用 demo brief 跑一遍完整流程（默认自动选后端）
  python -m creative_agent --brief demo/briefs/pindou_qixi.json --out output/pindou

  # 只出小红书 + 朋友圈两种尺寸
  python -m creative_agent --brief demo/briefs/coffee_autumn.json --layouts xhs_3x4,square_1x1

  # 完全离线（不发 LLM、不调用任何图片接口），用于演示现场兜底
  python -m creative_agent --brief demo/briefs/pindou_qixi.json --no-llm --no-image

  # 查看图像后端可用性
  python -m creative_agent --check
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .agent import CLASSIC_LAYOUTS, CreativeAgent, DEFAULT_LAYOUTS
from .composer import LAYOUTS as CLASSIC_LAYOUTS_MAP
from .design import DESIGNS
from .image_backend import describe_backends
from .llm import LLMClient
from .schema import load_brief

LAYOUTS = DESIGNS
CLASSIC_LAYOUTS = list(CLASSIC_LAYOUTS_MAP)


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser("creative_agent", description="广告素材生成 Agent")
    p.add_argument("--brief", help="brief JSON 路径（= 上游 state 片段）")
    p.add_argument("--out", default="output/run", help="输出目录")
    p.add_argument("--engine", default="design", choices=["design", "classic"],
                   help="design=四种经典商业版式（默认）；classic=v1 版式")
    p.add_argument("--layouts", default="", help="要出的版式，逗号分隔；留空用该引擎的全套")
    p.add_argument("--palette", action="store_true",
                   help="使用 LLM 指定的配色（默认从底图自动提取主色）")
    p.add_argument("--backend", default="auto",
                   choices=["auto", "xai", "codex", "openai_compat", "mock"],
                   help="图像后端")
    p.add_argument("--quality", default="medium", choices=["low", "medium", "high"])
    p.add_argument("--no-llm", action="store_true", help="不使用大模型，走规则兜底创意")
    p.add_argument("--no-image", action="store_true", help="不调用文生图，使用本地程序化底图")
    p.add_argument("--check", action="store_true", help="打印环境与后端可用性后退出")
    # 也支持不写 brief 文件，直接命令行给信息
    p.add_argument("--shop", default="", help="店名")
    p.add_argument("--city", default="", help="城市")
    p.add_argument("--category", default="", help="业态")
    p.add_argument("--theme", default="", help="活动主题")
    p.add_argument("--offer", default="", help="活动信息（价格/时间）")
    p.add_argument("--address", default="", help="门店地址")
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)

    if args.check:
        llm = LLMClient()
        print("== 环境检查 ==")
        print(f"LLM  : {'可用' if llm.available else '不可用（将走规则兜底）'}  "
              f"model={llm.model} base={llm.base_url}")
        from .design import FONT_FILES, _font_path
        print("字体：")
        for fam, cands in FONT_FILES.items():
            got = _font_path(fam)
            print(f"  - {fam:<8} {'✓ ' + Path(got).name if got else '✗ 未找到（回落到系统字体）'}")
        print("图像后端：")
        _, rows = describe_backends()
        for r in rows:
            print(f"  - {r['name']:<14} {'✓ 可用' if r['available'] else '✗ 不可用':<8} "
                  f"model={r['model']}")
        return 0

    valid = LAYOUTS if args.engine == "design" else CLASSIC_LAYOUTS
    default = DEFAULT_LAYOUTS if args.engine == "design" else CLASSIC_LAYOUTS
    layouts = [l.strip() for l in args.layouts.split(",") if l.strip()] or list(default)
    bad = [l for l in layouts if l not in valid]
    if bad:
        print(f"未知版式：{bad}，该引擎可选 {list(valid)}", file=sys.stderr)
        return 2

    if args.brief:
        state = load_brief(args.brief)
    else:
        state = {
            "customer_info": {"shop": args.shop, "city": args.city, "category": args.category},
            "analysis": {},
            "strategy": {"theme": args.theme, "offer": args.offer, "address": args.address},
        }

    agent = CreativeAgent(image_backend=args.backend, layouts=layouts,
                          use_llm=not args.no_llm, use_image_gen=not args.no_image,
                          quality=args.quality, engine=args.engine,
                          auto_palette=not args.palette)
    result = agent.run(state, out_dir=args.out)

    print("\n== 产出摘要 ==")
    print(result.summary())
    print(f"\n素材包预览页：file://{Path(args.out).resolve() / 'report.html'}")
    print(f"state['images'] 字段：{Path(args.out).resolve() / 'materials.json'}")
    print("\n" + json.dumps({"images": {
        "posters": [{"file": p["file"], "channel": p["channel"]} for p in result.posters],
        "copy": result.copy, "video_script": result.video_script,
        "publish_advice": result.publish_advice}}, ensure_ascii=False)[:600] + " ...")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
