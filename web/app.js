/* askdata · 前端（零构建，原生 JS） */

const PAGES = {
  ask: { idx: "01", en: "ASK", title: "问数", desc: "用一句中文问数据。系统会先判断信息够不够，不够就先反问你，而不是猜一个然后算错。" },
  dict: { idx: "02", en: "SCHEMA", title: "数据字典", desc: "表清单、字段、枚举值和样例数据。模型每次只「看见」其中被召回的几张。" },
  eval: { idx: "03", en: "EVAL", title: "评测", desc: "50 道题的 Execution Accuracy。判分不是比字符串，是把两条 SQL 都跑一遍、比结果集。" },
  guard: { idx: "04", en: "GUARD", title: "安全闸", desc: "模型写的 SQL 不会直接执行。先过 AST 白名单、强制 LIMIT、超时熔断、行级权限。" },
  clarify: { idx: "05", en: "CLARIFY", title: "澄清", desc: "该问就问，不该问别问。两边都要测——一个永远反问的系统也能拿 100 分。" },
  about: { idx: "06", en: "ABOUT", title: "说明", desc: "这个项目到底在干什么，以及那些数字是怎么来的。" },
};

const state = {
  overview: null,
  questions: [],
  attacks: [],
  reports: [],
  tables: [],
  clarify: null,
  closed: null,
  topK: 8,
  history: [],
  hints: null,
  dialog: [],
  sessionId: null,
  token: '',
  apiKey: '',
  serverKey: false,
  year: null,
  dim: null,
  metric: null,
  dictActive: null,
};

/* ---------------- 基础工具 ---------------- */

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];

// 兜底：任何未捕获的前端错误都直接显示出来，不要静默死掉
function showFatal(message) {
  const el = document.getElementById("toast");
  if (!el) return;
  el.textContent = `前端出错：${message}`;
  el.classList.add("show");
  el.style.background = "#c4382b";
  el.style.color = "#fff";
}

window.addEventListener("error", (event) => showFatal(event.message));
// async 函数里抛的错不会触发 error 事件，只会变成未处理的 rejection —— 之前就是这样静默失败的
window.addEventListener("unhandledrejection", (event) => {
  showFatal((event.reason && event.reason.message) || String(event.reason));
});

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

function fmtNum(v) {
  if (typeof v !== "number") return esc(v);
  if (Number.isInteger(v)) return v.toLocaleString("en-US");
  return v.toLocaleString("en-US", { maximumFractionDigits: 2 });
}

const pct = (v) => (typeof v === "number" ? `${(v * 100).toFixed(1)}%` : "—");
const ms = (v) => (typeof v === "number" ? `${v < 10 ? v.toFixed(1) : Math.round(v)}ms` : "");

/* ---------------- SQL 语法高亮 ---------------- */

const SQL_KEYWORDS = new Set(
  ("select from where join left right inner outer on group by order limit with as and or in not null " +
    "case when then else end sum count avg round max min distinct having union all timestamp date " +
    "interval over partition row_number lag lead filter using cross exists between is like asc desc " +
    "nulls first last total coalesce nullif extract strftime date_diff percentile_cont floor").split(" ")
);

const SQL_TOKEN = /(--[^\n]*)|('(?:[^']|'')*')|(\b\d+(?:\.\d+)?\b)|([A-Za-z_][A-Za-z0-9_]*)/g;

function highlightSQL(sql) {
  let out = "";
  let last = 0;
  let m;
  SQL_TOKEN.lastIndex = 0;
  while ((m = SQL_TOKEN.exec(sql)) !== null) {
    out += esc(sql.slice(last, m.index));
    const [whole, comment, str, num, word] = m;
    if (comment) out += `<span class="c">${esc(whole)}</span>`;
    else if (str) out += `<span class="s">${esc(whole)}</span>`;
    else if (num) out += `<span class="n">${whole}</span>`;
    else if (SQL_KEYWORDS.has(word.toLowerCase())) out += `<span class="k">${esc(whole)}</span>`;
    else out += esc(whole);
    last = m.index + whole.length;
  }
  out += esc(sql.slice(last));
  return out;
}

let toastTimer = null;
function toast(msg) {
  const el = $("#toast");
  el.textContent = msg;
  el.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => el.classList.remove("show"), 1800);
}

async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    toast("已复制");
  } catch {
    toast("复制失败，请手动选择");
  }
}

function downloadCSV(columns, rows, name = "result") {
  const cell = (v) => {
    const s = v === null || v === undefined ? "" : String(v);
    return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const csv = [columns.map(cell).join(","), ...rows.map((r) => r.map(cell).join(","))].join("\n");
  const blob = new Blob(["\ufeff" + csv], { type: "text/csv;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = `${name}.csv`;
  a.click();
  URL.revokeObjectURL(a.href);
  toast("已下载 CSV");
}

/* ---------------- 主题 ---------------- */

function applyTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("askdata-theme", theme);
}

function applyScale(scale) {
  document.documentElement.dataset.scale = scale;
  localStorage.setItem("askdata-scale", scale);
}

function initTheme() {
  // URL 参数优先：?theme=dark 可以直接分享"夜间版"链接，截图脚本也靠它
  const params = new URLSearchParams(location.search);
  const forcedTheme = params.get("theme");
  const saved = localStorage.getItem("askdata-theme");
  const theme =
    forcedTheme === "dark" || forcedTheme === "light"
      ? forcedTheme
      : saved === "dark" || saved === "light"
      ? saved
      : "light";
  applyTheme(theme);

  $("#theme-toggle").onclick = () => {
    const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
    applyTheme(next);
    toast(next === "dark" ? "切到夜间" : "切到日间");
  };

  const forcedScale = params.get("scale");
  const savedScale = localStorage.getItem("askdata-scale");
  const scale =
    forcedScale === "lg" || forcedScale === "std"
      ? forcedScale
      : savedScale === "lg"
      ? "lg"
      : "std";
  applyScale(scale);

  $("#scale-toggle").onclick = () => {
    const next = document.documentElement.dataset.scale === "lg" ? "std" : "lg";
    applyScale(next);
    toast(next === "lg" ? "字号：大" : "字号：标准");
  };
}

/* ---------------- 导航 ---------------- */

function setPage(name) {
  const meta = PAGES[name];
  if (!meta) return;
  $$(".nav-item").forEach((b) => b.classList.toggle("is-active", b.dataset.page === name));
  $$(".page").forEach((p) => p.classList.toggle("is-active", p.id === `page-${name}`));
  $("#page-eyebrow").textContent = `${meta.idx} / ${meta.en}`;
  $("#page-title").textContent = meta.title;
  $("#page-desc").textContent = meta.desc;
  history.replaceState(null, "", `#${name}`);
  if (name === "eval") ensureEval();
  if (name === "guard") ensureGuard();
  if (name === "clarify") ensureClarify();
  if (name === "dict") ensureDict();
  if (name === "about") renderAbout();
}

/* ---------------- API ---------------- */

function authHeaders(extra = {}) {
  const headers = { "Content-Type": "application/json", ...extra };
  if (state.token) headers.Authorization = `Bearer ${state.token}`;
  if (state.apiKey) headers["X-API-Key"] = state.apiKey;
  return headers;
}

async function api(path, options = {}) {
  const res = await fetch(path, { ...options, headers: authHeaders(options.headers) });
  const data = await res.json().catch(() => ({ ok: false, error: `HTTP ${res.status}` }));
  if (res.status === 401) {
    showAuthBar();
    throw new Error("需要访问令牌");
  }
  if (data.need_api_key) {
    showKeyBar(data.error);
    throw new Error(data.error || "需要配置模型 API Key");
  }
  if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
  return data;
}

/* ---------------- 模型 API Key ---------------- */

function showKeyBar(message) {
  const bar = $("#key-bar");
  if (!bar) return;
  bar.hidden = false;
  if (message) $("#key-msg").textContent = message;
  $("#key-input").focus();
}

/* 配置状态决定"要不要打扰用户"：
   没配 Key → 徽章变成醒目的警示色，配置栏直接铺开
   配好了  → 收起来，只留一个小徽章 */
function refreshKeyState() {
  const bar = $("#key-bar");
  const chip = $("#key-toggle");
  if (!chip) return;

  const local = Boolean(state.apiKey);
  const server = Boolean(state.serverKey);
  const ready = local || server;

  chip.classList.toggle("warn", !ready);
  chip.classList.toggle("ok", ready);
  const text = $("#key-text");
  if (text) {
    text.textContent = ready
      ? local
        ? `Key 已配置`
        : `Key（服务端）`
      : "未配置 Key";
  }
  chip.title = ready
    ? local
      ? `已配置 ${state.apiKey.slice(0, 6)}…${state.apiKey.slice(-4)}（点击修改）`
      : "服务端已配置 API Key（点击可换成本地 Key）"
    : "还没有模型 API Key，提问会失败——点这里配置";

  if (bar) {
    bar.classList.toggle("is-warn", !ready);
    $("#key-msg").textContent = local
      ? `已配置（${state.apiKey.slice(0, 6)}…${state.apiKey.slice(-4)}），只存在这台浏览器里`
      : server
        ? "服务端已配置，这里可以填自己的 Key 覆盖它"
        : "还没有 Key，提问会失败。粘贴后保存即可，只存在这台浏览器里";
  }

  // 没配好时把配置栏直接铺开，不指望用户去点那个按钮
  if (!ready) bar.hidden = false;
}

function bindKeyBar() {
  state.apiKey = localStorage.getItem("askdata-api-key") || "";
  refreshKeyState();
  // 支持 ?key=1 直接展开，方便把"去配 key"的链接发给别人
  if (new URLSearchParams(location.search).get("key") === "1") showKeyBar();

  $("#key-toggle").onclick = () => {
    const bar = $("#key-bar");
    bar.hidden = !bar.hidden;
    if (!bar.hidden) $("#key-input").focus();
  };

  $("#key-save").onclick = () => {
    const value = ($("#key-input").value || "").trim();
    if (!value) {
      $("#key-msg").textContent = "先填入 Key 再保存";
      return;
    }
    state.apiKey = value;
    localStorage.setItem("askdata-api-key", value);
    $("#key-input").value = "";
    refreshKeyState();
    toast("已保存模型 Key，可以提问了");
  };

  $("#key-clear").onclick = () => {
    state.apiKey = "";
    localStorage.removeItem("askdata-api-key");
    refreshKeyState();
    toast("已清除模型 Key");
  };

  $("#key-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#key-save").click();
  });
}

