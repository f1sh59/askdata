/* API 文档渲染：从 /api/openapi.json 读，纯 DOM 拼装，没有框架也没有 CDN。
   这样离线可用，也不会因为某个 CDN 挂了导致文档打不开。 */

const state = { spec: null };

const el = (tag, attrs = {}, children = []) => {
  const node = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === "class") node.className = v;
    else if (k === "text") node.textContent = v;
    else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
    else node.setAttribute(k, v);
  }
  for (const child of children) {
    if (child == null) continue;
    node.append(typeof child === "string" ? document.createTextNode(child) : child);
  }
  return node;
};

/* 把 $ref 展开成真正的内容，否则文档里全是 {"$ref": "..."} 看不懂 */
function resolve(node, spec, seen = new Set()) {
  if (!node || typeof node !== "object") return node;
  if (node.$ref) {
    const name = node.$ref.split("/").pop();
    if (seen.has(name)) return { type: "object", description: `（递归引用 ${name}）` };
    const target = (spec.components?.schemas || {})[name];
    if (!target) return { type: "unknown", description: `缺少 schema: ${name}` };
    return resolve(target, spec, new Set([...seen, name]));
  }
  return node;
}

function schemaToText(schema, spec, depth = 0) {
  const s = resolve(schema, spec);
  if (!s) return "—";
  if (s.type === "array") {
    return `[ ${schemaToText(s.items, spec, depth)} ]`;
  }
  if (s.type === "object" && s.properties) {
    const lines = Object.entries(s.properties).map(([key, val]) => {
      const v = resolve(val, spec);
      const type = v.type || (v.properties ? "object" : "any");
      const desc = v.description ? `  // ${v.description}` : "";
      return `  ${key}: ${Array.isArray(type) ? type.join(" | ") : type}${desc}`;
    });
    return `{\n${lines.join("\n")}\n}`;
  }
  if (Array.isArray(s.type)) return s.type.join(" | ");
  return s.type || "any";
}

function renderEndpoint(path, method, op, spec) {
  const body = el("div", { class: "ep-body" });

  if (op.description) body.append(el("p", { class: "ep-note", text: op.description }));

  if (op.parameters?.length) {
    const section = el("div", { class: "ep-section" }, [el("h4", { text: "查询参数" })]);
    section.append(
      el("pre", {
        text: op.parameters
          .map((p) => `${p.name}  ${p.in}${p.required ? "  必填" : ""}  ${p.schema?.type || "string"}`)
          .join("\n"),
      })
    );
    body.append(section);
  }

  if (op.requestBody) {
    const schema = op.requestBody.content?.["application/json"]?.schema;
    body.append(
      el("div", { class: "ep-section" }, [
        el("h4", { text: "请求体" }),
        el("pre", { text: schemaToText(schema, spec) }),
      ])
    );
  }

  const okEntry = Object.entries(op.responses || {}).find(([code]) => code.startsWith("2"));
  if (okEntry) {
    const [code, resp] = okEntry;
    const contentType = Object.keys(resp.content || {})[0] || "application/json";
    const schema = resp.content?.[contentType]?.schema;
    body.append(
      el("div", { class: "ep-section" }, [
        el("h4", { text: `响应 ${code} · ${contentType}` }),
        el("pre", { text: schemaToText(schema, spec) }),
      ])
    );
  }

  const card = el("section", { class: "ep" });
  card.append(
    el("div", { class: "ep-head", onclick: () => card.classList.toggle("is-open") }, [
      el("span", { class: `ep-method ${method}`, text: method.toUpperCase() }),
      el("span", { class: "ep-path", text: path }),
      el("span", { class: "ep-summary", text: op.summary || "" }),
    ])
  );
  card.append(body);
  return card;
}

function renderNav(spec) {
  const nav = document.getElementById("docs-nav");
  nav.innerHTML = "";

  const byTag = new Map();
  for (const [path, ops] of Object.entries(spec.paths || {})) {
    for (const [method, op] of Object.entries(ops)) {
      const tag = op.tags?.[0] || "其他";
      if (!byTag.has(tag)) byTag.set(tag, []);
      byTag.get(tag).push({ path, method, op });
    }
  }

  for (const [tag, items] of byTag) {
    const group = el("div", { class: "docs-nav-group" }, [el("h3", { text: tag })]);
    for (const item of items) {
      group.append(
        el("a", {
          class: "docs-nav-item",
          href: `#${item.method}-${item.path.replace(/[^a-zA-Z0-9]/g, "-")}`,
          text: `${item.method.toUpperCase()} ${item.path}`,
        })
      );
    }
    nav.append(group);
  }
}

function renderContent(spec) {
  const content = document.getElementById("docs-content");
  content.innerHTML = "";

  const byTag = new Map();
  for (const [path, ops] of Object.entries(spec.paths || {})) {
    for (const [method, op] of Object.entries(ops)) {
      const tag = op.tags?.[0] || "其他";
      if (!byTag.has(tag)) byTag.set(tag, []);
      byTag.get(tag).push({ path, method, op });
    }
  }

  const tagMeta = new Map((spec.tags || []).map((t) => [t.name, t.description]));
  for (const [tag, items] of byTag) {
    const section = el("div", { class: "docs-group" }, [
      el("h3", { text: tag, style: "margin:22px 0 10px;font-size:15px" }),
    ]);
    if (tagMeta.get(tag)) {
      section.append(el("p", { class: "docs-empty", text: tagMeta.get(tag) }));
    }
    for (const item of items) {
      section.append(renderEndpoint(item.path, item.method, item.op, spec));
    }
    content.append(section);
  }

  document.getElementById("docs-version").textContent = `OpenAPI ${spec.openapi} · v${spec.info?.version || "?"}`;
  document.getElementById("docs-title").textContent = spec.info?.title || "接口总览";
  document.getElementById("docs-desc").textContent = spec.info?.description || "";
}

async function boot() {
  try {
    const res = await fetch("/api/openapi.json");
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    state.spec = await res.json();
    renderNav(state.spec);
    renderContent(state.spec);
  } catch (err) {
    document.getElementById("docs-content").innerHTML =
      `<p class="docs-empty">加载失败：${err.message}</p>`;
  }
}

boot();
