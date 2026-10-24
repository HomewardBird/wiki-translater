# GUI v8 方案：套现成设计系统 + 一层自定义主题

## 0. 结论

- **不再手写组件**。骨架用 [Tabler](https://tabler.io/license)（MIT、Bootstrap 5、纯 HTML/CSS/JS、无构建、暗色与 RTL 内置、表格/表单/offcanvas/progress 齐全），本项目只加一层约 120 行的主题覆盖（`skin.css`）。
- **保留**：后端 API 全部、`app.js` 的交互逻辑（键盘光标、`state.jobLive`、`resetSearch`、批量草稿、Map 选中模型）、现有文案。
- **换掉**：`index.html` 的类名结构、`app.css` 里自造的组件样式（hero 排版、`--r-3/--r-4` 大圆角、发光阴影、01/02/03 编号、`.hero-steps` 教程卡）、各 render 函数的 DOM 结构。
- 与"再手写一版"的差别只有一句：上一版密度和层级由我发明，这一版由用了六年的框架保证，我只决定气质。

## 1. 候选对比

| 方案 | 需构建 | 离线可用 | 许可 | 组件覆盖 | 气质 | 改造量 |
|---|---|---|---|---|---|---|
| **Tabler**（推荐） | 否，单 css+js | 是，vendor 进仓库 | MIT | 表格/表单/offcanvas/progress/nav/暗色全有 | 干净的管理工具，需压 Bootstrap 味 | 中 |
| IBM Carbon v11 | 需编译 css（有预构建） | 是 | Apache-2.0 | 最全，数据密集最专业 | IBM 冷灰，偏企业 | 中偏大 |
| GitHub Primer CSS | 否 | 是 | MIT | 表格/表单强，缺浮层 | GitHub 味很重 | 中 |
| Pico / Simple.css | 否 | 是 | MIT | 只有语义化基础，浮层/进度/表格交互要自己写 | 干净但太素 | 低 → 但要补组件，回到手写 |

Tabler 是 MIT、基于 Bootstrap 5 的开源 UI kit，卡片/表格/表单/导航与页面布局开箱即用且自带暗色（[getting-started](https://docs-dev.tabler.io/ui/getting-started.md)、[license](https://tabler.io/license)）。Shoelace 已改名为 Web Awesome，旧版停更，其新版许可未核实 → 不选。

## 2. 视觉基调（简洁靠纪律，不靠装饰）

| 项 | 值 |
|---|---|
| 背景 / 面板 / 线 | `#0D0E10` / `#131518` / `rgba(255,255,255,.07)` |
| 强调色（唯一） | `#D9A94A`；成功 `#7FB894`；危险 `#D98078` |
| 字体 | 界面 sans（Segoe UI Variable / 微软雅黑）；**serif 只出现在书名、卷名** |
| 字号 | 11 / 12.5 / 14 / 17 / 22，行高 1.5 |
| 网格 / 圆角 | 8px 网格；圆角 6px（按钮 6、输入 6、卡片 8） |
| 阴影 | 0（层次靠 1px 线与背景明度差） |
| 动效 | 只留 120ms 的颜色与透明度；**禁止位移、发光、渐变描边** |
| 空态 | 一句话 + 一个动作；不做"三步教程卡" |
| 文字 | 控件自己说明自己，**不给交互写解释句** |

## 3. 三视图结构

**首页（整理）**
- 顶栏 44px：logo + 三个 tab + 右侧状态灯（生成中可点）
- 搜索行：`input-group`（搜索框 + 批量加入按钮），右侧一行索引状态
- 结果：`table table-sm table-hover`，四列 = 名称（serif 14）/ 代号（mono，dim）/ 类型（badge）/ 勾选
- 选中后表格上方 **sticky 操作条**：`已选 N 篇` · 调序 · 清空 · `整理成书`（Immich 式，选中才出现）
- 选集详情：右侧 `offcanvas-end`，`1 一钳之息 / 2 初醒` 行 + 拖拽手柄 + 生成设置

**书库**
- `table`：封面 40×56 缩略 / 书名（serif）/ 页数 / 格式 badges / 时间 / 操作（打开、文件、删除）
- 表头可点排序；顶部一行计数 + 目录路径

**设置**
- `list-group list-group-flush` 行式：左 `label`，右控件，说明 11px 在下一行
- 运行环境区只读，等宽字体

**生成面板**
- 右侧 `offcanvas-end`：head = 书名输入；body = 篇目折叠列表 + 格式（checkbox 组）+ 字号 + 封面；foot 固定 `开始生成`
- 进度：`progress` + 阶段文本 + `已用 00:42 · 约剩 01:10`；日志折叠在最后

## 4. 落地阶段（预计 2.5–3 小时）

| 阶段 | 内容 | 耗时 |
|---|---|---|
| P0 | `git commit` checkpoint 保留 v6；开分支 `gui-v8` | 5m |
| P1 | vendor `tabler.min.css` / `tabler.min.js` 到 `web/vendor/`，重写 `index.html` 骨架，写 `skin.css` | 30m |
| P2 | 首页：搜索行 + 结果表 + sticky 操作条 + 选集 offcanvas | 60m |
| P3 | 书库表 + 设置行式表单 | 45m |
| P4 | 生成 offcanvas + 进度 + 右键菜单 + 删除确认框 | 45m |
| P5 | 截图验收 + 交互回归 | 20m |

## 5. 验收

- 三视图 × 1440 / 1280 / 390 截图，`node --check` 通过，console 0 error
- 交互回归清单：↑↓ / Enter / Ctrl+Enter；搜索转圈；选中浮条出现与消失；调序；offcanvas 开合与 Esc；生成中收起 + 状态灯点回；批量草稿保留；删除二次确认；设置保存
- 目测否决项：出现位移 hover、发光描边、≥14px 圆角、01/02/03 编号、解释交互的小字

## 6. 风险与约束

- **Bootstrap 味**：靠 `skin.css` 压（阴影清零、半径 6px、行高与字号、配色、表格边框），这一步不能省。
- **离线**：Tabler 文件全部 vendor 进仓库，不走 CDN（本地工具不能依赖外网）。
- **体积**：多一个百 KB 级的 CSS 与 JS，不需要 node/构建。
- **回退**：v6 与 v7 都只在工作区、从未提交；动手前必须先 `git commit`，否则再改一次就真丢。

## 7. 需拍板

1. 骨架选 **Tabler**（推荐）/ Carbon / Primer？
2. 是否接受把第三方 CSS/JS vendor 进仓库（离线可用，代价是仓库多几十万字符）？
3. 选集放 **右侧 offcanvas**（信息全，需一步展开）还是底部浮条直接展开（更快，占高度）？