/* ---------------- 访问令牌 ---------------- */

function showAuthBar(message) {
  const bar = $("#auth-bar");
  if (!bar) return;
  bar.hidden = false;
  if (message) $("#auth-msg").textContent = message;
  $("#auth-input").focus();
}

function bindAuth() {
  state.token = localStorage.getItem("askdata-token") || "";
  if (state.token) $("#auth-bar").hidden = true;
  $("#auth-save").onclick = async () => {
    const value = ($("#auth-input").value || "").trim();
    if (!value) return;
    state.token = value;
    localStorage.setItem("askdata-token", value);
    try {
      await api("/api/overview");
      $("#auth-bar").hidden = true;
      toast("令牌已保存");
    } catch (err) {
      $("#auth-msg").textContent = `令牌不对：${err.message}`;
    }
  };
  $("#auth-input").addEventListener("keydown", (e) => {
    if (e.key === "Enter") $("#auth-save").click();
  });
}

/* ---------------- 图表 ---------------- */

function barChart(columns, rows) {
  const numeric = [];
  columns.forEach((c, i) => {
    const values = rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined);
    if (values.length && values.every((v) => typeof v === "number")) numeric.push(i);
  });
  if (!numeric.length || rows.length < 2) return "";

  const valueIdx = numeric[numeric.length - 1];
  const labelIdx = columns.findIndex((_, i) => !numeric.includes(i));
  const data = rows.slice(0, 12).map((r) => ({
    label: labelIdx >= 0 ? String(r[labelIdx] ?? "") : "",
    value: Number(r[valueIdx]) || 0,
  }));
  const max = Math.max(...data.map((d) => d.value), 1);
  const rowH = 22;
  const W = 640;
  const H = data.length * rowH + 8;
  const labelW = 96;
  const valueW = 78;
  const barW = W - labelW - valueW - 12;

  const bars = data
    .map((d, i) => {
      const y = i * rowH + 4;
      const w = Math.max(2, (d.value / max) * barW);
      return `
        <text class="lbl" x="0" y="${y + 13}">${esc(d.label.slice(0, 12))}</text>
        <rect class="bar" x="${labelW}" y="${y + 3}" width="${w}" height="12" rx="2"></rect>
        <text class="val" x="${labelW + barW + 8}" y="${y + 13}">${fmtNum(d.value)}</text>`;
    })
    .join("");

  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">${bars}</svg></div>`;
}

let trendRange = "";

