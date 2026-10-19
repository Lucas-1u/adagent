"""
AI Marketing Employee —— 工作台服务（FastAPI + SSE）
===================================================

职责：
    - 提供前端工作台页面（web/）
    - GET /api/run：触发一次执行，SSE 流式推送事件
        - mode=realtime：真实运行（LangGraph 逐步推送）
        - mode=fast：回放最近一次真实运行（缓存）；无缓存则转为实时运行
    - GET /api/health：健康检查

SSE 事件协议：
    event: trace   data: {单条执行痕迹事件}（节点完成 / Manager 决策）
    event: state   data: {最终 state}（含 report）
    event: failed  data: {"message": "..."}
    event: end     data: {}（流结束标记，前端据此关闭连接）

运行（项目根目录）：
    .venv/Scripts/python.exe -m uvicorn app.server:app --host 127.0.0.1 --port 8890
"""

import json
import time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from app.graph import build_graph
from app.agents import poster_agent
from app import llm_client

ROOT = Path(__file__).resolve().parent.parent
WEB_DIR = ROOT / "web"
CACHE_FILE = WEB_DIR / ".cache" / "last_run.json"
FAILED_FILE = WEB_DIR / ".cache" / "last_run_failed.json"

app = FastAPI(title="AI Marketing Employee 工作台")
app.mount("/static", StaticFiles(directory=str(WEB_DIR)), name="static")

# 素材目录（海报等生成物）静态服务
OUTPUTS_DIR = ROOT / "outputs"
OUTPUTS_DIR.mkdir(exist_ok=True)
app.mount("/outputs", StaticFiles(directory=str(OUTPUTS_DIR)), name="outputs")


def _sse(event: str, data: dict) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _load_cache() -> dict | None:
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return None
    return None


def _save_cache(payload: dict) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _demo_grade(state: dict) -> bool:
    """这次运行是否够格当"演示缓存"：找到真实商家、且出了海报。

    不够格时（例如 JustOneAPI 额度耗尽导致"未发现可核查商家"），作品页面应当保留上一次
    可展示的运行结果，而不是把成果栏替换成空结果——失败本身仍会在本次会话的 trace 里可见。
    """
    info = (state or {}).get("customer_info") or {}
    if (info.get("extra") or {}).get("no_match"):
        return False
    shop = str(info.get("shop") or "")
    if not shop or "未发现" in shop or "未确定" in shop:
        return False
    return bool((state or {}).get("images", {}).get("poster"))


def _run_realtime(user_input: str):
    """真实运行：逐节点推送 trace 事件，最后推送运行时长与完整 state，并写入缓存。"""
    graph = build_graph()
    events: list[dict] = []
    last_state: dict = {}
    t0 = time.time()
    for mode, chunk in graph.stream(
        {"user_input": user_input, "trace": []},
        config={"recursion_limit": 50},
        stream_mode=["updates", "values"],
    ):
        if mode == "values":
            last_state = chunk
        elif mode == "updates":
            for _node, update in (chunk or {}).items():
                for ev in (update or {}).get("trace") or []:
                    events.append(ev)
                    yield _sse("trace", ev)
    duration = round(time.time() - t0, 1)
    if events:
        payload = {"input": user_input, "trace": events, "state": last_state, "duration": duration}
        if _demo_grade(last_state):
            _save_cache(payload)
        else:
            # 保留上一次可展示的运行；本次失败的完整记录另存，便于排查
            FAILED_FILE.parent.mkdir(parents=True, exist_ok=True)
            FAILED_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    yield _sse("duration", {"seconds": duration})
    yield _sse("state", last_state)


def _run_fast(user_input: str):
    """加速回放：按节奏重放最近一次真实运行；无缓存则转实时运行。"""
    cached = _load_cache()
    if not cached:
        yield from _run_realtime(user_input)
        return

    for ev in cached.get("trace") or []:
        time.sleep(0.7)  # 回放节奏：可读且不拖沓
        yield _sse("trace", ev)
    time.sleep(0.4)
    duration = cached.get("duration")
    if duration is not None:
        yield _sse("duration", {"seconds": duration})
    yield _sse("state", cached.get("state") or {})


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/health")
def health():
    return {
        "status": "ok",
        "has_cache": _load_cache() is not None,
        "has_key": llm_client.has_api_key(),
    }


@app.get("/api/last")
def last():
    """最近一次真实运行的成果快照（前端首屏直接呈现已有的交付物）。"""
    cached = _load_cache()
    if not cached:
        return {"has": False}
    return {
        "has": True,
        "input": cached.get("input") or "",
        "duration": cached.get("duration"),
        "trace": cached.get("trace") or [],
        "state": cached.get("state") or {},
    }


@app.get("/api/poster-spec")
def poster_spec():
    """最近一次运行对应的海报视觉规格（前端可据此实时重绘，不必重新生成图片）。"""
    cached = _load_cache() or {}
    state = cached.get("state") or {}
    if not state:
        return {"has": False}
    return {"has": True, "spec": poster_agent.build_spec(state)}


@app.get("/api/poster.png")
def poster_png(force: int = 0):
    """按最近一次运行重新渲染海报 PNG（force=1 忽略缓存），返回图片本体。"""
    cached = _load_cache() or {}
    state = cached.get("state") or {}
    if not state:
        raise HTTPException(status_code=404, detail="尚无运行结果")
    spec = poster_agent.build_spec(state)
    path, info = poster_agent.render_png(spec, force=bool(force))
    if not path:
        raise HTTPException(status_code=503, detail=info.get("reason") or "海报渲染失败")
    return FileResponse(ROOT / path, media_type="image/png")


@app.get("/api/run")
def run(input: str = "", mode: str = "realtime"):
    user_input = (input or "").strip()
    if not user_input:
        user_input = "寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广"

    def generator():
        try:
            if mode == "fast":
                yield from _run_fast(user_input)
            else:
                yield from _run_realtime(user_input)
        except Exception as exc:  # 兜底：任何异常以 failed 事件收尾，前端可恢复
            yield _sse("failed", {"message": f"{type(exc).__name__}: {exc}"})
        finally:
            yield _sse("end", {})

    return StreamingResponse(
        generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
