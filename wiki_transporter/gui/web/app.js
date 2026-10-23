/* Wiki Transporter · 安巢鸟 —— 前端（无框架，单文件）
   三个视图：整理（搜索 + 选集 + 最近）、书库（以书为主体）、来源（表格）。
   生成进度全部来自后端任务快照：stage / done / total / progress。 */
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="token"]').content;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  const state = {
    sources: [],
    source: localStorage.getItem("wt.source") || "",
    results: [],
    mode: "",
    selected: new Map(),     // title -> entry
    plan: null,
    formats: new Set(["pdf"]),
    fontScale: parseFloat(localStorage.getItem("wt.fontScale") || "1") || 1,  // PDF 正文字号倍率
    coverMode: "",           // auto 抓取 / local 用自己的图片 / off 只用占位图
    coverPicked: false,
    job: null,
    es: null,
    recent: [],
    plans: [],
    open: new Set(),         // 书库里展开文件的书
    openKind: localStorage.getItem("wt.openKind") || "auto",  // 默认打开格式
  };

  // ---------------------------------------------------------------- 小工具
  function el(tag, props = {}, ...children) {
    const node = document.createElement(tag);
    for (const [k, v] of Object.entries(props)) {
      if (v == null || v === false) continue;
      if (k === "class") node.className = v;
      else if (k === "text") node.textContent = v;
      else if (k === "html") node.innerHTML = v;
      else if (k.startsWith("on")) node.addEventListener(k.slice(2), v);
      else if (k === "dataset") Object.assign(node.dataset, v);
      else node.setAttribute(k, v);
    }
    for (const c of children.flat()) {
      if (c == null || c === false) continue;
      node.append(typeof c === "string" ? document.createTextNode(c) : c);
    }
    return node;
  }

  function fmtSize(n) {
    if (!n) return "";
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(0) + " KB";
    return (n / 1024 / 1024).toFixed(1) + " MB";
  }

  function clock(sec) {
    sec = Math.max(0, Math.round(sec));
    const m = Math.floor(sec / 60), s = sec % 60;
    return String(m).padStart(2, "0") + ":" + String(s).padStart(2, "0");
  }

  function toast(msg, kind = "") {
    const t = el("div", { class: "toast " + kind, text: msg });
    $("#toasts").append(t);
    setTimeout(() => { t.style.opacity = "0"; }, 2800);
    setTimeout(() => t.remove(), 3300);
  }

  function setStat(text, cls = "") {
    const p = $("#status-pill");
    p.className = "stat " + cls;
    p.replaceChildren(el("i"), document.createTextNode(text));
  }

  async function api(path, body) {
    const opt = { headers: { "X-Token": TOKEN } };
    if (body !== undefined) {
      opt.method = "POST";
      opt.headers["Content-Type"] = "application/json";
      opt.body = JSON.stringify(body);
    }
    const res = await fetch(path, opt);
    let data = {};
    try { data = await res.json(); } catch (_) { /* 非 JSON */ }
    if (!res.ok || data.ok === false) throw new Error(data.error || ("请求失败：" + res.status));
    return data;
  }

  const sourceById = (id) => state.sources.find(s => s.id === id) || state.sources[0] || {};

  function thumbUrl(path, w, page) {
    return "/api/thumb?path=" + encodeURIComponent(path) + "&w=" + (w || 420)
      + "&page=" + (page || 0) + "&token=" + encodeURIComponent(TOKEN);
  }

  // ---------------------------------------------------------------- 视图切换
  const VIEWS = ["home", "library", "sources", "settings"];
  function showView(name) {
    for (const v of VIEWS) $("#view-" + v).hidden = v !== name;
    $$(".tab").forEach(b => b.classList.toggle("on", b.dataset.view === name));
    if (location.hash.slice(1) !== name) {
      history.replaceState(null, "", name === "home" ? location.pathname + location.search : "#" + name);
    }
    closeMenu();
    if (name === "library") loadLibrary();
    if (name === "sources") renderSources();
  }

  // ---------------------------------------------------------------- 来源选择
  function syncSource() {
    const s = sourceById(state.source);
    if (!s || !s.id) return;
    state.source = s.id;
    $("#pk-name").textContent = s.name;
    $("#pk-count").textContent = s.indexed ? s.indexed + " 页" : "未索引";
    $("#q").placeholder = s.parser === "generic"
      ? "输入剧情名、角色名，或用 \"…\" 精确搜索"
      : "输入剧情名、角色名或代号，用 “……” 精确搜索";
    const info = $("#src-info-body");
    if (info) {
      info.replaceChildren(
        el("div", { class: "src-info-k", text: s.name }),
        el("div", { class: "src-info-v", text: s.api }),
        el("div", { class: "src-info-line" },
          el("span", { text: "索引" }),
          el("b", { text: s.indexed ? s.indexed + " 页" : "未建" })),
        el("div", { class: "src-info-line" },
          el("span", { text: "解析" }),
          el("b", { text: s.parser === "prts" ? "PRTS 模板" : "通用" })),
        el("div", { class: "src-info-acts" },
          el("button", { class: "btn", text: "重建索引", onclick: () => startIndex(true, false) }),
          el("button", { class: "btn ghost", text: "管理来源", onclick: () => showView("sources") })),
      );
    }
  }

  /* 封面用第一卷的 PDF（00_ 是总目录页，白页） */
  function coverFile(item) {
    const pdfs = (item.files || []).filter(f => f.kind === "pdf");
    return pdfs.find(f => !/^00/.test(f.name)) || pdfs[0] || null;
  }

  function renderMenu() {
    const menu = $("#pk-menu");
    menu.replaceChildren();
    for (const s of state.sources) {
      const on = s.id === state.source;
      menu.append(el("button", {
        class: "pk-item" + (on ? " on" : ""),
        role: "option",
        "aria-selected": on ? "true" : "false",
        onclick: () => { pickSource(s.id); },
      },
        el("span", { class: "n", text: s.name }),
        el("span", { class: "c", text: s.indexed ? s.indexed + " 页" : "未索引" }),
        el("span", { class: "h", text: s.api }),
      ));
    }
  }

  function openMenu() {
    renderMenu();
    $("#pk-menu").hidden = false;
    $("#picker-btn").setAttribute("aria-expanded", "true");
  }
  function closeMenu() {
    const m = $("#pk-menu");
    if (!m || m.hidden) return;
    m.hidden = true;
    $("#picker-btn").setAttribute("aria-expanded", "false");
  }

  function pickSource(id) {
    closeMenu();
    if (state.source === id) return;
    state.source = id;
    localStorage.setItem("wt.source", id);
    state.selected.clear();
    syncSource();
    renderSelection();
    $("#index-banner").hidden = true;
    if ($("#q").value.trim()) doSearch();
    else $("#results").replaceChildren();
    maybePromptIndex();
  }

  // ---------------------------------------------------------------- 索引
  function maybePromptIndex() {
    const s = sourceById(state.source);
    const need = s && s.id && !s.indexed;
    $("#index-banner").hidden = !need;
    if (need) startIndex(false, true);
  }

  async function startIndex(force, silent) {
    const s = sourceById(state.source);
    if (!s || !s.id) return;
    const btn = $("#build-index");
    if (!silent) setStat("建立索引", "on");
    if (silent) { btn.disabled = true; btn.textContent = "索引中…"; }
    try {
      const { job } = await api("/api/index", { source: s.id, force });
      followJob(job, {
        onUpdate: (snap) => {
          if (silent) $("#index-prog").textContent = Math.round(snap.progress * 100) + "%";
        },
        onDone: (snap) => {
          btn.disabled = false;
          btn.textContent = "建立索引";
          $("#index-prog").textContent = "";
          if (snap.status === "done") {
            s.indexed = snap.result.count || 0;
            s.names = true;
            syncSource();
            $("#index-banner").hidden = true;
            setStat("就绪");
            toast("索引完成：" + s.indexed + " 个页面", "good");
            const q = $("#q").value.trim();
            if (q) doSearch();
          } else {
            setStat("索引失败", "err");
            toast(snap.error || "索引失败", "bad");
          }
        },
      });
    } catch (e) {
      btn.disabled = false;
      btn.textContent = "建立索引";
      setStat("就绪");
      toast(e.message, "bad");
    }
  }

  // ---------------------------------------------------------------- 搜索
  let searchTimer = 0;
  let searchSeq = 0;

  async function doSearch() {
    const q = $("#q").value.trim();
    $("#searchbox").classList.toggle("has-value", !!q);
    if (!q) {
      state.results = [];
      $("#results").replaceChildren();
      $("#recent-home").hidden = false;   // 空态引导也要能回来
      return;
    }
    $("#recent-home").hidden = true;
    const seq = ++searchSeq;
    let data;
    try {
      data = await api("/api/search", { q, source: state.source });
    } catch (e) {
      toast(e.message, "bad");
      return;
    }
    if (seq !== searchSeq) return;
    if (data.mode === "noindex") { $("#index-banner").hidden = false; return; }
    state.results = data.results || [];
    state.mode = data.mode;
    renderResults(data);
  }

  function highlight(name, q) {
    const raw = (q || "").trim().replace(/^["“「']|["”」']$/g, "");
    if (!raw) return document.createTextNode(name);
    const idx = name.toLowerCase().indexOf(raw.toLowerCase());
    if (idx < 0) return document.createTextNode(name);
    const frag = document.createDocumentFragment();
    frag.append(name.slice(0, idx));
    frag.append(el("span", { class: "hl", text: name.slice(idx, idx + raw.length) }));
    frag.append(name.slice(idx + raw.length));
    return frag;
  }

  function renderResults(data) {
    const box = $("#results");
    box.replaceChildren();
    const q = data.query != null ? data.query : $("#q").value;

    if (data.did_you_mean) {
      const d = data.did_you_mean;
      box.append(el("div", { class: "dym" },
        el("p", {}, "没有完全匹配 —— 是不是 ", el("b", { text: d.name }), " ？"),
        el("div", { class: "spacer" }),
        el("button", { class: "btn", text: "用这个", onclick: () => { addEntry(d); showPanel(); } }),
        el("button", {
          class: "btn ghost", text: "再看看",
          onclick: (ev) => ev.currentTarget.closest(".dym").remove(),
        }),
      ));
    }

    const head = el("div", { class: "res-head" });
    if (data.mode === "exact") {
      head.append(el("b", { text: "精确匹配" }),
        el("span", { text: data.total ? "共 " + data.total + " 篇" : "没有结果" }));
    } else {
      head.append(el("b", { text: "搜索结果" }),
        el("span", { text: (data.total || 0) + " 个相关页面" + (data.remote ? " · 含站内搜索" : "") }));
    }
    box.append(head);

    if (data.groups && data.groups.length) {
      const g = data.groups[0];
      box.append(el("div", { class: "topic-head" },
        el("b", { text: "已按主题展开：" + g.label }),
        el("span", { text: g.pages + " 篇" }),
        el("div", { class: "spacer" }),
        el("button", {
          class: "btn ghost", text: "整组加入",
          onclick: () => {
            for (const r of state.results) state.selected.set(r.title, r);
            renderSelection();
            syncRows();
          },
        }),
      ));
    }

    if (!state.results.length) {
      const exact = data.mode === "exact";
      box.append(el("div", { class: "empty" },
        el("b", { text: exact ? "没有叫「" + q + "」的篇目" : "没有和「" + q + "」相关的剧情" }),
        el("p", {
          text: exact
            ? "精确搜索只匹配完全同名的篇目。去掉引号再试一次可以做模糊搜索。"
            : "换个说法：角色名、剧情代号（如 AF-ST1）、活动名，或者粘贴 Wiki 页面链接。",
        })));
      return;
    }

    const list = el("div", { class: "list" });
    for (const r of state.results) list.append(hitRow(r, q));
    box.append(list);
  }

  function hitRow(r, q) {
    const on = state.selected.has(r.title);
    return el("button", {
      class: "row-hit" + (on ? " on" : "") + (r.group ? " grouped" : ""),
      dataset: { title: r.title },
      onclick: () => toggleEntry(r),
    },
      el("span", { class: "tick", text: "✓" }),
      el("span", { class: "hit-main" },
        el("span", { class: "hit-name" }, highlight(r.name, q)),
        el("span", { class: "hit-path", text: r.title }),
      ),
      el("span", { class: "hit-meta" },
        r.code ? el("span", { class: "code", text: r.code }) : null,
        el("span", { class: "kind", text: r.kind === "secret" ? "密录" : (r.code ? "剧情" : "页面") }),
      ),
    );
  }

  // ---------------------------------------------------------------- 选集
  function toggleEntry(entry) {
    if (state.selected.has(entry.title)) state.selected.delete(entry.title);
    else state.selected.set(entry.title, entry);
    const row = document.querySelector('.row-hit[data-title="' + CSS.escape(entry.title) + '"]');
    if (row) row.classList.toggle("on", state.selected.has(entry.title));
    renderSelection();
  }

  function addEntry(entry) {
    state.selected.set(entry.title, entry);
    renderSelection();
    const row = document.querySelector('.row-hit[data-title="' + CSS.escape(entry.title) + '"]');
    if (row) row.classList.add("on");
  }

  function renderSelection() {
    const n = state.selected.size;
    $("#sel-count").textContent = n;
    $("#sel-empty").hidden = n > 0;
    $("#sel-go").disabled = n === 0;
    const list = $("#sel-list");
    list.replaceChildren();
    // 篇名重复时（同一关的行动前 / 行动后）显示完整标题，不用两个一样的名字
    const seenName = new Map();
    for (const [, entry] of state.selected) {
      const base = entry.name || entry.title;
      seenName.set(base, (seenName.get(base) || 0) + 1);
    }
    for (const [title, entry] of state.selected) {
      const base = entry.name || title;
      const label = seenName.get(base) > 1 ? title : base;
      list.append(el("li", { class: "sel-item" },
        el("span", { class: "name", text: label, title: title }),
        el("button", {
          class: "x", text: "×", title: "移除",
          onclick: () => {
            state.selected.delete(title);
            const row = document.querySelector('.row-hit[data-title="' + CSS.escape(title) + '"]');
            if (row) row.classList.remove("on");
            renderSelection();
          },
        }),
      ));
    }
  }

  // ---------------------------------------------------------------- 任务进度
  function jobView() {
    const phase = el("b", { class: "job-phase", text: "准备" });
    const msg = el("span", { class: "job-msg", text: "正在准备…" });
    const pct = el("span", { class: "job-pct", text: "0%" });
    const fill = el("i");
    const bar = el("div", { class: "track" }, fill);
    const count = el("span", { class: "count", text: "" });
    const time = el("span", { class: "right", text: "00:00" });
    const root = el("div", { class: "job" },
      el("div", { class: "job-head" }, phase, msg, pct),
      bar,
      el("div", { class: "job-foot" }, count, time),
    );
    const seen = { p: 0 };
    function update(snap) {
      const p = Math.max(0, Math.min(1, snap.progress || 0));
      const running = snap.status === "running";
      phase.textContent = snap.phase || (running ? "进行中" : "完成");
      msg.textContent = snap.message || "";
      pct.textContent = Math.round(p * 100) + "%";
      fill.style.width = (p * 100).toFixed(1) + "%";
      bar.classList.toggle("indet", running && p < 0.004);
      if (snap.total) {
        const unit = snap.phase === "抓取原文" ? " 页" : " 项";
        count.textContent = snap.done + " / " + snap.total + unit;
      } else {
        count.textContent = "";
      }
      const elapsed = snap.elapsed || 0;
      let tail = "已用 " + clock(elapsed);
      if (running && p > 0.03 && elapsed > 1.5) {
        tail += " · 约剩 " + clock(elapsed * (1 - p) / p);
      }
      time.textContent = tail;
      seen.p = p;
    }
    return { root, update };
  }

  // ---------------------------------------------------------------- 生成面板
  function closePanel() {
    $("#panel").hidden = true;
    $("#scrim").hidden = true;
    if (state.es) { state.es.close(); state.es = null; }
  }

  // ---------------------------------------------------------------- 整套计划
  async function loadPlans() {
    try {
      const data = await api("/api/plans");
      state.plans = data.items || [];
    } catch (_) {
      state.plans = [];
    }
    renderPlans();
  }

  function renderPlans() {
    const block = $("#plan-block");
    const box = $("#plan-list");
    box.replaceChildren();
    if (!state.plans.length) { block.hidden = true; return; }
    block.hidden = false;
    for (const p of state.plans) {
      box.append(el("button", {
        class: "plan-card", type: "button", title: "按现有卷次整理整本",
        onclick: () => adoptPlan(p),
      },
        el("b", { class: "pc-t", text: p.title }),
        el("span", { class: "pc-m", text: p.volumes + " 卷 · " + p.pages + " 页" }),
      ));
    }
  }

  async function adoptPlan(p) {
    openPanel("整套整理");
    $("#panel-body").replaceChildren(el("div", { class: "status-line" },
      el("div", { class: "spin" }), el("span", { text: "正在载入 " + p.title + " …" })));
    try {
      const data = await api("/api/preview", { plan: p.id, source: state.source });
      state.plan = data.plan;
      state.selected.clear();
      renderSelection();
      renderPlanForm();
    } catch (e) {
      $("#panel-body").replaceChildren(el("div", { class: "empty" },
        el("b", { text: "载入失败" }), el("p", { text: e.message })));
    }
  }

  // ---------------------------------------------------------------- 批量加入
  function syncRows() {
    $$(".row-hit").forEach((row) => {
      row.classList.toggle("on", state.selected.has(row.dataset.title));
    });
  }

  function openBatch() {
    openPanel("批量加入");
    $("#panel-body").replaceChildren(
      el("p", {
        class: "page-sub", style: "margin:0 0 10px",
        text: "一行一个主题：活动名（将进酒）、干员密录（年、夕、令、重岳的干员密录）、"
              + "干员档案、篇名、代号（IW-1 化物/BEG），或者直接粘 Wiki 链接。",
      }),
      el("textarea", {
        id: "batch-text", class: "batch-ta", spellcheck: "false",
        placeholder: "将进酒\n登临意\n年、夕、令、重岳的干员密录\n五星术师惊蛰干员档案、密录\n画中人、怀黍离",
      }),
      el("div", { class: "batch-foot" },
        el("button", { class: "btn primary", text: "解析并加入选集", onclick: resolveBatch }),
        el("div", { class: "spacer" }),
        el("button", {
          class: "btn ghost", text: "清空选集",
          onclick: () => { state.selected.clear(); renderSelection(); syncRows(); },
        }),
      ),
      el("div", { id: "batch-result", class: "batch-result" }),
    );
    const ta = $("#batch-text");
    if (ta) ta.focus();
  }

  async function resolveBatch() {
    const text = $("#batch-text").value;
    const out = $("#batch-result");
    if (!text.trim()) { out.textContent = "先粘贴点东西。"; return; }
    out.className = "batch-result";
    out.textContent = "解析中…";
    let data;
    try {
      data = await api("/api/resolve", { source: state.source, text });
    } catch (e) {
      out.className = "batch-result bad";
      out.textContent = e.message;
      return;
    }
    for (const m of data.matched) state.selected.set(m.title, m);
    renderSelection();
    syncRows();
    out.replaceChildren();
    const groups = data.groups || [];
    const head = el("div", { class: "batch-head" },
      el("b", { text: "加入 " + data.matched.length + " 篇" }));
    if (groups.length) {
      head.append(el("span", { text: " · " + groups.length + " 组" }));
    }
    if (data.missing.length) {
      head.append(el("span", { class: "bad", text: " · " + data.missing.length + " 行没对上" }));
    }
    out.append(head);
    if (groups.length) {
      const box = el("div", { class: "batch-groups" });
      for (const g of groups) {
        box.append(el("div", { class: "batch-group" },
          el("div", { class: "batch-group-head" },
            el("b", { text: g.label }),
            el("span", { text: g.pages.length + " 篇" })),
          el("div", { class: "batch-pages", text: g.pages.map((p) => p.title).join(" · ") })));
      }
      out.append(box);
    }
    if (data.missing.length) {
      const list = el("div", { class: "batch-miss" });
      for (const m of data.missing) {
        const row = el("div", { class: "batch-miss-row" },
          el("code", { text: m.line }),
          el("span", { text: m.reason }));
        list.append(row);
        if (m.candidates && m.candidates.length) {
          list.append(el("div", { class: "batch-miss-cand",
            text: "　候选：" + m.candidates.join(" · ") }));
        }
      }
      out.append(list);
    }
    if (state.selected.size) {
      out.append(el("button", {
        class: "btn primary", style: "margin-top:12px",
        text: "整理这 " + state.selected.size + " 篇",
        onclick: () => { closePanel(); showPanel(); },
      }));
    }
  }

  function openPanel(title) {
    $("#panel").hidden = false;
    $("#scrim").hidden = false;
    $("#panel-title").textContent = title || "整理成书";
  }

  async function showPanel() {
    if (!state.selected.size) return;
    openPanel("整理成书");
    $("#panel-body").replaceChildren(el("div", { class: "status-line" },
      el("div", { class: "spin" }), el("span", { text: "正在准备篇目…" })));

    let data;
    try {
      data = await api("/api/preview", {
        source: state.source,
        titles: Array.from(state.selected.keys()),
        title: "",
      });
    } catch (e) {
      $("#panel-body").replaceChildren(el("div", { class: "empty" },
        el("b", { text: "准备失败" }), el("p", { text: e.message })));
      return;
    }
    state.plan = data.plan;
    renderPlanForm();
  }

  function renderPlanForm() {
    const plan = state.plan;
    const body = $("#panel-body");
    body.replaceChildren();

    body.append(el("div", { class: "field-inline" },
      el("span", { text: "书名" }),
      el("input", { id: "plan-title", value: plan.title, maxlength: "60" }),
    ));

    body.append(el("h4", { text: "篇目" }));
    const tree = el("div", { class: "tree" });
    for (const vol of plan.volumes) {
      tree.append(el("div", { class: "tree-vol", text: vol.title }));
      for (const sec of vol.sections) {
        tree.append(el("div", { class: "tree-sec" },
          sec.name, el("em", { text: (sec.pages || []).length + " 页" })));
      }
    }
    body.append(tree);

    body.append(el("h4", { text: "导出格式" }));
    const fmts = el("div", { class: "formats" });
    const defs = [["pdf", "PDF", "带书签"], ["docx", "Word", "可编辑"], ["epub", "EPUB", "电子书"]];
    for (const [key, label, sub] of defs) {
      fmts.append(el("button", {
        class: "fmt" + (state.formats.has(key) ? " on" : ""),
        onclick: (ev) => {
          if (state.formats.has(key)) state.formats.delete(key);
          else state.formats.add(key);
          if (!state.formats.size) state.formats.add("pdf");
          ev.currentTarget.classList.toggle("on", state.formats.has(key));
          if (typeof syncScale === "function") syncScale();
        },
      }, label, el("small", { text: sub })));
    }
    body.append(fmts);

    // PDF 字号：平板横屏双页时 A4 页面被缩到半个屏宽，字会偏小，这里让用户自己放大。
    // 只缩放字号，版心与页边距不变，所以页数会增加。
    body.append(el("h4", { text: "PDF 正文字号" }));
    const scales = el("div", { class: "formats" });
    const scaleHint = el("p", { class: "hint", text: "" });
    const scaleDefs = [[1, "100%", "打印原尺寸"], [1.15, "115%", "略大"], [1.3, "130%", "平板双页"]];
    const syncScale = () => {
      for (const b of scales.children) {
        b.classList.toggle("on", parseFloat(b.dataset.scale) === state.fontScale);
      }
      scales.style.opacity = state.formats.has("pdf") ? "1" : "0.45";
      scales.style.pointerEvents = state.formats.has("pdf") ? "" : "none";
      scaleHint.textContent = state.formats.has("pdf")
        ? "只影响 PDF；调大后每行字数变少、页数增加，会覆盖上一次生成的 PDF。"
        : "没选 PDF 时不生效。";
    };
    for (const [val, label, sub] of scaleDefs) {
      scales.append(el("button", {
        class: "fmt",
        dataset: { scale: String(val) },
        onclick: () => {
          state.fontScale = val;
          localStorage.setItem("wt.fontScale", String(val));
          syncScale();
        },
      }, label, el("small", { text: sub })));
    }
    body.append(scales, scaleHint);
    syncScale();

    body.append(el("h4", { text: "封面" }));
    const coverInfo = plan.cover || {};
    const localNames = (coverInfo.files || []).map((f) => f.name);
    if (!state.coverMode) {
      state.coverMode = localNames.length ? "local" : (coverInfo.fetch === false ? "off" : "auto");
    }
    state.coverPicked = state.coverPicked || localNames.length > 0;
    const cbox = el("div", { class: "formats" });
    const pick = el("button", { class: "btn", text: "选择图片…" });
    const hint = el("span", { class: "cover-hint", text: "" });
    const fileRow = el("div", { class: "cover-row" }, pick, hint);
    const syncCover = () => {
      for (const b of cbox.children) b.classList.toggle("on", b.dataset.mode === state.coverMode);
      fileRow.style.display = state.coverMode === "local" ? "flex" : "none";
      hint.textContent = localNames.length
        ? "已选：" + localNames.join("、")
        : (state.coverPicked ? "已选好图片" : "还没选图片（选了就不用联网抓）");
    };
    for (const [key, label, sub] of [
      ["auto", "自动抓取", "从站点取立绘"],
      ["local", "用我的图片", "生成前选本地图"],
      ["off", "只用占位图", "不联网"],
    ]) {
      cbox.append(el("button", {
        class: "fmt",
        dataset: { mode: key },
        onclick: () => { state.coverMode = key; syncCover(); },
      }, label, el("small", { text: sub })));
    }
    const fileInput = el("input", {
      type: "file", style: "display:none",
      accept: "image/png,image/jpeg,image/webp,image/gif,image/bmp",
    });
    fileInput.addEventListener("change", async () => {
      const f = fileInput.files && fileInput.files[0];
      if (!f) return;
      if (f.size > 24 * 1024 * 1024) { toast("图片太大（上限 24 MB）", "bad"); return; }
      hint.textContent = "正在读取…";
      try {
        const data = await new Promise((res, rej) => {
          const fr = new FileReader();
          fr.onload = () => res(String(fr.result));
          fr.onerror = () => rej(new Error("读取文件失败"));
          fr.readAsDataURL(f);
        });
        const r = await api("/api/cover", {
          plan_id: plan.id, action: "upload", name: f.name, data,
        });
        const names = (r.files || []).map((x) => x.name);
        if (names.length) { localNames.length = 0; localNames.push(...names); }
        state.coverPicked = true;
        state.coverMode = "local";
        syncCover();
        toast("封面已换成你的图片", "ok");
      } catch (e) {
        syncCover();
        toast(e.message, "bad");
      }
    });
    pick.addEventListener("click", () => fileInput.click());
    body.append(cbox, fileRow, fileInput);
    syncCover();

    body.append(el("p", {
      class: "page-sub", style: "margin:14px 0 0",
      text: "共 " + plan.pages + " 个页面，已缓存 " + plan.cached + " 个；缺的会在生成时补齐。",
    }));

    body.append(el("div", { style: "display:flex; gap:9px; margin-top:20px" },
      el("button", { class: "btn primary", style: "flex:1", text: "开始生成", onclick: startBuild }),
      el("button", { class: "btn", text: "取消", onclick: closePanel }),
    ));
  }

  async function startBuild() {
    if (!state.plan) return;
    const titleInput = $("#plan-title");
    const wanted = titleInput ? titleInput.value.trim() : "";
    if (wanted) state.plan.title = wanted;
    const body = $("#panel-body");
    $("#panel-title").textContent = state.plan.title;
    const view = jobView();
    const log = el("div", { class: "logbox" });
    body.replaceChildren(view.root, log);
    setStat("生成中", "on");

    try {
      const mode = state.coverMode || "auto";
      if (mode === "local" && !state.coverPicked) {
        setStat("就绪");
        toast("先点「选择图片…」挑一张本地封面，或改用自动抓取", "bad");
        renderPlanForm();
        return;
      }
      // 封面开关先落盘，生成时按它决定要不要联网
      await api("/api/cover", { plan_id: state.plan.id, action: "fetch", value: mode === "auto" });
      const { job } = await api("/api/build", {
        plan_id: state.plan.id,
        title: wanted,
        formats: Array.from(state.formats),
        font_scale: state.fontScale,
      });
      followJob(job, {
        onUpdate: (snap) => {
          view.update(snap);
          log.replaceChildren();
          for (const item of snap.log.slice(-40)) log.append(el("div", { text: item.m }));
          log.scrollTop = log.scrollHeight;
          if (snap.status === "running") {
            setStat((snap.phase || "生成") + " " + Math.round((snap.progress || 0) * 100) + "%", "on");
          }
        },
        onDone: (snap) => {
          if (snap.status === "done") { setStat("就绪", "done"); renderDone(snap); }
          else { setStat("生成失败", "err"); renderFailed(snap); }
        },
      });
    } catch (e) {
      setStat("就绪");
      toast(e.message, "bad");
      renderFailed({ error: e.message });
    }
  }

  function fileRow(f, extra = true) {
    const open = () => api("/api/open", { path: f.path })
      .catch(e => toast(e.message, "bad"));
    return el("div", {
      class: "frow", role: "button", title: "打开 " + f.name,
      onclick: open,
    },
      el("span", { class: "fk", text: f.kind }),
      el("span", { class: "fn", text: f.name.replace(/\.(pdf|docx|epub)$/i, "") }),
      el("span", { class: "fs", text: fmtSize(f.size) }),
      el("span", { class: "fo" },
        el("button", {
          class: "fa", text: "打开",
          onclick: (ev) => { ev.stopPropagation(); open(); },
        }),
        extra ? el("button", {
          class: "fa", text: "目录",
          onclick: (ev) => {
            ev.stopPropagation();
            api("/api/open", { path: f.path, reveal: true }).catch(e => toast(e.message, "bad"));
          },
        }) : null,
      ),
    );
  }

  function renderDone(snap) {
    const body = $("#panel-body");
    body.replaceChildren();
    const files = (snap.result && snap.result.files) || [];
    const cover = coverFile({ files });
    if (cover) {
      body.append(el("div", { class: "cover-wrap" },
        el("img", { class: "cover", src: thumbUrl(cover.path, 560, 0), alt: "封面预览", loading: "lazy" }),
        el("div", { class: "cover-cap" },
          el("span", { text: "封面" }),
          el("div", { class: "spacer" }),
          el("span", { text: cover.name }),
        ),
      ));
    }
    body.append(el("div", { class: "status-line", style: "margin-top:14px" },
      el("span", { class: "ok", text: "✓" }),
      el("b", { text: "生成完成" }),
      el("span", { text: snap.elapsed + "s · " + files.length + " 个文件" })));

    const list = el("div", { class: "files-in-panel" });
    for (const f of files) list.append(fileRow(f));
    body.append(list);

    body.append(el("div", { style: "display:flex; gap:9px; margin-top:20px" },
      el("button", {
        class: "btn", text: "打开输出目录",
        onclick: () => api("/api/open", { path: snap.result.out_dir }).catch(e => toast(e.message, "bad")),
      }),
      el("div", { class: "spacer" }),
      el("button", {
        class: "btn primary", text: "再整理一本",
        onclick: () => {
          closePanel();
          state.selected.clear();
          renderSelection();
          $("#q").value = "";
          $("#q").focus();
          $("#results").replaceChildren();
        },
      }),
    ));
    loadLibrary(true);
  }

  function renderFailed(snap) {
    const body = $("#panel-body");
    body.replaceChildren(
      el("div", { class: "status-line" },
        el("span", { class: "bad", text: "×" }), el("b", { text: "出错了" })),
      el("p", { class: "page-sub", text: snap.error || "未知错误" }),
    );
    const log = (snap.log || []).slice(-20);
    if (log.length) {
      const box = el("div", { class: "logbox" });
      for (const item of log) box.append(el("div", { text: item.m }));
      body.append(box);
    }
    body.append(el("div", { style: "margin-top:18px" },
      el("button", { class: "btn primary", text: "回到设置再试", onclick: () => renderPlanForm() })));
  }

  // ---------------------------------------------------------------- 任务流
  function followJob(jobId, { onUpdate, onDone }) {
    if (state.es) state.es.close();
    const es = new EventSource("/api/job/" + jobId + "/stream?token=" + encodeURIComponent(TOKEN));
    state.es = es;
    let finished = false;
    es.onmessage = (ev) => {
      let snap;
      try { snap = JSON.parse(ev.data); } catch (_) { return; }
      if (onUpdate) onUpdate(snap);
      if (snap.status !== "running" && !finished) {
        finished = true;
        es.close();
        state.es = null;
        if (onDone) onDone(snap);
      }
    };
    es.onerror = () => {
      if (finished) return;
      finished = true;
      es.close();
      state.es = null;
      if (onDone) onDone({ status: "error", error: "与生成进程的连接中断", log: [] });
    };
  }

  // ---------------------------------------------------------------- 书库
  async function loadLibrary(quiet) {
    try {
      const data = await api("/api/recent");
      state.recent = data.items || [];
      renderLibrary();
      renderRecent();
    } catch (e) {
      if (!quiet) toast(e.message, "bad");
    }
  }

  function bookCard(item) {
    const pdf = coverFile(item);
    const srcName = (sourceById(item.source_id) || {}).name || item.source_id || "";
    let cover;
    if (pdf) {
      cover = el("div", { class: "book-cover" },
        el("img", { src: thumbUrl(pdf.path, 460, 0), alt: "", loading: "lazy" }));
    } else {
      cover = el("div", { class: "book-cover ph" },
        el("div", { class: "ph-t", text: item.title }),
        el("div", { class: "ph-s", text: srcName }));
    }

    const kinds = [...new Set(item.files.map(f => f.kind))];
    const fmtText = kinds.length ? kinds.map(k => k.toUpperCase()).join(" · ") : "还没有导出";

    const metaBits = [srcName, item.pages ? item.pages + " 页" : ""].filter(Boolean);
    if (item.mtime) {
      const d = new Date(item.mtime * 1000);
      metaBits.push(d.getFullYear() + "-" + String(d.getMonth() + 1).padStart(2, "0")
        + "-" + String(d.getDate()).padStart(2, "0"));
    }

    const files = el("div", { class: "book-files" });
    for (const f of item.files) files.append(fileRow(f));
    files.hidden = !state.open.has(item.id);

    const toggle = el("button", { class: "files-toggle", type: "button" });
    const paint = () => {
      toggle.replaceChildren(files.hidden ? "文件" : "收起文件",
        el("span", { class: "n", text: item.files.length }));
    };
    paint();
    toggle.onclick = (ev) => {
      ev.stopPropagation();
      files.hidden = !files.hidden;
      if (files.hidden) state.open.delete(item.id); else state.open.add(item.id);
      paint();
    };

    return el("article", {
      class: "book", role: "button", tabindex: "0",
      "aria-label": "打开《" + item.title + "》",
      onclick: () => openBook(item),
      oncontextmenu: (ev) => bookMenu(ev, item),
      onkeydown: (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openBook(item); }
      },
    },
      cover,
      el("div", { class: "book-body" },
        el("h2", { class: "book-title", text: item.title }),
        el("p", { class: "book-meta", text: metaBits.join(" · ") }),
        el("p", { class: "book-formats" + (kinds.length ? "" : " none"), text: fmtText }),
        item.files.length ? toggle : el("span", { class: "book-formats none", text: "未导出" }),
        files,
        el("div", { class: "book-acts" },
          el("button", {
            class: "link danger", type: "button", text: "删除",
            onclick: (ev) => { ev.stopPropagation(); askDeleteBook(item); },
          }),
          el("span", { class: "book-hint", text: "右键可换格式打开" }),
        ),
      ),
    );
  }

  function renderLibrary() {
    const box = $("#library-list");
    box.replaceChildren();
    $("#lib-count").textContent = state.recent.length;
    if (!state.recent.length) {
      box.append(el("div", { class: "empty" },
        el("b", { text: "书库还是空的" }),
        el("p", { text: "回到「整理」，搜一个剧情名字，勾选篇目后点「整理成书」。" })));
      return;
    }
    for (const item of state.recent) box.append(bookCard(item));
  }

  function renderRecent() {
    const box = $("#recent-home");
    box.replaceChildren();
    box.hidden = false;
    if (!state.recent.length) {
      box.append(el("div", { class: "hero" },
        el("p", { class: "hero-k", text: "开始" }),
        el("h3", { class: "hero-t", text: "把一段剧情整理成能读的书" }),
        el("ol", { class: "hero-steps" },
          el("li", {}, el("b", { text: "选来源" }), "左上角切换站点，第一次会建一次页面索引。"),
          el("li", {}, el("b", { text: "挑篇目" }), "搜索勾选，或者「批量加入」一次粘一大段清单。"),
          el("li", {}, el("b", { text: "整理成书" }), "右边选集点「整理成书」，导出 PDF / Word / EPUB。"),
        ),
        el("div", { class: "hero-acts" },
          el("button", { class: "btn primary", text: "批量加入", onclick: openBatch }),
          el("button", {
            class: "btn", text: "试试「画中人」",
            onclick: () => {
              $("#q").value = "画中人";
              $("#searchbox").classList.add("has-value");
              doSearch();
            },
          }),
        ),
      ));
      return;
    }
    box.append(
      el("div", { class: "shelf-head" },
        el("h3", { text: "最近整理" }),
        el("span", { class: "shelf-hint", text: "右键书卡可换格式或删除" }),
        el("button", { class: "link more", text: "全部书目 →", onclick: () => showView("library") })),
    );
    const shelf = state.recent.filter((it) => (it.files || []).length);
    if (!shelf.length) {
      box.append(el("div", { class: "empty" },
        el("b", { text: "还没有导出过书" }),
        el("p", { text: "勾好篇目点「整理成书」，生成后的 PDF / Word / EPUB 会出现在这里；只有计划、还没生成的，在「全部书目」里。" })));
      return;
    }
    const grid = el("div", { class: "shelf-grid" });
    for (const item of shelf.slice(0, 4)) {
      const pdf = coverFile(item);
      const srcName = (sourceById(item.source_id) || {}).name || item.source_id || "";
      const kinds = [...new Set(item.files.map(f => f.kind))].map(k => k.toUpperCase());
      const cover = pdf
        ? el("div", { class: "shelf-cover" },
            el("img", { src: thumbUrl(pdf.path, 340, 0), alt: "", loading: "lazy" }))
        : el("div", { class: "shelf-cover ph" },
            el("div", { class: "t", text: item.title }),
            el("div", { class: "s", text: srcName }));
      grid.append(el("button", {
        class: "shelf-item",
        title: "左键按默认格式打开，右键换格式 / 删除",
        onclick: () => openBook(item),
        oncontextmenu: (ev) => bookMenu(ev, item),
      },
        cover,
        el("span", { class: "shelf-t", text: item.title }),
        el("span", { class: "shelf-m", text: (kinds.join(" · ") || "未导出") + " · " + (item.pages || 0) + " 页" }),
      ));
    }
    box.append(grid);
  }

  // ---------------------------------------------------------------- 打开方式 / 删除
  const KIND_LABEL = { pdf: "PDF", docx: "Word", epub: "EPUB" };
  const KIND_RANK = ["pdf", "docx", "epub"];
  let bookDlg = null;
  let ctxMenu = null;

  const kindLabel = (k) => KIND_LABEL[k] || (k || "").toUpperCase();

  function sortedFiles(item) {
    return [...(item.files || [])].sort(
      (a, b) => (KIND_RANK.indexOf(a.kind) + 1 || 9) - (KIND_RANK.indexOf(b.kind) + 1 || 9));
  }

  /* 同一个格式里优先「全一册」，其次 00_ 总目录，再次第一个分卷 */
  function pickFile(item, kind) {
    const list = sortedFiles(item).filter(f => !kind || f.kind === kind);
    if (!list.length) return null;
    return list.find(f => f.name.includes("全一册"))
      || list.find(f => /^00/.test(f.name)) || list[0];
  }

  function defaultKind(item) {
    const kinds = new Set((item.files || []).map(f => f.kind));
    if (state.openKind !== "auto" && kinds.has(state.openKind)) return state.openKind;
    return KIND_RANK.find(k => kinds.has(k)) || "pdf";
  }

  function openPath(path) {
    api("/api/open", { path }).catch(e => toast(e.message, "bad"));
  }
  function revealPath(path) {
    api("/api/open", { path, reveal: true }).catch(e => toast(e.message, "bad"));
  }

  function closeCtx() {
    if (ctxMenu) ctxMenu.hidden = true;
  }

  function showCtx(ev, entries) {
    ev.preventDefault();
    if (!ctxMenu) {
      ctxMenu = el("div", { class: "ctx", hidden: true, role: "menu" });
      document.body.append(ctxMenu);
      document.addEventListener("click", closeCtx, true);
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtx(); });
      window.addEventListener("resize", closeCtx);
      document.addEventListener("scroll", closeCtx, true);
    }
    ctxMenu.replaceChildren(...entries.map(e => e.sep
      ? el("div", { class: "ctx-sep" })
      : el("button", {
          class: "ctx-item" + (e.danger ? " danger" : ""),
          type: "button", role: "menuitem", text: e.text, disabled: !!e.disabled,
          onclick: () => { closeCtx(); e.run(); },
        })));
    ctxMenu.hidden = false;
    const r = ctxMenu.getBoundingClientRect();
    ctxMenu.style.left = Math.max(8, Math.min(ev.clientX, innerWidth - r.width - 8)) + "px";
    ctxMenu.style.top = Math.max(8, Math.min(ev.clientY, innerHeight - r.height - 8)) + "px";
  }

  /* 右键菜单：先用默认格式打开；再列本次可选的格式；最后删除 */
  function bookMenu(ev, item) {
    const files = item.files || [];
    const entries = [];
    if (files.length) {
      const kind = defaultKind(item);
      entries.push({ text: "默认打开（" + kindLabel(kind) + "）", run: () => openBook(item) });
      for (const k of [...new Set(files.map(f => f.kind))]) {
        if (k === kind) continue;
        const f = pickFile(item, k);
        entries.push({ text: "用 " + kindLabel(k) + " 打开", run: () => openPath(f.path) });
      }
      const dir = bookDir(item);
      if (dir) entries.push({ text: "打开所在文件夹", run: () => revealPath(dir) });
    } else {
      entries.push({ text: "还没有导出文件", disabled: true });
    }
    entries.push({ sep: true },
      { text: "删除这本书", danger: true, run: () => askDeleteBook(item) });
    showCtx(ev, entries);
  }

  function ensureBookDlg() {
    if (bookDlg) return bookDlg;
    const card = el("div", { class: "dlg-card", role: "dialog", "aria-modal": "true" });
    const wrap = el("div", { class: "dlg", hidden: true },
      el("div", { class: "dlg-scrim", onclick: closeBookDlg }), card);
    wrap._card = card;
    document.body.append(wrap);
    document.addEventListener("keydown", (ev) => {
      if (ev.key === "Escape" && !wrap.hidden) closeBookDlg();
    });
    bookDlg = wrap;
    return wrap;
  }

  function closeBookDlg() {
    if (bookDlg) bookDlg.hidden = true;
  }

  function bookDir(item) {
    if (item.out_dir) return item.out_dir;
    const f = (item.files || [])[0];
    return f ? f.path.replace(/[\\/][^\\/]+$/, "") : "";
  }

  /* 点一下 = 按默认格式打开；要挑单个分卷就在书库卡片里展开「文件」 */
  function openBook(item) {
    const kind = defaultKind(item);
    const f = pickFile(item, kind);
    if (!f) {
      toast("《" + item.title + "》还没有导出文件，回到「整理」生成一次", "bad");
      return;
    }
    openPath(f.path);
  }


  function askDeleteBook(item) {
    const dlg = ensureBookDlg();
    const card = dlg._card;
    card.replaceChildren(
      el("div", { class: "dlg-head" },
        el("b", { class: "dlg-title", text: "删除《" + item.title + "》？" }),
        el("button", { class: "icon-btn", type: "button", title: "关闭", text: "×", onclick: closeBookDlg })),
      el("p", { class: "dlg-sub", text: "计划文件和导出的 PDF / Word / EPUB 会一起移除，不能撤销。" }),
      el("div", { class: "dlg-foot" },
        el("button", { class: "btn ghost", type: "button", text: "取消", onclick: closeBookDlg }),
        el("div", { class: "spacer" }),
        el("button", { class: "btn danger", type: "button", text: "确认删除", onclick: () => deleteBook(item) })),
    );
    dlg.hidden = false;
  }

  async function deleteBook(item) {
    try {
      await api("/api/book/delete", { id: item.id });
    } catch (e) {
      toast(e.message, "bad");
      return;
    }
    state.open.delete(item.id);
    closeBookDlg();
    toast("已删除《" + item.title + "》", "good");
    await loadLibrary(true);
  }

  // ---------------------------------------------------------------- 来源
  function renderSources() {
    const tbody = $("#source-list");
    tbody.replaceChildren();
    for (const s of state.sources) {
      const active = s.id === state.source;
      const name = el("div", { class: "t-name" },
        active ? el("span", { class: "dot" }) : null,
        s.name,
        s.builtin ? el("span", { class: "t-flag", text: "内置" }) : null);
      const cellName = el("td", { class: "c-name" }, name,
        s.note ? el("p", { class: "t-note", text: s.note }) : null);
      const actions = el("div", { class: "t-act" },
        el("button", {
          class: "btn", text: "用这个",
          onclick: () => { state.source = s.id; localStorage.setItem("wt.source", s.id); syncSource(); renderSources(); renderMenu(); showView("home"); },
        }),
        el("button", { class: "btn", text: "重建索引", onclick: () => rebuild(s) }),
        el("button", { class: "btn", text: "编辑", onclick: () => fillForm(s) }),
        s.builtin ? null : el("button", { class: "btn danger", text: "删除", onclick: () => removeSource(s) }),
      );
      tbody.append(el("tr", {},
        cellName,
        el("td", { class: "c-api" }, el("div", { class: "t-api", text: s.api, title: s.api })),
        el("td", { class: "c-parser", text: s.parser === "prts" ? "PRTS 模板" : "通用" }),
        el("td", { class: "c-index num" },
          el("span", { class: "t-index" + (s.indexed ? "" : " none"),
                       text: s.indexed ? s.indexed + " 页" : "未索引" })),
        el("td", {}, actions),
      ));
    }
  }

  async function rebuild(s) {
    state.source = s.id;
    localStorage.setItem("wt.source", s.id);
    syncSource();
    renderSources();
    startIndex(true, false);
  }

  function setFormOpen(open) {
    $("#form-body").hidden = !open;
    $("#form-toggle").setAttribute("aria-expanded", open ? "true" : "false");
  }

  function fillForm(s) {
    setFormOpen(true);
    $("#form-title").textContent = "编辑来源：" + s.name;
    $("#f-name").value = s.name;
    $("#f-api").value = s.api;
    $("#f-home").value = s.home || "";
    $("#f-parser").value = s.parser;
    $("#f-categories").value = (s.categories || []).join(", ");
    $("#f-prefixes").value = (s.prefixes || []).join(", ");
    $("#f-regex").value = s.page_regex || "";
    $("#source-form-wrap").scrollIntoView({ behavior: "smooth", block: "center" });
    $("#f-name").focus();
  }

  function formData() {
    return {
      name: $("#f-name").value.trim(),
      api: $("#f-api").value.trim(),
      home: $("#f-home").value.trim(),
      parser: $("#f-parser").value,
      categories: $("#f-categories").value.trim(),
      prefixes: $("#f-prefixes").value.trim(),
      page_regex: $("#f-regex").value.trim(),
    };
  }

  async function testSource() {
    const probeEl = $("#f-probe");
    probeEl.className = "probe";
    probeEl.textContent = "连接中…";
    try {
      const data = await api("/api/source/test", { source: formData() });
      const p = data.probe || {};
      probeEl.className = "probe good";
      probeEl.textContent = "✓ " + (p.sitename || "已连接") + " · "
        + (p.generator || "") + " · " + (p.pages || 0) + " 页";
    } catch (e) {
      probeEl.className = "probe bad";
      probeEl.textContent = "× " + e.message;
    }
  }

  async function saveSource() {
    const data = formData();
    if (!data.api) { toast("请填写 api.php 地址", "bad"); return; }
    try {
      const res = await api("/api/source/save", { source: data });
      const idx = state.sources.findIndex(s => s.id === res.source.id);
      if (idx >= 0) state.sources[idx] = res.source;
      else state.sources.push(res.source);
      renderSources();
      renderMenu();
      syncSource();
      toast("已保存来源：" + res.source.name, "good");
      $("#form-reset").click();
      setFormOpen(false);
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  async function removeSource(s) {
    if (!confirm("删除来源「" + s.name + "」？已抓取的缓存不会删除。")) return;
    try {
      await api("/api/source/delete", { id: s.id });
      state.sources = state.sources.filter(x => x.id !== s.id);
      if (state.source === s.id) state.source = (state.sources[0] || {}).id || "";
      renderSources();
      renderMenu();
      syncSource();
      toast("已删除", "good");
    } catch (e) { toast(e.message, "bad"); }
  }

  // ---------------------------------------------------------------- 设置
  function paintOpenKind() {
    $$("#open-kind .seg-item").forEach(b => b.classList.toggle("on", b.dataset.kind === state.openKind));
  }

  function applySettings(s) {
    if (!s) return;
    if (s.out_root != null) $("#set-root-input").value = s.out_root;
    if (s.root) {
      $("#set-root").textContent = s.root;
      $("#lib-root").textContent = s.root;
    }
    if (s.settings_path) $("#set-settings").textContent = s.settings_path;
    if (s.sources_path) $("#set-sources").textContent = s.sources_path;
  }

  async function saveRoot() {
    const out_root = $("#set-root-input").value.trim();
    const btn = $("#root-save");
    btn.disabled = true;
    $("#root-hint").textContent = "正在检查目录…";
    try {
      const r = await api("/api/settings/save", { out_root });
      applySettings(r.settings);
      $("#root-hint").textContent = "已切换到 " + r.settings.root;
      toast("存放位置已更新", "good");
      state.recent = (await api("/api/recent")).items || [];
      renderRecent();
      renderLibrary();
    } catch (e) {
      $("#root-hint").textContent = "";
      toast(e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  }

  async function pickRoot() {
    const btn = $("#root-pick");
    btn.disabled = true;
    $("#root-hint").textContent = "请在弹出窗口里选择…";
    try {
      const r = await api("/api/pick-dir", { initial: $("#set-root-input").value.trim() });
      if (r.path) {
        $("#set-root-input").value = r.path;
        $("#root-hint").textContent = "已选择，点「保存」生效";
      } else {
        $("#root-hint").textContent = "没有选择目录";
      }
    } catch (e) {
      $("#root-hint").textContent = "";
      toast(e.message, "bad");
    } finally {
      btn.disabled = false;
    }
  }

  // ---------------------------------------------------------------- 初始化
  async function init() {
    try {
      const data = await api("/api/state");
      state.sources = data.sources || [];
      state.recent = data.recent || [];
      if (!state.sources.find(s => s.id === state.source)) state.source = (state.sources[0] || {}).id || "";
      $("#lib-root").textContent = data.root;
      $("#set-version").textContent = data.app.name + " " + data.app.version;
      if (data.settings) applySettings(data.settings);
      syncSource();
      renderMenu();
      renderSources();
      renderSelection();
      renderLibrary();
      renderRecent();
      loadPlans();
      const s = sourceById(state.source);
      if (s && s.id && !s.indexed) maybePromptIndex();
      else if (s && s.indexed) setStat("索引 " + s.indexed + " 页");
      else setStat("就绪");
      const wantView = location.hash.replace("#", "");
      if (VIEWS.includes(wantView) && wantView !== "home") showView(wantView);
    } catch (e) {
      toast("初始化失败：" + e.message, "bad");
    }
  }

  // ---------------------------------------------------------------- 事件
  $("#picker-btn").addEventListener("click", (ev) => {
    ev.stopPropagation();
    if ($("#pk-menu").hidden) openMenu(); else closeMenu();
  });
  document.addEventListener("click", (ev) => {
    if (!ev.target.closest("#picker")) closeMenu();
  });
  document.addEventListener("keydown", (ev) => {
    if (ev.key === "Escape") closeMenu();
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault(); showView("home"); $("#q").focus(); $("#q").select();
    }
    if (ev.key === "/" && document.activeElement !== $("#q") && !ev.ctrlKey && !ev.metaKey) {
      ev.preventDefault(); $("#q").focus();
    }
    if (ev.key === "Escape") {
      if (!$("#panel").hidden) closePanel();
      else if (document.activeElement === $("#q") && $("#q").value) {
        $("#q").value = ""; $("#searchbox").classList.remove("has-value");
        $("#results").replaceChildren(); $("#recent-home").hidden = false;
      }
    }
  });

  $("#q").addEventListener("input", () => {
    $("#searchbox").classList.toggle("has-value", !!$("#q").value);
    clearTimeout(searchTimer);
    searchTimer = setTimeout(doSearch, 170);
  });
  $("#q").addEventListener("keydown", (ev) => {
    if (ev.key === "Enter") {
      ev.preventDefault();
      clearTimeout(searchTimer);
      if (state.results.length === 1) { addEntry(state.results[0]); showPanel(); }
      else doSearch();
    }
  });
  $("#clear").addEventListener("click", () => {
    $("#q").value = ""; $("#searchbox").classList.remove("has-value");
    $("#results").replaceChildren(); $("#q").focus();
    $("#recent-home").hidden = false;
  });
  $("#hints").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-q]");
    if (!btn) return;
    $("#q").value = btn.dataset.q;
    $("#searchbox").classList.add("has-value");
    doSearch();
  });
  $("#sel-go").addEventListener("click", showPanel);
  $("#batch-open").addEventListener("click", openBatch);
  $("#panel-close").addEventListener("click", closePanel);
  $("#scrim").addEventListener("click", closePanel);
  $("#build-index").addEventListener("click", () => startIndex(true, false));
  $("#f-test").addEventListener("click", testSource);
  $("#f-save").addEventListener("click", saveSource);
  $("#form-toggle").addEventListener("click", () => {
    const open = $("#form-body").hidden;
    setFormOpen(open);
    if (open) $("#form-title").textContent = "添加来源";
  });
  $("#form-reset").addEventListener("click", () => {
    $("#form-title").textContent = "添加来源";
    for (const id of ["f-name", "f-api", "f-home", "f-categories", "f-prefixes", "f-regex"]) $("#" + id).value = "";
    $("#f-parser").value = "generic";
    $("#f-probe").textContent = "";
  });
  $$(".tab").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
  $("#brand").addEventListener("click", (ev) => { ev.preventDefault(); showView("home"); });
  $("#root-save").addEventListener("click", saveRoot);
  $("#root-pick").addEventListener("click", pickRoot);
  $("#root-reset").addEventListener("click", () => {
    $("#set-root-input").value = "";
    saveRoot();
  });
  $$("#open-kind .seg-item").forEach(b => b.addEventListener("click", () => {
    state.openKind = b.dataset.kind;
    localStorage.setItem("wt.openKind", state.openKind);
    paintOpenKind();
  }));
  paintOpenKind();
  $$("[data-open]").forEach(b => b.addEventListener("click", async () => {
    const kind = b.dataset.open;
    try {
      const path = kind === "root" ? $("#set-root").textContent
        : kind === "settings-file" ? $("#set-settings").textContent
          : $("#set-sources").textContent;
      await api("/api/open", { path, reveal: true });
    } catch (e) { toast(e.message, "bad"); }
  }));

  init();
})();
