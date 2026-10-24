/* Wiki Transporter · 安巢鸟 —— 前端（无框架，单文件）
   两个视图：整理（搜索 + 选集 + 最近）、书库；数据只来自 PRTS。
   生成进度全部来自后端任务快照：stage / done / total / progress。
   v9：DOM 标记移植到 Tabler + skin.css（.wt-*），逻辑与文案未改。 */
(() => {
  "use strict";

  const TOKEN = document.querySelector('meta[name="token"]').content;
  const $ = (sel, root = document) => root.querySelector(sel);
  const $$ = (sel, root = document) => Array.from(root.querySelectorAll(sel));

  /* 唯一来源：明日方舟 · PRTS */
  const SOURCE = { id: "prts", name: "明日方舟 · PRTS", api: "https://prts.wiki/api.php" };

  const state = {
    indexed: 0,              // 已索引页面数，0 表示还没建索引
    results: [],
    mode: "",
    selected: new Map(),     // title -> entry
    plan: null,
    formats: new Set(["pdf"]),
    fontScale: parseFloat(localStorage.getItem("wt.fontScale") || "1") || 1,  // PDF 正文字号倍率
    credit: localStorage.getItem("wt.credit") !== "0",   // 是否写整理者署名（默认写）
    coverMode: "",           // auto 抓取 / local 用自己的图片 / off 只用占位图
    coverPicked: false,
    job: null,
    es: null,
    recent: [],
    plans: [],
    open: new Set(),         // 书库里展开文件的书
    openKind: localStorage.getItem("wt.openKind") || "auto",  // 默认打开格式
    batchText: "",           // 批量加入的草稿：关掉面板再回来还在
    jobLive: false,          // 生成中：面板可以收起，任务不停，状态灯点得回来
    cursor: -1,              // 结果列表键盘光标
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
    if (kind === "good") kind = "ok";
    const t = el("div", { class: "toast " + kind, text: msg });
    $("#toasts").append(t);
    setTimeout(() => { t.style.opacity = "0"; }, 2800);
    setTimeout(() => t.remove(), 3300);
  }

  /* #status-pill 现在是 <button><i></i><span>文本</span></button>：
     只换 span 文本与整组类名，<i> 由标记提供。 */
  function setStat(text, cls = "") {
    const p = $("#status-pill");
    if (!p) return;
    const tone = cls === "on" ? "busy" : (cls === "err" ? "bad" : "");
    p.className = "wt-status" + (tone ? " " + tone : "");
    const label = p.querySelector("span");
    if (label) label.textContent = text;
  }

  function setHeroIndex(n) {
    const node = $("#hero-index");
    if (node) node.textContent = n ? n.toLocaleString("en-US") + " 页已索引" : "索引未建立";
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

  function thumbUrl(path, w, page) {
    return "/api/thumb?path=" + encodeURIComponent(path) + "&w=" + (w || 420)
      + "&page=" + (page || 0) + "&token=" + encodeURIComponent(TOKEN);
  }

  // ---------------------------------------------------------------- 视图切换
  const VIEWS = ["home", "library", "settings"];
  function showView(name) {
    for (const v of VIEWS) $("#view-" + v).hidden = v !== name;
    $$(".wt-tab").forEach(b => b.classList.toggle("on", b.dataset.view === name));
    if (location.hash.slice(1) !== name) {
      history.replaceState(null, "", name === "home" ? location.pathname + location.search : "#" + name);
    }
    if (name === "library") loadLibrary();
  }

  // ---------------------------------------------------------------- 来源（固定 PRTS）
  function renderSrcInfo() {
    const info = $("#src-info-body");
    if (!info) return;
    info.replaceChildren(
      el("div", { class: "wt-src-line", text: SOURCE.name }),
      el("code", { text: SOURCE.api }),
      el("div", { class: "wt-kv mt-2" },
        el("span", { text: "索引" }),
        el("b", { text: state.indexed ? state.indexed + " 页" : "未建" })),
      el("div", { class: "wt-src-acts" },
        el("button", {
          class: "btn btn-sm", id: "rebuild-index", text: "重建索引",
          onclick: () => startIndex(true, false),
        })),
    );
  }

  /* 封面用第一卷的 PDF（00_ 是总目录页，白页） */
  // 占位封面色板：按书名哈希稳定取一组书衣色，同名永远同色
  function coverTone(name) {
    const s = String(name || "?");
    let h = 0;
    for (let i = 0; i < s.length; i += 1) h = (h * 31 + s.charCodeAt(i)) % 100003;
    return "cv-" + (h % 6 + 1);
  }
  // 没有 PDF 时不再是「灰底 + 掐头四个字」，而是一块有排版的假书衣
  function phCover(item, cls) {
    const t = String(item.title || "").trim() || "未命名";
    return el("span", { class: cls + " ph " + coverTone(t) },
      el("i", { class: "cv-spine" }),
      el("b", { class: "cv-t", text: t }),
      el("i", { class: "cv-rule" }),
      el("b", { class: "cv-1", text: t.slice(0, 1) }));
  }

  function coverFile(item) {
    const pdfs = (item.files || []).filter(f => f.kind === "pdf");
    return pdfs.find(f => !/^00/.test(f.name)) || pdfs[0] || null;
  }

  // ---------------------------------------------------------------- 索引
  function maybePromptIndex() {
    const need = !state.indexed;
    $("#index-banner").hidden = !need;
    if (need) startIndex(false, true);
  }

  async function startIndex(force, silent) {
    const btn = $("#build-index");
    if (!silent) setStat("建立索引", "on");
    if (silent) { btn.disabled = true; btn.textContent = "索引中…"; }
    try {
      const { job } = await api("/api/index", { force });
      followJob(job, {
        onUpdate: (snap) => {
          if (silent) $("#index-prog").textContent = Math.round(snap.progress * 100) + "%";
        },
        onDone: (snap) => {
          btn.disabled = false;
          btn.textContent = "建立索引";
          $("#index-prog").textContent = "";
          if (snap.status === "done") {
            state.indexed = snap.result.count || 0;
            renderSrcInfo();
            setHeroIndex(state.indexed);
            $("#index-banner").hidden = true;
            setStat("就绪");
            toast("索引完成：" + state.indexed + " 个页面", "good");
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
  let searchSeq = 0;

  /* 清空搜索 = 回到首页初始态。清空按钮、Esc、「再整理一本」都走这里，
     免得某条路径漏掉 recent-home / 光标 之类的状态。 */
  function resetSearch(focus) {
    $("#q").value = "";
    $("#view-home").classList.remove("is-searching");
    $("#results").replaceChildren();
    $("#recent-home").hidden = false;
    state.results = [];
    state.cursor = -1;
    if (focus) $("#q").focus();
  }

  async function doSearch() {
    const q = $("#q").value.trim();
    $("#view-home").classList.toggle("is-searching", !!q);
    if (!q) {
      resetSearch(false);
      return;
    }
    $("#recent-home").hidden = true;
    $("#index-banner").hidden = true;
    const seq = ++searchSeq;
    state.cursor = -1;
    $("#results").replaceChildren(el("div", { class: "wt-searching" },
      el("div", { class: "spin" }), el("span", { text: "搜索中…" })));
    let data;
    try {
      data = await api("/api/search", { q });
    } catch (e) {
      toast(e.message, "bad");
      return;
    }
    if (seq !== searchSeq) return;
    if (data.mode === "noindex") $("#index-banner").hidden = false;
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

  /* 一行结果的勾选框：点它和点整行走同一个 toggleEntry，不另立状态 */
  function checkBox(entry, on) {
    return el("input", {
      type: "checkbox", checked: on,
      onclick: (ev) => { ev.stopPropagation(); toggleEntry(entry); },
    });
  }

  // 章节标签：主线按章号，活动按代号前缀——让正文命中按章节分组显示
  function arcLabel(code) {
    const m = String(code || "").match(/^(\d+)-/);
    if (m) return "第 " + m[1] + " 章 · 主线";
    const head = String(code || "").split("-")[0].toUpperCase();
    if (!head) return "其它剧情";
    return "剧情 · " + head;
  }

  function renderResults(data) {
    const box = $("#results");
    box.replaceChildren();
    const q = data.query != null ? data.query : $("#q").value;

    if (data.did_you_mean) {
      const d = data.did_you_mean;
      box.append(el("div", { class: "wt-dym" },
        el("span", {}, "没有完全匹配 —— 是不是 ", el("b", { text: d.name }), " ？"),
        el("span", { class: "ms-auto" }),
        el("button", {
          class: "btn btn-sm btn-primary", text: "用这个",
          onclick: () => { addEntry(d); showPanel(); },
        }),
        el("button", {
          class: "btn btn-sm btn-ghost-secondary", text: "再看看",
          onclick: (ev) => ev.currentTarget.closest(".wt-dym").remove(),
        }),
      ));
    }

    const head = el("div", { class: "wt-res-head" });
    head.append(el("b", { text: data.mode === "exact" ? "精确匹配" : "搜索结果" }));
    if (data.mode === "exact") {
      head.append(el("span", { text: data.total ? "共 " + data.total + " 篇" : "没有结果" }));
    } else {
      head.append(el("span", { text: (data.total || 0) + " 个相关页面" + (data.remote ? " · 含站内搜索" : "") }));
    }
    head.append(el("span", { class: "ms-auto" }));
    // 已选数常驻在结果区头部：勾了几篇、接下来能干什么，一眼可见
    head.append(el("button", {
      class: "wt-selink", hidden: !state.selected.size,
      text: state.selected.size ? "已选 " + state.selected.size + " 篇 →" : "",
      onclick: () => showPanel(),
    }));
    box.append(head);

    if (data.theme && data.theme.title) {
      // 命中的是用户整理的清单（plan），或自动活动表（activity）：整卷原顺序摆出来
      const auto = data.theme.source === "activity";
      box.append(el("div", { class: "wt-topic" },
        el("b", { text: (auto ? "已展开活动：" : "已展开你整理的主题：") + data.theme.title }),
        el("span", {
          text: auto
            ? " · " + (data.theme.pages || 0) + " 篇"
            : " · " + (data.theme.volumes || 0) + " 卷 " + (data.theme.pages || 0) + " 篇",
        }),
        el("span", { class: "ms-auto" }),
        el("button", {
          class: "btn btn-sm btn-ghost-secondary", text: "整组加入",
          onclick: () => {
            for (const r of state.results) state.selected.set(r.title, r);
            renderSelection();
            syncRows();
          },
        }),
      ));
    } else if (data.entity_hint) {
      // 命中已知实体（萨卡兹/岁家/深海…）：给一个「实体页」入口，不只是篇目列表
      box.append(el("div", { class: "wt-topic" },
        el("b", { text: data.entity_hint.name }),
        el("span", {
          text: " · " + (data.entity_hint.aliases || []).slice(0, 6).join("、"),
          title: (data.entity_hint.aliases || []).join("、"),
        }),
        el("span", { class: "ms-auto" }),
        el("button", {
          class: "btn btn-sm", text: "查看实体页 →",
          onclick: () => openEntity(data.entity_hint.name),
        }),
      ));
    } else if (data.groups && data.groups.length) {
      const g = data.groups[0];
      box.append(el("div", { class: "wt-topic" },
        el("b", { text: "已按主题展开：" + g.label }),
        el("span", { class: "ms-auto" }),
        el("button", {
          class: "btn btn-sm btn-ghost-secondary", text: "整组加入",
          onclick: () => {
            for (const r of state.results) state.selected.set(r.title, r);
            renderSelection();
            syncRows();
          },
        }),
      ));
    }

    // 顶部全选/取消：主题召回整卷摆出几十上百篇，逐卷点太累，给一个总开关
    if (state.results.length && (data.theme || (data.groups && data.groups.length))) {
      const allOn = state.results.every((r) => state.selected.has(r.title));
      head.append(el("button", {
        class: "btn btn-sm btn-ghost-secondary",
        text: allOn ? "全部取消" : "全选 " + state.results.length + " 篇",
        onclick: () => {
          const on = !state.results.every((r) => state.selected.has(r.title));
          for (const r of state.results) {
            if (on) state.selected.set(r.title, r);
            else state.selected.delete(r.title);
          }
          renderSelection(); syncRows();
        },
      }));
    }

    if (!state.results.length) {
      const exact = data.mode === "exact";
      const smart = smartBlock(data.text, q);
      if (smart) { head.remove(); box.append(smart); return; }
      const empty = el("div", { class: "wt-empty" },
        el("b", { text: exact ? "没有叫「" + q + "」的篇目" : "没有和「" + q + "」相关的剧情" }),
        el("p", {
          text: exact
            ? "精确搜索只匹配完全同名的篇目。去掉引号再试一次可以做模糊搜索。"
            : "换个说法：角色名、剧情代号（如 AF-ST1）、活动名，或者粘贴 Wiki 页面链接。",
        }));
      const acts = el("div", { class: "wt-empty-acts" });
      if (exact) {
        acts.append(el("button", {
          class: "btn btn-sm", text: "按模糊再搜一次",
          onclick: () => { $("#q").value = String(q).replace(/["“”「」']/g, ""); doSearch(); },
        }));
      }
      if (state.selected.size) {
        acts.append(el("button", {
          class: "btn btn-sm btn-primary", text: "用已选的 " + state.selected.size + " 篇接着整理",
          onclick: () => showPanel(),
        }));
      }
      if (acts.children.length) empty.append(acts);
      box.append(empty);
      return;
    }

    const smart = smartBlock(data.text, q);
    // 有主题召回（整卷已摆出）时不再重复显示 smartBlock 正文散落命中——
    // 它们缺胳膊少腿，和整卷清单同时出现只制造混乱。只有无主题时才让正文搜索补位。
    if (smart && !data.theme) box.append(smart);

    // 无主题召回：只有 smartBlock 一种结果（全是正文命中）时，不再渲染主表格，
    // 否则重复。有主题召回时，smartBlock 补在主表格后面。
    const allTextHits = data.results.length && !data.theme
      ? data.results.every(r => (r.score || 0) < 900) : false;
    if (smart && allTextHits) return;

    const tbody = el("tbody");
    // 主题召回的结果带 group（卷名）：按卷分段显示，整份清单才有「摆出来」的样子
    let lastGroup = "";
    for (const r of state.results) {
      if (r.group && r.group !== lastGroup) {
        lastGroup = r.group;
        const mates = state.results.filter((x) => x.group === r.group);
        const allOn = mates.every((x) => state.selected.has(x.title));
        tbody.append(el("tr", { class: "wt-volrow", dataset: { group: r.group } },
          el("td", { colspan: "4" },
            el("div", { class: "wt-topic" },
              // 卷名前的勾：一下把这一卷全选 / 全不选，不用逐行点
              el("input", {
                type: "checkbox", checked: allOn, class: "wt-volchk",
                title: allOn ? "取消这一卷" : "全选这一卷",
                onclick: (ev) => { ev.stopPropagation(); setGroup(mates, ev.target.checked); },
              }),
              el("b", { text: r.group }),
              el("span", { text: mates.length + " 篇" }),
              el("span", { class: "ms-auto" }),
              el("button", {
                class: "btn btn-sm btn-ghost-secondary",
                text: allOn ? "取消本卷" : "本卷全选",
                onclick: () => setGroup(mates, !allOn),
              })))));
      }
      tbody.append(hitRow(r, q));
    }
    box.append(el("div", { class: "card wt-card" },
      el("div", { class: "card-body p-0" },
        el("table", { class: "table wt-table" }, tbody))));
    // 有主题召回时，smartBlock（补主题外的散落正文命中）补在主表格后面
    if (smart && data.theme) box.append(smart);
  }

  /* 正文命中区：标题索引搜不到「萨卡兹」这类世界观词，这一层用正文索引兜住，
     并给出共现实体（点一下换个词再搜）和章节时间线。 */
  function smartBlock(d, q) {
    if (!d || d.error || !(d.hits || []).length) return null;
    const core = (d.intent && d.intent.core) || q;
    const box = el("div", { class: "card wt-card wt-smart" });
    // 主题召回（整卷清单 score≥900）已列出的篇目不在 smartBlock 重复——只补清单外的散落命中
    const themeTitles = new Set(
      state.results.filter(r => (r.score || 0) >= 900).map(r => r.title));
    const hits = (d.hits || []).filter(h => !themeTitles.has(h.title));
    if (!hits.length) return null;
    const head = el("div", { class: "card-header" },
      el("b", { class: "card-title", text: "正文提到「" + core + "」的剧情" }));
    head.append(el("span", {
      class: "wt-head-note",
      text: hits.length + " 篇 · 已索引 "
        + (((d.corpus || {}).docs) || 0) + " 篇"
        + (d.matched_total > hits.length
           ? " · 正文共 " + d.matched_total + " 篇提到" : "")
        + (d.mode && d.mode !== "offline" ? " · " + d.mode : ""),
    }));
    // 全选/取消：正文命中动辄几十篇，顶部常驻一个总开关
    const allHits = hits.slice();
    const allOn = () => allHits.every((h) => state.selected.has(h.title));
    const selBtn = el("button", {
      class: "btn btn-sm btn-ghost-secondary ms-auto",
      text: allOn() ? "全部取消" : "全选 " + allHits.length + " 篇",
      onclick: () => {
        const on = !allOn();
        for (const h of allHits) {
          const row = { title: h.title, name: h.name || h.title, code: h.code || "", kind: h.code ? "story" : "page", plan: h.plan || "" };
          if (on) state.selected.set(row.title, row);
          else state.selected.delete(row.title);
        }
        renderSelection(); syncRows();
      },
    });
    head.append(selBtn);
    box.append(head);

    if ((d.tree || []).length) {
      const tree = el("div", { class: "wt-chips" },
        el("span", { text: "相关" }));
      for (const t of d.tree.slice(0, 12)) {
        tree.append(el("button", {
          text: t.term,
          title: t.term + " · 与它同篇出现 " + t.docs + " 次",
          onclick: () => { $("#q").value = t.term; doSearch(); },
        }));
      }
      box.append(tree);
    }

    // 按活动/章节分组：hits 已按发布时间线排好序（后端 _route_key），
    // 同活动连续显示，组标题用活动名（洪炉示岁）而非代号（剧情·AF）
    const groups = [];
    let cur = null;
    for (const h of hits) {
      const label = h.arc || arcLabel(h.code || "");
      if (!cur || cur.label !== label) { cur = { label, items: [] }; groups.push(cur); }
      cur.items.push(h);
    }
    const PREVIEW = 3;     // 每组先展示 3 篇，其余折叠
    const tbody = el("tbody");
    for (const g of groups) {
      const shown = g.items.slice(0, PREVIEW);
      const hidden = g.items.slice(PREVIEW);
      const gAll = g.items;
      const gOn = () => gAll.every((h) => state.selected.has(h.title));
      tbody.append(el("tr", { class: "wt-volrow" },
        el("td", { colspan: "4" },
          el("div", { class: "wt-topic" },
            el("input", {
              type: "checkbox", checked: gOn(), class: "wt-volchk",
              title: gOn() ? "取消本组" : "全选本组",
              onclick: (ev) => {
                ev.stopPropagation();
                const on = ev.target.checked;
                for (const h of gAll) {
                  const row = { title: h.title, name: h.name || h.title, code: h.code || "", kind: h.code ? "story" : "page", plan: h.plan || "" };
                  if (on) state.selected.set(row.title, row);
                  else state.selected.delete(row.title);
                }
                renderSelection(); syncRows();
              },
            }),
            el("b", { text: g.label }),
            el("span", { text: g.items.length + " 篇" }),
            el("span", { class: "ms-auto" }),
            el("button", {
              class: "btn btn-sm btn-ghost-secondary",
              text: gOn() ? "取消本组" : "本组全选",
              onclick: () => {
                const on = !gOn();
                for (const h of gAll) {
                  const row = { title: h.title, name: h.name || h.title, code: h.code || "", kind: h.code ? "story" : "page", plan: h.plan || "" };
                  if (on) state.selected.set(row.title, row);
                  else state.selected.delete(row.title);
                }
                renderSelection(); syncRows();
              },
            })))));
      for (const h of shown) tbody.append(smartRow(h));
      if (hidden.length) {
        tbody.append(el("tr", { class: "wt-more-row" },
          el("td", { colspan: "4" },
            el("button", {
              class: "btn btn-sm btn-ghost-secondary wt-expand",
              text: "展开剩余 " + hidden.length + " 篇",
              onclick: (ev) => {
                const tr = ev.target.closest("tr");
                const frag = document.createDocumentFragment();
                for (const h of hidden) frag.append(smartRow(h));
                tr.replaceWith(frag);
              },
            }))));
      }
    }
    box.append(el("div", { class: "card-body p-0" },
      el("table", { class: "table wt-table" }, tbody)));

    if ((d.timeline || []).length > 1) {
      const tlCodes = d.timeline.map((x) => x.code || x.name);
      const preview = tlCodes.slice(0, 8).join(" → ");
      const rest = tlCodes.slice(8);
      const tlSpan = el("span", { text: preview + (rest.length ? " → …" : "") });
      if (rest.length) {
        const more = el("button", {
          class: "btn btn-sm btn-ghost-secondary wt-tl-more",
          text: "展开全部 " + tlCodes.length + " 步",
          style: "margin-left:6px",
          onclick: () => { tlSpan.textContent = tlCodes.join(" → "); more.remove(); },
        });
        box.append(el("div", { class: "wt-smart-line" },
          el("span", { text: "阅读顺序" }), tlSpan, more));
      } else {
        box.append(el("div", { class: "wt-smart-line" },
          el("span", { text: "阅读顺序" }), tlSpan));
      }
    }
    return box;
  }

  function smartRow(h) {
    const row = {
      title: h.title, name: h.name || h.title, code: h.code || "",
      kind: h.code ? "story" : "page", plan: h.plan || "",
    };
    const on = state.selected.has(row.title);
    const snip = el("span", { class: "wt-snip" });
    snip.innerHTML = h.snippet || "";
    return el("tr", {
      class: "wt-row-hit" + (on ? " on" : ""),
      dataset: { title: row.title },
      onclick: () => toggleEntry(row),
    },
      el("td", { class: "wt-check" }, checkBox(row, on)),
      el("td", { class: "wt-name" },
        el("span", { text: row.name }),
        el("span", { class: "wt-path", text: row.title }),
        snip,
      ),
      el("td", { class: "wt-code", text: row.code }),
      el("td", { class: "wt-kind" },
        el("span", { class: "badge wt-badge", text: row.code ? "剧情" : "页面" })),
    );
  }

  function hitRow(r, q) {
    const on = state.selected.has(r.title);
    return el("tr", {
      class: "wt-row-hit" + (on ? " on" : ""),
      dataset: { title: r.title },
      onclick: () => toggleEntry(r),
    },
      el("td", { class: "wt-check" }, checkBox(r, on)),
      el("td", { class: "wt-name" },
        el("span", {}, highlight(r.name, q)),
        el("span", { class: "wt-path", text: r.title })),
      el("td", { class: "wt-code", text: r.code || "" }),
      el("td", { class: "wt-kind" },
        el("span", {
          class: "badge wt-badge",
          text: r.kind === "secret" ? "密录" : (r.code ? "剧情" : "页面"),
        })),
    );
  }

  // ---------------------------------------------------------------- 选集
  function toggleEntry(entry) {
    if (state.selected.has(entry.title)) state.selected.delete(entry.title);
    else state.selected.set(entry.title, entry);
    const row = document.querySelector('.wt-row-hit[data-title="' + CSS.escape(entry.title) + '"]');
    if (row) {
      const on = state.selected.has(entry.title);
      row.classList.toggle("on", on);
      const box = row.querySelector(".wt-check input");
      if (box) box.checked = on;
    }
    renderSelection();
  }

  /* 一组（一卷 / 一整个主题）整批进选集或整批撤掉 */
  function setGroup(list, on) {
    for (const r of list) {
      if (on) state.selected.set(r.title, r);
      else state.selected.delete(r.title);
      const row = document.querySelector('.wt-row-hit[data-title="' + CSS.escape(r.title) + '"]');
      if (!row) continue;
      row.classList.toggle("on", on);
      const box = row.querySelector(".wt-check input");
      if (box) box.checked = on;
    }
    const chk = document.querySelector(".wt-volchk");
    if (chk) chk.checked = on;
    for (const tr of $$(".wt-volrow")) {
      const head = tr.querySelector(".wt-volchk");
      const btn = tr.querySelector("button");
      const g = tr.dataset.group;
      if (!g) continue;
      const mates = state.results.filter((x) => (x.group || "") === g);
      const allOn = mates.length > 0 && mates.every((x) => state.selected.has(x.title));
      if (head) head.checked = allOn;
      if (btn) btn.textContent = allOn ? "取消本卷" : "本卷全选";
    }
    renderSelection();
  }

  function addEntry(entry) {
    state.selected.set(entry.title, entry);
    renderSelection();
    const row = document.querySelector('.wt-row-hit[data-title="' + CSS.escape(entry.title) + '"]');
    if (row) {
      row.classList.add("on");
      const box = row.querySelector(".wt-check input");
      if (box) box.checked = true;
    }
  }

  function renderSelection() {
    const n = state.selected.size;
    $("#sel-count").textContent = n;
    $("#sel-empty").hidden = n > 0;
    $("#sel-go").disabled = n === 0;
    $("#sel-go span").textContent = n ? "整理这 " + n + " 篇" : "整理成书";
    // 结果区头部的「已选 N 篇」跟着选集走，不用重搜
    const badge = $("#results .wt-selink");
    if (badge) {
      badge.hidden = !n;
      badge.textContent = n ? "已选 " + n + " 篇 →" : "";
    }
    const list = $("#sel-list");
    list.replaceChildren();
    const entries = Array.from(state.selected.entries());
    // 篇名重复时（同一关的行动前 / 行动后）显示完整标题，不用两个一样的名字
    const seenName = new Map();
    for (const [, entry] of entries) {
      const base = entry.name || entry.title;
      seenName.set(base, (seenName.get(base) || 0) + 1);
    }
    entries.forEach(([title, entry], i) => {
      const base = entry.name || title;
      const label = seenName.get(base) > 1 ? title : base;
      list.append(el("li", {},
        el("span", { class: "idx", text: String(i + 1).padStart(2, "0") }),
        el("span", { class: "name", text: label, title: title }),
        el("span", { class: "mv" },
          el("button", {
            class: "mv-btn", type: "button", text: "↑", title: "上移",
            disabled: i === 0, onclick: () => moveEntry(i, -1),
          }),
          el("button", {
            class: "mv-btn", type: "button", text: "↓", title: "下移",
            disabled: i === entries.length - 1, onclick: () => moveEntry(i, 1),
          })),
        el("button", {
          class: "x", text: "×", title: "移除",
          onclick: () => {
            state.selected.delete(title);
            const row = document.querySelector('.wt-row-hit[data-title="' + CSS.escape(title) + '"]');
            if (row) {
              row.classList.remove("on");
              const box = row.querySelector(".wt-check input");
              if (box) box.checked = false;
            }
            renderSelection();
          },
        }),
      ));
    });
  }

  function moveEntry(i, dir) {
    const entries = Array.from(state.selected.entries());
    const j = i + dir;
    if (j < 0 || j >= entries.length) return;
    [entries[i], entries[j]] = [entries[j], entries[i]];
    state.selected = new Map(entries);
    renderSelection();
  }

  // ---------------------------------------------------------------- 任务进度
  function jobView() {
    const phase = el("b", { text: "准备" });
    const msg = el("span", { text: "正在准备…" });
    const pct = el("span", { class: "wt-job-pct", text: "0%" });
    const fill = el("div", { class: "progress-bar", style: "width:0%" });
    const bar = el("div", { class: "progress" }, fill);
    const count = el("span", { text: "" });
    const time = el("span", { class: "right", text: "00:00" });
    const root = el("div", { class: "wt-job" },
      el("div", { class: "wt-job-head" }, phase, msg, pct),
      bar,
      el("div", { class: "wt-job-foot" }, count, time),
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
  /* 关面板。生成中只收起、不中断：任务继续跑，右上角状态灯点一下回到进度。
     这样按 Esc、点遮罩都不会误杀正在跑的生成。 */
  function closePanel() {
    $("#panel").hidden = true;
    $("#panel").classList.remove("show");
    $("#scrim").hidden = true;
    if (state.jobLive) {
      toast("生成在后台继续；点右上角状态可以回来看进度");
      return;
    }
    if (state.es) { state.es.close(); state.es = null; }
  }

  function reopenJob() {
    if (!state.jobLive) return;
    $("#panel").hidden = false;
    $("#panel").classList.add("show");
    $("#scrim").hidden = false;
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
        class: "wt-plan-row", type: "button", title: "按现有卷次整理整本",
        onclick: () => adoptPlan(p),
      },
        el("span", { class: "t", text: p.title }),
        el("span", { class: "m", text: p.volumes + " 卷 · " + p.pages + " 页" }),
      ));
    }
  }

  async function adoptPlan(p) {
    openPanel("整套整理");
    $("#panel-body").replaceChildren(el("div", { class: "wt-status-line" },
      el("div", { class: "spin" }), el("span", { text: "正在载入 " + p.title + " …" })));
    try {
      const data = await api("/api/preview", { plan: p.id });
      state.plan = data.plan;
      state.selected.clear();
      renderSelection();
      renderPlanForm();
    } catch (e) {
      $("#panel-body").replaceChildren(el("div", { class: "wt-empty" },
        el("b", { text: "载入失败" }), el("p", { text: e.message })));
    }
  }

  // ---------------------------------------------------------------- 批量加入
  function syncRows() {
    $$(".wt-row-hit").forEach((row) => {
      const on = state.selected.has(row.dataset.title);
      row.classList.toggle("on", on);
      const box = row.querySelector(".wt-check input");
      if (box) box.checked = on;
    });
  }

  function openBatch() {
    openPanel("批量加入");
    $("#panel-body").replaceChildren(
      el("p", {
        class: "wt-note mt-0 mb-3",
        text: "一行一个主题：活动名（将进酒）、干员密录（年、夕、令、重岳的干员密录）、"
              + "干员档案、篇名、代号（IW-1 化物/BEG），或者直接粘 Wiki 链接。",
      }),
      el("textarea", {
        id: "batch-text", class: "form-control", rows: "10", spellcheck: "false",
        placeholder: "将进酒\n登临意\n年、夕、令、重岳的干员密录\n五星术师惊蛰干员档案、密录\n画中人、怀黍离",
      }),
      el("div", { class: "d-flex align-items-center gap-2 mt-2" },
        el("button", { class: "btn btn-primary", text: "解析并加入选集", onclick: resolveBatch }),
        el("div", { class: "spacer" }),
        el("button", {
          class: "btn btn-ghost-secondary", text: "清空选集",
          onclick: () => { state.selected.clear(); renderSelection(); syncRows(); },
        }),
      ),
      el("div", { id: "batch-result", class: "mt-3" }),
    );
    const ta = $("#batch-text");
    if (ta) {
      ta.value = state.batchText || "";
      ta.addEventListener("input", () => { state.batchText = ta.value; });
      ta.focus();
      ta.selectionStart = ta.selectionEnd = ta.value.length;
    }
  }

  async function resolveBatch() {
    const text = $("#batch-text").value;
    const out = $("#batch-result");
    if (!text.trim()) { out.textContent = "先粘贴点东西。"; return; }
    out.className = "mt-3";
    out.textContent = "解析中…";
    let data;
    try {
      data = await api("/api/resolve", { text });
    } catch (e) {
      out.className = "wt-note mt-3";
      out.textContent = e.message;
      return;
    }
    for (const m of data.matched) state.selected.set(m.title, m);
    renderSelection();
    syncRows();
    out.replaceChildren();
    const groups = data.groups || [];
    const head = el("div", { class: "wt-res-head" },
      el("b", { text: "加入 " + data.matched.length + " 篇" }));
    if (groups.length) {
      head.append(el("span", { text: " · " + groups.length + " 组" }));
    }
    if (data.missing.length) {
      head.append(el("span", { class: "text-danger", text: " · " + data.missing.length + " 行没对上" }));
    }
    out.append(head);
    if (groups.length) {
      const box = el("div", { class: "mt-2" });
      for (const g of groups) {
        box.append(el("div", { class: "wt-kv" },
          el("b", { text: g.label }),
          el("span", { text: g.pages.length + " 篇" })));
        box.append(el("div", { class: "wt-note", text: g.pages.map((p) => p.title).join(" · ") }));
      }
      out.append(box);
    }
    if (data.missing.length) {
      const list = el("div", { class: "wt-logbox mt-2" });
      for (const m of data.missing) {
        list.append(el("div", {},
          el("code", { text: m.line }),
          el("span", { text: " " + m.reason })));
        if (m.candidates && m.candidates.length) {
          list.append(el("div", { class: "wt-note", text: "　候选：" + m.candidates.join(" · ") }));
        }
      }
      out.append(list);
    }
    if (state.selected.size) {
      out.append(el("button", {
        class: "btn btn-primary mt-3",
        text: "整理这 " + state.selected.size + " 篇",
        onclick: () => { closePanel(); showPanel(); },
      }));
    }
  }

  function openPanel(title) {
    $("#panel").hidden = false;
    $("#panel").classList.add("show");
    $("#scrim").hidden = false;
    $("#panel-title").textContent = title || "整理成书";
  }

  async function showPanel() {
    if (!state.selected.size) return;
    openPanel("整理成书");
    $("#panel-body").replaceChildren(el("div", { class: "wt-status-line" },
      el("div", { class: "spin" }), el("span", { text: "正在准备篇目…" })));

    let data;
    try {
      data = await api("/api/preview", {
        titles: Array.from(state.selected.keys()),
        title: "",
      });
    } catch (e) {
      $("#panel-body").replaceChildren(el("div", { class: "wt-empty" },
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

    body.append(el("div", { class: "wt-field" },
      el("label", { for: "plan-title", text: "书名" }),
      el("input", { id: "plan-title", class: "form-control", value: plan.title, maxlength: "60" }),
    ));

    const vols = el("ul", { class: "wt-list" });
    let vi = 0;
    for (const vol of plan.volumes) {
      vi += 1;
      const sections = vol.sections || [];
      vols.append(el("li", {},
        el("span", { class: "idx", text: String(vi).padStart(2, "0") }),
        el("span", { text: vol.title }),
        el("span", { class: "wt-num ms-auto", text: sections.length + " 节" })));
      for (const sec of sections) {
        vols.append(el("li", {},
          el("span", { class: "idx" }),
          el("span", { class: "wt-num", text: sec.name }),
          el("span", { class: "wt-num ms-auto", text: (sec.pages || []).length + " 页" })));
      }
    }
    body.append(el("div", { class: "wt-field" },
      el("label", { text: "篇目" }), vols));

    const defs = [["pdf", "PDF", "带书签"], ["docx", "Word", "可编辑"], ["epub", "EPUB", "电子书"]];
    const fmts = el("div", { class: "wt-formats" });
    const syncFmt = () => {
      for (const lab of fmts.children) {
        const on = state.formats.has(lab.dataset.key);
        lab.classList.toggle("on", on);
        const box = lab.querySelector("input");
        if (box) box.checked = on;
      }
      if (typeof syncScale === "function") syncScale();
    };
    for (const [key, label, sub] of defs) {
      const lab = el("label", { dataset: { key }, title: sub },
        el("input", { type: "checkbox", checked: state.formats.has(key) }),
        el("span", { text: label }),
        el("small", { class: "wt-num", text: sub }));
      lab.addEventListener("click", (ev) => {
        ev.preventDefault();
        if (state.formats.has(key)) state.formats.delete(key);
        else state.formats.add(key);
        if (!state.formats.size) state.formats.add("pdf");
        syncFmt();
      });
      fmts.append(lab);
    }
    body.append(el("div", { class: "wt-field" },
      el("label", { text: "格式" }),
      fmts,
      el("p", { class: "wt-note", text: "可以同时选多种格式，至少留一种；生成后每种格式各出一份文件。" })));

    // PDF 字号：平板横屏双页时 A4 页面被缩到半个屏宽，字会偏小，这里让用户自己放大。
    // 只缩放字号，版心与页边距不变，所以页数会增加。
    const scales = el("div", { class: "btn-group" });
    const scaleHint = el("p", { class: "wt-note", text: "" });
    const scaleDefs = [[1, "100%", "打印原尺寸"], [1.15, "115%", "略大"], [1.3, "130%", "平板双页"]];

    /* 字号预览：按 PDF 同一套字体/字号/行距 1:1 画一段正文，pt 在浏览器里就是
       1/72 英寸，和 PDF 的 pt 等价，所以这里看到的字就是纸上的字那么大。 */
    const prevBody = el("div", {
      text: "她抬起头，看着灯下那道长长的影子，什么也没说。",
    });
    prevBody.style.cssText = 'font-family:"宋体",SimSun,serif;color:#333;line-height:1.55;margin:2px 0 6px';
    const prevCue = el("div", { text: "（灯光暗下，雨声渐远）" });
    prevCue.style.cssText = 'font-family:"楷体",KaiTi,serif;color:#6b6b6b;line-height:1.55';
    const prevTag = el("div", { text: "" });
    prevTag.style.cssText = "font:11px/1.6 ui-monospace,Menlo,Consolas,monospace;color:#8a8a8a;margin-bottom:6px";
    const prevBox = el("div", {}, prevTag, prevBody, prevCue);
    prevBox.style.cssText = [
      "margin-top:8px", "padding:10px 12px", "background:#fbfaf7", "border:1px solid #e2ddd2",
      "border-radius:6px", "overflow:hidden",
    ].join(";");

    const syncScale = () => {
      for (const b of scales.children) {
        b.classList.toggle("on", parseFloat(b.dataset.scale) === state.fontScale);
      }
      scales.style.opacity = state.formats.has("pdf") ? "1" : "0.45";
      scales.style.pointerEvents = state.formats.has("pdf") ? "" : "none";
      scaleHint.textContent = state.formats.has("pdf")
        ? "只影响 PDF；调大后每行字数变少、页数增加，会覆盖上一次生成的 PDF。"
        : "没选 PDF 时不生效。";
      // 底本字号见 pdf_html.py：正文 11pt、演出提示 9pt
      const body = 11 * state.fontScale;
      const cue = 9 * state.fontScale;
      prevBody.style.fontSize = body.toFixed(2) + "pt";
      prevCue.style.fontSize = cue.toFixed(2) + "pt";
      prevTag.textContent = `正文 ${body.toFixed(1)}pt ≈ ${(body * 0.3528).toFixed(1)}mm　`
        + `演出提示 ${cue.toFixed(1)}pt　行距 1.55（${(body * 1.55).toFixed(1)}pt）`;
      prevBox.style.opacity = state.formats.has("pdf") ? "1" : "0.45";
    };
    for (const [val, label, sub] of scaleDefs) {
      scales.append(el("button", {
        class: "btn", type: "button", dataset: { scale: String(val) }, title: sub,
        onclick: () => {
          state.fontScale = val;
          localStorage.setItem("wt.fontScale", String(val));
          syncScale();
        },
      }, label));
    }
    body.append(el("div", { class: "wt-field" },
      el("label", { text: "PDF 正文字号" }), scales, prevBox, scaleHint));
    syncScale();

    // 整理者署名：默认写，勾掉就完全不留
    const creditChk = el("input", {
      type: "checkbox", checked: state.credit,
      onclick: () => {
        state.credit = creditChk.checked;
        localStorage.setItem("wt.credit", state.credit ? "1" : "0");
      },
    });
    body.append(el("div", { class: "wt-field" },
      el("label", {}, creditChk, el("span", { text: " 写入整理者署名（安巢鸟 · 整理）" })),
      el("p", { class: "wt-note",
        text: "写进 PDF 文档属性、Word 作者、EPUB 的 dc:creator，以及封面落款和凡例那一行；勾掉则这些位置都不出现署名。" })));

    const coverInfo = plan.cover || {};
    const localNames = (coverInfo.files || []).map((f) => f.name);
    if (!state.coverMode) {
      state.coverMode = localNames.length ? "local" : (coverInfo.fetch === false ? "off" : "auto");
    }
    state.coverPicked = state.coverPicked || localNames.length > 0;
    const cbox = el("div", { class: "btn-group" });
    const pick = el("button", { class: "btn btn-sm", type: "button", text: "选择图片…" });
    const hint = el("span", { class: "wt-note", text: "" });
    const fileRow = el("div", { class: "d-flex align-items-center gap-2 mt-2" }, pick, hint);
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
        class: "btn", type: "button", dataset: { mode: key }, title: sub,
        onclick: () => { state.coverMode = key; syncCover(); },
      }, label));
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
    body.append(el("div", { class: "wt-field" },
      el("label", { text: "封面" }), cbox, fileRow, fileInput));
    syncCover();

    body.append(el("p", {
      class: "wt-note",
      text: "共 " + plan.pages + " 个页面，已缓存 " + plan.cached + " 个；缺的会在生成时补齐。",
    }));

    body.append(el("div", { class: "d-flex gap-2 mt-3" },
      el("button", { class: "btn btn-primary flex-fill", text: "开始生成", onclick: startBuild }),
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
    const log = el("div", { class: "wt-logbox" });
    body.replaceChildren(el("div", { class: "d-flex flex-column gap-3" }, view.root, log));
    setStat("生成中", "on");
    state.jobLive = true;
    $("#status-pill").classList.add("act");
    $("#status-pill").title = "点击查看生成进度";

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
        credit: state.credit,
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
          state.jobLive = false;
          // 结束（成功或失败）都把面板带回来，别让结果停在看不见的地方
          $("#panel").hidden = false;
          $("#panel").classList.add("show");
          $("#scrim").hidden = false;
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
      class: "wt-file", role: "button", title: "打开 " + f.name,
      onclick: open,
    },
      el("span", { class: "k", text: f.kind }),
      el("span", { class: "p", text: f.name.replace(/\.(pdf|docx|epub)$/i, "") }),
      el("span", { class: "sz", text: fmtSize(f.size) }),
      el("button", {
        class: "wt-link", text: "打开",
        onclick: (ev) => { ev.stopPropagation(); open(); },
      }),
      extra ? el("button", {
        class: "wt-link", text: "目录",
        onclick: (ev) => {
          ev.stopPropagation();
          api("/api/open", { path: f.path, reveal: true }).catch(e => toast(e.message, "bad"));
        },
      }) : null,
    );
  }

  function renderDone(snap) {
    const body = $("#panel-body");
    body.replaceChildren();
    const files = (snap.result && snap.result.files) || [];
    const cover = coverFile({ files });
    if (cover) {
      body.append(el("div", { class: "wt-field" },
        el("span", { class: "wt-shelf-cover" },
          el("img", { src: thumbUrl(cover.path, 560, 0), alt: "封面预览", loading: "lazy" })),
        el("p", { class: "wt-note", text: cover.name })));
    }
    body.append(el("div", { class: "wt-status-line mt-3" },
      el("span", { class: "badge wt-badge ok", text: "生成完成" }),
      el("span", { text: snap.elapsed + "s · " + files.length + " 个文件" })));

    const list = el("div", { class: "mt-2" });
    for (const f of files) list.append(fileRow(f));
    body.append(list);

    body.append(el("div", { class: "d-flex gap-2 mt-3" },
      el("button", {
        class: "btn", text: "打开输出目录",
        onclick: () => api("/api/open", { path: snap.result.out_dir }).catch(e => toast(e.message, "bad")),
      }),
      el("div", { class: "spacer" }),
      el("button", {
        class: "btn btn-primary", text: "再整理一本",
        onclick: () => {
          closePanel();
          state.selected.clear();
          renderSelection();
          resetSearch(true);
        },
      }),
    ));
    loadLibrary(true);
  }

  function renderFailed(snap) {
    const body = $("#panel-body");
    body.replaceChildren(
      el("div", { class: "wt-status-line" },
        el("span", { class: "badge wt-badge bad", text: "出错了" })),
      el("p", { class: "wt-note", text: snap.error || "未知错误" }),
    );
    const log = (snap.log || []).slice(-20);
    if (log.length) {
      const box = el("div", { class: "wt-logbox mt-2" });
      for (const item of log) box.append(el("div", { text: item.m }));
      body.append(box);
    }
    body.append(el("div", { class: "mt-3" },
      el("button", { class: "btn btn-primary", text: "回到设置再试", onclick: () => renderPlanForm() })));
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

  /* 一行书 = 两个 <tr>：主行 + 展开的文件行，包在同一个 fragment 里返回 */
  function bookCard(item) {
    const pdf = coverFile(item);
    const cover = pdf
      ? el("img", { class: "wt-cover", src: thumbUrl(pdf.path, 460, 0), alt: "", loading: "lazy" })
      : phCover(item, "wt-cover");

    const kinds = [...new Set(item.files.map(f => f.kind))];
    let stamp = "";
    if (item.mtime) {
      const d = new Date(item.mtime * 1000);
      stamp = String(d.getMonth() + 1).padStart(2, "0") + "-" + String(d.getDate()).padStart(2, "0")
        + " " + String(d.getHours()).padStart(2, "0") + ":" + String(d.getMinutes()).padStart(2, "0");
    }

    const filesBox = el("div", {});
    for (const f of item.files) filesBox.append(fileRow(f));
    const filesRow = el("tr", { class: "wt-files", hidden: !state.open.has(item.id) },
      el("td", { colspan: "6" }, filesBox));
    filesRow.addEventListener("click", (ev) => ev.stopPropagation());

    const toggle = el("button", { class: "btn btn-sm btn-ghost-secondary", type: "button" });
    const paint = () => {
      toggle.replaceChildren(document.createTextNode(filesRow.hidden ? "文件" : "收起文件"),
        el("span", { class: "badge wt-badge ms-1", text: String(item.files.length) }));
    };
    paint();
    toggle.onclick = (ev) => {
      ev.stopPropagation();
      filesRow.hidden = !filesRow.hidden;
      if (filesRow.hidden) state.open.delete(item.id); else state.open.add(item.id);
      paint();
    };

    const row = el("tr", {
      dataset: { id: item.id },
      role: "button", tabindex: "0",
      "aria-label": "打开《" + item.title + "》",
      onclick: () => openBook(item),
      oncontextmenu: (ev) => bookMenu(ev, item),
      onkeydown: (ev) => {
        if (ev.key === "Enter" || ev.key === " ") { ev.preventDefault(); openBook(item); }
      },
    },
      el("td", {}, cover),
      el("td", {},
        el("span", { class: "wt-book-title", text: item.title }),
        el("span", { class: "wt-book-dir", text: bookDir(item) })),
      el("td", {}, el("span", { class: "wt-num", text: (item.pages || 0) + " 页" })),
      el("td", {}, kinds.length
        ? kinds.map((k) => el("span", {
            class: "badge wt-badge" + (k === "epub" ? " ok" : ""),
            text: k.toUpperCase(),
          }))
        : el("span", { class: "wt-num", text: "还没有导出" })),
      el("td", {}, el("span", { class: "wt-num", text: stamp })),
      el("td", {},
        el("div", { class: "wt-acts" },
          el("button", {
            class: "btn btn-sm", type: "button", text: "打开",
            onclick: (ev) => { ev.stopPropagation(); openBook(item); },
          }),
          item.files.length ? toggle : null,
          el("button", {
            class: "btn btn-sm btn-ghost-secondary", type: "button", text: "⋯", title: "更多",
            onclick: (ev) => { ev.stopPropagation(); bookMenu(ev, item); },
          }))),
    );

    const frag = document.createDocumentFragment();
    frag.append(row, filesRow);
    return frag;
  }

  function renderLibrary() {
    const box = $("#library-list");
    box.replaceChildren();
    $("#lib-count").textContent = state.recent.length;
    if (!state.recent.length) {
      box.append(el("div", { class: "wt-empty" },
        el("b", { text: "书库还是空的" }),
        el("p", { text: "回到「整理」，搜一个剧情名字，勾选篇目后点「整理成书」。" })));
      return;
    }
    const thead = el("thead", {},
      el("tr", {},
        el("th", { style: "width:44px" }),
        el("th", { text: "书名" }),
        el("th", { style: "width:96px", text: "页数" }),
        el("th", { style: "width:150px", text: "格式" }),
        el("th", { style: "width:110px", text: "时间" }),
        el("th", { style: "width:170px" })));
    const tbody = el("tbody");
    for (const item of state.recent) tbody.append(bookCard(item));
    box.append(el("table", { class: "table wt-table" }, thead, tbody));
  }

  function renderRecent() {
    const box = $("#recent-home");
    box.replaceChildren();
    box.hidden = false;
    if (!state.recent.length) {
      box.append(el("div", { class: "wt-sect" },
        el("div", { class: "wt-sect-head" },
          el("h2", { text: "三步做出一本能读的书" })),
        el("ul", { class: "wt-list" },
          el("li", {}, el("b", { text: "建索引" }),
            el("span", { class: "wt-note", text: "第一次使用会建一次页面索引，只读标题，几秒钟。" })),
          el("li", {}, el("b", { text: "挑篇目" }),
            el("span", { class: "wt-note", text: "搜索勾选，或者「批量加入」一次粘一整段清单。" })),
          el("li", {}, el("b", { text: "整理成书" }),
            el("span", { class: "wt-note", text: "右侧选集点「整理成书」，导出 PDF / Word / EPUB。" }))),
        el("div", { class: "d-flex gap-2 mt-3" },
          el("button", {
            class: "btn btn-primary", text: "试试「画中人」",
            onclick: () => {
              $("#q").value = "画中人";
              doSearch();
            },
          }),
          el("button", { class: "btn", text: "批量加入…", onclick: openBatch })),
      ));
      return;
    }
    box.append(
      el("div", { class: "wt-shelf-head" },
        el("b", { text: "最近整理" }),
        el("span", { text: "右键书卡可换格式或删除" }),
        el("span", { class: "ms-auto" },
          el("button", { class: "wt-link", text: "全部书目 →", onclick: () => showView("library") }))),
    );
    const shelf = state.recent.filter((it) => (it.files || []).length);
    if (!shelf.length) {
      box.append(el("div", { class: "wt-empty" },
        el("b", { text: "还没有导出过书" }),
        el("p", { text: "勾好篇目点「整理成书」，生成后的 PDF / Word / EPUB 会出现在这里；只有计划、还没生成的，在「全部书目」里。" })));
      return;
    }
    const grid = el("div", { class: "wt-shelf-grid" });
    for (const item of shelf.slice(0, 4)) {
      const pdf = coverFile(item);
      const kinds = [...new Set(item.files.map(f => f.kind))].map(k => k.toUpperCase());
      const cover = pdf
        ? el("span", { class: "wt-shelf-cover" },
            el("img", { src: thumbUrl(pdf.path, 340, 0), alt: "", loading: "lazy" }))
        : phCover(item, "wt-shelf-cover");
      grid.append(el("button", {
        class: "wt-shelf-item",
        title: "左键按默认格式打开，右键换格式 / 删除",
        onclick: () => openBook(item),
        oncontextmenu: (ev) => bookMenu(ev, item),
      },
        cover,
        el("span", { class: "wt-shelf-t", text: item.title }),
        el("span", { class: "wt-shelf-m", text: (kinds.join(" · ") || "未导出") + " · " + (item.pages || 0) + " 页" }),
      ));
    }
    box.append(grid);
  }

  // ---------------------------------------------------------------- 实体浏览页
  /* 点实体名 → 打开一个专页：按章节分组列出所有相关篇目 + 交叉链接相关实体。
     数据来自 /api/entity，后端复用别名表+共现树，不需要 LLM。 */
  async function openEntity(name) {
    const view = $("#view-entity");
    if (!view) return;
    showViewEntity();
    const body = $("#entity-body");
    body.replaceChildren(el("div", { class: "wt-searching" },
      el("div", { class: "spin" }), el("span", { text: "加载「" + name + "」…" })));
    let data;
    try {
      data = await api("/api/entity", { name });
    } catch (e) {
      body.replaceChildren(el("div", { class: "wt-empty" },
        el("b", { text: "加载失败" }), el("p", { text: e.message })));
      return;
    }
    renderEntity(data.entity, name);
  }

  function renderEntity(ent, qName) {
    const body = $("#entity-body");
    body.replaceChildren();
    $("#entity-title").textContent = ent.name || qName;
    if (ent.aliases && ent.aliases.length) {
      $("#entity-sub").textContent = "别名：" + ent.aliases.join("、");
    } else {
      $("#entity-sub").textContent = "";
    }

    // 相关实体：交叉链接 chips
    const relBox = $("#entity-related");
    relBox.replaceChildren();
    if (ent.related && ent.related.length) {
      relBox.append(el("span", { text: "相关" }));
      for (const r of ent.related) {
        relBox.append(el("button", {
          text: r.term,
          title: r.term + " · 同篇出现 " + r.docs + " 次",
          onclick: () => openEntity(r.term),
        }));
      }
    }

    // 篇目按章节分组
    const groups = ent.groups || [];
    const tbody = el("tbody");
    for (const g of groups) {
      tbody.append(el("tr", { class: "wt-volrow" },
        el("td", { colspan: "4" },
          el("div", { class: "wt-topic" },
            el("b", { text: g.label }),
            el("span", { text: g.pages.length + " 篇" })))));
      for (const p of g.pages) {
        const row = {
          title: p.title, name: p.name || p.title, code: p.code || "",
          kind: p.code ? "story" : "page",
        };
        const on = state.selected.has(row.title);
        tbody.append(el("tr", {
          class: "wt-row-hit" + (on ? " on" : ""),
          dataset: { title: row.title },
          onclick: () => toggleEntry(row),
        },
          el("td", { class: "wt-check" }, checkBox(row, on)),
          el("td", { class: "wt-name" },
            el("span", { text: row.name }),
            el("span", { class: "wt-path", text: row.title })),
          el("td", { class: "wt-code", text: row.code }),
          el("td", { class: "wt-kind" },
            el("span", { class: "badge wt-badge", text: row.code ? "剧情" : "页面" })),
        ));
      }
    }
    const list = $("#entity-list");
    list.replaceChildren();
    if (!groups.length) {
      list.append(el("div", { class: "wt-empty" },
        el("b", { text: "没有找到相关篇目" }),
        el("p", { text: "这个实体还没收录到别名表，或正文索引里没提到。" })));
    } else {
      list.append(el("div", { class: "card wt-card" },
        el("div", { class: "card-body p-0" },
          el("table", { class: "table wt-table" }, tbody))));
    }

    // 整批加入按钮
    const addAll = $("#entity-add-all");
    addAll.hidden = !groups.length;
    addAll.onclick = () => {
      for (const g of groups) {
        for (const p of g.pages) {
          state.selected.set(p.title, {
            title: p.title, name: p.name || p.title, code: p.code || "",
            kind: p.code ? "story" : "page",
          });
        }
      }
      renderSelection();
      syncRows();
      toast("已加入 " + ent.total + " 篇到选集", "good");
    };
  }

  function showViewEntity() {
    $("#view-home").hidden = true;
    $("#view-library").hidden = true;
    $("#view-settings").hidden = true;
    $("#view-entity").hidden = false;
    $$(".wt-tab").forEach(b => b.classList.remove("on"));
    history.replaceState(null, "", "#entity");
  }

  function closeEntity() {
    $("#view-entity").hidden = true;
    showView("home");
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
      ctxMenu = el("div", { class: "dropdown-menu show", hidden: true, role: "menu" });
      document.body.append(ctxMenu);
      document.addEventListener("click", closeCtx, true);
      document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeCtx(); });
      window.addEventListener("resize", closeCtx);
      document.addEventListener("scroll", closeCtx, true);
    }
    ctxMenu.replaceChildren(...entries.map(e => e.sep
      ? el("div", { class: "dropdown-divider" })
      : el("button", {
          class: "dropdown-item" + (e.danger ? " text-danger" : ""),
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
    const card = el("div", { class: "dlg-card", role: "document" });
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

  /* 点一下 = 按默认格式打开；要挑单个分卷就在书库列表里展开「文件」 */
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
        el("h3", { class: "dlg-title", text: "删除《" + item.title + "》？" }),
        el("button", {
          class: "icon-btn", type: "button", title: "关闭", "aria-label": "关闭", text: "×",
          onclick: closeBookDlg,
        })),
      el("p", { class: "dlg-sub", text: "计划文件和导出的 PDF / Word / EPUB 会一起移除，不能撤销。" }),
      el("div", { class: "dlg-foot" },
        el("button", { class: "btn btn-ghost-secondary", type: "button", text: "取消", onclick: closeBookDlg }),
        el("div", { class: "spacer" }),
        el("button", {
          class: "btn danger", type: "button", text: "确认删除",
          onclick: () => deleteBook(item),
        })),
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

  // ---------------------------------------------------------------- 设置
  function paintOpenKind() {
    $$("#open-kind .btn").forEach(b => b.classList.toggle("on", b.dataset.kind === state.openKind));
  }

  function applySettings(s) {
    if (!s) return;
    if (s.out_root != null) $("#set-root-input").value = s.out_root;
    if (s.root) {
      $("#set-root").textContent = s.root;
      $("#lib-root").textContent = s.root;
    }
    if (s.settings_path) $("#set-settings").textContent = s.settings_path;
    if (s.llm) paintLlm(s.llm);
  }

  function paintLlm(l) {
    if (!l) return;
    if ($("#llm-enabled")) $("#llm-enabled").checked = !!l.enabled;
    if ($("#llm-base")) $("#llm-base").value = l.base_url || "";
    if ($("#llm-model")) $("#llm-model").value = l.model || "";
    if ($("#llm-embed")) $("#llm-embed").value = l.embed_model || "";
    if ($("#llm-key")) {
      $("#llm-key").placeholder = l.has_key ? "已保存，留空不改" : "本地模型留空";
    }
    const st = $("#llm-status");
    if (st) {
      st.textContent = l.configured
        ? (l.enabled ? "已启用" : "已配置，未启用")
        : (l.embed_local ? "本地向量已启用" : "未配置（离线别名扩展）");
    }
    // 本地向量模型：下好了才能建索引，按钮跟着状态换
    const m = l.local || {};
    const mst = $("#model-state");
    if (mst) {
      mst.textContent = m.ready
        ? (m.deps === false
          ? "缺少运行库 onnxruntime / tokenizers"
          : ((l.embed_local ? "已启用" : "已下载，未启用") + " · " + (m.size_mb || 0) + "MB"))
        : "未下载";
    }
    if ($("#model-download")) $("#model-download").hidden = !!m.ready;
    if ($("#model-remove")) $("#model-remove").hidden = !m.ready;
  }

  async function saveLlm() {
    const body = {
      llm_enabled: $("#llm-enabled").checked,
      llm_base_url: $("#llm-base").value.trim(),
      llm_model: $("#llm-model").value.trim(),
      llm_embed_model: $("#llm-embed").value.trim(),
    };
    const key = $("#llm-key").value.trim();
    if (key) body.llm_api_key = key;
    try {
      const r = await api("/api/settings/save", body);
      applySettings(r.settings);
      $("#llm-hint").textContent = "已保存";
      toast("AI 设置已更新", "good");
    } catch (e) {
      toast(e.message, "bad");
    }
  }

  async function testLlm() {
    const btn = $("#llm-test");
    btn.disabled = true;
    $("#llm-hint").textContent = "正在连接…";
    try {
      await saveLlm();
      const r = await api("/api/settings/test", {});
      $("#llm-hint").textContent = r.ok
        ? ("可用：" + (r.reply || "ok") + (r.embed ? " · " + r.embed : "")
           + " · " + r.seconds + "s")
        : ("失败：" + (r.error || "未知错误"));
    } catch (e) {
      $("#llm-hint").textContent = "失败：" + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  async function buildVectors() {
    const btn = $("#llm-embed-build");
    btn.disabled = true;
    $("#llm-hint").textContent = "已提交，后台向量化…";
    try {
      await saveLlm();
      const r = await api("/api/llm/embed-build", {});
      $("#llm-hint").textContent = r.ok ? "向量索引任务已开始" : ("失败：" + (r.error || ""));
    } catch (e) {
      $("#llm-hint").textContent = "失败：" + e.message;
    } finally {
      btn.disabled = false;
    }
  }

  async function downloadModel() {
    const btn = $("#model-download");
    btn.disabled = true;
    $("#model-hint").textContent = "提交下载…";
    try {
      const r = await api("/api/model/download", {});
      if (!r.ok) throw new Error(r.error || "下载失败");
      followJob(r.job, {
        onUpdate: (snap) => {
          $("#model-hint").textContent = snap.phase
            ? ("[" + snap.phase + "] " + (snap.message || ""))
            : (snap.message || "下载中…");
        },
        onDone: async (snap) => {
          btn.disabled = false;
          if (snap.status === "done") {
            $("#model-hint").textContent = "模型已就绪，接着点上面的「建立向量索引」";
            toast("本地向量模型已下载", "good");
            applySettings((await api("/api/settings")).settings);
          } else {
            $("#model-hint").textContent = "失败：" + (snap.error || "下载中断");
            toast(snap.error || "下载失败", "bad");
          }
        },
      });
    } catch (e) {
      btn.disabled = false;
      $("#model-hint").textContent = "失败：" + e.message;
      toast(e.message, "bad");
    }
  }

  async function removeModel() {
    const btn = $("#model-remove");
    btn.disabled = true;
    try {
      const r = await api("/api/model/remove", {});
      applySettings(r.settings || (await api("/api/settings")).settings);
      $("#model-hint").textContent = "已删除，语义检索退回关键词层";
      toast("本地模型已删除", "good");
    } catch (e) {
      $("#model-hint").textContent = "失败：" + e.message;
    } finally {
      btn.disabled = false;
    }
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
    for (const [id, fn] of [["llm-save", saveLlm], ["llm-test", testLlm],
                            ["llm-embed-build", buildVectors],
                            ["model-download", downloadModel],
                            ["model-remove", removeModel]]) {
      const b = $("#" + id);
      if (b) b.addEventListener("click", fn);
    }
    try {
      const data = await api("/api/state");
      state.indexed = data.indexed || 0;
      state.recent = data.recent || [];
      $("#lib-root").textContent = data.root;
      $("#set-version").textContent = data.app.name + " " + data.app.version;
      if (data.settings) applySettings(data.settings);
      setHeroIndex(state.indexed);
      renderSrcInfo();
      renderSelection();
      renderLibrary();
      renderRecent();
      loadPlans();
      if (!state.indexed) maybePromptIndex();
      else setStat("就绪");
      const wantView = location.hash.replace("#", "");
      if (VIEWS.includes(wantView) && wantView !== "home") showView(wantView);
    } catch (e) {
      toast("初始化失败：" + e.message, "bad");
    }
  }

  // ---------------------------------------------------------------- 事件
  /* 结果区的键盘光标：↑↓ 挪，Enter 勾选，Ctrl+Enter 直接去整理。
     鼠标与键盘都走同一个 toggleEntry，不会有第二套选中状态。 */
  function moveCursor(delta) {
    const rows = $$(".wt-row-hit");
    if (!rows.length) return;
    const next = state.cursor < 0
      ? (delta > 0 ? 0 : rows.length - 1)
      : Math.max(0, Math.min(rows.length - 1, state.cursor + delta));
    state.cursor = next;
    rows.forEach((r, i) => r.classList.toggle("cursor", i === next));
    rows[next].scrollIntoView({ block: "nearest" });
  }

  function cursorRow() {
    const rows = $$(".wt-row-hit");
    return rows[state.cursor] || null;
  }

  document.addEventListener("keydown", (ev) => {
    const tag = (document.activeElement && document.activeElement.tagName) || "";
    const typing = tag === "INPUT" || tag === "TEXTAREA";
    if ((ev.ctrlKey || ev.metaKey) && ev.key.toLowerCase() === "k") {
      ev.preventDefault(); showView("home"); $("#q").focus(); $("#q").select();
    }
    if (ev.key === "/" && !typing && !ev.ctrlKey && !ev.metaKey) {
      ev.preventDefault(); showView("home"); $("#q").focus();
    }
    if (ev.key === "Escape") {
      if (!$("#panel").hidden) closePanel();
      else if ($("#q").value) resetSearch(true);
    }
  });

  /* 打字不发请求，回车才搜：边打边搜会让结果列表一直跳，手感很差 */
  $("#q").addEventListener("keydown", (ev) => {
    if (ev.key === "ArrowDown") { ev.preventDefault(); moveCursor(1); return; }
    if (ev.key === "ArrowUp") { ev.preventDefault(); moveCursor(-1); return; }
    if (ev.key !== "Enter") return;
    ev.preventDefault();
    const row = cursorRow();
    if (ev.ctrlKey || ev.metaKey) {
      if (row && !row.classList.contains("on")) row.click();
      if (state.selected.size) showPanel();
      return;
    }
    if (row) { row.click(); return; }          // 勾选 / 取消当前行，光标不动
    if (state.results.length === 1) { toggleEntry(state.results[0]); showPanel(); }
    else doSearch();
  });
  $("#clear").addEventListener("click", () => resetSearch(true));
  $("#hints").addEventListener("click", (ev) => {
    const btn = ev.target.closest("[data-q]");
    if (!btn) return;
    $("#q").value = btn.dataset.q;
    doSearch();
  });
  $("#status-pill").addEventListener("click", reopenJob);
  $("#sel-go").addEventListener("click", showPanel);
  $("#batch-open").addEventListener("click", openBatch);
  $("#panel-close").addEventListener("click", closePanel);
  $("#scrim").addEventListener("click", closePanel);
  $("#build-index").addEventListener("click", () => startIndex(true, false));
  $$(".wt-tab").forEach(b => b.addEventListener("click", () => showView(b.dataset.view)));
  const eb = $("#entity-back");
  if (eb) eb.addEventListener("click", closeEntity);
  $("#brand").addEventListener("click", (ev) => { ev.preventDefault(); showView("home"); });
  $("#root-save").addEventListener("click", saveRoot);
  $("#root-pick").addEventListener("click", pickRoot);
  $("#root-reset").addEventListener("click", () => {
    $("#set-root-input").value = "";
    saveRoot();
  });
  $$("#open-kind .btn").forEach(b => b.addEventListener("click", () => {
    state.openKind = b.dataset.kind;
    localStorage.setItem("wt.openKind", state.openKind);
    paintOpenKind();
  }));
  paintOpenKind();
  $$("[data-open]").forEach(b => b.addEventListener("click", async () => {
    const kind = b.dataset.open;
    try {
      const path = kind === "root" ? $("#set-root").textContent : $("#set-settings").textContent;
      await api("/api/open", { path, reveal: true });
    } catch (e) { toast(e.message, "bad"); }
  }));

  init();
})();
