# AdAgent

基于 Agent 的"AI 一人广告团队"（黑客松项目）。

> 发现客户 → 分析需求 → 制定方案 → 生成广告素材

## 目录结构

```
AdAgent/
├── README.md                    # 本文件
├── requirements.txt             # Python 依赖
├── .env.example                 # 环境变量模板（复制为 .env 填 key）
├── app/
│   ├── state.py                 # ★ 数据契约 v0.5（全项目唯一权威）
│   ├── llm_client.py            # 统一 LLM 封装（DeepSeek）
│   ├── manager.py               # 决策层（入口裁剪 + 质检 + 路由）
│   ├── guard.py                 # 节点保护壳（降级兜底 + trace 记录）
│   ├── graph.py                 # LangGraph 编排
│   ├── final.py                 # 报告整合层
│   ├── search_client.py         # DeepSeek 网页搜索
│   ├── server.py                # 工作台服务（FastAPI + SSE）
│   └── agents/
│       ├── customer_agent.py         # 客户发现（数据源接入层）
│       ├── search_customer_agent.py  # 客户发现 · DeepSeek 搜索实现
│       ├── analysis_agent.py         # 商家分析（队友 2）
│       ├── marketing_agent.py        # 营销策划（队友 3）
│       ├── creative_agent.py         # 广告素材（接入层 → creative_agent/）
│       └── _normalize.py             # 模型输出键名归一
├── customer_discovery/          # JustOneAPI 采集链路（队友 1 · 备用数据源）
├── creative_agent/              # 素材模块（队友 4 · 设计引擎 / 质检 / 图像后端）
├── web/                         # 工作台前端（原生三件套，零构建）
├── scripts/                     # 运维脚本（打包 / 配置 / 案例 / 字体下载）
├── deploy/                      # 部署脚本与 Nginx 示例
└── smoke_test.py                # 冒烟测试（离线 / 降级 / 决策链 / 素材质检）
```

## 快速开始

```bash
# 1. 安装依赖
pip install -r requirements.txt

# 2. 配置 key：复制 .env.example 为 .env，填入 DEEPSEEK_API_KEY

# 3. 端到端跑一遍（业务节点为 mock 数据；有 key 时启用 Manager 智能决策）
python -m app.graph
```

> **本机（总控）环境**：已在项目内建好虚拟环境 `.venv`（Python 3.13 + LangGraph 1.2，
> 依赖齐全，2026-10-15 验证跑通）。运行命令：
>
> ```
> .venv\Scripts\python.exe -m app.graph      # 完整链路
> .venv\Scripts\python.exe smoke_test.py     # 冒烟测试（不需要 key）
.venv\Scripts\python.exe scripts\verify_pipeline.py   # 走一遍 LangGraph 真链路，打印海报节点 detail 与 report.poster
> .venv\Scripts\python.exe -m app.agents.poster_agent render   # 出三张海报样例（拼豆 / 咖啡 / 烘焙）
> ```

## 广告素材（creative_agent）怎么出

**视觉与文字解耦**：底图由图像模型生成（或本地程序化兜底），中文标题/副标/卖点/CTA 由 Pillow 设计引擎排版——
中文永不糊字，改价格/改地址不用重新出图。一次运行产出 **3 创意 × 5 版式 = 15 张**成品，并自动做设计质检
（留白率 / 安全边距 / WCAG 对比度 / 标题字号 / 元素数，不合格自动加深重出）。

```bash
python scripts/fetch_fonts.py   # 首次运行前执行：下载三款开源字体到 assets/fonts/（约 45MB，不入库）
```

> 队友的原始交付包（`analysis-agent-v0.4` / `customer_discovery_handoff` / `黑客松（营销策划）` / `creative-agent`）
> **未入库**，代码已并入 `app/agents/`、`customer_discovery/`、`creative_agent/`，仓库可直接运行。


## 客户发现：数据源两选一

| 数据源 | 开关 | 成本 | 说明 |
| --- | --- | --- | --- |
| **DeepSeek 搜索（默认）** | DISCOVERY_PROVIDER=search | 约 **0.01 元/轮** | 官方 /v1/tools/search 抓公开网页（新闻、点评、小红书笔记…），LLM 只从证据里挑一家真实门店；不需要额外账号 |
| JustOneAPI 采集 | DISCOVERY_PROVIDER=justone | 3 次调用/轮 | 队友 1 的 customer_discovery/ 链路，需要 token 与余额 |

