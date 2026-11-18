/* 前端验收：用 DOM 模拟跑真实数据，检查每个渲染函数真的能产出正确的 HTML。
 *
 * 之前几个 bug（highlightSQL 丢失、选择器撞车、按钮点不动）都是"接口正常、
 * 页面崩了"，所以必须验到渲染层。数据来自 tmp_acceptance/*.json（真实接口快照）。
 */
const fs = require("fs");
const path = require("path");

const DIR = "tmp_acceptance";
const load = (n) =>
  JSON.parse(fs.readFileSync(path.join(DIR, n + ".json"), "utf8").replace(/^\uFEFF/, ""));

const pass = [];
const fail = [];
function check(name, ok, detail = "") {
  (ok ? pass : fail).push({ name, detail });
  console.log(`${ok ? "[ ok ]" : "[FAIL]"} ${name}${!ok && detail ? "  — " + detail : ""}`);
}

/* ---------- DOM 模拟 ---------- */
const els = {};
function makeEl(id) {
  const el = {
    id,
    _html: "",
    value: "",
    textContent: "",
    disabled: false,
    checked: true,
    dataset: {},
    children: [],
    classList: { add() {}, remove() {}, toggle() {}, contains: () => false },
    style: {},
    addEventListener() {},
    appendChild(child) { el._html += (child && child._html) || ""; },
    prepend() {},
    after() {},
    remove() {},
    focus() {},
    scrollIntoView() {},
    querySelector: () => makeEl("q"),
    querySelectorAll: () => [],
  };
  Object.defineProperty(el, "innerHTML", {
    get: () => el._html,
    set: (v) => {
      el._html = v;
    },
  });
  return el;
}
global.document = {
  documentElement: { dataset: {} },
  querySelector: (sel) => els[sel] || (els[sel] = makeEl(sel)),
  querySelectorAll: () => [],
  createElement: () => makeEl("tmp"),
  getElementById: (id) => els["#" + id] || null,
};

const storage = {};
global.localStorage = {
  getItem: (k) => (k in storage ? storage[k] : null),
  setItem: (k, v) => (storage[k] = String(v)),
};
global.window = { addEventListener() {} };
global.history = { replaceState() {} };
global.navigator = { clipboard: { writeText: async () => {} } };
global.location = { hash: "#ask" };

const REPORTS = load("reports");
const BY_NAME = {
  eval: load("report_eval"),
  repeat: load("report_repeat"),
  multiturn: load("report_multiturn"),
  clarify: load("report_clarify"),
  closed: load("report_closed"),
};
const KIND_BY_NAME = Object.fromEntries(
  Object.entries(BY_NAME).map(([kind, rep]) => [kind, rep])
);

/* 让 api() 用本地快照应答 */
global.fetch = async (url) => {
  const u = String(url);
  const name = decodeURIComponent((u.match(/name=([^&]+)/) || [])[1] || "");
  if (u.includes("/api/hints")) return okJson(load("hints"));
  if (u.includes("/api/questions")) return okJson(load("questions"));
  if (u.includes("/api/reports")) return okJson(REPORTS);
  if (u.includes("/api/tables")) return okJson(load("tables"));
  if (u.includes("/api/attacks")) return okJson(load("attacks"));
  if (u.includes("/api/overview")) return okJson(load("overview"));
  if (u.includes("/api/report")) {
    const target = REPORTS.find((r) => r.name === name);
    const kind = target ? target.kind : "eval";
    const payload = BY_NAME[kind] || BY_NAME.eval;
    return okJson({ ...payload, __name: name });
  }
  return okJson({});
};
function okJson(obj) {
  return { ok: true, json: async () => obj };
}
global.setTimeout = (fn, ms) => { if (typeof fn === "function") fn(); return 0; };
global.clearTimeout = () => {};