function trendChart(reports) {
  const points = reports
    .filter((r) => typeof r.ex === "number")
    .sort((a, b) => a.mtime - b.mtime)
    .slice(-12);
  if (points.length < 2) return `<p class="dim small">报告还不够，多跑几份就能看到趋势。</p>`;

  const W = 640, H = 120, padL = 28, padR = 10, padT = 12, padB = 22;
  const series = points.map((p) => ({ ex: p.ex, name: p.name, kind: p.kind, runs: p.n_runs }));
  const xs = series.map((_, i) => padL + (i * (W - padL - padR)) / (series.length - 1));
  const ys = series.map((s) => padT + (1 - s.ex) * (H - padT - padB));

  const line = xs.map((x, i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const area = `${line} L${xs[xs.length - 1].toFixed(1)},${H - padB} L${xs[0].toFixed(1)},${H - padB} Z`;
  const bestIdx = series.reduce((best, s, i) => (s.ex > series[best].ex ? i : best), 0);

  const dots = series
    .map((s, i) => {
      const cls = i === bestIdx ? "pt best" : "pt";
      return `<circle class="${cls}" cx="${xs[i].toFixed(1)}" cy="${ys[i].toFixed(1)}" r="3.2"><title>${esc(
        s.name
      )} · ${pct(s.ex)}</title></circle>`;
    })
    .join("");

  const grid = [0.25, 0.5, 0.75].map((f) => {
    const y = padT + f * (H - padT - padB);
    return `<line class="grid-line" x1="${padL}" y1="${y}" x2="${W - padR}" y2="${y}"></line>`;
  });

  // y 轴刻度：只有 0% / 50% / 100% 三条参考线的话，86%~100% 之间的差别看不出来
  const exMax = Math.max(...series.map((s) => s.ex));
  const exMin = Math.min(...series.map((s) => s.ex));
  const axis = [1, 0.5, 0].map((value) => {
    const y = padT + (1 - value) * (H - padT - padB) + 3;
    return `<text class="axis" x="${padL - 6}" y="${y.toFixed(1)}" text-anchor="end">${Math.round(
      value * 100
    )}%</text>`;
  }).join("");

  trendRange = `最高 ${pct(exMax)} · 最低 ${pct(exMin)}`;

  return `<svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">
    ${grid}
    ${axis}
    <path class="area" d="${area}"></path>
    <path class="line" d="${line}"></path>
    ${dots}
    <text x="${padL}" y="${H - 6}">最早</text>
    <text class="tip" x="${W - padR}" y="${H - 6}" text-anchor="end">最新 ${pct(series[series.length - 1].ex)}</text>
  </svg>`;
}

/* ---------------- 01 问数 ---------------- */

/* 预设问题不带年份 —— 年份由上面的时间标签决定，用户自己选 */
const PRESETS = [
  "各渠道的销售额",
  "销售额 Top 5 商品",
  "各品类的毛利率",
  "各会员等级的客单价",
  "各省份的订单数",
  "各支付方式的金额",
  "退款率",
  "退款原因分布",
  "月度销售额走势",
  "复购率",
  "各渠道的退款率",
  "销售额同比",
];

const DIMENSIONS = ["渠道", "商品品类", "会员等级", "省份", "城市", "支付方式"];
const METRICS = ["销售额", "订单数", "客单价", "退款金额", "退款率", "毛利率"];

function composeQuestion() {
  const parts = [];
  if (state.year) parts.push(`${state.year} 年`);
  if (state.dim) parts.push(`各${state.dim}的`);
  if (state.metric) parts.push(state.metric);
  if (!state.dim && !state.metric) return state.year ? `${state.year} 年` : "";
  return parts.join("");
}

function refreshCompose() {
  const text = composeQuestion();
  if (text) $("#q").value = text;
  $("#q").focus();
}

function chipRow(container, items, key, onPick) {
  container.innerHTML = items
    .map((item) => {
      const value = typeof item === "string" ? item : item.value;
      const label = typeof item === "string" ? item : item.label;
      return `<button class="chip-btn ${state[key] === value ? "is-on" : ""}" data-value="${esc(value)}">${esc(
        label
      )}</button>`;
    })
    .join("");
  container.onclick = (e) => {
    const btn = e.target.closest(".chip-btn");
    if (!btn) return;
    state[key] = state[key] === btn.dataset.value ? null : btn.dataset.value;
    renderQuickActive();
    onPick();
  };
}

function ensureQuick() {
  const hints = state.hints || {};
  const range = hints.time;
  $("#quick-range").textContent = range?.min
    ? `数据范围 ${range.min} ~ ${range.max} · ${range.month_count} 个月`
    : "未读取到数据范围";

  chipRow(
    $("#quick-years"),
    (range?.years || []).map((y) => ({ value: String(y), label: `${y} 年` })),
    "year",
    refreshCompose
  );
  // 维度和指标都以接口为准：库里能按什么分、能算什么，由后端遍历 schema 得出。
  // 前端只留一份兜底常量，避免"后端加了维度、界面还看不到"这种漂移。
  const dims = Object.keys(hints.dimensions || {});
  const metrics = Array.isArray(hints.metrics) && hints.metrics.length ? hints.metrics : METRICS;
  chipRow($("#quick-dims"), dims.length ? dims : DIMENSIONS, "dim", refreshCompose);
  chipRow($("#quick-metrics"), metrics, "metric", refreshCompose);

  const presets = $("#quick-presets");
  presets.innerHTML = PRESETS.map(
    (p) => `<button class="chip-btn preset" data-text="${esc(p)}">${esc(p)}</button>`
  ).join("");
  presets.onclick = (e) => {
    const btn = e.target.closest(".chip-btn");
    if (!btn) return;
    $("#q").value = state.year ? `${state.year} 年${btn.dataset.text}` : btn.dataset.text;
    $("#q").focus();
  };

  // 没有内容的行整行隐藏 —— 否则只会剩下一个孤零零的标签，看着像"空的快捷提问"
  $$("#quick .quick-row").forEach((row) => {
    const chips = row.querySelector(".chips");
    row.classList.toggle("is-empty", !chips || chips.children.length === 0);
  });
}

function renderQuickActive() {
  const map = { "#quick-years": "year", "#quick-dims": "dim", "#quick-metrics": "metric" };
  Object.entries(map).forEach(([sel, key]) => {
    $$(".chip-btn", $(sel)).forEach((b) => b.classList.toggle("is-on", b.dataset.value === state[key]));
  });
}

function loadHistory() {
  try {
    state.history = JSON.parse(localStorage.getItem("askdata-history") || "[]");
  } catch {
    state.history = [];
  }
  renderHistory();
}

function pushHistory(question, ok) {
  state.history = [{ q: question, t: Date.now(), ok }, ...state.history.filter((h) => h.q !== question)].slice(0, 12);
  localStorage.setItem("askdata-history", JSON.stringify(state.history));
  renderHistory();
}

function renderHistory() {
  $("#history-count").textContent = state.history.length ? `${state.history.length} 条` : "";
  if (!state.history.length) {
    $("#history").innerHTML = `<p class="dim small">问过的题会存在本地，点一下可以重问。</p>`;
    return;
  }
  $("#history").innerHTML = state.history
    .map(
      (h, i) =>
        `<div class="hist-item" data-i="${i}"><span class="hist-q">${esc(h.q)}</span><span class="hist-t">${
          h.ok ? "✓" : "·"
        }</span></div>`
    )
    .join("");
  $("#history").onclick = (e) => {
    const row = e.target.closest(".hist-item");
    if (!row) return;
    $("#q").value = state.history[Number(row.dataset.i)].q;
    $("#q").focus();
  };
}

function pipelineHTML(stages) {
  return `<div class="pipeline">${stages
    .map((s) => `<div class="stage" title="${esc(s.detail)}"><i></i><b>${esc(s.name)}</b><span>${ms(s.ms)}</span></div>`)
    .join("")}</div>`;
}

const sqlBlock = (sql) => `<div class="sqlblock">${highlightSQL(sql)}</div>`;

function resultTable(columns, rows) {
  if (!columns.length) return "";
  const head = columns.map((c) => `<th>${esc(c)}</th>`).join("");
  const body = rows
    .map((r) => `<tr>${r.map((c) => `<td>${c === null ? '<span class="dim">NULL</span>' : fmtNum(c)}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="table-wrap"><table class="result-table"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table></div>`;
}

function renderRetrieval(tables) {
  $("#retrieval-hint").textContent = `${tables.length} 张`;
  $("#retrieval").innerHTML = tables
    .map(
      (t) => `<div class="ret-item">
        <div class="ret-top"><span class="ret-name ${t.business ? "biz" : ""}">${esc(t.name)}</span><span class="ret-score">${t.score}</span></div>
        <div class="ret-bar"><i style="width:${Math.max(6, t.rel * 100)}%"></i></div>
      </div>`
    )
    .join("");
}

function beginTurn(label) {
  $("#thread-empty")?.remove();
  const turn = document.createElement("div");
  turn.className = "turn";
  const user = document.createElement("div");
  user.className = "msg-user";
  user.textContent = label;
  turn.appendChild(user);
  $("#thread").prepend(turn);
  turn.scrollIntoView({ behavior: "smooth", block: "nearest" });
  return turn;
}

function appendLoading(turn) {
  const card = document.createElement("div");
  card.className = "card";
  card.innerHTML = `<div class="pipeline"><div class="stage"><i></i><b>处理中</b><span>…</span></div></div>
    <div class="card-body"><p class="dim small">正在召回表、写 SQL、过安全闸。</p></div>`;
  turn.appendChild(card);
  return card;
}

/* 澄清时：把库里真实存在的取值列出来，点一下就填进回答框 */
function renderHints(hints) {
  if (!hints) return "";
  const t = hints.time || {};
  const dims = hints.dimensions || {};
  const chip = (text) => `<button class="chip-btn" data-fill="${esc(text)}">${esc(text)}</button>`;
  const row = (k, chips) =>
    chips && chips.length
      ? `<div class="hint-row"><span class="hint-k">${k}</span><div class="chips">${chips.map(chip).join("")}</div></div>`
      : "";

  const timeChips = [];
  (t.years || []).forEach((y) => timeChips.push(`${y} 年`));
  (t.months || []).slice(-2).forEach((m) => timeChips.push(`${m.slice(0, 4)} 年 ${Number(m.slice(5))} 月`));
  if (t.min && t.max) timeChips.push(`${t.min} 至 ${t.max}`);

  // 维度值可能很多（现在有 12 个维度组），澄清卡片不能无限长：
  // 按"最可能被追问"的顺序挑几组，总数封顶 16 个标签
  const dimChips = [];
  const priority = ["渠道", "商品品类", "省份", "城市", "会员等级", "支付方式", "渠道类型", "二级品类"];
  const groups = [...priority, ...Object.keys(dims).filter((k) => !priority.includes(k))];
  for (const key of groups) {
    for (const value of dims[key] || []) {
      if (dimChips.length >= 16) break;
      if (!dimChips.includes(value)) dimChips.push(value);
    }
    if (dimChips.length >= 16) break;
  }

  const rules = (hints.rules || [])
    .map(
      (r) =>
        `<button class="rule-line" data-fill="${esc(r)}" title="点一下把这句口径填进回答框">· ${esc(
          r
        )}<span>填入</span></button>`
    )
    .join("");

  return `<div class="hints">
    <div class="hint-head">库里的数据范围（实时读取）
      <span>${t.min ? `${t.min} ~ ${t.max} · ${t.month_count} 个月` : "—"}</span>
    </div>
    ${row("时间", timeChips.slice(0, 8))}
    ${row("维度", dimChips)}
    ${row("指标", hints.metrics || [])}
    ${rules ? `<div class="hint-row"><span class="hint-k">口径</span><div class="rule-list">${rules}</div></div>` : ""}
  </div>`;
}

/* 时间序列折线图（月度走势这类结果） */
function lineChart(labels, values) {
  const W = 660, H = 200, padL = 16, padR = 16, padT = 20, padB = 28;
  const max = Math.max(...values);
  const min = Math.min(...values);
  const span = max - min || 1;
  const xs = values.map((_, i) => padL + (i * (W - padL - padR)) / Math.max(1, values.length - 1));
  const ys = values.map((v) => padT + (1 - (v - min) / span) * (H - padT - padB));
  const line = xs.map((x, i) => `${i ? "L" : "M"}${x.toFixed(1)},${ys[i].toFixed(1)}`).join(" ");
  const area = `${line} L${xs[xs.length - 1].toFixed(1)},${H - padB} L${xs[0].toFixed(1)},${H - padB} Z`;
  const dots = xs
    .map(
      (x, i) =>
        `<circle class="pdot" cx="${x.toFixed(1)}" cy="${ys[i].toFixed(1)}" r="2.8"><title>${esc(
          labels[i]
        )} · ${fmtNum(values[i])}</title></circle>`
    )
    .join("");
  const idx = [0, Math.floor((labels.length - 1) / 2), labels.length - 1].filter((v, i, a) => a.indexOf(v) === i);
  const ticks = idx
    .map(
      (i) =>
        `<text x="${xs[i].toFixed(1)}" y="${H - 8}" text-anchor="${
          i === 0 ? "start" : i === labels.length - 1 ? "end" : "middle"
        }">${esc(labels[i])}</text>`
    )
    .join("");
  return `<div class="chart"><svg viewBox="0 0 ${W} ${H}" preserveAspectRatio="xMinYMin meet">
    <path class="area" d="${area}"></path><path class="line" d="${line}"></path>${dots}${ticks}
  </svg></div>`;
}

/* 排名榜：名次 + 名称 + 比例条 + 数值 */
function rankingList(labels, values, nameTitle, valueTitle) {
  const max = Math.max(...values.map((v) => Math.abs(v)), 1);
  const rows = labels
    .map((label, i) => {
      const v = values[i];
      const w = Math.max(3, (Math.abs(v) / max) * 100);
      const isBlank = !label || !String(label).trim();
      return `<div class="rank-row">
        <span class="rank-no">${String(i + 1).padStart(2, "0")}</span>
        <span class="rank-name ${isBlank ? "is-blank" : ""}" title="${esc(label)}">${
        isBlank ? "（未填写）" : esc(label)
      }</span>
        <span class="rank-bar"><i style="width:${w}%"></i></span>
        <span class="rank-val">${fmtNum(v)}</span>
      </div>`;
    })
    .join("");
  return `<div class="rank">
    <div class="rank-cap"><span>#</span><span>${esc(nameTitle || "名称")}</span><span></span><span>${esc(
    valueTitle || "值"
  )}</span></div>
    ${rows}</div>`;
}

const TIME_LIKE = /^\d{4}([-/]\d{1,2}){0,2}/;

/* 答案区：先按结果形状挑一种最好看的呈现方式 */
function renderAnswer(data) {
  const columns = data.columns || [];
  const rows = data.rows || [];
  if (!columns.length || !rows.length) return `<p class="dim small">查询没有返回数据。</p>`;

  if (rows.length === 1 && columns.length === 1) {
    const v = rows[0][0];
    return `<div class="hero">
      <div class="hero-k">${esc(columns[0])}</div>
      <div class="hero-v">${typeof v === "number" ? fmtNum(v) : esc(v ?? "NULL")}</div>
    </div>`;
  }

  if (rows.length === 1) {
    const items = columns
      .map((c, i) => {
        const v = rows[0][i];
        return `<div class="kv-item"><div class="k">${esc(c)}</div><div class="v">${
          typeof v === "number" ? fmtNum(v) : esc(v ?? "NULL")
        }</div></div>`;
      })
      .join("");
    return `<div class="kv-strip">${items}</div>`;
  }

  const numeric = [];
  columns.forEach((c, i) => {
    const values = rows.map((r) => r[i]).filter((v) => v !== null && v !== undefined);
    if (values.length && values.every((v) => typeof v === "number")) numeric.push(i);
  });
  const valueIdx = numeric[numeric.length - 1];
  const labelIdx = columns.findIndex((_, i) => !numeric.includes(i));

  if (valueIdx === undefined || labelIdx < 0) {
    return resultTable(columns, rows);
  }

  const labels = rows.map((r) => String(r[labelIdx] ?? ""));
  const values = rows.map((r) => Number(r[valueIdx]) || 0);

  // 月度 / 年度序列：折线更有信息量
  if (rows.length >= 6 && labels.every((l) => TIME_LIKE.test(l))) {
    return lineChart(labels, values) + resultTable(columns, rows);
  }
  // 排行榜（≤15 行最像"Top N"）
  if (rows.length <= 15) {
    return rankingList(labels, values, columns[labelIdx], columns[valueIdx]);
  }
  return barChart(columns, rows) + resultTable(columns, rows);
}

function sqlDetails(data) {
  if (!data.sql) return "";
  return `<details class="fold" open>
    <summary>它写的 SQL</summary>
    <div class="fold-body">${sqlBlock(data.sql)}</div>
  </details>`;
}

function execDetails(data) {
  const stages = data.stages || [];
  if (!stages.length) return "";
  const bits = [];
  if (data.total_ms) bits.push(`${Math.round(data.total_ms)}ms`);
  if (data.usage) bits.push(`${data.usage.prompt}+${data.usage.completion} tokens`);
  return `<details class="fold">
    <summary>执行详情<span>${bits.join(" · ")}</span></summary>
    <div class="fold-body">${pipelineHTML(stages)}</div>
  </details>`;
}

/* 校验 Agent 的提示条：数字可能算错时，先让人看见警告再看数字 */
function issuesHTML(issues) {
  if (!issues || !issues.length) return "";
  const rows = issues
    .map(
      (i) => `<div class="issue-item ${i.level === "info" ? "info" : "warn"}">
        <span class="issue-dot"></span><span>${esc(i.message)}</span>
      </div>`
    )
    .join("");
  return `<div class="issue-strip">${rows}</div>`;
}

function appendAssistant(turn, data, question) {
  const card = document.createElement("div");
  card.className = "card";
  let body = "";

  if (data.error) {
    body = `<div class="error-box">${esc(data.error)}</div>`;
    if (data.sql) body += `<div style="margin-top:12px">${sqlBlock(data.sql)}</div>`;
  } else if (data.need_clarification) {
    body = `<div class="clarify-tag">需要补充信息</div>
      <div class="clarify-bubble">${esc(data.clarification)}</div>
      <div class="answer-row"><input type="text" placeholder="回答它，或者点下面的标签直接填…" /><button class="btn btn-primary">回答</button></div>
      ${renderHints(data.hints)}${execDetails(data)}`;
  } else {
    // 顺序很重要：校验提示 → 答案 → SQL → 耗时。
    // 校验必须排在答案前面：如果一个数字可能算错了，先让人看见警告，再看见数字。
    body =
      (data.rewritten && data.rewritten !== question
        ? `<div class="rewrite-tag">理解为：${esc(data.rewritten)}</div>`
        : "") +
      issuesHTML(data.issues) +
      renderAnswer(data) +
      sqlDetails(data) +
      execDetails(data);
    if (data.truncated) body += `<p class="dim small" style="margin-top:10px">只显示前 200 行。</p>`;
  }

  const status = data.need_clarification
    ? "需要补充信息"
    : data.error
    ? "出错"
    : `${data.row_count ?? 0} 行`;

  const actions = data.sql
    ? `<div class="card-actions">
         <button class="btn btn-ghost" data-act="copy">复制 SQL</button>
         ${data.rows?.length ? '<button class="btn btn-ghost" data-act="csv">下载 CSV</button>' : ""}
       </div>`
    : "";

  card.innerHTML = `<div class="card-body">${body}</div>
    <div class="card-foot"><span>${esc(status)}</span>${actions}</div>`;

  card.querySelector('[data-act="copy"]')?.addEventListener("click", () => copyText(data.sql));
  card.querySelector('[data-act="csv"]')?.addEventListener("click", () =>
    downloadCSV(data.columns, data.rows, "askdata-result")
  );

  const input = card.querySelector("input");
  if (input) {
    card.querySelectorAll(".chip-btn, .rule-line").forEach((btn) => {
      btn.addEventListener("click", () => {
        const value = btn.dataset.fill || "";
        input.value = input.value ? `${input.value}；${value}` : value;
        input.focus();
      });
    });
    const submit = () => {
      const answer = input.value.trim();
      if (!answer) return;
      askQuestion(question, { clarification: data.clarification, answer });
    };
    input.addEventListener("keydown", (e) => {
      if (e.key === "Enter") submit();
    });
    card.querySelector("button.btn-primary").onclick = submit;
    setTimeout(() => input.focus(), 60);
  }

  turn.appendChild(card);
  card.scrollIntoView({ behavior: "smooth", block: "nearest" });
}

async function askQuestion(question, opts = {}) {
  const label = opts.answer ? `补充：${opts.answer}` : question;
  const turn = beginTurn(label);
  const loading = appendLoading(turn);
  $("#send").disabled = true;
  try {
    const payload = { question, top_k: state.topK };
    if (state.sessionId) payload.session_id = state.sessionId;
    // 多轮：把最近 3 轮带上去，服务端会把追问改写成独立问题
    if (state.dialog.length) payload.history = state.dialog.slice(-3);
    if (opts.clarification && opts.answer) {
      Object.assign(payload, { clarification: opts.clarification, answer: opts.answer });
    }
    const data = await askStream(payload, loading);
    loading.remove();
    if (data.tables) renderRetrieval(data.tables);
    appendAssistant(turn, data, question);
    pushHistory(question, !data.error && !data.need_clarification);
    if (data.session_id) updateSessionChip(data.session_turns);

    // 记住这一轮，供下一轮改写用
    if (!data.error) {
      if (opts.clarification && opts.answer) {
        const last = state.dialog[state.dialog.length - 1];
        if (last) last.sql = data.sql || last.sql;
      } else {
        state.dialog.push({ question, sql: data.sql || "" });
        if (state.dialog.length > 6) state.dialog.shift();
      }
    }
  } catch (err) {
    loading.remove();
    const card = document.createElement("div");
    card.className = "card";
    card.innerHTML = `<div class="card-body"><div class="error-box">${esc(err.message)}</div></div>`;
    turn.appendChild(card);
  } finally {
    $("#send").disabled = false;
  }
}

/* 空对话占位：清空会话后要能复原成刚打开的样子 */
function emptyThreadHTML() {
  return `<div class="empty" id="thread-empty">
    <div class="empty-mark">⌁</div>
    <p>还没有对话。上面随便问一句试试。</p>
    <p class="dim">它可能会反问你，也可能会给你一张表。</p>
  </div>`;
}

/* ---------------- 流式问数（SSE） ---------------- */

/* 边收边渲染：每来一个阶段就更新卡片上的进度条，最后再替换成完整结果 */
async function askStream(payload, loadingCard) {
  const res = await fetch("/api/ask/stream", {
    method: "POST",
    headers: authHeaders({ Accept: "text/event-stream" }),
    body: JSON.stringify(payload),
  });
  if (res.status === 401) {
    showAuthBar();
    throw new Error("需要访问令牌");
  }
  if (res.status === 400) {
    // 流式接口在缺 key 时会退回普通 JSON，这里要读出来给用户看
    const detail = await res.json().catch(() => ({}));
    if (detail.need_api_key) {
      showKeyBar(detail.error);
      throw new Error(detail.error || "需要配置模型 API Key");
    }
    throw new Error(detail.error || "请求被拒绝");
  }
  if (!res.ok || !res.body) throw new Error(`HTTP ${res.status}`);

  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let result = null;
  let finished = false;
  const stages = [];

  const paintStages = () => {
    const pipeline = loadingCard?.querySelector(".pipeline");
    if (!pipeline) return;
    pipeline.innerHTML =
      stages
        .map(
          (s) =>
            `<div class="stage"><i></i><b>${esc(s.name)}</b><span>${esc(
              s.detail
            )}</span></div>`
        )
        .join("") || `<div class="stage"><i></i><b>处理中</b><span>…</span></div>`;
  };

  for (;;) {
    if (finished) break;
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let split;
    while ((split = buffer.indexOf("\n\n")) >= 0) {
      const chunk = buffer.slice(0, split);
      buffer = buffer.slice(split + 2);
      let event = "";
      let dataRaw = "";
      for (const line of chunk.split("\n")) {
        if (line.startsWith("event: ")) event = line.slice(7).trim();
        else if (line.startsWith("data: ")) dataRaw = line.slice(6);
      }
      if (!dataRaw) continue;
      let data;
      try {
        data = JSON.parse(dataRaw);
      } catch {
        continue;
      }
      if (event === "stage") {
        stages.push(data);
        paintStages();
      } else if (event === "result") {
        result = data;
        // 收到结果就结束，不要等连接关闭——服务端即使忘了 close 也不会卡住
        finished = true;
      } else if (event === "done") {
        finished = true;
      } else if (event === "error") {
        throw new Error(data.error || "流式请求失败");
      }
    }
  }
  try {
    await reader.cancel();
  } catch {
    /* 关闭失败无所谓 */
  }
  if (!result) throw new Error("流式响应提前结束");
  return result;
}

/* ---------------- 会话（服务端记忆 + 手动清除） ---------------- */

function ensureSession() {
  if (state.sessionId) return state.sessionId;
  let id = localStorage.getItem("askdata-session");
  if (!id) {
    id = (crypto.randomUUID && crypto.randomUUID()) || `s-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`;
    localStorage.setItem("askdata-session", id);
  }
  state.sessionId = id;
  updateSessionChip(0);
  return id;
}

function updateSessionChip(turns) {
  const chip = $("#session-chip");
  if (!chip) return;
  const n = Number(turns) || 0;
  chip.textContent = n ? `已记住 ${n} 轮` : "新会话";
  chip.classList.toggle("has-turns", n > 0);
}

async function clearSession() {
  // 先清服务端，再清界面：反过来的话，万一服务端失败会出现
  // "界面空了但模型还记得上文"这种更糟的状态。
  try {
    await api("/api/session/clear", {
      method: "POST",
      body: JSON.stringify({ session_id: state.sessionId }),
    });
  } catch (err) {
    toast(`清空失败：${err.message}`);
    return;
  }
  state.dialog = [];
  localStorage.removeItem("askdata-session");
  state.sessionId = null;
  ensureSession();
  $$("#thread .turn").forEach((node) => node.remove());
  $("#thread").innerHTML = emptyThreadHTML();
  toast("已清空对话上下文");
}

function bindAsk() {
  loadHistory();
  ensureSession();
  $("#clear-session").onclick = clearSession;
  const send = () => {
    const text = $("#q").value.trim();
    if (!text) return;
    $("#q").value = "";
    askQuestion(text);
  };
  $("#send").onclick = send;
  $("#q").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send();
    }
  });
  $("#topk").addEventListener("input", (e) => {
    state.topK = Number(e.target.value);
    $("#topk-val").textContent = state.topK;
  });
}

