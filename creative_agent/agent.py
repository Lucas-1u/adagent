"""Creative Agent 主流程。

    state(上游) ──► 创意简报(LLM) ──► 文生图底图 ──► 多尺寸排版合成 ──► 素材包
                                                  └─► 配套文案 / 短视频脚本 / 投放建议

产出目录结构（一次 run）：
    output/<run>/
      brief.json          创意简报（给 orchestrator 写 state 用）
      brief.md            人类可读的创意简报（PPT / 汇报直接用）
      raw/                文生图原始底图（无文字）
      materials/          合成好的成品素材（多尺寸）
      materials.json      ★ 写入 state["images"] 的完整结果
      report.html         素材包预览页（演示 / 截图用）
"""
from __future__ import annotations

import time
import traceback
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import composer, design, quality
from .fallback import rule_based_brief
from .image_backend import BaseBackend, ImageBackendError, MockBackend, get_backend
from .llm import LLMClient
from .schema import CreativeResult, load_brief, normalize_state, save_json, stamp

# v2 设计版式引擎（默认）：四种经典商业版式，留白 ≥30%
DEFAULT_LAYOUTS = design.DEFAULT_DESIGNS
# v1 兼容版式（--engine classic）
CLASSIC_LAYOUTS = list(composer.LAYOUTS)


