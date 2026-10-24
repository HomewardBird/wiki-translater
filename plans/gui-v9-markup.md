# GUI v9 · app.js 标记移植规格（Tabler 版）

`index.html` 与 `skin.css` 已重写完成并生效。`app.js` 只改 **DOM 生成部分**，交互逻辑、事件绑定、状态机、API 调用一律不动。

## 铁律

1. **不改行为**：函数签名、`state`、fetch 路径、事件监听、键盘逻辑、SSE、错误处理全部保留原文。
2. **只换标记**：把 `el("div", {class:"row-hit"})` 这类旧 class 换成下表的新 class；结构可以是 `table/tr/td` 或 `ul/li`。
3. **ID 全部保留**：`#q #clear #hints #hero-index #batch-open #index-banner #index-prog #build-index #results #recent-home #plan-block #plan-list #sel-count #sel-list #sel-empty #sel-go #src-info-body #lib-count #lib-root #library-list #set-root #set-root-input #set-settings #set-version #root-hint #root-pick #root-reset #root-save #open-kind #status-pill #panel #panel-body #panel-title #panel-close #scrim #toasts #tabs #brand #view-home` 以及 JS 内注入的 `#batch-text #batch-result #plan-title`。
4. **废弃旧 class**：`.row-hit .sel-item .hit-* .tick .dym .shelf* .book-card .plan-card .side-* .btn.primary .btn.ghost .sub .bad .fa .seg .seg-item .track .job-* .status-line .logbox` 在 `skin.css` 里已不存在，必须换成下表。
5. `.tab` → `.wt-tab`；`#panel` 的显隐：`hidden` 属性照旧，但**打开时同时 `classList.add("show")`，关闭时 `remove("show")`**（Tabler offcanvas 需要 `.show`）。
6. `#status-pill` 现在是 `<button><i></i><span>文本</span></button>`：`setStat` 改 `span.textContent`，类名用 `busy` / `bad` / 空。
7. 写完跑 `node --check wiki_transporter/gui/web/app.js`，再 `grep -n 'row-hit\|sel-item\|hit-name\|hit-path\|hit-main\|hit-meta\|shelf-item\|book-card\|plan-card\|\.tab\b\|#searchbox' app.js` 确认无残留（`#searchbox` 已从 HTML 删除，若 JS 引用需改为 `#q` 或去掉）。

## 目标标记（逐区）

### 1. 结果区 `renderResults` / `hitRow` / `smartBlock`

```
#results（静态容器，flex 列）
  .wt-res-head              ← 结果标题行
      <b>搜索结果</b> <span>N 个相关页面</span> .ms-auto <button class="wt-selink" hidden>已选 N 篇 →</button>
  <div class="card wt-card"><div class="card-body p-0"><table class="table wt-table"><tbody>
      <tr class="wt-row-hit" data-title="…">
        <td class="wt-check"><input type="checkbox" checked></td>
        <td class="wt-name">篇名<span class="wt-path">完整标题</span></td>
        <td class="wt-code">AF-ST1</td>
        <td class="wt-kind"><span class="badge wt-badge">剧情</span></td>
      </tr>
  </tbody></table></div></div>
```
- 选中态：`tr.wt-row-hit.on`，并把 `input` 打勾（`toggleEntry/addEntry` 里同步整行的 `.on` 和 `input.checked`，选择器 `'.wt-row-hit[data-title="…"]'`）。
- `did_you_mean` → `<div class="wt-dym"><span>没有完全匹配 —— 是不是 <b>名</b>？</span><span class="ms-auto"></span><button class="btn btn-sm btn-primary">用这个</button><button class="btn btn-sm btn-ghost-secondary">再看看</button></div>`
- 主题组头 → `<div class="wt-topic"><b>已按主题展开：…</b><span class="ms-auto"></span><button class="btn btn-sm btn-ghost-secondary">整组加入</button></div>`
- 空态 → `<div class="wt-empty"><b>…</b><p>…</p><div class="wt-empty-acts">按钮</div></div>`（按钮 `btn btn-sm` / `btn btn-sm btn-primary`）
- 正文命中块 → `<div class="card wt-card wt-smart">`
  - header：`<div class="card-header"><b class="card-title">正文提到「…」的剧情</b><span class="wt-head-note">N 篇 · 已索引 M 篇</span></div>`
  - 相关词：`<div class="wt-chips"><span>相关</span><button>词</button>…</div>`
  - 命中行：同一张 `table.wt-table` + `tr.wt-row-hit`，`wt-name` 里追加 `<span class="wt-snip">…</span>`（snippet 仍是 innerHTML）
  - 时间线：`<div class="wt-smart-line"><span>阅读顺序</span><span>AF-ST1 → …</span></div>`

### 2. 选集 `renderSelection`