/* ---------------- 02 数据字典 ---------------- */

let dictLoaded = false;

async function ensureDict() {
  if (!dictLoaded) {
    dictLoaded = true;
    try {
      state.tables = await api("/api/tables");
    } catch (err) {
      $("#dict-items").innerHTML = `<p class="dim small">加载失败：${esc(err.message)}</p>`;
      return;
    }
    $("#dict-search").addEventListener("input", renderDictList);
    bindImport();
    renderDictList();
    const first = state.tables.find((t) => t.business) || state.tables[0];
    if (first) selectTable(first.name);
  }
}

function renderDictList() {
  const kw = ($("#dict-search").value || "").trim().toLowerCase();
  const list = state.tables.filter(
    (t) => !kw || t.name.toLowerCase().includes(kw) || (t.comment || "").toLowerCase().includes(kw)
  );
  $("#dict-count").textContent = `${list.length} / ${state.tables.length}`;
  $("#dict-items").innerHTML = list
    .map(
      (t) => `<div class="dict-item ${t.business ? "biz" : ""} ${
        t.name === state.dictActive ? "is-active" : ""
      }" data-name="${esc(t.name)}">
        <span class="dn">${esc(t.name)}</span>
        <span class="dc">${t.row_count.toLocaleString()}</span>
      </div>`
    )
    .join("");
  $("#dict-items").onclick = (e) => {
    const row = e.target.closest(".dict-item");
    if (row) selectTable(row.dataset.name);
  };
}

