/* ============================================================
   AdAgent · 线路调度台（前端逻辑）
   SSE 事件 → 线路图状态 + 执行流列表 + 报告渲染
   ============================================================ */

(function () {
  "use strict";

  var $ = function (id) { return document.getElementById(id); };

  var STATIONS = ["customer", "analysis", "marketing", "creative", "final"];
  var DEMO_INPUT = "寻找武汉地区适合年轻女性消费的拼豆店，帮它做一轮推广";
  var STATION_X = { customer: 70, analysis: 220, marketing: 370, creative: 520, final: 680 };
  var LABELS = {
    customer: "客户发现", analysis: "商家分析", marketing: "营销策划",
    creative: "广告素材", final: "方案整合"
  };
  var DEFAULT_WORDS = {
    customer: "STANDBY", analysis: "STANDBY", marketing: "STANDBY",
    creative: "STANDBY", final: "TERMINUS"
  };

  var app = {
    mode: "realtime",
    running: false,
    started: false,
    plan: null,
    stationState: {},
    es: null,
    popTimer: null,
    runSeconds: null,
    demoMode: false
  };

  /* ---------------- 工具 ---------------- */
  function fmtTime(v) {
    return (typeof v === "number" && v >= 0.1) ? v.toFixed(1) + "s" : "—";
  }

  function esc(s) {
    return String(s == null ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  /* ---------------- 界面复位 ---------------- */
  function resetUI() {
    app.plan = null;
    app.started = false;
    STATIONS.forEach(function (k) {
      app.stationState[k] = "idle";
      var g = $("st-" + k);
      g.classList.remove("is-idle", "is-run", "is-done", "is-degraded", "is-skipped");
      g.classList.add("is-idle");
      g.querySelector(".st-state").textContent = DEFAULT_WORDS[k];
    });
    ["customer", "analysis", "marketing", "creative"].forEach(function (k) {
      $("seg-" + k).classList.remove("is-on");
    });
    var rp = $("retry-path");
    rp.style.transition = "none";
    rp.style.strokeDasharray = 600;
    rp.style.strokeDashoffset = 600;
    $("retry-tag").classList.remove("is-on");
    $("retry-arrow").classList.remove("is-on");
    $("dispatch-pop").hidden = true;
    $("stream").innerHTML = "";
    $("report").innerHTML =
      '<div class="report-skeleton" id="report-skeleton">' +
      '<div class="sk" style="width:62%"></div><div class="sk" style="width:88%"></div>' +
      '<div class="sk" style="width:76%"></div><div class="sk" style="width:40%"></div></div>' +
      '<p class="report-idle" id="report-idle">团队交稿后，这里呈现本案交付：主视觉海报、主标语、成品文案与执行清单。</p>';
    $("error-bar").hidden = true;
    $("flow-note").textContent = "ROUTING";
    $("result-note").textContent = "GENERATING";
  }

  function setControlsDisabled(disabled) {
    $("req").disabled = disabled;
    document.querySelectorAll(".chip").forEach(function (c) { c.disabled = disabled; });
    document.querySelectorAll(".mode").forEach(function (m) { m.disabled = disabled; });
    $("start").disabled = disabled || !$("req").value.trim();
  }

  /* ---------------- 站点状态 ---------------- */
  function setStation(key, state, timeText) {
    app.stationState[key] = state;
    var g = $("st-" + key);
    g.classList.remove("is-idle", "is-run", "is-done", "is-degraded", "is-skipped");
    g.classList.add("is-" + state);
    var word = g.querySelector(".st-state");
    if (state === "done") { word.textContent = "DONE" + (timeText && timeText !== "—" ? " · " + timeText : ""); }
    else if (state === "run") { word.textContent = "RUNNING"; }
    else if (state === "degraded") { word.textContent = "DEGRADED" + (timeText && timeText !== "—" ? " · " + timeText : ""); }
    else if (state === "skipped") { word.textContent = "此单不停"; }
    else { word.textContent = DEFAULT_WORDS[key]; }
  }

  function updateRunningStation() {
    if (!app.started) return;
    var order = app.plan || ["customer", "analysis", "marketing", "creative"];
    for (var i = 0; i < order.length; i++) {
      var k = order[i];
      var s = app.stationState[k];
      if (s !== "done" && s !== "degraded" && s !== "skipped") {
        setStation(k, "run");
        return;
      }
    }
    if (app.stationState["final"] !== "done" && app.stationState["final"] !== "degraded") {
      setStation("final", "run");
    }
  }

  /* ---------------- 执行流列表 ---------------- */
  function setStreamIdle() {
    var st = $("stream");
    st.innerHTML = '<li class="ev ev-idle">等待发车 —— 输入需求后点击「开始执行」</li>';
  }

  function pushStream(li) {
    var st = $("stream");
    st.appendChild(li);
    st.scrollTop = st.scrollHeight;
  }

  function appendStreamNode(ev) {
    var li = document.createElement("li");
    li.className = "ev ev-node";
    var word = ev.status === "done" ? "DONE" : ev.status === "degraded" ? "DEGRADED" : String(ev.status || "").toUpperCase();
    li.innerHTML =
      '<span class="ev-name">' + esc(LABELS[ev.node] || ev.node) + "</span>" +
      '<span class="ev-state mono is-' + esc(ev.status) + '">' + word + "</span>" +
      '<span class="ev-time mono">' + fmtTime(ev.elapsed) + "</span>";
    pushStream(li);
  }

  var ACTION_WORDS = { plan: "计划", "continue": "质检通过", retry: "退回重做", rollback: "回退重做", accept: "超限接受", skip: "跳过" };

  function appendStreamDecision(ev) {
    var li = document.createElement("li");
    li.className = "ev ev-decision";
    li.innerHTML =
      '<span class="ev-tag mono">' + esc(ACTION_WORDS[ev.action] || "调度注") + "</span>" +
      '<span class="ev-text">' + esc(ev.reason || "") + "</span>";
    pushStream(li);
  }

  var PURPOSE_WORDS = { intake: "入口裁剪", check: "质检判断", summary: "方案综述", chat: "生成", chat_json: "生成" };

  function llmField(label, text) {
    return '<div class="llm-field"><b>' + label + "</b><pre>" + esc(text) + "</pre></div>";
  }

  function appendStreamLlm(ev) {
    var li = document.createElement("li");
    li.className = "ev ev-llm";
    var purpose = PURPOSE_WORDS[ev.purpose] || ev.purpose || "LLM 调用";
    var t = fmtTime(ev.elapsed);
    var meta = esc(ev.model || "") + (t !== "—" ? " · " + t : "");
    var body =
      (ev.system ? llmField("系统提示", ev.system) : "") +
      (ev.prompt ? llmField("提示词", ev.prompt) : "") +
      (ev.reasoning ? llmField("思考过程", ev.reasoning) : "") +
      (ev.response ? llmField("响应", ev.response) : "");
    li.innerHTML =
      '<button class="llm-head" type="button">' +
      '<span class="llm-tag mono">LLM 记录</span>' +
      '<span class="llm-purpose">' + esc(purpose) + "</span>" +
      '<span class="llm-meta mono">' + meta + "</span>" +
      '<span class="llm-caret mono">＋</span>' +
      "</button>" +
      '<div class="llm-body" hidden>' + body + "</div>";
    pushStream(li);
  }

  var NEXT_STATION = { customer: "analysis", analysis: "marketing", marketing: "creative", creative: "final" };

  /* ---------------- 调度注浮卡 ---------------- */
  function showDispatch(ev) {
    var pop = $("dispatch-pop");
    pop.hidden = false;
    $("dispatch-tag").textContent = ACTION_WORDS[ev.action] || "调度注";
    var reason = ev.reason || "";
    $("dispatch-text").textContent = reason.length > 44 ? reason.slice(0, 44) + "…" : reason;
    var key = (ev.target && STATION_X[ev.target] != null) ? ev.target
      : (app.plan && app.plan.length ? app.plan[0] : "analysis");
    var pct = (STATION_X[key] / 760) * 100;
    pop.classList.remove("is-left", "is-right");
    if (pct < 16) { pop.classList.add("is-left"); }
    else if (pct > 84) { pop.classList.add("is-right"); }
    pop.style.left = pct + "%";
    pop.classList.remove("is-pop", "is-out");
    void pop.offsetWidth;
    pop.classList.add("is-pop");
    clearTimeout(app.popTimer);
    app.popTimer = setTimeout(function () { pop.classList.add("is-out"); }, 4000);
  }

  /* 返工折线：从重做站与下一站的外侧折回，避免压到站名文字 */
  function drawRetour(target) {
    if (STATION_X[target] == null || NEXT_STATION[target] == null) { return; }
    var x1 = STATION_X[target];
    var x2 = STATION_X[NEXT_STATION[target]];
    var lx = x1 - 56;
    var rx = x2 + 56;
    var path = $("retry-path");
    path.setAttribute("d", "M" + lx + ",138 L" + lx + ",196 L" + rx + ",196 L" + rx + ",138");
    var len = 600;
    try { len = path.getTotalLength(); } catch (err) { len = 600; }
    path.style.transition = "none";
    path.style.strokeDasharray = len;
    path.style.strokeDashoffset = len;
    void path.getBoundingClientRect();
    path.style.transition = "stroke-dashoffset .9s cubic-bezier(.16, 1, .3, 1)";
    path.style.strokeDashoffset = 0;
    var mx = (lx + rx) / 2;
    var arrow = $("retry-arrow");
    arrow.setAttribute("points", mx + ",189 " + (mx - 13) + ",196 " + mx + ",203");
    arrow.classList.add("is-on");
    var tag = $("retry-tag");
    tag.setAttribute("x", mx);
    tag.classList.add("is-on");
  }

  /* ---------------- SSE 事件处理 ---------------- */
  function handleTrace(ev) {
    if (ev.status === "llm") {
      appendStreamLlm(ev);
    } else if (ev.node === "manager") {
      if (ev.action === "plan" && ev.detail && Array.isArray(ev.detail.run)) {
        app.plan = ev.detail.run;
        STATIONS.forEach(function (k) {
          if (k !== "final" && app.plan.indexOf(k) === -1) setStation(k, "skipped");
        });
      }
      if (ev.action === "retry" || ev.action === "rollback") {
        if (ev.target) {
          drawRetour(ev.target);
          setStation(ev.target, "run");
        }
      }
      if (["plan", "retry", "rollback", "accept", "skip"].indexOf(ev.action) !== -1) {
        showDispatch(ev);
      }
      appendStreamDecision(ev);
    } else if (STATION_X[ev.node] != null) {
      var state = ev.status === "degraded" ? "degraded" : "done";
      setStation(ev.node, state, fmtTime(ev.elapsed));
      if (ev.node !== "final") $("seg-" + ev.node).classList.add("is-on");
      appendStreamNode(ev);
    }
    updateRunningStation();
  }

  function handleState(state, opts) {
    opts = opts || {};
    renderReport(state, opts);
    if (app.stationState["final"] !== "done" && app.stationState["final"] !== "degraded") {
      setStation("final", "done");
    }
    var total = (typeof app.runSeconds === "number") ? app.runSeconds.toFixed(1) + "s" : "";
    var blocked = !!(state && state.report && state.report.outcome === "blocked");
    var note = $("result-note");
    note.classList.toggle("is-blocked", blocked);
    if (blocked) {
      note.textContent = "NO DELIVERABLE";
      $("flow-note").textContent = (opts.history ? "LAST RUN" : "ARRIVED") + (total ? " · " + total : "");
      return;
    }
    if (opts.history) {
      note.textContent = "DELIVERED";
      $("flow-note").textContent = "LAST RUN" + (total ? " · " + total : "");
      return;
    }
    note.textContent = "READY";
    $("flow-note").textContent = "ARRIVED" + (total ? " · " + total : "");
  }

  function showError(msg) {
    var bar = $("error-bar");
    bar.hidden = false;
    bar.textContent = msg;
  }

  function finishRun() {
    if (!app.running) return;
    app.running = false;
    setControlsDisabled(false);
    if (app.stationState["final"] !== "done" && app.stationState["final"] !== "degraded") {
      setStation("final", "done");
    }
    $("seg-creative").classList.add("is-on");
  }

  /* ---------------- 交付成果渲染 ---------------- */
  function section(title, html) {
    return '<div class="report-sec"><h4>' + esc(title) + "</h4>" + html + "</div>";
  }

  function resolveAsset(p) {
    if (!p) { return ""; }
    return /^https?:/i.test(p) ? p : "/" + String(p).replace(/^\.?\//, "");
  }

  function pillList(items) {
    var arr = (items || []).filter(function (t) { return !!t; });
    if (!arr.length) { return ""; }
    return '<ul class="tagline">' + arr.map(function (t) {
      return "<li>" + esc(t) + "</li>";
    }).join("") + "</ul>";
  }

  function shortChannel(ch) {
    return String(ch || "").split(/[（(]/)[0].trim();
  }

  /* 主题句兜底成"标语"：取冒号前短句，过长就截断 —— 海报没生成时别把整段主题当大标题 */
  function shortSlogan(theme) {
    var t = String(theme || "").trim();
    if (!t) { return ""; }
    var head = t.split(/[:：]/)[0].trim();
    if (head.length >= 6 && head.length <= 20) { return head; }
    return t.length > 20 ? t.slice(0, 20) + "…" : t;
  }

  function secHead(num, title, aside) {
    return '<h4 class="dlv-sec-h"><span class="mono">' + esc(num) + "</span>" + esc(title) +
      (aside ? '<span class="dlv-sec-x">' + aside + "</span>" : "") + "</h4>";
  }

  function stepsList(steps) {
    var arr = (steps || []).filter(function (t) { return !!t; });
    if (!arr.length) { return ""; }
    return '<ol class="steps collapsible" style="--clamp-h:216px">' +
      arr.map(function (t) {
        return "<li>" + esc(String(t).replace(/^\s*\d+\s*[.、:：]\s*/, "")) + "</li>";
      }).join("") + "</ol>" +
      '<button class="more-btn" type="button" hidden>展开全部</button>';
  }

  /* 依据区：客户概况 / 分析结论 / 分渠道打法（报告中每一条都能追溯到上游字段） */
  function evidenceBlocks(r) {
    var out = [];
    var c = r.customer || {};
    if (c.shop) {
      var lines = ["<p>" + esc(c.shop) + (c.type ? " · " + esc(c.type) : "") + "</p>"];
      if (c.platform) { lines.push('<p class="kv"><b>来源</b>' + esc(c.platform) + "</p>"); }
      if (c.highlights) { lines.push('<p class="kv"><b>店铺特点</b><span>' + esc(c.highlights) + "</span></p>"); }
      if (c.potential_need) { lines.push('<p class="kv"><b>潜在需求</b><span>' + esc(c.potential_need) + "</span></p>"); }
      out.push(section("客户简报", lines.join("")));
    }
    var a = r.analysis || {};
    if (a.user) {
      var probs = (a.problems || []).map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("");
      var sugs = (a.suggestions || []).map(function (p) { return "<li>" + esc(p) + "</li>"; }).join("");
      out.push(section("洞察结论",
        '<p class="kv"><b>目标用户</b><span>' + esc(a.user) + "</span></p>" +
        "<h5>营销问题</h5><ul>" + probs + "</ul>" +
        "<h5>改进建议</h5><ul>" + sugs + "</ul>"));
    }
    var s = r.strategy || {};
    var plans = (s.extra || {}).channel_plans || [];
    if (plans.length) {
      out.push(section("分渠道打法", plans.map(function (p) {
        return '<div class="plan-row"><p class="plan-ch">' +
          '<span class="mono">' + esc(shortChannel(p.channel)) + "</span>" + esc(p.content_format || "") + "</p>" +
          "<p>" + esc(p.content_idea || "") + "</p>" +
          (p.cta ? '<p class="kv"><b>行动指引</b><span>' + esc(p.cta) + "</span></p>" : "") +
          "</div>";
      }).join("")));
    }
    return out.join("");
  }

  function renderReport(state, opts) {
    var r = state && state.report;
    if (!r) { return; }
    opts = opts || {};
    if (r.outcome === "blocked") { renderBlocked(r, opts); return; }

    var c = r.customer || {};
    var s = r.strategy || {};
    var extra = s.extra || {};
    var im = r.images || {};
    var poster = resolveAsset(im.poster);
    var out = [];

    out.push('<div class="dlv">');

    /* 交付头 */
    out.push('<header class="dlv-head">');
    out.push('<p class="dlv-kicker mono">' + (opts.history ? "LAST DELIVERABLE" : "DELIVERABLE") + "</p>");
    out.push('<h3 class="dlv-title">' + esc(r.title || "营销方案") + "</h3>");
    var meta = [];
    if (r.generated_at) { meta.push('<span class="mono">' + esc(r.generated_at) + "</span>"); }
    if (c.platform) { meta.push("来源 · " + esc(c.platform)); }
    if (c.type) { meta.push(esc(c.type)); }
    if (meta.length) { out.push('<p class="dlv-meta">' + meta.join('<span class="meta-dot">/</span>') + "</p>"); }
    out.push("</header>");

    /* 成果主轴：海报 + 主标语 + 渠道 / 关键词 */
    out.push('<section class="hero">');
    out.push('<figure class="hero-poster">');
    if (poster) {
      out.push('<button class="poster-btn" type="button" data-zoom="' + esc(poster) + '" aria-label="查看海报大图">');
      out.push('<img class="poster-img" src="' + esc(poster) + '" alt="AI 生成的营销海报"' +
        " onerror=\"this.closest('.hero-poster').classList.add('is-missing')\">");
      out.push("</button>");
      var pb = r.poster || {};
      var pbMeta = [];
      if (pb.size) { pbMeta.push(pb.size.replace("x", "×")); }
      var LAYOUT_LABEL = { bead: "珠板拼贴", arch: "窗前内景", street: "巷口街景", still: "静物桌景" };
      if (pb.layout) {
        pbMeta.push(LAYOUT_LABEL[pb.layout] || String(pb.layout).toUpperCase());
      } else if (pb.motif) {
        pbMeta.push(String(pb.motif).split("-")[0].toUpperCase());
      }
      var renderInfo = ((im.extra || {}).render || {});
      if (renderInfo.elapsed) { pbMeta.push(renderInfo.elapsed + "s"); }
      out.push('<figcaption class="hero-cap"><span class="mono cap-tag">AI POSTER</span>' +
        "<span>" + esc(im.copywriting || "") + "</span>" +
        (pbMeta.length ? '<span class="cap-meta mono">' + esc(pbMeta.join(" · ")) + "</span>" : "") +
        '<span class="cap-acts"><button class="cap-btn" type="button" data-zoom="' + esc(poster) + '">查看大图</button>' +
        '<a class="cap-btn" href="' + esc(poster) + '" download>下载原图</a></span></figcaption>');
      out.push('<div class="poster-ph"><span class="mono">POSTER</span>素材加载失败 · 待重新生成</div>');
    } else {
      out.push('<div class="poster-ph"><span class="mono">POSTER</span>素材待接入</div>');
    }
    out.push("</figure>");

    out.push('<div class="hero-text">');
    var pbPoster = r.poster || {};
    var slogan = String(pbPoster.slogan || shortSlogan(s.theme) || "").trim();
    if (slogan) {
      out.push('<p class="hero-k mono">SLOGAN</p>');
      out.push('<p class="hero-slogan">' + esc(slogan) + "</p>");
      if (pbPoster.subline && pbPoster.subline !== slogan) {
        out.push('<p class="hero-sub">' + esc(pbPoster.subline) + "</p>");
      }
    }
    var chans = (s.channels || []).map(shortChannel);
    if (chans.length) {
      out.push('<p class="hero-k mono">CHANNELS</p>');
      out.push(pillList(chans));
    }
    if ((s.keywords || []).length) {
      out.push('<p class="hero-k mono">KEYWORDS</p>');
      out.push(pillList(s.keywords));
    }
    out.push("</div>");
    out.push("</section>");

    /* 素材包：整套多版式素材（默认收起，点缩略图可左右翻看） */
    var posters = ((im.extra || {}).posters || []).filter(function (p) { return p && p.file; });
    if (posters.length > 1) {
      var LAYOUT_CN = {
        cover_hero: "小红书封面 · 大图压字",
        split_top: "小红书封面 · 上下分区",
        center_bold: "朋友圈 / 社群方图",
        poster_tall: "门店海报 / 易拉宝",
        magazine: "横版 banner"
      };
      out.push('<section class="dlv-sec asset-sec">');
      out.push('<details class="asset-fold">');
      out.push('<summary><span class="mono">00</span>素材包 · ' + posters.length + ' 张' +
        '<span class="asset-hint mono">多版式 · 点击翻看</span></summary>');
      out.push('<div class="asset-grid">');
      posters.forEach(function (p, i) {
        var label = LAYOUT_CN[p.layout] || p.layout || "素材";
        out.push('<button class="asset-thumb" type="button" data-asset-index="' + i + '"' +
          ' aria-label="查看第 ' + (i + 1) + " 张：" + esc(label) + '">' +
          '<img src="' + esc(resolveAsset(p.file)) + '" alt="' + esc(label) + '" loading="lazy">' +
          '<span class="asset-tag mono">' + esc(String(p.layout || "").toUpperCase()) + "</span>" +
          "</button>");
      });
      out.push("</div>");
      out.push("</details>");
      out.push("</section>");
    }

    if (r.summary) {
      out.push('<p class="dlv-sub">方案综述</p>');
      out.push('<p class="dlv-lede">' + esc(r.summary) + "</p>");
    }

    /* 成品文案 */
    if (s.copywriting) {
      out.push('<section class="dlv-sec">');
      out.push(secHead("01", "成品文案", "可直接取用"));
      out.push('<p class="copy-block collapsible" style="--clamp-h:10.4em">' +
        esc(s.copywriting).replace(/\n/g, "<br>") + "</p>");
      out.push('<button class="more-btn" type="button" hidden>展开全文</button>');
      out.push("</section>");
    }

    /* 执行清单 */
    var steps = extra.execution_steps || [];
    if (steps.length) {
      out.push('<section class="dlv-sec">');
      out.push(secHead("02", "执行清单", '<span class="mono">' + steps.length + " STEPS</span>"));
      out.push(stepsList(steps));
      out.push("</section>");
    }

    /* 依据（默认收起） */
    var ev = evidenceBlocks(r);
    if (ev) {
      out.push('<details class="dlv-ev">');
      out.push('<summary><span class="mono">03</span>方案依据 · 客户简报、洞察结论与投放打法</summary>');
      out.push(ev);
      out.push("</details>");
    }

    out.push("</div>");
    $("report").innerHTML = out.join("");
    $("report").scrollTop = 0;
    setupCollapsible($("report"));
  }

  /* 本轮没有交付物（例如没找到可核查商家）：不摆假成果，讲清卡在哪、下一步怎么走 */
  function renderBlocked(r, opts) {
    var c = r.customer || {};
    var blockers = r.blockers || [];
    var out = [];

    out.push('<div class="dlv">');
    out.push('<header class="dlv-head">');
    out.push('<p class="dlv-kicker mono">' + (opts.history ? "LAST RUN" : "THIS RUN") + "</p>");
    out.push('<h3 class="dlv-title">' + esc(r.title || "本轮未产出交付物") + "</h3>");
    var meta = [];
    if (r.generated_at) { meta.push('<span class="mono">' + esc(r.generated_at) + "</span>"); }
    if (meta.length) { out.push('<p class="dlv-meta">' + meta.join('<span class="meta-dot">/</span>') + "</p>"); }
    out.push("</header>");

    out.push('<section class="blk">');
    out.push('<p class="blk-tag mono">NO DELIVERABLE</p>');
    if (r.summary) { out.push('<p class="blk-lead">' + esc(r.summary) + "</p>"); }
    if (blockers.length) {
      out.push('<ul class="blk-list">');
      blockers.forEach(function (b) {
        out.push("<li><span>" + esc(b.text) + "</span>" +
          (b.hint ? '<em>' + esc(b.hint) + "</em>" : "") + "</li>");
      });
      out.push("</ul>");
    }
    out.push('<div class="blk-acts">' +
      '<button class="blk-btn is-primary" type="button" id="blk-retry">重新执行</button>' +
      '<button class="blk-btn" type="button" id="blk-demo">看演示案例（加速回放）</button></div>');
    out.push("</section>");

    var ev = evidenceBlocks(r);
    if (ev) {
      out.push('<details class="dlv-ev">');
      out.push('<summary><span class="mono">01</span>本轮依据 · 客户简报与洞察结论</summary>');
      out.push(ev);
      out.push("</details>");
    }
    out.push("</div>");

    $("report").innerHTML = out.join("");
    $("report").scrollTop = 0;
    setupCollapsible($("report"));
  }

  /* 长文折叠：内容超出一屏高度时才出现「展开」 */
  function setupCollapsible(root) {
    root.querySelectorAll(".collapsible").forEach(function (box) {
      var btn = box.nextElementSibling;
      if (!btn || !btn.classList.contains("more-btn")) { return; }
      var closedLabel = btn.textContent;
      var measure = function () {
        if (box.classList.contains("is-expanded")) { return; }
        btn.hidden = box.scrollHeight - box.clientHeight <= 4;
      };
      requestAnimationFrame(measure);
      btn.addEventListener("click", function () {
        var open = box.classList.toggle("is-expanded");
        btn.textContent = open ? "收起" : closedLabel;
        if (!open) { box.scrollIntoView({ block: "nearest" }); }
      });
    });
  }

  /* ---------------- 素材大图 ---------------- */
  /* ---------------- 素材大图（支持组内左右翻看） ---------------- */
  var lightboxState = { list: [], index: 0 };

  function openLightbox(src, list, index) {
    lightboxState.list = (list && list.length) ? list.slice() : [src];
    var start = typeof index === "number" ? index : lightboxState.list.indexOf(src);
    lightboxState.index = start >= 0 ? start : 0;
    showLightboxAt(lightboxState.index);
    $("lightbox").hidden = false;
    document.body.classList.add("lb-open");
  }

  function showLightboxAt(i) {
    var n = lightboxState.list.length;
    if (!n) { return; }
    lightboxState.index = ((i % n) + n) % n;
    $("lb-img").src = lightboxState.list[lightboxState.index];
    var multi = n > 1;
    $("lb-prev").hidden = !multi;
    $("lb-next").hidden = !multi;
    $("lb-count").textContent = multi ? (lightboxState.index + 1) + " / " + n : "";
  }

  function stepLightbox(delta) {
    if (lightboxState.list.length > 1) { showLightboxAt(lightboxState.index + delta); }
  }

  function closeLightbox() {
    $("lightbox").hidden = true;
    $("lb-img").src = "";
    $("lb-count").textContent = "";
    document.body.classList.remove("lb-open");
  }

  /* ---------------- 首屏直接呈现上一轮真实成果 ---------------- */
  function hydrateLast() {
    fetch("/api/last").then(function (r) { return r.json(); }).then(function (d) {
      if (!d || !d.has || !d.state || !d.state.report || app.running || app.started) { return; }
      if (d.state.plan) { app.plan = d.state.plan; }
      if (d.input) {
        $("req").value = d.input;
        $("req").dispatchEvent(new Event("input"));
      }
      $("stream").innerHTML = "";
      (d.trace || []).forEach(function (ev) {
        try { handleTrace(ev); } catch (e) { /* 忽略坏包 */ }
      });
      app.started = true;
      app.runSeconds = (typeof d.duration === "number") ? d.duration : null;
      handleState(d.state, { history: true });
      $("dispatch-pop").hidden = true;
      var note = document.createElement("li");
      note.className = "ev ev-idle ev-hist";
      note.textContent = "上一轮运行记录" +
        (typeof d.duration === "number" ? " · " + d.duration.toFixed(1) + "s" : "") +
        (app.demoMode ? "：点击「开始回放」查看完整过程。" : "：点击「开始执行」可重新运行。");
      $("stream").insertBefore(note, $("stream").firstChild);
      $("stream").scrollTop = 0;
    }).catch(function () { /* 无缓存时保持待发车空台 */ });
  }

  /* ---------------- 启动运行 ---------------- */
  function startRun() {
    var input = $("req").value.trim();
    if (!input || app.running) { return; }
    resetUI();
    app.running = true;
    app.started = true;
    app.runSeconds = null;
    setControlsDisabled(true);

    var url = "/api/run?mode=" + encodeURIComponent(app.mode) +
      "&input=" + encodeURIComponent(input);
    var es = new EventSource(url);
    app.es = es;

    es.addEventListener("trace", function (e) {
      try { handleTrace(JSON.parse(e.data)); } catch (err) { /* 忽略坏包 */ }
    });
    es.addEventListener("duration", function (e) {
      try { app.runSeconds = JSON.parse(e.data).seconds; } catch (err) { /* 忽略坏包 */ }
    });
    es.addEventListener("state", function (e) {
      try { handleState(JSON.parse(e.data)); } catch (err) { /* 忽略坏包 */ }
    });
    es.addEventListener("failed", function (e) {
      try {
        var d = JSON.parse(e.data);
        showError("运行出错：" + (d.message || "未知错误") + "（可再次点击开始执行重试）");
      } catch (err) { showError("运行出错，可重试。"); }
    });
    es.addEventListener("end", function () {
      es.close();
      app.es = null;
      finishRun();
    });
    es.onerror = function () {
      if (app.running) {
        showError("连接中断：请确认工作台服务仍在运行，然后重试。");
        finishRun();
      }
    };
  }

  /* ---------------- 初始化 ---------------- */
  function init() {
    setStreamIdle();
    $("stream").addEventListener("click", function (e) {
      var head = e.target && e.target.closest ? e.target.closest(".llm-head") : null;
      if (!head) { return; }
      var body = head.parentNode.querySelector(".llm-body");
      if (!body) { return; }
      var willOpen = body.hidden;
      body.hidden = !willOpen;
      head.classList.toggle("is-open", willOpen);
      var caret = head.querySelector(".llm-caret");
      if (caret) { caret.textContent = willOpen ? "－" : "＋"; }
    });
    $("req").addEventListener("input", function () {
      $("start").disabled = app.running || !$("req").value.trim();
    });
    $("start").addEventListener("click", startRun);

    /* 素材大图：点海报看大图，ESC 或点击空白处关闭 */
    $("report").addEventListener("click", function (e) {
      var t = e.target;
      if (!t || !t.closest) { return; }
      var thumb = t.closest("[data-asset-index]");
      if (thumb) {
        var imgs = Array.prototype.map.call(
          document.querySelectorAll(".asset-thumb img"),
          function (img) { return img.getAttribute("src"); }
        );
        var idx = Number(thumb.getAttribute("data-asset-index")) || 0;
        openLightbox(imgs[idx], imgs, idx);
        return;
      }
      var btn = t.closest("[data-zoom]");
      if (btn) { openLightbox(btn.getAttribute("data-zoom")); return; }
      if (t.closest("#blk-retry")) { startRun(); return; }
      if (t.closest("#blk-demo")) {
        $("req").value = DEMO_INPUT;
        $("req").dispatchEvent(new Event("input"));
        var fb = document.querySelector('.mode[data-mode="fast"]');
        if (fb) { fb.click(); }
        startRun();
      }
    });
    $("lightbox").addEventListener("click", function (e) {
      var t = e.target;
      if (t && t.closest && t.closest(".lb-nav")) { return; }
      if (t && t.closest && t.closest(".lb-close")) { closeLightbox(); return; }
      if (t !== $("lb-img")) { closeLightbox(); }
    });
    $("lb-prev").addEventListener("click", function () { stepLightbox(-1); });
    $("lb-next").addEventListener("click", function () { stepLightbox(1); });
    document.addEventListener("keydown", function (e) {
      if ($("lightbox").hidden) { return; }
      if (e.key === "Escape") { closeLightbox(); }
      else if (e.key === "ArrowLeft") { stepLightbox(-1); }
      else if (e.key === "ArrowRight") { stepLightbox(1); }
    });

    document.querySelectorAll(".chip").forEach(function (c) {
      c.addEventListener("click", function () {
        $("req").value = c.dataset.fill || "";
        $("req").dispatchEvent(new Event("input"));
        $("req").focus();
      });
    });

    document.querySelectorAll(".mode").forEach(function (btn) {
      btn.addEventListener("click", function () {
        if (app.running) { return; }
        /* 加速回放 = 演示模式：跳转到 ?demo=1（提示「上一次真实测试」+ 锁定需求 + 等评委点「开始回放」） */
        if (btn.dataset.mode === "fast" && !app.demoMode) { location.href = "/?demo=1"; return; }
        /* 演示模式里切回实时：回到普通页，需求输入恢复可编辑 */
        if (btn.dataset.mode === "realtime" && app.demoMode) { location.href = "/"; return; }
        app.mode = btn.dataset.mode;
        document.querySelectorAll(".mode").forEach(function (b) {
          b.classList.toggle("is-on", b === btn);
        });
        $("mode-hint").textContent = app.mode === "fast"
          ? "加速回放：重放最近一次真实运行的结果（未预热时自动转为实时）。"
          : "实时模式：真实运行，约 30–60 秒；可切加速回放。";
      });
    });

    fetch("/api/health").then(function (r) { return r.json(); }).then(function (h) {
      $("conn").classList.add("is-ok");
      $("conn-text").textContent = h.has_cache ? "就绪 · 可加速回放" : "就绪 · 加速需预热";
    }).catch(function () {
      $("conn").classList.add("is-bad");
      $("conn-text").textContent = "服务未连接";
    });

    // 演示直达：?input=...&mode=fast&autostart=1 ；简写 ?demo=1
    var params = new URLSearchParams(location.search);
    if (params.get("demo") === "1") {
      /* 演示模式：不自动跑 —— 先说明「这是上一次真实测试的回放」，需求锁定，等评委点「开始回放」 */
      app.demoMode = true;
      $("req").value = DEMO_INPUT;
      $("req").readOnly = true;
      $("req").classList.add("is-locked");
      $("demo-notice").hidden = false;
      $("start").textContent = "开始回放";
      var fastBtn = document.querySelector('.mode[data-mode="fast"]');
      if (fastBtn) { fastBtn.click(); }
    } else {
      var input = params.get("input");
      if (input) { $("req").value = input; }
      var m = params.get("mode");
      if (m === "fast") {
        var fb = document.querySelector('.mode[data-mode="fast"]');
        if (fb) { fb.click(); }
      }
    }
    $("start").disabled = !$("req").value.trim();

    if (params.get("autostart") === "1") {
      setTimeout(function () { if (!$("start").disabled) { startRun(); } }, 500);
    } else {
      // 首屏即成果：把最近一次真实运行的结果直接铺开，评委打开页面即可看到交付物。
      // （?demo=1 也走这里：先展示上一轮成果，等评委点「开始回放」再播整个过程）
      hydrateLast();
    }
  }

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", init);
  } else {
    init();
  }
})();