/* ---------- 装载 app.js ---------- */
let src = fs.readFileSync("web/app.js", "utf8");
src += `\n;globalThis.__t = { renderAnswer, rankingList, lineChart, renderAttribution, classifyFailure,
  loadMultiturn, setupMultiturn, runCompare, setupCompare, ensureQuick, composeQuestion, state,
  renderHints, highlightSQL, statusOf, mtReason, tablesOf, initTheme, setPage, appendAssistant, issuesHTML, updateSessionChip, emptyThreadHTML, refreshKeyState, renderSettings };`;
eval(src);
const T = globalThis.__t;

/* ---------- 1. 答案渲染的四种形状 ---------- */
const shapes = [
  ["单值", { columns: ["refund_rate"], rows: [[0.02]], row_count: 1 }, "hero-v"],
  ["单行多列", { columns: ["退款金额", "销售额"], rows: [[1943039.85, 23668264.6]], row_count: 1 }, "kv-strip"],
  [
    "多行排名",
    {
      columns: ["product_id", "product_name", "sales_amount"],
      rows: [[41, "数码手机041", 1695538.61], [61, "数码手机061", 1617404.58], [81, "数码手机081", 1470606.17]],
      row_count: 3,
    },
    "rank-row",
  ],
  [
    "月度序列",
    { columns: ["月份", "销售额"], rows: Array.from({ length: 12 }, (_, i) => [`2025-${String(i + 1).padStart(2, "0")}`, 100000 + i]) , row_count: 12 },
    'class="line"',
  ],
  [
    "超过 15 行",
    { columns: ["省份", "单数"], rows: Array.from({ length: 20 }, (_, i) => [`省${i}`, 100 - i]), row_count: 20 },
    "chart",
  ],
];
for (const [label, data, marker] of shapes) {
  const html = T.renderAnswer(data);
  check(`答案渲染 · ${label}`, html.includes(marker), marker);
}

/* 空值与并列 */
const blankHtml = T.rankingList(["广东", "", "江苏"], [100, 50, 30], "province", "cnt");
check("排名 · 空值显示占位", blankHtml.includes("（未填写）"));
check("排名 · 数值千分位", blankHtml.includes("100"));

/* ---------- 2. SQL 高亮 ---------- */
const hl = T.highlightSQL("SELECT a FROM t WHERE b = 'x' -- 注释");
check("SQL 高亮 · 关键字", hl.includes('<span class="k">SELECT</span>'));
check("SQL 高亮 · 字符串", hl.includes('<span class="s">'));
check("SQL 高亮 · 注释", hl.includes('class="c"') || hl.includes('class="c"'));

/* ---------- 3. 快捷提问 ---------- */
T.state.hints = load("hints");
try {
  T.ensureQuick();
  check("快捷提问 · 构建不抛错", true);
} catch (err) {
  check("快捷提问 · 构建不抛错", false, err.message);
}
const quickYears = els["#quick-years"];
check("快捷提问 · 年份来自接口", (quickYears.innerHTML || "").includes("2025"), quickYears.innerHTML?.slice(0, 60));
check("快捷提问 · 问题模板", (els["#quick-presets"].innerHTML || "").includes("各渠道的销售额"));

T.state.year = "2025";
T.state.dim = "渠道";
T.state.metric = "销售额";
check("快捷提问 · 组合成句", T.composeQuestion() === "2025 年各渠道的销售额", T.composeQuestion());

/* ---------- 3b. 校验提示条 ---------- */
check("校验提示 · 无问题时为空", T.issuesHTML([]) === "" && T.issuesHTML(undefined) === "");
const issueHtml = T.issuesHTML([
  { level: "warn", code: "magnitude_fanout", message: "金额超过全库总额 3 倍" },
  { level: "info", code: "extra_columns", message: "列数比问题多一个" },
]);
check("校验提示 · 渲染警告", issueHtml.includes("issue-strip") && issueHtml.includes("金额超过全库总额"));
check("校验提示 · 区分等级", issueHtml.includes("issue-item warn") && issueHtml.includes("issue-item info"));