function selectTable(name) {
  const t = state.tables.find((x) => x.name === name);
  if (!t) return;
  state.dictActive = name;
  $$(".dict-item").forEach((el) => el.classList.toggle("is-active", el.dataset.name === name));

  const cols = t.columns
    .map(
      (c) => `<tr>
        <td class="mono">${esc(c.name)}</td>
        <td class="type">${esc(c.type)}</td>
        <td class="note">${esc(c.note || "")}</td>
      </tr>`
    )
    .join("");

  const enumBlocks = Object.entries(t.enums || {})
    .map(([col, values]) => `<div style="margin-top:8px"><span class="dim small">${esc(col)}：</span>${values
      .map((v) => `<span class="tag">${esc(v)}</span>`)
      .join(" ")}</div>`)
    .join("");

  const sample = t.samples?.length
    ? resultTable(t.sample_columns, t.samples)
    : `<p class="dim small" style="margin-top:12px">没有样例数据。</p>`;

  $("#dict-detail").innerHTML = `
    <div class="panel-head">
      <div>
        <div class="eyebrow">${t.business ? "业务表" : "无关表（干扰项）"}</div>
        <h2 style="margin:6px 0 2px;font-size:20px;letter-spacing:-0.02em">${esc(t.name)}</h2>
        <p class="dim small" style="margin:0">${esc(t.comment || "无注释")}</p>
      </div>
      <div style="text-align:right">
        <div class="tag ${t.business ? "biz" : ""}">${t.columns.length} 字段</div>
        <div class="tag">${t.row_count.toLocaleString()} 行</div>
      </div>
    </div>
    <table class="schema-table">
      <thead><tr><th>字段</th><th>类型</th><th>说明</th></tr></thead>
      <tbody>${cols}</tbody>
    </table>
    ${enumBlocks}
    <div class="eyebrow" style="margin-top:18px">样例数据</div>
    ${sample}`;
}

/* ---------------- 02b 导入数据 ---------------- */

const IMPORT_OPTIONS = [
  ["trim", "去掉首尾空白"],
  ["empty_as_null", "空值转 NULL"],
  ["clean_numbers", "清洗数字（千分位 / 货币符号）"],
  ["parse_dates", "识别多种日期写法"],
  ["dedupe", "整行去重"],
  ["drop_empty_rows", "删除全空行"],
  ["normalize_headers", "表头规范化"],
];

const IMPORT_TYPES = [
  ["VARCHAR", "文本"],
  ["BIGINT", "整数"],
  ["DOUBLE", "小数"],
  ["DATE", "日期"],
  ["BOOLEAN", "布尔"],
];

let importState = { text: "", preview: null };

function readImportOptions() {
  const options = {};
  $$("#import-opts input[type=checkbox]").forEach((el) => {
    options[el.dataset.opt] = el.checked;
  });
  return options;
}

function bindImport() {
  $("#import-opts").innerHTML = IMPORT_OPTIONS.map(
    ([key, label]) =>
      `<label class="opt"><input type="checkbox" data-opt="${key}" checked /><span>${esc(label)}</span></label>`
  ).join("");

  const setText = (text, filename) => {
    $("#import-text").value = text;
    if (filename) {
      $("#import-name").value = filename.replace(/\.[^.]+$/, "");
    }
    $("#do-commit").disabled = true;
    $("#import-preview").innerHTML = "";
    $("#import-status").textContent = `已载入 ${text.length.toLocaleString()} 个字符，点「预览与整理」。`;
  };

  $("#pick-file").onclick = () => $("#file-input").click();
  $("#file-input").onchange = (e) => {
    const file = e.target.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ""), file.name);
    reader.readAsText(file, "utf-8");
  };

  const dropzone = $("#dropzone");
  ["dragenter", "dragover"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.add("is-over");
    })
  );
  ["dragleave", "drop"].forEach((evt) =>
    dropzone.addEventListener(evt, (e) => {
      e.preventDefault();
      dropzone.classList.remove("is-over");
    })
  );
  dropzone.addEventListener("drop", (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (!file) return;
    const reader = new FileReader();
    reader.onload = () => setText(String(reader.result || ""), file.name);
    reader.readAsText(file, "utf-8");
  });

  $("#do-preview").onclick = doImportPreview;
  $("#do-commit").onclick = doImportCommit;
}

async function doImportPreview() {
  const text = $("#import-text").value;
  if (!text.trim()) return toast("先粘贴或选择一份数据");
  $("#do-preview").disabled = true;
  $("#import-status").textContent = "正在解析…";
  try {
    const data = await api("/api/import/preview", {
      method: "POST",
      body: JSON.stringify({ text, options: readImportOptions() }),
    });
    if (!data.ok) {
      $("#import-status").textContent = `解析失败：${data.error}`;
      $("#do-commit").disabled = true;
      return;
    }
    importState = { text, preview: data };
    renderImportPreview(data);
    $("#do-commit").disabled = false;
    const s = data.stats;
    $("#import-status").textContent = `识别到 ${s.columns} 列 / 可导入 ${s.rows} 行（原始 ${s.raw_rows} 行）。确认列类型后点导入。`;
  } catch (err) {
    $("#import-status").textContent = `失败：${err.message}`;
  } finally {
    $("#do-preview").disabled = false;
  }
}

function renderImportPreview(data) {
  const s = data.stats;
  const head = `<thead><tr><th style="width:32px"></th><th>列名</th><th style="width:110px">类型</th><th style="width:64px">空值</th><th>样例</th></tr></thead>`;
  const body = data.columns
    .map(
      (c, i) => `<tr data-row="${i}" data-name="${esc(c.name)}">
        <td><input type="checkbox" data-include checked /></td>
        <td class="mono">${esc(c.name)}</td>
        <td><select data-type>${IMPORT_TYPES.map(
          ([v, label]) => `<option value="${v}" ${v === c.type ? "selected" : ""}>${label}</option>`
        ).join("")}</select></td>
        <td class="num">${c.nulls}</td>
        <td class="dim small">${esc((c.samples || []).slice(0, 2).join(" · "))}</td>
      </tr>`
    )
    .join("");

  const sampleHead = data.headers.map((h) => `<th>${esc(h)}</th>`).join("");
  const sampleBody = data.sample
    .map((r) => `<tr>${r.map((c) => `<td>${esc(c)}</td>`).join("")}</tr>`)
    .join("");

  $("#import-preview").innerHTML = `
    <div class="import-stats">
      <span>原始 <b>${s.raw_rows.toLocaleString()}</b> 行</span>
      <span>去重 <b>${s.duplicates_removed}</b></span>
      <span>可导入 <b>${s.rows.toLocaleString()}</b> 行</span>
      <span><b>${s.columns}</b> 列</span>
    </div>
    <table class="schema-table import-cols">${head}<tbody>${body}</tbody></table>
    <div class="eyebrow" style="margin:16px 0 8px">前 ${data.sample.length} 行预览</div>
    <div class="table-wrap"><table class="result-table"><thead><tr>${sampleHead}</tr></thead><tbody>${sampleBody}</tbody></table></div>`;
}

async function doImportCommit() {
  if (!importState.text) {
    $("#import-status").textContent = "请先点「预览与整理」，确认列类型后再导入。";
    return;
  }

  // 注意：只能在这个列设置表里找行。样例数据表也在 #import-preview 里，
  // 早先直接用 "#import-preview tbody tr" 会连样例行一起选中，取不到 select 就抛错。
  const specRows = $$("#import-preview table.import-cols tbody tr");
  const columns = specRows
    .map((tr) => {
      const select = tr.querySelector("select[data-type]");
      const include = tr.querySelector("input[data-include]");
      if (!select || !include) return null;
      return { name: tr.dataset.name, type: select.value, include: include.checked };
    })
    .filter(Boolean);

  if (!columns.length) {
    $("#import-status").textContent = "没有读到列信息，请重新点一次「预览与整理」。";
    return;
  }
  if (!columns.some((c) => c.include)) {
    $("#import-status").textContent = "至少要勾选一列。";
    return;
  }

  $("#do-commit").disabled = true;
  $("#import-status").textContent = "正在写入数据库…";
  let ok = false;
  try {
    const data = await api("/api/import/commit", {
      method: "POST",
      body: JSON.stringify({
        text: importState.text,
        name: $("#import-name").value || "dataset",
        columns,
        options: readImportOptions(),
      }),
    });
    if (!data.ok) {
      $("#import-status").textContent = `导入失败：${data.error}`;
      return;
    }
    $("#import-status").textContent = `已导入 ${data.rows.toLocaleString()} 行 → 表 ${data.table}`;
    toast(`导入成功：${data.table}（${data.rows} 行）`);
    ok = true;
    importState = { text: "", preview: null };
    await refreshDictTables(data.table);
  } catch (err) {
    $("#import-status").textContent = `导入失败：${err.message}`;
  } finally {
    // 成功时按钮保持禁用（数据已导完，预览也清空了）；失败时允许重试
    $("#do-commit").disabled = ok;
  }
}

async function refreshDictTables(selectName) {
  try {
    state.tables = await api("/api/tables");
  } catch {
    return;
  }
  renderDictList();
  const target =
    selectName && state.tables.some((t) => t.name === selectName) ? selectName : state.tables[0]?.name;
  if (target) selectTable(target);
  $("#import-preview").innerHTML = "";
}

/* ---------------- 03 评测 ---------------- */

let evalLoaded = false;

function statusOf(status) {
  if (status === "correct") return { cls: "correct", label: "正确" };
  if (status === "correct_lenient") return { cls: "lenient", label: "列序不同" };
  if (status === "unstable") return { cls: "lenient", label: "不稳定" };
  return { cls: "wrong", label: "错误" };
}

function ensureEval() {
  if (evalLoaded) return;
  evalLoaded = true;

  const trendable = state.reports.filter((r) => ["eval", "repeat"].includes(r.kind));
  $("#trend").innerHTML = trendChart(trendable);
  $("#trend-hint").textContent = trendable.length
    ? `${trendable.length} 份报告` + (trendRange ? ` · ${trendRange}` : "")
    : "暂无报告";

  const options = trendable;
  const select = $("#report-select");
  select.innerHTML = options
    .map((r) => {
      const label = r.kind === "repeat" ? `${r.name} · ${r.n_runs} 次 · 均值 ${pct(r.ex)}` : `${r.name} · ${pct(r.ex)}`;
      return `<option value="${esc(r.name)}">${esc(label)}</option>`;
    })
    .join("");
  if (!options.length) {
    $("#eval-metrics").innerHTML = `<div class="metric"><div class="metric-k">NO DATA</div><div class="metric-v">—</div></div>`;
    return;
  }
  select.onchange = () => loadReport(select.value);
  loadReport(options[0].name);
  setupCompare();
  setupMultiturn();
}

