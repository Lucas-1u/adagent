/**
 * 海报渲染器 —— 前端代码作画（无 AI 生图）
 * ==========================================
 *
 * 输入：URL 上的 spec（base64url(JSON)），由 app/agents/poster_agent.py 编排：
 *      { category, motif, label, seed, palette{bg,surface,ink,dim,accent,beads?},
 *        eyebrow, headline, subline, points[{k,v}], chips[], shop, meta, cta, fine }
 *
 * 输出：1024×1536 版式（截图时 @2x → 2048×3072 PNG）
 *
 * 母题（motif）：beads 拼豆 / latte 手冲 / wheat 烘焙 / petals 花艺 /
 *                swirl 甜品 / grid 通用
 * 同 seed 同结果：一颗珠子落在哪、散落几颗，都是确定的，方便复现与截图比对。
 */
(function () {
  "use strict";

  var W = 1024;
  var H = 740;
  var query = new URLSearchParams(location.search);
  var STATIC = query.get("static") === "1";

  // ------------------------------------------------------------ 工具

  function mulberry32(seed) {
    var a = (seed >>> 0) || 1;
    return function () {
      a = (a + 0x6d2b79f5) >>> 0;
      var t = a;
      t = Math.imul(t ^ (t >>> 15), t | 1);
      t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
      return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
  }

  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"]/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[ch];
    });
  }

  function round(n) {
    return Math.round(n * 10) / 10;
  }

  function radialBg(id, from, to, cx, cy, r) {
    return (
      '<defs><radialGradient id="' + id + '" cx="' + cx + '" cy="' + cy + '" r="' + r + '">' +
      '<stop offset="0%" stop-color="' + from + '"/><stop offset="100%" stop-color="' + to + '"/>' +
      "</radialGradient></defs>"
    );
  }

  function baseCanvas(P, id) {
    return radialBg(id, P.surface || P.bg, P.bg, "50%", "32%", "76%") +
      '<rect width="' + W + '" height="' + H + '" fill="url(#' + id + ')"/>';
  }

  function bead(cx, cy, r, color) {
    return (
      '<g><circle class="bead" cx="' + round(cx) + '" cy="' + round(cy) + '" r="' + round(r) + '" fill="' + color + '"/>' +
      '<circle cx="' + round(cx - r * 0.3) + '" cy="' + round(cy - r * 0.34) + '" r="' + round(r * 0.3) + '" fill="#ffffff" opacity="0.42"/>' +
      "</g>"
    );
  }

  // ------------------------------------------------------------ 母题：拼豆

  // 拼豆网格：15×14，心形用位图模板（比曲线在粗网格上更像真正的拼豆作品）
  var BEAD_COLS = 15;
  var BEAD_ROWS = 14;
  var HEART_GRID = [
    "...............",
    "..####...####..",
    ".######.######.",
    "###############",
    "###############",
    "###############",
    "###############",
    ".#############.",
    "..###########..",
    "...#########...",
    "....#######....",
    ".....#####.....",
    "......###......",
    ".......#......."
  ];

  function shapeKind(spec, rnd) {
    var name = spec && spec.shape;
    if (name === "heart") return 0;
    if (name === "star") return 1;
    if (name === "flower") return 2;
    return Math.floor(rnd() * 3);
  }

  function inShape(kind, u, v) {
    if (kind === 0) {
      var gi = Math.round(((u + 1) / 2) * (BEAD_COLS - 1));
      var gj = Math.round(((1 - v) / 2) * (BEAD_ROWS - 1));
      gi = Math.min(BEAD_COLS - 1, Math.max(0, gi));
      gj = Math.min(BEAD_ROWS - 1, Math.max(0, gj));
      return HEART_GRID[gj].charAt(gi) === "#";
    }
    if (kind === 1) {
      var pts = [];
      for (var i = 0; i < 10; i++) {
        var ang = -Math.PI / 2 + (i * Math.PI) / 5;
        var rr = i % 2 === 0 ? 1 : 0.44;
        pts.push([Math.cos(ang) * rr * 0.98, Math.sin(ang) * rr * 0.98]);
      }
      return inPolygon(u, v, pts);
    }
    var theta = Math.atan2(v, u);
    var rad = Math.sqrt(u * u + v * v);
    return rad <= 0.35 + 0.55 * Math.cos(5 * theta) && rad <= 1;
  }

  function inPolygon(x, y, poly) {
    var inside = false;
    for (var i = 0, j = poly.length - 1; i < poly.length; j = i++) {
      var xi = poly[i][0];
      var yi = poly[i][1];
      var xj = poly[j][0];
      var yj = poly[j][1];
      if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
    }
    return inside;
  }

  function motifBeads(P, rnd, spec) {
    var out = [baseCanvas(P, "bd-bg")];
    var bx = 196;
    var by = 92;
    var bw = 632;
    var bh = 604;

    out.push('<g transform="rotate(-2 512 370)">');
    out.push('<rect x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + bh + '" rx="8" fill="#ffffff" fill-opacity="0.05"/>');
    out.push('<rect x="' + bx + '" y="' + by + '" width="' + bw + '" height="' + bh + '" rx="8" fill="none" stroke="#ffffff" stroke-opacity="0.1"/>');
    out.push("</g>");

    var colors = P.beads && P.beads.length ? P.beads : [P.accent];
    var cols = BEAD_COLS;
    var rows = BEAD_ROWS;
    var cell = 42;
    var ox = (W - cols * cell) / 2;
    var oy = by + (bh - rows * cell) / 2;
    var kind = shapeKind(spec, rnd);
    var cx0 = (cols - 1) / 2;
    var cy0 = (rows - 1) / 2;

    // 棋盘底：淡淡的定位孔
    for (var gj = 0; gj < rows; gj++) {
      for (var gi = 0; gi < cols; gi++) {
        out.push('<circle cx="' + round(ox + gi * cell + cell / 2) + '" cy="' + round(oy + gj * cell + cell / 2) + '" r="2" fill="#ffffff" opacity="0.07"/>');
      }
    }

    for (var j = 0; j < rows; j++) {
      for (var i = 0; i < cols; i++) {
        var u = (i - cx0) / (cols / 2 - 0.5);
        var v = -(j - cy0) / (rows / 2 - 0.5);
        if (!inShape(kind, u, v)) continue;
        var color = colors[Math.floor(rnd() * colors.length)];
        out.push(bead(ox + i * cell + cell / 2, oy + j * cell + cell / 2, 19.5, color));
      }
    }

    for (var k = 0; k < 26; k++) {
      var lx = 0;
      var ly = 0;
      var ok = false;
      for (var guard = 0; guard < 40 && !ok; guard++) {
        lx = 40 + rnd() * (W - 80);
        ly = 40 + rnd() * (H - 80);
        ok = lx < bx - 26 || lx > bx + bw + 26 || ly < by - 26 || ly > by + bh + 26;
      }
      if (!ok) continue;
      out.push(bead(lx, ly, 15, colors[Math.floor(rnd() * colors.length)]));
    }
    return out.join("");
  }

  // ------------------------------------------------------------ 母题：手冲

  function motifLatte(P, rnd) {
    var cx = 512;
    var cy = 360;
    var out = [baseCanvas(P, "lt-bg")];

    // 碟
    out.push('<circle cx="' + cx + '" cy="' + (cy + 14) + '" r="310" fill="#000000" opacity="0.16"/>');
    out.push('<circle cx="' + cx + '" cy="' + (cy + 14) + '" r="304" fill="none" stroke="#ffffff" stroke-opacity="0.08" stroke-width="3"/>');
    // 杯身
    out.push('<circle cx="' + cx + '" cy="' + cy + '" r="232" fill="' + P.surface + '" stroke="#ffffff" stroke-opacity="0.14" stroke-width="2"/>');
    out.push('<circle cx="' + cx + '" cy="' + cy + '" r="210" fill="#57331b"/>');
    out.push(
      '<defs><radialGradient id="lt-crema" cx="50%" cy="46%" r="62%">' +
        '<stop offset="0%" stop-color="' + P.accent + '" stop-opacity="0.95"/>' +
        '<stop offset="62%" stop-color="#9a5c2c" stop-opacity="0.75"/>' +
        '<stop offset="100%" stop-color="#4a2a13" stop-opacity="0.95"/></radialGradient></defs>'
    );
    out.push('<circle cx="' + cx + '" cy="' + cy + '" r="210" fill="url(#lt-crema)"/>');
    out.push('<circle cx="' + cx + '" cy="' + cy + '" r="210" fill="none" stroke="#ffffff" stroke-opacity="0.06" stroke-width="7"/>');

    // 心形拉花：两圆 + 三角并集
    out.push('<g fill="#f6e9d2" opacity="0.94">');
    out.push('<circle cx="' + (cx - 54) + '" cy="' + (cy - 40) + '" r="74"/>');
    out.push('<circle cx="' + (cx + 54) + '" cy="' + (cy - 40) + '" r="74"/>');
    out.push('<path d="M' + (cx - 128) + " " + (cy - 22) + " L" + cx + " " + (cy + 128) + " L" + (cx + 128) + " " + (cy - 22) + " Z" + '"/>');
    out.push("</g>");
    out.push('<circle cx="' + (cx - 54) + '" cy="' + (cy - 58) + '" r="30" fill="#ffffff" opacity="0.18"/>');

    // 咖啡豆
    [
      [150, 150, -18],
      [878, 168, 16],
      [122, 606, 8],
      [900, 620, -10]
    ].forEach(function (b) {
      out.push(
        '<g transform="translate(' + b[0] + " " + b[1] + ") rotate(" + b[2] + ')">' +
          '<ellipse rx="34" ry="23" fill="#3f2412"/>' +
          '<ellipse rx="34" ry="23" fill="none" stroke="#7a4d2b" stroke-width="3"/>' +
          '<path d="M0 -19 C -9 -6, -9 6, 0 19" fill="none" stroke="#96643a" stroke-width="4" stroke-linecap="round"/>' +
          "</g>"
      );
    });

    // 热气
    [430, 512, 594].forEach(function (x, idx) {
      out.push(
        '<path class="steam" style="animation-delay:' + (idx * 1.3).toFixed(1) + 's" ' +
          'd="M' + x + " " + (cy - 244) + " C " + (x - 30) + " " + (cy - 300) + ", " + (x + 30) + " " + (cy - 344) + ", " + x + " " + (cy - 388) + '"' +
          ' fill="none" stroke="#ffffff" stroke-width="8" stroke-opacity="0.16" stroke-linecap="round"/>'
      );
    });
    return out.join("");
  }

  // ------------------------------------------------------------ 母题：烘焙

  function motifWheat(P, rnd) {
    var out = [baseCanvas(P, "wk-bg")];
    out.push('<path d="M0 ' + H + " Q " + W / 2 + " " + (H - 190) + " " + W + " " + H + '" fill="#000000" opacity="0.16"/>');

    [
      [512, 0, 1],
      [286, -15, 0.8],
      [736, 15, 0.8]
    ].forEach(function (cfg, idx) {
      var x0 = cfg[0];
      var rot = cfg[1];
      var scale = cfg[2];
      out.push(
        '<g class="stalk" style="animation-delay:' + (idx * 0.8).toFixed(1) + 's" ' +
          'transform="translate(' + x0 + " 706) rotate(" + rot + ") scale(" + scale + ')" ' +
          'stroke="' + P.accent + '" fill="' + P.accent + '">'
      );
      out.push('<path d="M0 0 L0 -560" stroke-width="7" stroke-opacity="0.7" fill="none"/>');
      for (var i = 0; i < 9; i++) {
        var y = -118 - i * 52;
        var w = 30 - i * 1.5;
        out.push('<ellipse cx="' + -w * 0.92 + '" cy="' + y + '" rx="' + w + '" ry="' + w * 0.5 + '" transform="rotate(-24 ' + -w * 0.92 + " " + y + ')" opacity="0.85"/>');
        out.push('<ellipse cx="' + w * 0.92 + '" cy="' + y + '" rx="' + w + '" ry="' + w * 0.5 + '" transform="rotate(24 ' + w * 0.92 + " " + y + ')" opacity="0.85"/>');
      }
      out.push('<path d="M0 -556 q 0 -62 24 -86" stroke-width="5" fill="none" stroke-opacity="0.55"/>');
      out.push("</g>");
    });

    for (var i = 0; i < 46; i++) {
      out.push('<circle cx="' + Math.round(rnd() * W) + '" cy="' + Math.round(rnd() * H) + '" r="' + round(1.5 + rnd() * 2.5) + '" fill="' + P.ink + '" opacity="0.14"/>');
    }
    return out.join("");
  }

  // ------------------------------------------------------------ 母题：花艺

  function motifPetals(P, rnd) {
    var out = [baseCanvas(P, "pt-bg")];
    for (var b = 0; b < 6; b++) {
      var cx = 130 + rnd() * (W - 260);
      var cy = 120 + rnd() * (H - 240);
      var r = 64 + rnd() * 66;
      out.push('<g transform="translate(' + round(cx) + " " + round(cy) + ')">');
      for (var i = 0; i < 7; i++) {
        var ang = (360 / 7) * i;
        out.push(
          '<ellipse cx="0" cy="' + round(-r * 0.62) + '" rx="' + round(r * 0.31) + '" ry="' + round(r * 0.62) +
            '" fill="' + P.accent + '" opacity="' + (0.3 + rnd() * 0.4).toFixed(2) + '" transform="rotate(' + ang + ')"/>'
        );
      }
      out.push('<circle r="' + round(r * 0.19) + '" fill="' + P.ink + '" opacity="0.72"/>');
      out.push("</g>");
    }
    return out.join("");
  }

  // ------------------------------------------------------------ 母题：甜品

  function motifSwirl(P, rnd) {
    var cx = 512;
    var cy = 356;
    var out = [baseCanvas(P, "sw-bg")];
    out.push('<circle cx="' + cx + '" cy="' + (cy + 160) + '" r="300" fill="#000000" opacity="0.14"/>');
    out.push('<circle cx="' + cx + '" cy="' + cy + '" r="252" fill="' + P.surface + '" stroke="#ffffff" stroke-opacity="0.1"/>');

    for (var ring = 0; ring < 6; ring++) {
      out.push(
        '<circle cx="' + cx + '" cy="' + cy + '" r="' + (40 + ring * 38) + '" fill="none" stroke="' + P.accent +
          '" stroke-width="' + round(18 - ring * 1.7) + '" stroke-opacity="' + (0.75 - ring * 0.09).toFixed(2) + '"/>'
      );
    }

    var d = "";
    for (var t = 0; t < 6 * Math.PI; t += 0.1) {
      var r = 8 + t * 12.4;
      d += (d ? " L" : "M") + round(cx + Math.cos(t) * r) + " " + round(cy + Math.sin(t) * r);
    }
    out.push('<path class="swirl" d="' + d + '" fill="none" stroke="' + P.ink + '" stroke-width="10" stroke-opacity="0.55" stroke-linecap="round"/>');

    for (var i = 0; i < 44; i++) {
      out.push('<circle cx="' + Math.round(rnd() * W) + '" cy="' + Math.round(rnd() * H) + '" r="' + round(2 + rnd() * 3) + '" fill="' + P.ink + '" opacity="0.26"/>');
    }
    return out.join("");
  }

  // ------------------------------------------------------------ 母题：通用

  function motifGrid(P, rnd) {
    var out = [baseCanvas(P, "gd-bg")];
    out.push('<circle cx="512" cy="356" r="250" fill="none" stroke="' + P.accent + '" stroke-width="2" stroke-opacity="0.5"/>');
    out.push('<circle cx="512" cy="356" r="176" fill="' + P.surface + '" fill-opacity="0.9" stroke="#ffffff" stroke-opacity="0.08"/>');
    for (var i = 0; i < 16; i++) {
      var x = 120 + (i % 8) * 110;
      var y = 240 + Math.floor(i / 8) * 120;
      out.push('<circle cx="' + x + '" cy="' + y + '" r="' + round(3 + rnd() * 4) + '" fill="' + P.accent + '" opacity="0.5"/>');
    }
    out.push('<path d="M212 640 L812 640" stroke="#ffffff" stroke-opacity="0.12"/>');
    return out.join("");
  }

  // ============================================================ 场景合成（内容驱动）
  // 思路：海报上半部分不再"套品类母题"，而是按这家店真实查出的事实拼一张图：
  //   cues  = 数据里命中的元素（猫 / 老房子 / 水彩 / 咖啡 / 面包 / 窗 / 巷 / 绿植…）
  //   badges= 真实数字（人均 37 元、评分 4.6、123 条评价、营业时间）
  //   numero= 门牌号，做成背景里的大号幽灵数字
  //   glyph = 店名首字，拼豆店直接把它"拼"成珠子画
  // 版式（layout）由 seed + cues 决定，三家店不会长成同一张。

  function grain(P, rnd, count) {
    var out = [];
    for (var i = 0; i < count; i++) {
      out.push('<circle cx="' + round(rnd() * W) + '" cy="' + round(rnd() * H) + '" r="' + round(0.6 + rnd() * 1.6) +
        '" fill="' + P.ink + '" opacity="' + round(0.03 + rnd() * 0.05) + '"/>');
    }
    return out.join("");
  }

  function cueMap(spec) {
    var map = {};
    (spec && spec.cues ? spec.cues : []).forEach(function (cue) { map[cue] = true; });
    return map;
  }

  // ------------------------------------------------------------ 元素（每个都在画"这家店有的东西"）

  function propCat(x, y, s, P) {
    // 坐着的猫剪影：机身 + 头 + 双耳 + 尾巴
    var c = P.ink;
    return (
      '<g opacity="0.92" transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">' +
      '<path d="M-46 0 C-46 -34 -26 -62 0 -62 C26 -62 46 -34 46 0 Z" fill="' + c + '" opacity="0.9"/>' +
      '<circle cx="0" cy="-96" r="34" fill="' + c + '"/>' +
      '<path d="M-32 -118 L-18 -150 L-4 -120 Z" fill="' + c + '"/>' +
      '<path d="M32 -118 L18 -150 L4 -120 Z" fill="' + c + '"/>' +
      '<path d="M44 -18 C86 -18 92 -70 64 -84" stroke="' + c + '" stroke-width="11" fill="none" stroke-linecap="round"/>' +
      '<circle cx="-11" cy="-98" r="4.4" fill="' + P.bg + '"/><circle cx="11" cy="-98" r="4.4" fill="' + P.bg + '"/>' +
      "</g>"
    );
  }

  function propCup(x, y, s, P) {
    var c = P.beads || P.accent;
    return (
      '<g transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">' +
      '<ellipse cx="0" cy="4" rx="64" ry="12" fill="' + P.ink + '" opacity="0.10"/>' +
      '<path d="M-40 -8 L-32 54 C-30 66 -20 72 -8 72 L8 72 C20 72 30 66 32 54 L40 -8 Z" fill="' + P.surface + '" stroke="' + c + '" stroke-opacity="0.6" stroke-width="2"/>' +
      '<ellipse cx="0" cy="-8" rx="40" ry="11" fill="' + c + '" opacity="0.9"/>' +
      '<ellipse cx="0" cy="-8" rx="24" ry="6" fill="' + P.ink + '" opacity="0.85"/>' +
      '<path d="M40 -2 C62 -2 64 34 40 34" stroke="' + c + '" stroke-width="5" fill="none" stroke-opacity="0.7"/>' +
      "</g>"
    );
  }

  function propEasel(x, y, s, P) {
    // 画架 + 一张水彩纸
    var c = P.beads || P.accent;
    return (
      '<g transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">' +
      '<path d="M-70 130 L0 -70 L70 130" stroke="' + P.ink + '" stroke-opacity="0.34" stroke-width="7" fill="none"/>' +
      '<rect x="-58" y="-64" width="116" height="92" fill="' + P.ink + '" opacity="0.10"/>' +
      '<rect x="-58" y="-64" width="116" height="92" fill="none" stroke="' + P.ink + '" stroke-opacity="0.28"/>' +
      '<path d="M-40 6 C-14 -34 6 20 34 -18" stroke="' + c + '" stroke-width="9" fill="none" stroke-linecap="round" stroke-opacity="0.85"/>' +
      '<circle cx="22" cy="-34" r="11" fill="' + c + '" opacity="0.55"/>' +
      "</g>"
    );
  }

  function propPlant(x, y, s, P) {
    var c = P.leaf || "#6fbf8f";
    var out = ['<g transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">'];
    out.push('<path d="M-30 0 L30 0 L22 74 L-22 74 Z" fill="' + P.ink + '" opacity="0.16"/>');
    for (var i = 0; i < 5; i++) {
      var a = -62 + i * 31;
      out.push('<path d="M0 -4 C' + (-18 + i * 3) + ' -52 ' + (-40 + i * 14) + ' -104 0 -128 C' + (30 - i * 6) + ' -100 ' + (24 - i * 4) + ' -46 0 -4 Z" fill="' + c +
        '" opacity="' + round(0.32 + i * 0.1) + '" transform="rotate(' + a + ' 0 -4)"/>');
    }
    out.push("</g>");
    return out.join("");
  }

  function propBread(x, y, s, P) {
    var c = P.beads || P.accent;
    return (
      '<g transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">' +
      '<ellipse cx="0" cy="6" rx="86" ry="14" fill="' + P.ink + '" opacity="0.10"/>' +
      '<path d="M-78 0 C-78 -40 -40 -58 0 -58 C40 -58 78 -40 78 0 Z" fill="' + c + '" opacity="0.9"/>' +
      '<path d="M-46 -30 L-16 -18 M-14 -40 L16 -26 M22 -38 L50 -24" stroke="' + P.bg + '" stroke-width="7" stroke-linecap="round" stroke-opacity="0.55"/>' +
      "</g>"
    );
  }

  function propWindow(x, y, s, P, rnd) {
    // 拱形窗：玻璃 + 窗棂 + 外面的一点天光
    var c = P.ink;
    var out = ['<g transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">'];
    out.push('<path d="M-120 200 L-120 40 A120 120 0 0 1 120 40 L120 200 Z" fill="' + P.surface + '" fill-opacity="0.85" stroke="' + c + '" stroke-opacity="0.30" stroke-width="3"/>');
    out.push('<path d="M-120 40 A120 120 0 0 1 120 40" fill="none" stroke="' + c + '" stroke-opacity="0.22" stroke-width="3"/>');
    out.push('<path d="M0 -80 L0 200 M-120 96 L120 96" stroke="' + c + '" stroke-opacity="0.24" stroke-width="3"/>');
    for (var i = 0; i < 5; i++) {
      out.push('<circle cx="' + round(-70 + rnd() * 140) + '" cy="' + round(-30 + rnd() * 120) + '" r="' + round(2 + rnd() * 3) + '" fill="' + (P.beads || P.accent) + '" opacity="0.5"/>');
    }
    out.push("</g>");
    return out.join("");
  }

  function propLamp(x, y, s, P) {
    var c = P.beads || P.accent;
    return (
      '<g transform="translate(' + round(x) + "," + round(y) + ') scale(' + s + ')">' +
      '<path d="M0 0 L0 -140" stroke="' + P.ink + '" stroke-opacity="0.4" stroke-width="4"/>' +
      '<path d="M-52 -140 L52 -140 L30 -196 L-30 -196 Z" fill="' + c + '" opacity="0.85"/>' +
      '<circle cx="0" cy="-140" r="7" fill="' + P.ink + '" opacity="0.8"/>' +
      "</g>"
    );
  }

  // 巷子：两侧墙面收拢成深处，店名首字挂在招牌上（"巷子里的店"才这样画）
  function propAlley(P, rnd, spec) {
    var out = [];
    var glyph = spec && spec.glyph ? spec.glyph : "店";
    var c = P.beads || P.accent;
    out.push('<defs><linearGradient id="alley-g" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="' + c + '" stop-opacity="0.20"/>' +
      '<stop offset="100%" stop-color="' + P.bg + '" stop-opacity="0.05"/></linearGradient></defs>');
    // 两侧墙（近处深、远处浅，做出纵深）
    out.push('<path d="M0 740 L0 96 L336 286 L336 740 Z" fill="' + P.ink + '" opacity="0.10"/>');
    out.push('<path d="M1024 740 L1024 96 L688 286 L688 740 Z" fill="' + P.ink + '" opacity="0.10"/>');
    out.push('<path d="M0 96 L336 286 L688 286 L1024 96 L1024 0 L0 0 Z" fill="' + P.ink + '" opacity="0.05"/>');
    // 巷子尽头（透光的墙 + 远处地面）
    out.push('<rect x="336" y="286" width="352" height="454" fill="url(#alley-g)"/>');
    out.push('<path d="M336 286 L688 286" stroke="' + P.ink + '" stroke-opacity="0.24" stroke-width="2"/>');
    out.push('<ellipse cx="512" cy="330" rx="150" ry="34" fill="' + c + '" opacity="0.16"/>');
    // 招牌：挂在巷口，写店名首字
    out.push('<path d="M404 286 L404 352 M620 286 L620 352" stroke="' + P.ink + '" stroke-opacity="0.34" stroke-width="3"/>');
    out.push('<rect x="392" y="352" width="240" height="104" rx="12" fill="' + c + '" fill-opacity="0.18" stroke="' + c + '" stroke-opacity="0.6" stroke-width="2"/>');
    out.push('<text x="512" y="430" text-anchor="middle" font-size="70" font-weight="700" fill="' + P.ink + '" opacity="0.92" font-family="system-ui, \'Microsoft YaHei\', sans-serif">' + esc(glyph) + "</text>");
    // 地面接缝
    out.push('<path d="M336 640 L688 640" stroke="' + P.ink + '" stroke-opacity="0.10" stroke-width="2"/>');
    return out.join("");
  }

  // ------------------------------------------------------------ 拼豆：把店名首字拼成珠子

  function glyphCells(text, cols, rows) {
    var cells = [];
    if (!text) return cells;
    try {
      var canvas = document.createElement("canvas");
      canvas.width = cols * 10;
      canvas.height = rows * 10;
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "#000";
      ctx.fillRect(0, 0, canvas.width, canvas.height);
      ctx.fillStyle = "#fff";
      var box = Math.min(canvas.width, canvas.height) * 0.78;
      ctx.font = "700 " + Math.round(box) + 'px "Microsoft YaHei", "PingFang SC", system-ui, sans-serif';
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      ctx.fillText(String(text).slice(0, 1), canvas.width / 2, canvas.height / 2);
      var data = ctx.getImageData(0, 0, canvas.width, canvas.height).data;
      // 每格取中心 3×3 采样，命中率过半才算
      for (var j = 0; j < rows; j++) {
        for (var i = 0; i < cols; i++) {
          var hit = 0;
          for (var dy = 0; dy < 3; dy++) {
            for (var dx = 0; dx < 3; dx++) {
              var px = Math.floor((i + (dx + 0.5) / 3) * 10);
              var py = Math.floor((j + (dy + 0.5) / 3) * 10);
              var idx = (py * canvas.width + px) * 4;
              if (data[idx] > 120) hit++;
            }
          }
          if (hit >= 5) cells.push([i, j]);
        }
      }
    } catch (err) {
      return [];
    }
    return cells;
  }

  function beadBoard(P, rnd, spec) {
    var cols = 17;
    var rows = 16;
    var out = [];
    var cell = 34;
    var left = 512 - (cols * cell) / 2;
    var top = 62;
    var boardW = cols * cell;
    var boardH = rows * cell;
    // 板子（带一点厚度与孔位，像真的拼豆板）
    out.push('<rect x="' + round(left - 26) + '" y="' + round(top - 26) + '" width="' + round(boardW + 52) + '" height="' + round(boardH + 52) +
      '" rx="16" fill="' + P.surface + '" stroke="#ffffff" stroke-opacity="0.14" stroke-width="2"/>');
    for (var r = 0; r < rows; r++) {
      for (var c = 0; c < cols; c++) {
        out.push('<circle cx="' + round(left + c * cell + cell / 2) + '" cy="' + round(top + r * cell + cell / 2) + '" r="1.8" fill="#ffffff" opacity="0.09"/>');
      }
    }
    var cells = glyphCells(spec.glyph || "", cols, rows);
    var palette = (P.beads || []).length ? P.beads : ["#e8b0c0", "#f0c978", "#a8c8e8"];
    if (!cells.length) {
      // 兜底：没有字就退回心形位图
      for (var j = 0; j < rows; j++) {
        for (var i = 0; i < cols; i++) {
          if (HEART_GRID[j] && HEART_GRID[j][i] === "#") cells.push([i, j]);
        }
      }
    }
    cells.forEach(function (cellPos) {
      var cx = left + cellPos[0] * cell + cell / 2;
      var cy = top + cellPos[1] * cell + cell / 2;
      out.push(bead(cx, cy, cell * 0.46, palette[Math.floor(rnd() * palette.length)]));
    });
    // 散落的珠子：只在板子外面撒，别把字糊掉
    for (var k = 0; k < 34; k++) {
      var bx = 40 + rnd() * (W - 80);
      var by = 40 + rnd() * (H - 150);
      if (bx > left - 54 && bx < left + boardW + 54 && by > top - 54 && by < top + boardH + 54) continue;
      out.push(bead(bx, by, 11 + rnd() * 9, palette[Math.floor(rnd() * palette.length)]));
    }
    return out.join("");
  }

  // ------------------------------------------------------------ 场景版式

  function sceneArch(P, rnd, spec, cues) {
    var out = [baseCanvas(P, "sc-arch")];
    out.push(grain(P, rnd, 70));
    // 墙：两侧压暗，中间拱窗透光
    out.push('<path d="M0 0 L1024 0 L1024 740 L0 740 Z" fill="' + P.bg + '" fill-opacity="0.55"/>');
    out.push('<path d="M212 740 L212 232 A300 300 0 0 1 812 232 L812 740 Z" fill="' + P.surface + '" fill-opacity="0.92" stroke="' + P.ink + '" stroke-opacity="0.22" stroke-width="3"/>');
    out.push('<path d="M244 740 L244 240 A268 268 0 0 1 780 240 L780 740 Z" fill="' + P.beads || P.accent + '" fill-opacity="0.06"/>');
    out.push('<ellipse cx="512" cy="330" rx="250" ry="200" fill="' + (P.beads || P.accent) + '" opacity="0.10"/>');
    if (cues.window || cues.oldhouse) {
      out.push(propWindow(512, 250, 1.5, P, rnd));
    }
    if (cues.cat) out.push(propCat(300, 660, 1.0, P));
    if (cues.coffee || cues.tea) out.push(propCup(806, 596, 1.15, P));
    if (cues.plant) out.push(propPlant(132, 520, 1.35, P));
    if (cues.watercolor || cues.art) out.push(propEasel(640, 452, 0.86, P));
    if (cues.bread) out.push(propBread(330, 630, 0.92, P));
    if (cues.lamp || cues.night) out.push(propLamp(918, 470, 0.95, P));
    return out.join("");
  }

  function sceneStreet(P, rnd, spec, cues) {
    var out = [baseCanvas(P, "sc-street")];
    out.push(grain(P, rnd, 80));
    var x = -20;
    if (!cues.street) {
      // 远景房子剪影（只有数据里没提到"巷/街"时才用天际线）
      while (x < W + 40) {
        var bw = 90 + rnd() * 120;
        var bh = 120 + rnd() * 150;
        out.push('<rect x="' + round(x) + '" y="' + round(560 - bh) + '" width="' + round(bw) + '" height="' + round(bh + 180) + '" fill="' + P.ink + '" opacity="' + round(0.04 + rnd() * 0.05) + '"/>');
        if (rnd() > 0.55) {
          out.push('<rect x="' + round(x + bw * 0.3) + '" y="' + round(560 - bh + 26) + '" width="' + round(bw * 0.22) + '" height="' + round(bw * 0.3) + '" fill="' + (P.beads || P.accent) + '" opacity="0.22"/>');
        }
        x += bw + 14;
      }
    } else {
      out.push(propAlley(P, rnd, spec));
    }
    // 地面
    out.push('<path d="M0 560 L1024 560 L1024 740 L0 740 Z" fill="' + P.bg + '" fill-opacity="0.62"/>');
    out.push('<path d="M0 560 L1024 560" stroke="' + P.ink + '" stroke-opacity="0.18" stroke-width="2"/>');
    // 地面反光/暖光：让道具不是浮在黑底上
    out.push('<ellipse cx="512" cy="566" rx="430" ry="46" fill="' + (P.beads || P.accent) + '" opacity="0.10"/>');
    if (cues.cat) out.push(propCat(210, 690, 1.2, P));
    if (cues.dog) out.push(propCat(880, 690, 1.0, P));
    if (cues.coffee || cues.tea) out.push(propCup(660, 640, 1.3, P));
    if (cues.bread) out.push(propBread(470, 668, 1.05, P));
    if (cues.plant) out.push(propPlant(910, 620, 1.5, P));
    if (cues.watercolor || cues.art) out.push(propEasel(780, 520, 1.0, P));
    if (cues.lamp || cues.night) out.push(propLamp(110, 640, 1.15, P));
    if (cues.bead) out.push(beadBoard(P, rnd, spec));
    return out.join("");
  }

  function sceneStill(P, rnd, spec, cues) {
    var out = [baseCanvas(P, "sc-still")];
    out.push(grain(P, rnd, 60));
    // 桌面 + 布
    out.push('<path d="M0 210 L1024 210 L1024 740 L0 740 Z" fill="' + P.surface + '" fill-opacity="0.7"/>');
    out.push('<path d="M0 210 L1024 210" stroke="' + P.ink + '" stroke-opacity="0.14" stroke-width="2"/>');
    out.push('<path d="M120 210 L-60 740 L360 740 Z" fill="' + P.ink + '" opacity="0.05"/>');
    if (cues.bead) out.push(beadBoard(P, rnd, spec));
    if (cues.coffee || cues.tea) out.push(propCup(800, 430, 1.5, P));
    if (cues.cat) out.push(propCat(230, 700, 1.0, P));
    if (cues.watercolor || cues.art) out.push(propEasel(690, 430, 1.1, P));
    if (cues.bread) out.push(propBread(520, 640, 1.2, P));
    if (cues.plant) out.push(propPlant(150, 470, 1.3, P));
    return out.join("");
  }

  // 真实数字做成图里的"贴纸"：人均 / 评分 / 评价数 / 营业时间 / 门牌号
  function stamps(P, spec) {
    var out = [];
    var badges = spec.badges || [];
    var x = 40;
    badges.slice(0, 3).forEach(function (badge) {
      var text = badge.k + " " + badge.v;
      var width = 26 + text.length * 15;
      out.push('<rect x="' + round(x) + '" y="672" width="' + round(width) + '" height="44" rx="22" fill="' + P.bg + '" fill-opacity="0.72" stroke="' + (P.beads || P.accent) + '" stroke-opacity="0.42"/>');
      out.push('<text x="' + round(x + width / 2) + '" y="701" text-anchor="middle" font-size="21" fill="' + P.ink + '" font-family="system-ui, sans-serif">' + esc(text) + "</text>");
      x += width + 14;
    });
    if (spec.numero) {
      out.push('<text x="' + (W - 36) + '" y="730" text-anchor="end" font-size="122" font-weight="700" fill="' + (P.beads || P.accent) +
        '" opacity="0.13" font-family="system-ui, sans-serif">' + esc(spec.numero) + "</text>");
    }
    return out.join("");
  }

  function scene(P, rnd, spec) {
    var cues = cueMap(spec);
    var layout = spec.layout || "street";
    if (spec.category === "beads" || cues.bead) {
      var beadParts = [baseCanvas(P, "sc-bead"), grain(P, rnd, 50), beadBoard(P, rnd, spec)];
      // 店里真有猫，就画在板子旁边（cue 来自真实评论，不是装饰）
      if (cues.cat) beadParts.push(propCat(150, 630, 0.92, P));
      if (cues.plant) beadParts.push(propPlant(886, 520, 1.25, P));
      beadParts.push(stamps(P, spec));
      return beadParts.join("");
    }
    var core = layout === "arch" ? sceneArch(P, rnd, spec, cues) :
      layout === "still" ? sceneStill(P, rnd, spec, cues) : sceneStreet(P, rnd, spec, cues);
    return core + stamps(P, spec);
  }

  var MOTIFS = {
    beads: motifBeads,
    latte: motifLatte,
    wheat: motifWheat,
    petals: motifPetals,
    swirl: motifSwirl,
    grid: motifGrid
  };

  // ------------------------------------------------------------ 版式装配

  function fitText(host) {
    var body = host.querySelector(".body");
    if (!body) return;
    var fs = 62;
    host.style.setProperty("--fs", fs + "px");
    for (var i = 0; i < 14 && fs > 44; i++) {
      if (body.scrollHeight <= body.clientHeight + 1) break;
      fs -= 2;
      host.style.setProperty("--fs", fs + "px");
    }
  }

  function setText(id, value) {
    var node = document.getElementById(id);
    if (!node) return;
    if (value) {
      node.textContent = value;
      node.hidden = false;
    } else {
      node.hidden = true;
    }
  }

  function paint(spec) {
    var P = spec.palette || {};
    var host = document.getElementById("poster");
    var fallbackBg = "#101a3c";
    host.style.setProperty("--bg", P.bg || fallbackBg);
    host.style.setProperty("--surface", P.surface || "#16224a");
    host.style.setProperty("--ink", P.ink || "#f2f4f8");
    host.style.setProperty("--dim", P.dim || "#98a5cd");
    host.style.setProperty("--accent", P.accent || "#f0a322");
    document.body.style.background = P.bg || fallbackBg;

    if (spec.animate && !STATIC) host.classList.add("is-anim");

    var rnd = mulberry32(spec.seed || 1);
    // spec.scene = true → 走内容驱动的场景合成（按这家店真实查出的事实拼图）；
    // 否则退回旧的品类母题（兼容历史 spec 与回放缓存）。
    var draw = spec.scene
      ? function (P, rnd2, spec2) { return scene(P, rnd2, spec2); }
      : (MOTIFS[spec.motif] || MOTIFS.grid);
    document.getElementById("motif").innerHTML =
      '<svg viewBox="0 0 ' + W + " " + H + '" width="' + W + '" height="' + H + '" preserveAspectRatio="xMidYMid slice" xmlns="http://www.w3.org/2000/svg">' +
      draw(P, rnd, spec) +
      "</svg>";

    document.getElementById("chips").innerHTML = (spec.chips || [])
      .map(function (chip, idx) {
        return '<span class="chip ' + (idx === 0 ? "is-primary" : "is-ghost") + '">' + esc(chip) + "</span>";
      })
      .join("");

    setText("eyebrow", spec.eyebrow);
    setText("headline", spec.headline);
    setText("subline", spec.subline);

    document.getElementById("points").innerHTML = (spec.points || [])
      .map(function (point, idx) {
        var no = String(idx + 1).padStart(2, "0");
        return (
          "<li><span class=\"point-n\">" + no + '</span><div class="point-body">' +
          '<p class="point-k">' + esc(point.k) + "</p>" +
          (point.v ? '<p class="point-v">' + esc(point.v) + "</p>" : "") +
          "</div></li>"
        );
      })
      .join("");

    // 卖点不足三条时，用关键词药丸补齐下方留白（数据仍来自 strategy.keywords）
    var kw = document.getElementById("kw");
    var keywords = (spec.keywords || []).slice(0, 4);
    if (kw) {
      if ((spec.points || []).length < 3 && keywords.length) {
        kw.hidden = false;
        kw.innerHTML = keywords.map(function (word) {
          return '<span class="kw-pill">' + esc(word) + "</span>";
        }).join("");
      } else {
        kw.hidden = true;
        kw.innerHTML = "";
      }
    }

    setText("shop-name", spec.shop);
    setText("shop-meta", spec.meta);
    setText("cta", spec.cta);
    setText("fine", spec.fine);

    host.hidden = false;
    window.__posterReady = true;
    fitText(host);
    if (document.fonts && document.fonts.ready) {
      document.fonts.ready.then(function () {
        fitText(host);
      });
    }
  }

  function readSpec() {
    var raw = query.get("spec");
    if (!raw) return null;
    try {
      var normalized = raw.replace(/-/g, "+").replace(/_/g, "/");
      while (normalized.length % 4) normalized += "=";
      var binary = atob(normalized);
      var bytes = new Uint8Array(binary.length);
      for (var i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
      return JSON.parse(new TextDecoder("utf-8").decode(bytes));
    } catch (err) {
      return null;
    }
  }

  var spec = readSpec();
  if (!spec) {
    document.getElementById("fallback").hidden = false;
    window.__posterReady = false;
    return;
  }
  paint(spec);
})();