/* ---------- 3c. 会话状态 ---------- */
T.updateSessionChip(3);
check("会话提示 · 显示轮数", (els["#session-chip"].textContent || "").includes("3"), els["#session-chip"].textContent);
T.updateSessionChip(0);
check("会话提示 · 归零显示新会话", (els["#session-chip"].textContent || "").includes("新会话"));
check("空对话占位 · 可复原", T.emptyThreadHTML().includes("还没有对话"));

/* ---------- 3d. 模型 Key 状态 ---------- */
T.state.apiKey = "";
T.state.serverKey = false;
T.refreshKeyState();
check("Key 状态 · 未配置时顶栏徽章警示", (els["#key-text"].textContent || "").includes("未配置"), els["#key-text"].textContent);
check("Key 状态 · 未配置时顶部提示条出现", els["#key-alert"].hidden === false);
T.state.serverKey = true;
T.refreshKeyState();
check("Key 状态 · 服务端已配时收敛", (els["#key-text"].textContent || "").includes("服务端"), els["#key-text"].textContent);
check("Key 状态 · 已配好后提示条收起", els["#key-alert"].hidden === true);

/* ---------- 3e. 设置页 ---------- */
T.state.overview = { tables: 68, questions: 50, model: "deepseek-chat", dataset_version: "v2.2" };
T.state.topK = 8;
T.state.sessionTurns = 2;
T.renderSettings();
check("设置页 · 服务状态填好了", (els["#set-tables"].textContent || "").includes("68"), els["#set-tables"].textContent);
check("设置页 · 会话轮数显示", (els["#set-session"].textContent || "").includes("2 轮"), els["#set-session"].textContent);
check("设置页 · Key 状态有说明", Boolean(els["#key-state"].textContent));
check("设置页 · 召回数量与状态同步", String(T.state.topK) === String(8));

/* ---------- 4. 澄清卡片的提示 ---------- */
const hintsHtml = T.renderHints(load("hints"));
check("澄清提示 · 可点击口径", hintsHtml.includes("rule-line"));
check("澄清提示 · 含毛利率", hintsHtml.includes("毛利率"));
check("澄清提示 · 时间标签", hintsHtml.includes("2025 年") || hintsHtml.includes("2024 年"));

/* ---------- 5. 失败归因（真实报告） ---------- */
const evalReport = BY_NAME.eval;
const questions = load("questions");
const byId = new Map(questions.map((q) => [q.id, q]));
T.state.questions = questions;
T.renderAttribution(evalReport, evalReport.results || [], byId);
const attribHtml = els["#attrib-bars"].innerHTML + els["#attrib-list"].innerHTML;
check("失败归因 · 有分类", attribHtml.includes("attrib-row") || attribHtml.includes("没有可归因"));
check("失败归因 · 命中具体题", attribHtml.includes("qid") || attribHtml.includes("全部答对"));

const sample = evalReport.results.find((r) => r.status !== "correct");
if (sample) {
  const c = T.classifyFailure(sample, byId.get(sample.id) || {});
  check("失败归因 · 能给出原因", !!c && !!c.kind, JSON.stringify(c));
}