async function loadReport(name) {
  const data = await api(`/api/report?name=${encodeURIComponent(name)}`);
  const isRepeat = data.n_runs && data.passes;
  const byId = new Map(state.questions.map((q) => [q.id, q]));

  let rows;
  if (isRepeat) {
    rows = Object.entries(data.passes).map(([id, passed]) => ({
      id,
      status: passed === data.n_runs ? "correct" : passed === 0 ? "wrong" : "unstable",
      label: `${passed}/${data.n_runs}`,
    }));
  } else {
    rows = (data.results || []).map((r) => ({ id: r.id, status: r.status, label: null }));
  }

  const correct = rows.filter((r) => r.status === "correct").length;
  const ex = isRepeat ? data.mean_ex : data.execution_accuracy;
  const total = rows.length || 50;

  const diff = { easy: [0, 0], medium: [0, 0], hard: [0, 0] };
  rows.forEach((r) => {
    const q = byId.get(r.id);
    if (!q || !diff[q.difficulty]) return;
    diff[q.difficulty][1] += 1;
    if (r.status === "correct") diff[q.difficulty][0] += 1;
  });

  const metric = (k, v, sub) =>
    `<div class="metric"><div class="metric-k">${k}</div><div class="metric-v">${v}</div>${
      sub ? `<div class="metric-sub">${sub}</div>` : ""
    }</div>`;

  $("#eval-metrics").innerHTML =
    metric("EXECUTION ACCURACY", `${(ex * 100).toFixed(1)}<small>%</small>`, `${correct}/${total} 题`) +
    metric("EASY", `${diff.easy[0]}<small>/${diff.easy[1]}</small>`, "单表 / 简单过滤") +
    metric("MEDIUM", `${diff.medium[0]}<small>/${diff.medium[1]}</small>`, "join / 分组 / 口径") +
    metric("HARD", `${diff.hard[0]}<small>/${diff.hard[1]}</small>`, "窗口 / 累计 / 分摊") +
    (isRepeat ? metric("RUNS", data.n_runs, `σ = ${(data.std_ex * 100).toFixed(1)}pt`) : "");

  $("#report-meta").textContent = isRepeat
    ? `${data.n_runs} 次独立运行，均值 ${pct(data.mean_ex)}，区间 ${pct(data.min_ex)} ~ ${pct(data.max_ex)}`
    : `数据集 ${data.dataset_version || ""} · ${total} 题 · 单次运行`;

  $("#heatmap").innerHTML = rows
    .map((r) => {
      const q = byId.get(r.id) || {};
      const cls = r.status === "correct" ? "correct" : r.status === "correct_lenient" || r.status === "unstable" ? "lenient" : "wrong";
      return `<div class="tile ${cls}" title="${esc(r.id)} · ${esc(q.question || "")} · ${esc(r.label || statusOf(r.status).label)}"></div>`;
    })
    .join("");

  $("#qtable").innerHTML = rows
    .map((r) => {
      const q = byId.get(r.id) || {};
      const st = statusOf(r.status);
      const shown = r.label ? `${st.label} · ${r.label}` : st.label;
      return `<div class="qrow">
        <span class="qid">${esc(r.id)}</span>
        <span class="badge ${st.cls}">${esc(shown)}</span>
        <span class="qtext">${esc(q.question || "")}</span>
        <span class="qsub">${esc(q.difficulty || "")}</span>
      </div>`;
    })
    .join("");

  $("#q-detail-hint").textContent = `${total} 题`;
  const heatTitle = document.getElementById("heatmap-title");
  if (heatTitle) heatTitle.textContent = `${total} 题一览`;

  renderAttribution(data, rows, byId);
}

/* ---------------- 失败归因 ---------------- */

const FAIL_KINDS = {
  syntax: { label: "SQL 执行失败", hint: "语法 / 表名 / 字段名写错，压根没跑通", cls: "wrong" },
  table: { label: "选错表", hint: "用到的表和标答不一样，属于召回或选表问题", cls: "wrong" },
  column: { label: "指标数量不符", hint: "列数对不上，多算或少算了一个指标", cls: "wrong" },
  group: { label: "分组 / 过滤不符", hint: "行数不同，分组维度或 WHERE 条件不一样", cls: "lenient" },
  caliber: { label: "口径 / 聚合算错", hint: "表对了、行数也对了，但数值不对", cls: "lenient" },
};