搜索结果带 	itle / url / site_name / date / authority / content，客户节点把命中的链接与原文片段写进
customer_info.extra.source_urls / evidence，可逐条回溯。找不到门店时报告进入「未交付」态。

`ash
.venv\Scripts\python.exe scripts\check_search.py     # 冒烟：一次搜索，打印结果条数与样例
`

## 找店只花 2 次接口调用（默认单店模式）

需求是「找到一家店，给它做海报」，所以客户发现默认不扫全网：计划裁剪成 **1 组关键词 × 1 个平台**，
拿到第一家可核查商家就收工，实际调用 **1 次搜索 + 1 次详情 = 2 次 JustOneAPI**（预算上限 3）。
遇到余额/额度类错误（`upstream_business_code_601`）会**立即断电**，只花 1 次就停，不再重试。
需要完整市场扫描时用环境变量放开（进程环境优先于 `.env`）：

| 变量 | 默认 | 作用 |
| --- | --- | --- |
| `DISCOVERY_PLAN_QUERIES` | 1 | 执行计划里的前几组「关键词 × 平台」 |
| `DISCOVERY_MAX_API_CALLS` | 3 | JustOneAPI 调用总预算 |
| `DISCOVERY_MAX_TOOL_CALLS` | 6 | LLM 工具调用上限 |
| `DISCOVERY_SHOPS_PER_SEARCH` / `DISCOVERY_MERCHANTS_PER_SEARCH` | 1 / 1 | 每组目标商家数（完整扫描时通常 5） |
| `DISCOVERY_PAGES` | 1 | 可翻页上限 |
| `DISCOVERY_STOP_AFTER_FIRST` | 1 | 第一家确认即停止 |

```bash
.venv\Scripts\python.exe scripts\check_discovery_budget.py   # 断言「1 次搜索 + 1 次详情」的预算生效
```

## 演示案例切换（离线也能演）

真实跑一轮要 1–2 分钟（客户发现最慢），所以线上演示走「加速回放」：服务端按 0.7s/事件重放
`web/.cache/last_run.json`。已备好 4 个案例（2 个真实搜索 + 2 个早期样例），一条命令切换：

```bash
.venv\Scripts\python.exe scripts\use_case.py                 # 列出案例（含店铺名 / 时长 / 海报）
.venv\Scripts\python.exe scripts\use_case.py pindou_search   # 金豆和瑞可的拼豆店（真实搜索，68.5s）
.venv\Scripts\python.exe scripts\use_case.py coffee_search   # 老时光·Old Times（真实搜索，51.1s）
.venv\Scripts\python.exe scripts\use_case.py pindou          # 早期样例（虚构数据，32.3s）
.venv\Scripts\python.exe scripts\use_case.py --save 名字      # 把当前结果存成新案例
```

注意：真实运行如果没搜到可核查商家，报告会进入「未交付」态（讲清卡点、不编方案），
这种运行不会覆盖演示缓存（另存为 `web/.cache/last_run_failed.json`）。

## 分工速览

| 角色 | 负责 | 对应文件 / 字段 |
| --- | --- | --- |
| 总控 | Orchestrator + 前端 UI | `graph.py` · `state.py` · `llm_client.py` |
| 队友 1 | 数据接口、搜索、信息整理 | `customer_agent.py` → `customer_info` |
| 队友 2 | Prompt 设计、用户画像、商业分析 | `analysis_agent.py` → `analysis` |
| 队友 3 | 营销模板、文案、策略 | `marketing_agent.py` → `strategy` |
| 队友 4 | 图片 API、Prompt 优化、展示 | `poster_agent.py` → `images`（代码出图） |

## 核心机制

- **数据契约**（`app/state.py`）：每个 Agent 只写自己的字段，互不覆盖，
  最后统一输出 —— 防止"能跑但组合不起来"。
- **统一 LLM 封装**（`app/llm_client.py`）：DeepSeek，OpenAI 兼容协议，
  全组只维护一处。

字段定义与接口约定见 `app/state.py`；各节点只写自己的字段，互不覆盖。