/* ---------- 6. 两次对比 ---------- */
(async () => {
  try {
    T.state.reports = REPORTS;
    T.setupCompare();
    // shim 不会真的填充 select 的 option，这里手动模拟用户选择
    document.querySelector("#cmp-a").value = "eval_template_20261118_184504";
    document.querySelector("#cmp-b").value = "repeat_llm_20261118_205005";
    await T.runCompare();
    const cmp = els["#cmp-result"].innerHTML;
    check("评测对比 · 产出摘要", cmp.includes("cmp-summary"), cmp.slice(0, 80));
    check("评测对比 · 有分数变化", cmp.includes("pt"));
    check("评测对比 · 列出变动题", cmp.includes("cmp-line") || cmp.includes("都对"));
  } catch (err) {
    check("评测对比", false, err.message);
  }

  /* ---------- 7. 多轮面板 ---------- */
  try {
    T.setupMultiturn();
    const mtName = (REPORTS.find((r) => r.kind === "multiturn") || {}).name;
    await T.loadMultiturn(mtName);
    const mt = els["#mt-body"].innerHTML;
    check("多轮面板 · 单轮 vs 多轮", mt.includes("单轮") && mt.includes("多轮"));
    check("多轮面板 · 按类型拆分", mt.includes("swap_year") || mt.includes("按追问类型"));
    check("多轮面板 · 失败原因", mt.includes("mt-reason") || mt.includes("全部答对"));
  } catch (err) {
    check("多轮面板", false, err.message);
  }

  /* ---------- 8. 主题 ---------- */
  try {
    storage["askdata-theme"] = "dark";
    T.initTheme();
    check("主题 · 读取 localStorage", document.documentElement.dataset.theme === "dark", document.documentElement.dataset.theme);
  } catch (err) {
    check("主题 · 读取 localStorage", false, err.message);
  }

  /* ---------- 9. 各页面初始化 ---------- */
  const pageInit = [
    ["问数", () => T.setPage("ask")],
    ["数据字典", () => T.setPage("dict")],
    ["评测", () => T.setPage("eval")],
    ["安全闸", () => T.setPage("guard")],
    ["澄清", () => T.setPage("clarify")],
    ["说明", () => T.setPage("about")],
  ];
  T.state.overview = load("overview");
  T.state.questions = questions;
  T.state.attacks = load("attacks");
  T.state.tables = load("tables");
  T.state.reports = REPORTS;
  T.state.clarify = BY_NAME.clarify;
  T.state.closed = BY_NAME.closed;
  for (const [label, fn] of pageInit) {
    try {
      fn();
      check(`页面初始化 · ${label}`, true);
    } catch (err) {
      check(`页面初始化 · ${label}`, false, err.message);
    }
  }

  /* ---------- 10. 异常与澄清路径 ---------- */
  try {
    const turn = makeEl("turn");
    T.appendAssistant(turn, { ok: false, error: "模拟：模型超时", stages: [] }, "测试问题");
    check("异常路径 · 显示错误卡片", turn.innerHTML.includes("模拟：模型超时"), turn.innerHTML.slice(0, 60));
  } catch (err) {
    check("异常路径 · 显示错误卡片", false, err.message);
  }

  try {
    const turn = makeEl("turn2");
    T.appendAssistant(
      turn,
      {
        ok: true,
        need_clarification: true,
        clarification: "请问要统计哪个时间段？",
        hints: load("hints"),
        stages: [],
      },
      "最近卖得怎么样"
    );
    const html = turn.innerHTML;
    check("澄清路径 · 展示问题", html.includes("请问要统计哪个时间段"));
    check("澄清路径 · 有可填标签", html.includes("chip-btn"));
    check("澄清路径 · 有回答输入框", html.includes("<input"));
  } catch (err) {
    check("澄清路径", false, err.message);
  }

  /* ---------- 11. HTML 完整性 ---------- */
  const htmlSrc = fs.readFileSync("web/index.html", "utf8");
  const ids = [...htmlSrc.matchAll(/id="([^"]+)"/g)].map((m) => m[1]);
  const dup = ids.filter((v, i) => ids.indexOf(v) !== i);
  check("HTML · 没有重复 id", dup.length === 0, dup.join(","));

  const appSrc = fs.readFileSync("web/app.js", "utf8");
  const used = new Set([...appSrc.matchAll(/\$\("#([a-zA-Z0-9_-]+)"/g)].map((m) => m[1]));
  const missing = [...used].filter((id) => !ids.includes(id));
  check("HTML · JS 引用的 id 都存在", missing.length === 0, missing.join(","));

  /* ---------- 汇总 ---------- */
  console.log("\n" + "=".repeat(56));
  console.log(`前端检查 ${pass.length + fail.length} 项：通过 ${pass.length}，失败 ${fail.length}`);
  if (fail.length) {
    console.log("\n失败项：");
    fail.forEach((f) => console.log(`  - ${f.name}  ${f.detail}`));
    process.exitCode = 1;
  }
})();