function tablesOf(sql) {
  const text = sql || "";
  // CTE 名字不是表：`WITH m AS (...) SELECT * FROM m` 里的 m 要排除
  const cteNames = new Set();
  const cteRe = /\b(?:WITH|,)\s+([A-Za-z_][\w]*)\s+AS\s*\(/gi;
  let c;
  while ((c = cteRe.exec(text))) cteNames.add(c[1]);

  const names = new Set();
  const re = /\b(?:FROM|JOIN)\s+["']?([A-Za-z_][\w]*)/gi;
  let m;
  while ((m = re.exec(text))) {
    const name = m[1];
    if (["select", "where", "group", "order", "as"].includes(name.toLowerCase())) continue;
    if (cteNames.has(name)) continue;
    names.add(name);
  }
  return [...names].sort();
}

function classifyFailure(result, question) {
  if (!result || result.status === "correct" || result.status === "correct_lenient") return null;

  const predTables = tablesOf(result.generated_sql);
  const goldTables = question?.gold_tables || [];

  if (result.status === "sql_error" || result.error) {
    return { kind: "syntax", detail: String(result.error || "执行失败").slice(0, 90) };
  }
  if (goldTables.length && predTables.length) {
    const same = predTables.length === goldTables.length && predTables.every((t) => goldTables.includes(t));
    if (!same) {
      return { kind: "table", detail: `用了 ${predTables.join(" + ")}，标答是 ${goldTables.join(" + ")}` };
    }
  }
  if (result.status === "column_mismatch") {
    return { kind: "column", detail: "结果列数与标答不一致" };
  }
  if (
    result.gold_row_count !== undefined &&
    result.pred_row_count !== undefined &&
    result.gold_row_count !== result.pred_row_count
  ) {
    return { kind: "group", detail: `${result.pred_row_count} 行，标答 ${result.gold_row_count} 行` };
  }
  return { kind: "caliber", detail: "行数一致，数值对不上" };
}

function renderAttribution(report, rows, byId) {
  const panel = $("#attrib-panel");
  if (!panel) return;

  if (!report?.results) {
    // 重复实验报告没有单次 SQL，归因不适用
    panel.style.display = "none";
    return;
  }

  const buckets = new Map();
  const items = [];
  for (const result of report.results) {
    const question = byId.get(result.id);
    const fail = classifyFailure(result, question);
    if (!fail) continue;
    buckets.set(fail.kind, (buckets.get(fail.kind) || 0) + 1);
    items.push({ id: result.id, question: question?.question || "", ...fail });
  }

  if (!items.length) {
    panel.style.display = "";
    $("#attrib-hint").textContent = "本次没有错题";
    $("#attrib-bars").innerHTML = "";
    $("#attrib-list").innerHTML = `<p class="dim small">全部答对，没有可归因的失败。</p>`;
    return;
  }

  panel.style.display = "";
  const total = report.results.length;
  $("#attrib-hint").textContent = `${items.length} 道错题，${buckets.size} 类原因`;

  const max = Math.max(...buckets.values());
  $("#attrib-bars").innerHTML = [...buckets.entries()]
    .sort((a, b) => b[1] - a[1])
    .map(([kind, count]) => {
      const meta = FAIL_KINDS[kind] || { label: kind, hint: "" };
      return `<div class="attrib-row">
        <span class="attrib-k">${esc(meta.label)}</span>
        <span class="attrib-bar"><i style="width:${Math.max(6, (count / max) * 100)}%"></i></span>
        <span class="attrib-n">${count}</span>
        <span class="attrib-hint">${esc(meta.hint)}</span>
      </div>`;
    })
    .join("");

  $("#attrib-list").innerHTML = items
    .map(
      (it) => `<div class="attrib-item">
        <span class="qid">${esc(it.id)}</span>
        <span class="badge ${(FAIL_KINDS[it.kind] || {}).cls || "wrong"}">${esc((FAIL_KINDS[it.kind] || {}).label || it.kind)}</span>
        <span class="qtext">${esc(it.question)}</span>
        <span class="attrib-detail">${esc(it.detail)}</span>
      </div>`
    )
    .join("");
}

/* ---------------- 两次评测对比 ---------------- */

async function runCompare() {
  const nameA = $("#cmp-a").value;
  const nameB = $("#cmp-b").value;
  if (!nameA || !nameB) return;
  if (nameA === nameB) {
    $("#cmp-result").innerHTML = `<p class="dim small">选两份不同的报告。</p>`;
    return;
  }

  const box = $("#cmp-result");
  box.innerHTML = `<p class="dim small">对比中…</p>`;

  let a;
  let b;
  try {
    [a, b] = await Promise.all([
      api(`/api/report?name=${encodeURIComponent(nameA)}`),
      api(`/api/report?name=${encodeURIComponent(nameB)}`),
    ]);
  } catch (err) {
    box.innerHTML = `<p class="dim small">读取失败：${esc(err.message)}</p>`;
    return;
  }

  // 重复实验报告用 passes 判断，单次报告用 status
  const verdictOf = (report) => {
    if (report.passes && report.n_runs) {
      const out = new Map();
      for (const [id, passed] of Object.entries(report.passes)) {
        out.set(id, passed === report.n_runs ? "correct" : "wrong");
      }
      return out;
    }
    const out = new Map();
    for (const r of report.results || []) {
      out.set(r.id, r.status === "correct" || r.status === "correct_lenient" ? "correct" : "wrong");
    }
    return out;
  };

  const va = verdictOf(a);
  const vb = verdictOf(b);
  const byId = new Map(state.questions.map((q) => [q.id, q]));

  const fixed = [];
  const broke = [];
  const stillWrong = [];
  let bothRight = 0;

  for (const [id, sa] of va) {
    if (!vb.has(id)) continue;
    const sb = vb.get(id);
    if (sa === "correct" && sb === "correct") bothRight += 1;
    else if (sa === "wrong" && sb === "correct") fixed.push(id);
    else if (sa === "correct" && sb === "wrong") broke.push(id);
    else stillWrong.push(id);
  }

  // 两份报告的题集可能不同（比如一份 50 题、一份只跑了留出的 25 题），
  // 直接比各自的 EX 是错的；只在共同题上算分，才是可比的。
  const common = [...va.keys()].filter((id) => vb.has(id));
  const commonA = common.filter((id) => va.get(id) === "correct").length;
  const commonB = common.filter((id) => vb.get(id) === "correct").length;
  const exA = common.length ? commonA / common.length : 0;
  const exB = common.length ? commonB / common.length : 0;
  const delta = (exB - exA) * 100;
  const rawA = a.mean_ex ?? a.execution_accuracy;
  const rawB = b.mean_ex ?? b.execution_accuracy;
  const mismatch = va.size !== vb.size;

  const line = (ids, label, cls) =>
    ids.length
      ? `<div class="cmp-line ${cls}"><span class="cmp-tag">${label}</span>${ids
          .map(
            (id) =>
              `<span class="cmp-id" title="${esc(byId.get(id)?.question || "")}">${esc(id)}</span>`
          )
          .join("")}</div>`
      : "";

  box.innerHTML = `
    <div class="cmp-summary">
      <div class="cmp-score">
        <span class="cmp-k">A</span>
        <b>${pct(exA)}</b>
        <span class="cmp-name">${esc(nameA.replace(/\.json$/, ""))}</span>
      </div>
      <div class="cmp-arrow">→</div>
      <div class="cmp-score">
        <span class="cmp-k">B</span>
        <b>${pct(exB)}</b>
        <span class="cmp-name">${esc(nameB.replace(/\.json$/, ""))}</span>
      </div>
      <div class="cmp-delta ${delta > 0 ? "up" : delta < 0 ? "down" : ""}">
        ${delta > 0 ? "+" : ""}${delta.toFixed(1)}<small>pt</small>
      </div>
    </div>
    <div class="cmp-note">
      B 修好 <b>${fixed.length}</b> 题 · B 弄坏 <b>${broke.length}</b> 题 ·
      都错 <b>${stillWrong.length}</b> 题 · 都对 <b>${bothRight}</b> 题
    </div>
    ${
      mismatch
        ? `<div class="cmp-warn">
             两份报告题集不同（A ${va.size} 题 / B ${vb.size} 题），
             以上分数只在<b>共同 ${common.length} 题</b>上计算。
             全量 EX 分别是 ${pct(rawA)} 和 ${pct(rawB)}，不能直接相减。
           </div>`
        : ""
    }
    ${line(fixed, "B 修好", "good")}
    ${line(broke, "B 弄坏", "bad")}
    ${line(stillWrong, "仍错", "warn")}
  `;
}

function setupCompare() {
  const options = state.reports.filter((r) => ["eval", "repeat"].includes(r.kind));
  if (options.length < 2) {
    $("#cmp-result").innerHTML = `<p class="dim small">至少需要两份评测报告。</p>`;
    return;
  }
  const opts = options
    .map((r) => `<option value="${esc(r.name)}">${esc(r.name)} · ${pct(r.ex)}</option>`)
    .join("");
  $("#cmp-a").innerHTML = opts;
  $("#cmp-b").innerHTML = opts;
  $("#cmp-a").selectedIndex = Math.min(1, options.length - 1);
  $("#cmp-b").selectedIndex = 0;
  $("#cmp-run").onclick = runCompare;
  runCompare();
}

/* ---------------- 多轮对话（P9） ---------------- */

const MT_REASONS = {
  column_mismatch: "列数不符：改写后丢了维度或指标",
  empty_result: "结果为空",
  value_mismatch: "值不符：格式或并列名次差异",
  sql_error: "SQL 执行失败",
  no_sql: "没产出 SQL：改写可能失败",
};

function mtReason(pair) {
  if (pair.rewritten === null || pair.rewritten === undefined || pair.rewritten === "") {
    return "改写产出为空，回退原句后必然失败";
  }
  return MT_REASONS[pair.status] || pair.status_label || pair.status;
}

async function loadMultiturn(name) {
  const body = $("#mt-body");
  body.innerHTML = `<p class="dim small">读取中…</p>`;
  let data;
  try {
    data = await api(`/api/report?name=${encodeURIComponent(name)}`);
  } catch (err) {
    body.innerHTML = `<p class="dim small">读取失败：${esc(err.message)}</p>`;
    return;
  }

  const control = data.control || { ex: 0, correct: 0, total: 0 };
  const runs = data.multiturn || [];
  const mean = data.mean_ex || 0;
  const bar = (rate, cls) =>
    `<span class="mt-bar"><i class="${cls}" style="width:${Math.max(2, rate * 100)}%"></i></span>`;

  const kindRows = (data.by_kind || [])
    .map(
      (k) =>
        `<div class="mt-kind"><span>${esc(k.kind)}</span>${bar(k.rate, "")}<b>${(k.rate * 100).toFixed(0)}%</b>
         <span class="dim small">${k.n} 条</span></div>`
    )
    .join("");

  const failures = (data.pairs || []).filter((p) => p.status !== "correct");
  const failRows = failures
    .map(
      (p) => `<div class="mt-fail">
        <span class="qid">${esc(p.id)}</span>
        <span class="badge wrong">${esc(p.status_label || p.status)}</span>
        <span class="qtext">${esc(p.followup)}</span>
        <span class="mt-rewrite">${p.rewritten ? `改写：${esc(p.rewritten)}` : "改写：<em>空</em>"}</span>
        <span class="mt-reason">${esc(mtReason(p))}</span>
      </div>`
    )
    .join("");

  body.innerHTML = `
    <div class="mt-compare">
      <div class="mt-row">
        <span class="mt-label">单轮（不带历史）</span>
        ${bar(control.ex, "bad")}
        <b class="mt-val">${(control.ex * 100).toFixed(1)}%</b>
        <span class="dim small">${control.correct}/${control.total}</span>
      </div>
      <div class="mt-row">
        <span class="mt-label">多轮（带历史）</span>
        ${bar(mean, "good")}
        <b class="mt-val">${(mean * 100).toFixed(1)}%</b>
        <span class="dim small">${data.repeats || runs.length} 次均值</span>
      </div>
      <div class="mt-runs dim small">
        每次：${runs.map((r) => `${(r.ex * 100).toFixed(0)}%`).join(" / ")}
        · 改写产出率 ${runs.length ? (runs[0].rewrite_rate * 100).toFixed(0) : 0}%
      </div>
    </div>
    <div class="eyebrow" style="margin:18px 0 10px">按追问类型</div>
    <div class="mt-kinds">${kindRows}</div>
    <div class="eyebrow" style="margin:18px 0 10px">失败的追问（${failures.length} 条）</div>
    <div class="mt-fails">${failRows || '<p class="dim small">全部答对。</p>'}</div>
  `;
}

function setupMultiturn() {
  const options = state.reports.filter((r) => r.kind === "multiturn");
  if (!options.length) {
    $("#mt-hint").textContent = "还没有多轮评测报告";
    $("#mt-body").innerHTML = `<p class="dim small">跑一次 <code>python -m askdata.cli multiturn --repeat 3</code> 就会出现。</p>`;
    return;
  }
  const select = $("#mt-select");
  select.innerHTML = options
    .map(
      (r) =>
        `<option value="${esc(r.name)}">${esc(r.name)} · 多轮 ${pct(r.ex)} · 单轮 ${pct(r.control_ex ?? 0)}</option>`
    )
    .join("");
  select.onchange = () => loadMultiturn(select.value);
  $("#mt-hint").textContent = `${options.length} 份多轮报告`;
  loadMultiturn(options[0].name);
}

/* ---------------- 04 安全闸 ---------------- */

let guardLoaded = false;

function ensureGuard() {
  if (guardLoaded) return;
  guardLoaded = true;

  const presets = [
    { name: "— 选一条用例 —", sql: "" },
    { name: "正常查询", sql: "SELECT c.channel_name, SUM(o.pay_amount) FROM orders o JOIN channels c ON c.channel_id = o.channel_id GROUP BY 1" },
    ...state.attacks.map((a) => ({ name: `[${a.expect === "blocked" ? "拦" : "放"}] ${a.name}`, sql: a.sql })),
  ];
  $("#preset").innerHTML = presets.map((p, i) => `<option value="${i}">${esc(p.name)}</option>`).join("");
  $("#preset").onchange = (e) => {
    const p = presets[Number(e.target.value)];
    if (p) $("#sql").value = p.sql;
  };

  $("#attack-grid").innerHTML = state.attacks
    .map(
      (a, i) =>
        `<div class="acell ${a.expect === "blocked" ? "blocked" : "allowed"}" data-i="${i}" title="${esc(a.name)} · ${esc(
          a.category
        )}"></div>`
    )
    .join("");

  $("#attack-grid").onclick = (e) => {
    const cell = e.target.closest(".acell");
    if (!cell) return;
    $("#sql").value = state.attacks[Number(cell.dataset.i)].sql;
    runCheck();
  };

  $("#check").onclick = runCheck;
  $("#policy").onchange = runCheck;
  $("#sql").addEventListener("keydown", (e) => {
    if ((e.metaKey || e.ctrlKey) && e.key === "Enter") runCheck();
  });
  $("#attack-hint").textContent = `${state.attacks.filter((a) => a.expect === "blocked").length} 注入 + ${
    state.attacks.filter((a) => a.expect === "allowed").length
  } 正常`;

  $("#sql").value = "SELECT * FROM read_csv('C:/secret.csv')";
  runCheck();
}

async function runCheck() {
  const sql = $("#sql").value.trim();
  if (!sql) return;
  $("#check").disabled = true;
  try {
    const data = await api("/api/check", {
      method: "POST",
      body: JSON.stringify({ sql, policy: $("#policy").checked }),
    });
    const allow = data.verdict === "allowed";
    const exec = data.executed;
    const box = $("#verdict");
    box.className = `verdict ${allow ? "allow" : "block"}`;
    box.innerHTML = `
      <div class="vhead"><span class="vdot"></span>${allow ? "放行" : "被拦截"}</div>
      ${allow ? "" : `<div class="vreason">${esc(data.reason)}</div>`}
      ${allow && data.policy && data.rewritten ? `<div class="vreason">行级权限改写为：<br>${esc(data.rewritten)}</div>` : ""}
      ${allow && !data.policy && data.rewritten && data.rewritten !== sql ? `<div class="vreason">改写为：<br>${esc(data.rewritten)}</div>` : ""}
      ${exec ? (exec.ok ? `<div class="vreason">执行成功 · ${exec.row_count} 行 · ${ms(exec.ms)}</div>` : `<div class="vreason">执行失败：${esc(exec.error)}</div>`) : ""}
      <div class="vreason">AST 校验 ${ms(data.guard_ms)}${data.policy ? " · 行级权限 开" : ""}</div>`;
  } catch (err) {
    $("#verdict").className = "verdict block";
    $("#verdict").innerHTML = `<div class="vhead"><span class="vdot"></span>请求失败</div><div class="vreason">${esc(err.message)}</div>`;
  } finally {
    $("#check").disabled = false;
  }
}

/* ---------------- 05 澄清 ---------------- */

let clarifyLoaded = false;

function ensureClarify() {
  if (clarifyLoaded) return;
  clarifyLoaded = true;

  const rows = state.clarify?.vague || [];
  const askRate = rows.length ? rows.filter((r) => r.asked).length / rows.length : 0;
  const goodRate = rows.length ? rows.filter((r) => r.status === "good").length / rows.length : 0;
  const overRate = state.clarify?.control?.length
    ? state.clarify.control.filter((c) => c.asked).length / state.clarify.control.length
    : 0;

  const metric = (k, v, sub) =>
    `<div class="metric"><div class="metric-k">${k}</div><div class="metric-v">${v}</div>${
      sub ? `<div class="metric-sub">${sub}</div>` : ""
    }</div>`;

  $("#clarify-metrics").innerHTML =
    metric("追问率", `${(askRate * 100).toFixed(0)}<small>%</small>`, `${rows.filter((r) => r.asked).length}/${rows.length} 道含糊题被反问`) +
    metric("问到点上", `${(goodRate * 100).toFixed(0)}<small>%</small>`, "覆盖了全部缺失点") +
    metric("误反问率", `${(overRate * 100).toFixed(0)}<small>%</small>`, `${state.clarify?.control?.length || 0} 道完整题里被反问`) +
    (state.closed ? metric("闭环正确率", `${(state.closed.closed_ex * 100).toFixed(0)}<small>%</small>`, `不问直接答：${(state.closed.baseline_ex * 100).toFixed(0)}%`) : "");

  $("#vague-list").innerHTML = rows
    .map(
      (r, i) => `<div class="vrow" data-i="${i}">
        <span class="vid">${esc(r.id)}</span>
        <span class="vq">${esc(r.question)}</span>
        <span class="vcov">${r.coverage === 1 ? "✓" : Math.round(r.coverage * 100) + "%"}</span>
      </div>`
    )
    .join("");

  $("#vague-list").onclick = (e) => {
    const row = e.target.closest(".vrow");
    if (!row) return;
    $$(".vrow").forEach((r) => r.classList.remove("is-active"));
    row.classList.add("is-active");
    const item = rows[Number(row.dataset.i)];
    $("#clarify-detail").innerHTML = `
      <blockquote>${esc(item.clarification || "（没有反问）")}</blockquote>
      <div class="why">用户问的是「${esc(item.question)}」——缺的是时间范围、指标或口径。
      它没有硬猜，而是把缺的东西列出来问回去。</div>`;
  };
}

/* ---------------- 06 说明 ---------------- */

function renderAbout() {
  const o = state.overview || {};
  const timeline = [
    ["P0", "造数据 + 30 题 + 判卷器 + 关键词 baseline", "30%"],
    ["P1", "接上大模型，全量 schema 零样本", "83%"],
    ["P1.1", "发现 6/10 的失败是题目写错 → 修题面", "90%"],
    ["P3", "执行反馈自纠：喂报错让它改", "修语法错"],
    ["P1.5", "扩到 50 题，想制造难度", "没制造出来"],
    ["P2'", "7 张表 → 67 张表 + 表召回", "省 70% token"],
    ["P6", "重复运行 + 配对 bootstrap", "±2pt 噪声"],
    ["P4", "四道安全闸", "拦 50/50"],
    ["P5", "该不该反问", "追问 100% / 误问 0%"],
    ["P5.5", "反问之后能不能答对", "0% → 87%"],
    ["P7", "数据导入 + 自动整理", "CSV/JSON 即插即用"],
    ["P8", "动态 few-shot（检索相似题）", "+4pt，不显著"],
    ["P9", "多轮对话：先改写再生成", "0% → 76.7%"],
    ["P10", "结果校验 Agent：数字不对就报警", "扇出/越界/空结果"],
  ];

  $("#about").innerHTML = `
    <div class="panel about-card">
      <div class="eyebrow">这个项目在干什么</div>
      <p class="cta-line">让 AI 报出来的数字<b>可以被信任</b>——不清楚就问，问清楚再算，算完能核对，
      而且整套判断标准经过校准、<b>有误差范围</b>。</p>
    </div>

    <div class="panel">
      <div class="panel-head"><span class="eyebrow">它怎么走一条链路</span></div>
      <div class="arch">
        <div class="arch-row">
          <div class="arch-box">你的问题</div><span class="arch-arrow">→</span>
          <div class="arch-box acc">表召回（${o.tables ?? "—"} 选 ${state.topK}）</div><span class="arch-arrow">→</span>
          <div class="arch-box acc">判定：够不够清楚</div>
        </div>
        <div class="arch-row">
          <div class="arch-box">够 → 写 SQL</div><span class="arch-arrow">→</span>
          <div class="arch-box acc">安全闸</div><span class="arch-arrow">→</span>
          <div class="arch-box">只读沙箱执行</div><span class="arch-arrow">→</span>
          <div class="arch-box acc">结果校验</div>
        </div>
        <div class="arch-row">
          <div class="arch-box">不够 → 反问你一句</div><span class="arch-arrow">→</span>
          <div class="arch-box">你补充</div><span class="arch-arrow">→</span>
          <div class="arch-box">再写 SQL</div>
        </div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><span class="eyebrow">${timeline.length} 个阶段</span><span class="panel-hint">每一格都有报告留档</span></div>
      <div class="timeline">
        ${timeline.map(([k, v, m]) => `<div class="tl-item"><span class="tl-k">${k}</span><span class="tl-v">${v}</span><span class="tl-m">${m}</span></div>`).join("")}
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><span class="eyebrow">规模</span></div>
      <div class="kv">
        <div class="kv-row"><span>业务表 / 总表数</span><span>7 / ${o.tables ?? "—"}</span></div>
        <div class="kv-row"><span>表字段总数</span><span>${o.columns ?? "—"}</span></div>
        <div class="kv-row"><span>schema 字符数</span><span>${(o.schema_chars ?? 0).toLocaleString()}</span></div>
        <div class="kv-row"><span>评测题</span><span>${o.questions ?? "—"}</span></div>
        <div class="kv-row"><span>注入用例</span><span>${o.attacks?.blocked ?? "—"} 拦截 + ${o.attacks?.allowed ?? "—"} 放行</span></div>
        <div class="kv-row"><span>单测</span><span>96</span></div>
      </div>
    </div>

    <div class="panel">
      <div class="panel-head"><span class="eyebrow">如果只记三句话</span></div>
      <ol class="steps">
        <li><b>没有可信评测的 AI 项目，分数没有意义。</b>这个项目 90 分里有 24 分是修尺子修出来的。</li>
        <li><b>测量精度会成为瓶颈。</b>50 道题分辨不出 3 个点的差异，所以"我觉得它更好"不算结论。</li>
        <li><b>知道"测不出来"也是结论。</b>比拍脑袋说"提升了 5%"强得多。</li>
      </ol>
    </div>`;
}

/* ---------------- 启动 ---------------- */

async function boot() {
  initTheme();
  $$(".nav-item").forEach((b) => (b.onclick = () => setPage(b.dataset.page)));

  try {
    const [overview, questions, attacks, reports] = await Promise.all([
      api("/api/overview"),
      api("/api/questions"),
      api("/api/attacks"),
      api("/api/reports"),
    ]);
    Object.assign(state, { overview, questions, attacks, reports });
    // overview.model 为「（未配置 API Key）」说明服务端也没有可用密钥
    state.serverKey = !String(overview.model || "").includes("未配置");
    refreshKeyState();

    // 数据范围单独取，失败也不该拖垮整页
    try {
      state.hints = await api("/api/hints");
    } catch {
      state.hints = { time: {}, dimensions: {}, metrics: [], rules: [] };
    }

    $("#foot-tables").textContent = overview.tables;
    $("#foot-questions").textContent = overview.questions;
    $("#foot-model").textContent = overview.model;
    // 表数会变（导入一张表就 +1），所以这些地方不能写死
    const totalTables = document.getElementById("retrieval-total");
    if (totalTables) totalTables.textContent = overview.tables;
    const explainTotal = document.getElementById("explain-total");
    if (explainTotal) explainTotal.textContent = overview.tables;
    $("#foot-meta").textContent = `${overview.model} · 数据集 ${overview.dataset_version || "v2.2"} · ${
      overview.schema_chars?.toLocaleString() || "—"
    } 字符 schema`;

    $("#topbar-chips").innerHTML = `
      <span class="chip"><b>${overview.tables}</b> 张表</span>
      <span class="chip"><b>${overview.questions}</b> 道题</span>
      <span class="chip accent">安全闸 开</span>
      <span class="chip">${esc(overview.model)}</span>`;

    const clarifyReport = reports.find((r) => r.kind === "clarify");
    const closedReport = reports.find((r) => r.kind === "closed");
    if (clarifyReport) state.clarify = await api(`/api/report?name=${encodeURIComponent(clarifyReport.name)}`);
    if (closedReport) state.closed = await api(`/api/report?name=${encodeURIComponent(closedReport.name)}`);
  } catch (err) {
    $("#topbar-chips").innerHTML = `<span class="chip">初始化失败：${esc(err.message)}</span>`;
  }

  bindAuth();
  bindKeyBar();
  bindAsk();
  ensureQuick();
  const initial = (location.hash || "#ask").slice(1);
  setPage(PAGES[initial] ? initial : "ask");
}

boot();