class CreativeAgent:
    """广告素材生成 Agent（队友 4）。只读上游字段，只写 state["images"]。"""

    def __init__(self,
                 image_backend: Optional[str] = None,
                 layouts: Optional[List[str]] = None,
                 use_llm: bool = True,
                 use_image_gen: bool = True,
                 quality: str = "medium",
                 timeout: int = 120,
                 engine: str = "design",
                 auto_palette: bool = True):
        self.image_backend_name = image_backend or "auto"
        self.engine = engine if engine in ("design", "classic") else "design"
        self.layouts = layouts or (DEFAULT_LAYOUTS if self.engine == "design"
                                   else CLASSIC_LAYOUTS)
        self.use_llm = use_llm
        self.use_image_gen = use_image_gen
        self.quality = quality
        self.auto_palette = auto_palette
        self.llm = LLMClient(timeout=timeout)
        self.backend: Optional[BaseBackend] = None
        self.log: List[str] = []

    # ------------------------------------------------------------------ 日志

    def _say(self, msg: str) -> None:
        line = f"[{time.strftime('%H:%M:%S')}] {msg}"
        self.log.append(line)
        print(line, flush=True)

    # ------------------------------------------------------------------ 主流程

    def run(self, state: Dict[str, Any], out_dir: str = "output/run",
            use_llm: Optional[bool] = None) -> CreativeResult:
        t0 = time.time()
        use_llm = self.use_llm if use_llm is None else use_llm
        out = Path(out_dir)
        for sub in ("raw", "materials", "_baseline"):
            (out / sub).mkdir(parents=True, exist_ok=True)

        norm = normalize_state(state)
        customer, analysis, strategy = norm["customer"], norm["analysis"], norm["strategy"]
        channels = "、".join(strategy.channels) if strategy.channels else "小红书、朋友圈"
        self._say(f"开始生成素材：{customer.describe() or '（未提供商家信息）'}")

        # 1) 创意简报 ------------------------------------------------------
        brief, source = self._creative_brief(customer, analysis, strategy, channels, use_llm)
        save_json(brief, str(out / "brief.json"))
        (out / "brief.md").write_text(self._brief_markdown(customer, analysis, strategy, brief),
                                      encoding="utf-8")
        self._say(f"创意简报完成（来源：{source}），主题「{brief.get('theme', '')}」")

        # 2) 图像后端 ------------------------------------------------------
        fallback_used = False
        if self.use_image_gen:
            if self.backend is None:
                self.backend = get_backend(None if self.image_backend_name == "auto"
                                           else self.image_backend_name,
                                           out_dir=out / "raw", quality=self.quality)
            self._say(f"图像后端：{self.backend.name}"
                      f"（模型 {getattr(self.backend, 'model', '-')}）")
        else:
            self.backend = MockBackend(out / "raw")
            fallback_used = True
            self._say("未启用文生图，使用本地程序化底图")

        # 3) 逐创意出图 + 排版 ---------------------------------------------
        concepts = (brief.get("concepts") or [])[:3]
        pal = brief.get("palette", composer.DEFAULT_PALETTE)
        fstyle = brief.get("font_style", "sans") or "sans"
        theme_tag = brief.get("theme", "")
        shop = customer.shop or ""

        posters: List[Dict[str, Any]] = []
        qa_reports: List[Dict[str, Any]] = []
        for i, concept in enumerate(concepts, start=1):
            tag = f"c{i}"
            spec = {
                "headline": concept.get("headline", ""),
                "headline_lines": concept.get("headline_lines") or [],
                "subheadline_lines": concept.get("subheadline_lines") or [],
                "subheadline": concept.get("subheadline", ""),
                "bullets": concept.get("bullets", []),
                "cta": concept.get("cta", ""),
            }
            bg_path = self._make_background(concept, tag, out, i)
            if bg_path is None:
                fallback_used = True
                bg_path = self._mock_background(concept, tag, out)
            self._say(f"  创意 {i}「{concept.get('name', '')}」底图就绪 -> "
                      f"{Path(bg_path).name}")
            for layout in self.layouts:
                dst = out / "materials" / f"{tag}_{layout}.png"
                try:
                    if self.engine == "design":
                        base_png = out / "_baseline" / f"{tag}_{layout}_base.png"
                        # 质检不过 → 自动加深文字区重出一张，最多修 2 轮
                        for attempt in range(3):
                            meta = design.compose(
                                bg_path, spec, str(dst), design=layout,
                                palette="" if self.auto_palette else pal,
                                font_family=fstyle if fstyle in ("sans", "serif") else "sans",
                                theme_tag=theme_tag, shop=shop, offer=strategy.offer,
                                address=strategy.address, phone=strategy.phone,
                                baseline_out=str(base_png),
                                contrast_boost=1.0 + 0.45 * attempt)
                            probe = quality.audit(meta["file"], meta.get("baseline"),
                                                  meta.get("headline_px"),
                                                  meta.get("elements", 0), meta.get("warnings"))
                            if probe["pass"] or attempt == 2:
                                break
                            self._say(f"      质检未过，第 {attempt + 1} 次自动加深重出 …")
                        meta["auto_repaired"] = attempt > 0
                    else:
                        meta = composer.compose(
                            bg_path, spec, str(dst), layout=layout,
                            palette=pal, font_style=fstyle, theme_tag=theme_tag,
                            shop=shop, offer=strategy.offer, address=strategy.address,
                            phone=strategy.phone)
                except Exception as exc:      # noqa: BLE001
                    self._say(f"  排版失败 {tag}/{layout}：{exc}")
                    continue
                meta.update({"concept": concept.get("name", f"创意{i}"),
                             "headline": spec["headline"], "index": i})
                # 设计质检：每次出图自动卡一遍规范（留白/压边/对比度/标题字号/元素数）
                if self.engine == "design":
                    report = quality.audit(meta["file"], meta.get("baseline"),
                                           meta.get("headline_px"), meta.get("elements", 0),
                                           meta.get("warnings"))
                    meta["quality"] = report
                    qa_reports.append(report)
                    if not report["pass"]:
                        bad = [c["check"] for c in report["checks"] if not c["pass"]]
                        self._say(f"      ⚠ 质检未达标 {tag}/{layout}：{'、'.join(bad)}")
                posters.append(meta)
                self._say(f"    ✓ {meta['channel']}  {meta['w']}x{meta['h']}  "
                          f"{dst.relative_to(out)}")

        # 4) 汇总素材包 ----------------------------------------------------
        result = CreativeResult(
            theme=brief.get("theme", ""),
            palette=pal,
            font_style=fstyle,
            concepts=concepts,
            posters=posters,
            copy=brief.get("copy", {}),
            video_script=brief.get("video_script", {}),
            publish_advice=brief.get("publish_advice", []),
            meta={
                "agent": "creative_agent",
                "version": "0.2.0",
                "brief_source": source,
                "engine": self.engine,
                "palette_mode": "auto" if (self.engine == "design" and self.auto_palette) else "brief",
                "image_backend": self.backend.name if self.backend else "none",
                "image_model": getattr(self.backend, "model", None),
                "fallback_used": fallback_used,
                "layouts": self.layouts,
                "materials_count": len(posters),
                "quality": {
                    "audited": len(qa_reports),
                    "passed": sum(1 for r in qa_reports if r["pass"]),
                    "avg_score": (sum(r["score"] for r in qa_reports if r["score"] is not None)
                                  // max(1, len([r for r in qa_reports if r["score"] is not None])))
                    if qa_reports else None,
                },
                "elapsed_s": round(time.time() - t0, 1),
                "generated_at": stamp(),
            })
        save_json(result.to_dict(), str(out / "materials.json"))
        if qa_reports:
            save_json({"summary": result.meta["quality"], "reports": qa_reports},
                      str(out / "quality.json"))
            self._say("设计质检：" + quality.summarize(qa_reports))
        (out / "report.html").write_text(self._report_html(customer, result), encoding="utf-8")
        self._say(f"完成：{len(posters)} 张素材 / {result.meta['elapsed_s']}s -> {out}/")
        return result

    # ------------------------------------------------------------------ 内部

    def _creative_brief(self, customer, analysis, strategy, channels, use_llm):
        if use_llm and self.llm.available:
            try:
                data = self.llm.creative_brief(customer.describe(), analysis.describe(),
                                               strategy.describe(), channels)
                if data.get("concepts"):
                    data["_source"] = f"llm:{self.llm.model}"
                    return self._fill_defaults(data), f"LLM({self.llm.model})"
                self._say("LLM 返回缺少 concepts，转规则兜底")
            except Exception as exc:          # noqa: BLE001
                self._say(f"LLM 生成失败（{exc}），转规则兜底")
        else:
            self._say("未配置 LLM key 或已禁用，使用规则兜底创意库")
        return rule_based_brief(customer.describe(), analysis.describe(),
                                strategy.describe(), channels, strategy.theme), "rule_based"

    @staticmethod
    def _fill_defaults(data: Dict[str, Any]) -> Dict[str, Any]:
        """补齐 LLM 可能漏掉的字段，避免下游 KeyError。"""
        data.setdefault("theme", "本季限定活动")
        data.setdefault("palette", composer.DEFAULT_PALETTE)
        data.setdefault("font_style", "sans")
        data.setdefault("copy", {})
        data.setdefault("video_script", {"duration_s": 15, "shots": []})
        data.setdefault("publish_advice", [])
        for c in data.get("concepts", []):
            c.setdefault("bullets", [])
            c.setdefault("cta", "到店体验")
            c.setdefault("headline", data["theme"])
            c.setdefault("subheadline", "")
            if c.get("subheadline") and not c.get("subheadline_lines"):
                c["subheadline_lines"] = [c["subheadline"]]
        return data

    @staticmethod
    def _info_line(strategy) -> str:
        """底部信息条：优先活动信息与地址，过长则截断（避免溢出）。"""
        parts = [p for p in (strategy.offer, strategy.address, strategy.phone) if p]
        info = "  |  ".join(parts)
        return info[:46]

    def _make_background(self, concept: Dict[str, Any], tag: str, out: Path,
                         index: int) -> Optional[str]:
        if not self.use_image_gen or self.backend is None:
            return None
        prompt = (concept.get("visual_prompt") or "").strip()
        if not prompt:
            return None
        prompt = (f"{prompt}. Clean composition with uncluttered area, "
                  "absolutely no text, no letters, no numbers, no logo, no watermark, "
                  "no poster, no signage. "
                  "STRICTLY NO PEOPLE: no humans, no faces, no portraits, no hands, "
                  "no fingers, no body parts, no staff, no customers, no crowds; "
                  "product, props and scene only.")
        primary = self.layouts[0]
        if self.engine == "design":
            kind = (design.DESIGNS.get(primary) or {}).get("kind", "hero")
            ratio = {"hero": "portrait", "split": "portrait",
                     "center": "square", "magazine": "landscape"}.get(kind, "portrait")
        else:
            ratio = "portrait" if primary in ("poster_2x3", "xhs_3x4") else "square"
        try:
            return self.backend.generate(prompt, ratio=ratio, tag=f"{tag}_raw")
        except (ImageBackendError, Exception) as exc:   # noqa: BLE001
            self._say(f"  文生图失败（{exc}），该创意改用本地兜底底图")
            return None

    def _mock_background(self, concept: Dict[str, Any], tag: str, out: Path) -> str:
        mock = MockBackend(out / "raw")
        return mock.generate(concept.get("visual_prompt", tag), ratio="portrait", tag=tag)

    # ------------------------------------------------------------------ 汇报物料

    @staticmethod
    def _brief_markdown(customer, analysis, strategy, brief: Dict[str, Any]) -> str:
        L = [f"# 广告素材创意简报", "",
             f"- 商家：{customer.describe() or '（未提供）'}",
             f"- 分析：{analysis.describe() or '（未提供）'}",
             f"- 策划：{strategy.describe() or '（未提供）'}",
             f"- 主题：**{brief.get('theme', '')}**　配色：{brief.get('palette', '')}"
             f"　字体：{brief.get('font_style', '')}",
             f"- 生成时间：{stamp()}", "", "## 创意方向", ""]
        for i, c in enumerate(brief.get("concepts", []), 1):
            L += [f"### {i}. {c.get('name', '')}",
                  f"- 思路：{c.get('idea', '')}",
                  f"- 主标题：**{c.get('headline', '')}**",
                  f"- 副标题：{c.get('subheadline', '')}",
                  f"- 卖点标签：{' / '.join(c.get('bullets', []))}",
                  f"- CTA：{c.get('cta', '')}",
                  f"- 视觉 prompt：`{c.get('visual_prompt', '')}`", ""]
        copy = brief.get("copy", {})
        L += ["## 配套文案", "",
              f"**标题**：{copy.get('title', '')}", "",
              f"{copy.get('body', '')}", "",
              "标签：" + " ".join(copy.get("hashtags", [])), "",
              f"CTA：{copy.get('cta', '')}", ""]
        vs = brief.get("video_script", {})
        L += [f"## 短视频脚本（{vs.get('duration_s', 15)}s）", "",
              "| 时间 | 画面 | 口播 | 字幕 |", "| --- | --- | --- | --- |"]
        for s in vs.get("shots", []):
            L.append(f"| {s.get('t', '')} | {s.get('visual', '')} | "
                     f"{s.get('voiceover', '')} | {s.get('onscreen', '')} |")
        L += ["", "## 投放建议", ""]
        for a in brief.get("publish_advice", []):
            L.append(f"- **{a.get('channel', '')}**｜{a.get('best_time', '')}｜"
                     f"{a.get('format', '')}｜{a.get('note', '')}")
        return "\n".join(L)

    @staticmethod
    def _report_html(customer, result: CreativeResult) -> str:
        def esc(s: Any) -> str:
            return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))

        cards = []
        for p in result.posters:
            rel = "materials/" + Path(p["file"]).name
            qa = p.get("quality") or {}
            badge = ""
            if qa.get("score") is not None:
                ok = qa.get("pass")
                badge = (f'<span class="qa {"ok" if ok else "bad"}">'
                         f'质检 {qa["score"]} 分 {"通过" if ok else "未达标"}</span>')
            cards.append(f"""
      <figure class="card">
        <img src="{rel}" alt="{esc(p.get('headline', ''))}">
        <figcaption><b>{esc(p['channel'])}</b><span>{p['w']}×{p['h']} · {esc(p.get('concept',''))}</span>
        {badge}<code>{esc(rel)}</code></figcaption>
      </figure>""")
        qsum = (result.meta or {}).get("quality") or {}
        qa_line = (f'｜ 设计质检：{qsum.get("passed")}/{qsum.get("audited")} 通过'
                   f'（均分 {qsum.get("avg_score")}）') if qsum.get("audited") else ""
        shots = "".join(
            f"<tr><td>{esc(s.get('t',''))}</td><td>{esc(s.get('visual',''))}</td>"
            f"<td>{esc(s.get('voiceover',''))}</td><td>{esc(s.get('onscreen',''))}</td></tr>"
            for s in (result.video_script.get("shots") or []))
        advice = "".join(
            f"<li><b>{esc(a.get('channel',''))}</b> · {esc(a.get('best_time',''))} · "
            f"{esc(a.get('format',''))}<br><span>{esc(a.get('note',''))}</span></li>"
            for a in result.publish_advice)
        copy = result.copy or {}
        tags = " ".join(esc(t) for t in copy.get("hashtags", []))
        meta = result.meta or {}
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>广告素材包 · {esc(customer.shop or 'AI Marketing Employee')}</title>
<style>
 :root{{--ink:#1d1b1a;--muted:#7c736d;--line:#e6e0da;--bg:#faf7f4;--accent:#c4763a}}
 *{{box-sizing:border-box}}
 body{{margin:0;background:var(--bg);color:var(--ink);
   font-family:-apple-system,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}}
 header{{padding:40px 48px 24px;border-bottom:1px solid var(--line)}}
 h1{{margin:0 0 8px;font-size:28px}}
 .sub{{color:var(--muted);font-size:14px;line-height:1.9}}
 .pill{{display:inline-block;margin:0 8px 8px 0;padding:4px 12px;border-radius:999px;
   background:#fff;border:1px solid var(--line);font-size:13px}}
 main{{padding:28px 48px 64px;max-width:1400px}}
 h2{{font-size:19px;margin:36px 0 14px;padding-left:10px;border-left:4px solid var(--accent)}}
 .grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:22px}}
 .card{{margin:0;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden}}
 .card img{{width:100%;display:block}}
 figcaption{{padding:10px 12px;font-size:12.5px;color:var(--muted);display:grid;gap:3px}}
 figcaption b{{color:var(--ink);font-size:13.5px}}
 code{{font-size:11px;color:#a49a93}}
 .copybox{{background:#fff;border:1px solid var(--line);border-radius:14px;padding:20px 22px;line-height:1.9}}
 .copybox h3{{margin:0 0 10px;font-size:17px}}
 .kw{{color:var(--accent);font-size:13px}}
 .qa{{display:inline-block;padding:1px 8px;border-radius:999px;font-size:11px;width:fit-content}}
 .qa.ok{{background:#e8f3ea;color:#2c6b3f;border:1px solid #cfe4d4}}
 .qa.bad{{background:#fdecea;color:#a4342a;border:1px solid #f3cfc9}}
 table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:14px;overflow:hidden;font-size:13.5px}}
 th,td{{padding:10px 12px;text-align:left;border-bottom:1px solid var(--line);vertical-align:top}}
 th{{background:#f2ece7;font-size:12.5px;color:var(--muted)}}
 ul.advice{{list-style:none;padding:0;margin:0;display:grid;gap:10px}}
 ul.advice li{{background:#fff;border:1px solid var(--line);border-radius:12px;padding:12px 16px;font-size:14px}}
 ul.advice span{{color:var(--muted);font-size:13px}}
 footer{{padding:24px 48px 48px;color:var(--muted);font-size:12.5px}}
</style></head><body>
<header>
  <h1>{esc(customer.shop or '广告素材包')} · {esc(result.theme)}</h1>
  <div class="sub">主题配色：{esc(result.palette)} ｜ 字体风格：{esc(result.font_style)}
    ｜ 素材数：{meta.get('materials_count')} 张 ｜ 生成耗时：{meta.get('elapsed_s')}s{qa_line}<br>
    创意来源：{esc(meta.get('brief_source'))} ｜ 图像引擎：{esc(meta.get('image_backend'))}
    ({esc(meta.get('image_model') or '-')}) ｜ 生成时间：{esc(meta.get('generated_at'))}</div>
  <div style="margin-top:14px">{''.join(f'<span class="pill">{esc(c.get("name",""))}</span>' for c in result.concepts)}</div>
</header>
<main>
  <h2>成品素材（一张底图 → 多尺寸适配）</h2>
  <div class="grid">{''.join(cards)}</div>

  <h2>配套文案</h2>
  <div class="copybox">
    <h3>{esc(copy.get('title',''))}</h3>
    <p>{esc(copy.get('body',''))}</p>
    <p>{''.join(f'· {esc(b)}<br>' for b in copy.get('bullets', []))}</p>
    <p class="kw">{tags}</p>
    <p>{esc(copy.get('cta',''))}</p>
  </div>

  <h2>短视频脚本（{esc(result.video_script.get('duration_s', 15))} 秒）</h2>
  <table><thead><tr><th>时间</th><th>画面</th><th>口播</th><th>字幕</th></tr></thead>
  <tbody>{shots}</tbody></table>

  <h2>投放建议</h2>
  <ul class="advice">{advice}</ul>
</main>
<footer>本页面由 Creative Agent 自动生成 · AI Marketing Employee 项目</footer>
</body></html>"""