```
<ol class="wt-sel-list" id="sel-list">
  <li><span class="idx">01</span><span class="name" title="完整标题">篇名</span>
      <span class="mv"><button class="mv-btn" title="上移">↑</button><button class="mv-btn" title="下移">↓</button></span>
      <button class="x" title="移除">×</button></li>
```
序号两位补零、`#sel-empty` 文案不动、`#sel-go` 里 `<span>` 文案逻辑不动。

### 3. 来源与索引 `renderSrcInfo`

```
<div class="wt-src-line">明日方舟 · PRTS</div><code>https://prts.wiki/api.php</code>
<div class="wt-kv"><span>索引</span><b>2,127 页</b></div>
<div class="wt-kv"><span>更新</span><b>刚刚 / 3 天前</b></div>
<div class="wt-src-acts"><button class="btn btn-sm" id="rebuild-index">重建索引</button></div>
```

### 4. 整套整理 `renderPlans`

```
<button class="wt-plan-row"><span class="t">明日方舟 · 罗萨剧情文字稿</span><span class="m">10 卷 · 142 页</span></button>
```
（不再有 01/02 装饰序号、不再用卡片；沿用原来的点击行为）

### 5. 首页最近 `renderRecent` + `bookCard`

```
<div class="wt-shelf-head"><b>最近整理</b><span>右键书卡可换格式或删除</span><span class="ms-auto"><button class="wt-link">全部书目 →</button></span></div>
<div class="wt-shelf-grid">
  <button class="wt-shelf-item"><span class="wt-shelf-cover"><img src=thumb></span>
    <span class="wt-shelf-t">书名</span><span class="wt-shelf-m">PDF · 142 页</span></button>
</div>
```
无封面：`<span class="wt-shelf-cover ph">书名首 4 字</span>`。

### 6. 书库 `renderLibrary` + `fileRow`

```
<div class="wt-books"><table class="table wt-table">
 <thead><tr><th style="width:44px"></th><th>书名</th><th style="width:96px">页数</th><th style="width:150px">格式</th><th style="width:132px">时间</th><th style="width:150px"></th></tr></thead>
 <tbody>
  <tr data-id="…">
    <td><img class="wt-cover" src=thumb> | <span class="wt-cover ph">…</span></td>
    <td><span class="wt-book-title">书名</span><span class="wt-book-dir">目录</span></td>
    <td><span class="wt-num">142 页 · 10 卷</span></td>
    <td><span class="badge wt-badge">PDF</span> <span class="badge wt-badge ok">EPUB</span></td>
    <td><span class="wt-num">10-23 21:44</span></td>
    <td><div class="wt-acts"><button class="btn btn-sm">打开</button><button class="btn btn-sm btn-ghost-secondary">文件</button><button class="btn btn-sm btn-ghost-secondary">⋯</button></div></td>
  </tr>
  <tr class="wt-files" hidden><td colspan="6">
     <div class="wt-file"><span class="k">PDF</span><span class="p">路径</span><span class="sz">12.3 MB</span><button class="wt-link">打开</button></div>
  </td></tr>
 </tbody></table></div>
```
空库：`<div class="wt-empty"><b>书库还是空的</b><p>…</p></div>`。

### 7. 生成面板 `openPanel` / `renderPlanForm` / `jobView` / `renderDone` / `renderFailed`

```
<div class="wt-field"><label for="plan-title">书名</label><input id="plan-title" class="form-control"></div>
<div class="wt-field"><label>篇目</label><ul class="wt-list"><li><span class="idx">01</span><span>篇名</span><button class="drop">×</button></li></ul></div>
<div class="wt-field"><label>格式</label><div class="wt-formats">
  <label class="on"><input type="checkbox" checked><span>PDF</span></label> … </div></div>
<div class="wt-field"><label>正文字号</label><div class="btn-group">…</div></div>
```
进度：
```
<div class="wt-job">
  <div class="wt-job-head"><b>抓取原文</b><span>第 3 / 142 页</span><span class="wt-job-pct">37%</span></div>
  <div class="progress"><div class="progress-bar" style="width:37%"></div></div>
  <div class="wt-job-foot"><span>46 / 142 页</span><span class="right">已用 00:42 · 约剩 01:10</span></div>
</div>
```
（`indet` 类加在 `.progress` 上；完成/失败用 `.wt-status-line` + `.badge wt-badge ok|bad`；日志用 `<div class="wt-logbox">`）

### 8. 批量加入（`openBatch`）

textarea 用 `class="form-control"` + `rows="10"`，按钮 `btn btn-primary` / `btn btn-ghost-secondary`，预览表用 `table.wt-table`。

### 9. toast

`el("div", {class:"toast " + kind, text: msg})` 保留；kind 只用 `"ok"` / `"bad"` / `""`。

## 验收

- `node --check app.js` 通过
- 上述 grep 无旧 class 残留
- `curl -s http://127.0.0.1:8791/ | head -20` 返回新 index.html（含 `skin.css`）
- 汇报：改了哪些函数、每处新标记的一行摘要、grep 与 node --check 的实际输出
