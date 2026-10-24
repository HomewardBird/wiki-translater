# Wiki Transporter

把 PRTS（明日方舟 Wiki）上的剧情页 wikitext 抓下来，清洗成纯文本，再排成分卷的 Word / PDF / EPUB，附一份「全一册」合订本。

仓库带一份「岁家剧情」计划：10 卷 / 142 篇 / 约 67 万字，合订本 PDF 1208 页。

MIT License · Python 3.9+ · 运行依赖只有 `python-docx`（PDF 收尾用 `pymupdf`，不装也能出 PDF）

## 30 秒上手

### 下载 exe（Windows，免安装）

Release 里是三个文件（1.6.0 起拆成命令行版和纯图形版，各约 68 MB）：

| 文件 | 内容 |
|---|---|
| `WikiTransporter-GUI.exe` | **图形版**，不弹控制台窗口。双击直接起界面：本机起一个服务，再用 Chrome / Edge 开一个应用窗口（Windows 自带 Edge 就够） |
| `wiki-transporter.exe` | **命令行版**，带控制台。双击（不带参数）同样是图形界面；在终端里可以跑 `doctor` / `search` / `all` 等子命令，例如 `wiki-transporter.exe doctor` |
| `sui-story.zip` | 已经排好的成品：`sui/`（PDF / Word / EPUB，10 卷 + 全一册）以及计划文件 `sui.json`。这个附件留在 v1.3.0 的 Release 里，不随每个版本重发 |

两个 exe 都是单文件、免安装：不装 Python、不写注册表，删掉 exe 就算卸载，且都不带任何剧情文本和计划文件。
图形版为了不弹黑框去掉了控制台，崩溃信息会写在 exe 同目录的 `gui-error.log`；命令行版在终端里能直接看到报错。

<!-- checksum:begin -->
两个 exe 的 SHA256（自己核对一下再双击，值不对就别运行）：

- `wiki-transporter.exe` — `5eca8d3f7809cf46c813ea505b6b18b26c1a0a8a8869d92478d625379570896d`
- `WikiTransporter-GUI.exe` — `836693856b8ebcc6e3d77c8c9351a4dc9c863a5e8fb85170b24c58ae8ff4518e`

<!-- checksum:end -->

界面里的流程就三步：

1. 「建立索引」——只抓页面标题，几秒钟；
2. 搜索并勾选要收进书里的篇目；
3. 「整理成书」——一次跑完抓取 → 解析 → Word/EPUB → PDF。

勾好的篇目会存成计划文件（`%LOCALAPPDATA%\WikiTransporter\plans\<id>.json`），之后可以反复生成。

想直接要「岁家剧情」这套：把压缩包里的 `sui.json` 放到 exe 同级的 `plans\` 目录（没有就新建），重启 exe。（夕宝真的很可爱吧！！！）
书架里会出现这本书，点一下就能重新印全套。
压缩包里的 `sui/` 是成品，不装 exe 也能直接读。

### 从源码跑

```bash
git clone https://github.com/HomewardBird/wiki-transporter.git
cd wiki-transporter
pip install -r requirements.txt

python -m wiki_transporter doctor     # 自检：Python / python-docx / Chromium / pymupdf
python -m wiki_transporter all        # 抓取 + 生成 Word/EPUB + 导出 PDF
```

`pip install .` 之后可以用 `wiki-transporter all`；不带参数运行（或 `wiki-transporter gui`）打开图形界面。

Windows 上也可以直接双击 `scripts\一键构建.bat`，它会装依赖再跑一遍 `all`。

## 只做 PRTS

1.5.0 起本项目只对接 PRTS：早期版本带的 BWIKI（原神 / 重返未来：1999 / 鸣潮）来源、通用 wikitext 解析 `generic.py`、
以及 `source add` 这类来源管理命令都已移除。旧计划文件里残留的 `source_id` 会被忽略，一律按 PRTS 抓取和解析。

| 站点 | 剧情索引 | 解析 |
|---|---|---|
| PRTS（prts.wiki） | 分类：剧情 / 干员密录 | 专用模板解析，演出提示最完整 |

抓取走 `https://prts.wiki/api.php`，默认 0.35 秒/页；站点偶发 429 / 5xx 会退避重试 3 次。
文字与美术的权利归属、抓取方式与使用条件写在 [NOTICE.md](NOTICE.md)；本仓库不含任何剧情原文。

## 生成结果

一本书输出三套格式，按卷拆分，外加合订本：

```
out/sui/
├── docx/
│   ├── 00_总目录与说明.docx
│   ├── 01_卷一_洪炉示岁.docx
│   ├── …
│   ├── 10_卷十_辞岁行.docx
│   └── 全一册.docx              # 目录横跨全书、页码连续
├── pdf/                         # 与 docx 同名，含 PDF 书签与页脚页码
└── epub/
    ├── 01_卷一_洪炉示岁.epub
    ├── …
    └── 全一册.epub              # 两级导航：卷 → 篇

cache/raw/sui/                   # 抓下来的原始 wikitext
cache/img/                       # 下载的立绘 / 头像，以及本地生成的占位封面
build/sui/books.json             # 解析后的结构化中间产物，可二次加工
```

单本书的内页结构：

```
封面 → 凡例 → 目录（真页码） → 正文
                              ├ 卷扉页（合订本：卷次 / 卷名 / 括注）
                              ├ 节标题（一行浅灰小字）
                              ├ 篇名（居中大字 + 一条细线，干员密录配立绘）
                              ├ 小标题（「第一关（前）」「07上」，居中灰黑）
                              ├ 旁白（楷体，首行缩进 2 字）
                              ├ 台词（说话人着色成一列，折行对齐台词列）
                              ├ 演出提示（居中浅灰小字：音乐 · 停 / 换景）
                              └ 【选项】A ／ B（楷体灰小字）
```

正文版式：宋体 11pt、行距 1.55、A4 边距 2.9cm（每行约 38 字）、每篇另起页。
EPUB 的内联样式带 `!important`，不加载出版方 CSS 的阅读器（KoodoReader 等）也能保住说话人配色与字号。

## 封面

每卷一张封面图，取该卷出场最多的角色（密录优先用计划里 `portrait` 声明的干员），
按 `文件:立绘_<角色>_1.png`、`立绘_<角色>_2.png`、`半身像_<角色>_1.png` 的命名去下载，
存进 `cache/img/`，再次生成时直接复用。

拿到图之后按图的性质分两种版式：

- **透明底立绘**（PRTS 那套）：满版叠在封面右侧，深色渐变压上来，和标题同处一个画面；
- **不透明图 / 画面上自带字**：放进右下角一块竖版画框，边缘渐隐，不压标题。

一张都拿不到时，用本机生成一张**占位封面**：金色竖向渐变 + 一个简笔剪影，
不联网、不含任何第三方美术。占位图在 `cache/img/占位_prts.png`，同目录还有 SVG 版。

Word 只能嵌位图，所以占位图在构建时转成 PNG；`pymupdf` 没装时 Word 退回纯字封面，PDF / EPUB 不受影响。

## 语义检索（可选，纯本地）

搜索默认是字符串层：精确、子序列、滑窗形近（`特蕾西娅`↔`特蕾西亚`）加 `difflib`。想按**意思**找
（「干员的个人剧情」→「干员密录」），可以在 `设置 → AI 智能检索 → 本地向量模型` 点「下载模型」：

- 模型是 `bge-small-zh-v1.5` 的 ONNX INT8 版，约 24MB，纯 CPU 跑，不联网、不要 API Key，
  剧情文本不出本机；文件放在 `<用户目录>/models/bge-small-zh-v1.5/`；
- 下载完再点「建立向量索引」，给已索引的篇目逐条编码（首次几分钟，之后增量）；
- 检索结果由「关键词层 + 向量层」融合排序；删除模型后立刻退回纯关键词层，不会报错。

依赖 `onnxruntime` 与 `tokenizers`（`pip install -e ".[semantic]"`）；没装时设置页会显示
「缺少运行库」，其余功能照常。想改用 API 提供的向量模型，就在同一页的「向量模型」里填模型名。

## 搜索是怎么找剧情的

搜索结果分两层：**主题召回**（整卷清单）和**正文命中**（哪几篇正文提到了这个词）。

主题召回靠 `themes.py` 从 PRTS 结构化数据里生成的「活动 → 篇目」表，所以搜「将进酒」
会直接把这一卷排好的篇目摆出来，不需要正文里逐字出现。

正文命中走 `smart.py` 的 FTS5 trigram 索引，2 字词走 LIKE 兜底；`deep.py` 在其上加
概念层（别名表、共现扩展、可选向量融合）。别名表里「岁家」展开成洪炉示岁 / 画中人 /
将进酒 / 登临意 / 怀黍离 / 相见欢 / 镜中集 / 辞岁行 等活动名，加几位角色的名字。

**降噪规则**（1.6.0 起）：一个活动只要在正文里被提到，就把整卷补全，这个行为会把一句
客串台词放大成噪声——搜「岁家」时火蓝之心只出现一句台词，补全后 1 篇变 25 篇。现在的门槛是：

- 活动名出现在查询别名里（如「岁家」→ 洪炉示岁）→ 整卷补全，不看比例；
- 否则该活动需有 **≥ 总篇数一半（向上取整）** 的篇目实打实命中（`score > 0.5`，
  即 3 字以上的词真出现，而不是 2 字角色名蹭到一句）才补全；
- 不达标的活动，它的散落命中整条丢掉；主线篇目只留真命中；
- 过滤后一条不剩时，正文面板整块不渲染（不会出现空标题）。

判据里的 `0.40` 是短别名 LIKE 的固定权重，`0.5` 是「真命中」的下界，两者都在
`smart.py` / `deep.py` 里有注释。

## 命令参考

```
wiki-transporter doctor                      环境自检（Python / python-docx / Chromium / pymupdf）
wiki-transporter fetch  [--force] [--delay]  抓取原始 wikitext 到 cache/（可断点续抓）
wiki-transporter build  [--volumes 1-3,5] [--no-combined]
                                             生成 Word + EPUB（默认含全一册合订本）
wiki-transporter pdf    [--volumes ...] [--engine auto|chrome|word|soffice]
                                             生成 PDF（默认 Chromium 引擎，目录页码与书签齐全）
wiki-transporter all                         一条龙：fetch → build → pdf
wiki-transporter search 关键词               在命令行里搜索剧情页面（--remote 叠加站内搜索）
wiki-transporter config [--out 目录]         查看/修改书籍存放位置
wiki-transporter gui                         打开图形界面（不带参数运行也是这个）
```

通用参数：

| 参数 | 说明 |
|---|---|
| `--plan sui` | 选择计划，对应 `plans/<name>.json`，默认 `sui` |
| `--root PATH` | 项目根目录（默认从当前目录向上找含 `plans/` 的目录） |
| `--volumes "1-3,5"` | 只处理指定卷，下标从 1 开始 |
| `--no-combined` | 不生成 `全一册` 合订本 |
| `--allow-missing` | 缺页只警告不中止 |
| `--delay 0.35` | 抓取间隔秒数，别低于 0.2 |

```bash
# 只重排第 1、5 卷并导出 PDF
python -m wiki_transporter pdf --volumes 1,5

# 抓一次，之后反复调排版（命中缓存，秒级）
python -m wiki_transporter fetch
python -m wiki_transporter build
```

## 计划文件

一个计划就是一份 JSON，描述这本书的卷次和篇目。`plans/sui.json` 是完整例子：

```json
{
  "title": "明日方舟 · 某某活动剧情文字稿",
  "fanli": ["凡例第一条", "凡例第二条"],
  "volumes": [
    {
      "title": "卷一　某某活动",
      "sections": [
        {
          "name": "某某活动",
          "kind": "story",
          "pages": ["XX-ST-1 开篇/NBT", "XX-1 第一关/BEG", "XX-1 第一关/END"]
        },
        {
          "name": "干员密录·某某",
          "kind": "secret",
          "portrait": "某某",
          "pages": ["某某/干员密录/1"]
        }
      ]
    }
  ]
}
```

- `pages` 填 wiki 页面名，就是 `https://prts.wiki/w/` 后面的部分，`/` 不要转义；
- `portrait` 给这一节配干员头像，也是封面主角的首选；
- `kind` 决定篇名的取法：
  - `story`：普通剧情，`XXX/BEG` →「（行动前）」、`/END` →「（行动后）」、`/NBT` 去掉后缀；
  - `secret`：干员密录，篇名取页面里的 `<标题>`，取不到用 `name`；
  - `archive`：干员档案，按「基础档案 / 档案资料一 …」分节；
- 可选 `titles: {"RO5-END-2": "长卷留痕"}` 自定义篇名，界园志异那种多结局页面靠它；
- 改完 `python -m wiki_transporter all --plan 我的活动`。

`plans/*.json` 和 `%LOCALAPPDATA%\WikiTransporter\plans\*.json` 都会被读取，同名时用户目录优先。

## 存放位置

`out/`、`cache/`、`build/` 默认都在程序目录里。存放位置可改，存在 `%LOCALAPPDATA%\WikiTransporter\settings.json`。
换位置后，新抓的缓存和新导出的书去新目录，已经生成的书不会自动搬家。

设置页里有「书籍存放位置」，可以直接填路径或「选择文件夹…」，「恢复默认」改回程序目录。命令行等价写法：

```bash
python -m wiki_transporter config                     # 查看当前设置
python -m wiki_transporter config --out "D:\我的书"    # 改位置（自动建目录并验证可写）
python -m wiki_transporter config --out default       # 恢复默认
```

环境变量 `WIKITRANSPORTER_OUT` 优先级最高，适合临时覆盖。

## 署名与出处

整理者署名「安巢鸟 · 整理」**默认写入**：PDF 文档属性、Word 作者、EPUB 的 `dc:creator`，
以及每本书封面落款与「凡例」里的「整理与排版：安巢鸟 · 整理。」一行。

不想让自己的名字出现在产物里就关掉它：GUI 生成面板取消勾选「写入整理者署名」，
或命令行加 `--no-credit`（`build` / `pdf` / `all` 都支持），关掉后上述五处一个都不写。

书里印的来源（凡例第一条、总目录页脚、Word 备注、EPUB 的 `dc:description`、PDF 的 `subject` / `keywords`）
写的是「明日方舟 · PRTS」。

文字与美术的权利归属、抓取方式与使用条件写在 [NOTICE.md](NOTICE.md)。
本仓库不附带任何剧情原文，生成物仅供个人阅读存档，禁止商用或付费分发；
抓取请保持间隔（默认 0.35s/页），不要并发轰炸 wiki。

## 文字处理规则

| 原始指令 | 处理 |
|---|---|
| `[name="年"]` / `[multiline="年"]` | → `年：台词` |
| `[subtitle]` / `[sticker]` / `[animtext]` | → 旁白 / 字幕文字 |
| `[header]` | → 居中小标题 |
| `[decision]` | → `【选项】A / B / C` |
| `[background]`（`bg_*`） | → `换景`（内部记作 `◆`；`avg_*` 等镜头构图不显示） |
| `[image]` / `[cgitem]` / `[charactercutin]` | 丢弃（纯摄影指令） |
| `[playmusic]` / `[stopmusic]` | → `音乐 · 幽暗` / `音乐 · 停`（曲名见 `cues.py`，可覆盖） |
| `[video]` | → `影像` |
| 时间轴、淡入淡出、音效、特效、镜头、charslot 等 | 丢弃 |

原始页里的舞台指令大多是引擎资源 id（`bg_rooftop_2`、`$darkness01_loop`），直接印出来像乱码。这里把它们排成话剧剧本式的舞台提示：居中、疏字距的浅灰小字，连续几条合并成一组。这些参数占原页字符约 70%，全部丢掉，只留「配乐起 / 停乐 / 换景 / 影像」这几类。

其余清洗：wikitext 标记（`'''粗体'''`、`{{color|…}}`、`[[链接|文字]]`、`<br>` 等）一律变纯文本；干员密录自动取剧本自带书名（如《千年一叹》），取不到退回「干员密录·X」；同一个人连说几句保持紧凑，换人才拉开间距；无说话人段落按楷体缩进的旁白处理；只剩「……」的行留一点空白。

## PDF 引擎与目录页码

`pdf` 默认走 Chromium 引擎（`--engine auto` 时优先用它）：

| 引擎 | 需要什么 | 目录页码 | 说明 |
|---|---|---|---|
| `chrome`（默认） | Chrome 或 Edge（Windows 自带 Edge） | 真页码 | 不依赖 Office。渲染两遍：先出草稿，读出每个标题落在第几页，回填目录再渲一遍；同时写入 PDF 书签、页脚页码、文档属性 |
| `word` | Windows + Microsoft Word | 真页码 | 用 Word COM 更新域后导出，只读打开、只导出不回存 |
| `soffice` | LibreOffice | 占位符 | 直接转 PDF，目录页码要在 Word/WPS 里自己更新 |

```bash
python -m wiki_transporter pdf                    # Chromium
python -m wiki_transporter pdf --engine word      # 有 Word 且想和 docx 版式完全一致
PRTS_CHROME="D:\Chrome\chrome.exe" python -m wiki_transporter pdf   # 指定浏览器
```

默认不用 Word 的原因：`ExportAsFixedFormat` / `SaveAs2` 在部分机器上会永久挂起（实测连一页空白文档存成 `.rtf` 都卡死，重启、换默认打印机、停杀软、清 Normal.dotm、重置 Word 注册表都无效）。这类环境问题脚本层修不了，所以 PDF 渲染换成 Chromium，Word 引擎作为可选项保留。

只跑 `build` 时，Word 文档里的目录还是域占位符：打开后 `Ctrl+A` → `F9` 选「更新整个目录」即可；或者直接用 `pdf` 出 PDF，Chromium 引擎的页码由我们自己回填。

Word 引擎带三重兜底：清理残留的 `~$xxx.docx` 锁文件；清掉 Word 崩溃后写进注册表 `Resiliency\DisabledItems` 的黑名单（只解封指向本工具输出目录的条目）；整轮导出有硬超时，超时报错退出而不是一直挂着。

`pymupdf` 只在收尾阶段用（回填页码、写书签、盖页脚），另外还负责读立绘尺寸与透明通道、生成占位封面。
没装也能出 PDF，只是没有页码回填和书签。

## 项目结构

```
wiki_transporter/
├── config.py      计划加载、路径、设置、来源短名与主色
├── sources.py     PRTS 来源定义、MediaWiki API 客户端、图片地址解析
├── catalog.py     建索引：分类成员 → 页面清单
├── net.py         出站请求：域名白名单、私网拒绝、重定向与体积上限
├── fetch.py       MediaWiki API 抓取（缓存 / 重试 / 限速）
├── parse.py       PRTS wikitext → 结构化 blocks（台词/旁白/提示/选项）
├── style.py       三种产物共用的排版规则（说话人配色、舞台提示、停顿留白、标题去重）
├── images.py      头像 / 立绘下载与缓存、透明通道判断、占位封面生成
├── docx_build.py  Word 排版（分卷 / 总目录 / 合订本共享同一套样式）
├── epub_build.py  EPUB3（手写 NCX + nav，零依赖）
├── pdf_html.py    Chromium 引擎：数据 → 打印用 HTML → PDF（页脚 / 目录页码回填 / 书签）
├── pdf.py         PDF 引擎调度（Chromium / Word COM / LibreOffice）+ 环境自检
├── smart.py       正文全文索引（FTS5 trigram）：词法检索、短词 LIKE 兜底、发布时间线排序
├── deep.py        概念层：别名表、共现扩展、向量融合、活动补全与降噪
├── themes.py      从 PRTS 结构化数据生成「主题 = 一卷篇目」（活动名 / 代号 / 篇目清单）
├── entity.py      角色实体页：出场篇目、关系、时间线
├── fulltext.py    正文抽取与清洗（索引前的统一入口）
├── llm.py         可选：远端 LLM 的查询扩展与向量编码（OpenAI 兼容接口）
├── localembed.py  可选：本地 bge-small-zh ONNX 编码（纯 CPU、不联网）
├── plot_dates.py  CN 服实装日期表（剧情时间线排序依据）
├── gui/           本地 Web 界面（server.py 起服务，web/ 是前端）
└── cli.py         命令行
plans/sui.json     岁家剧情计划（10 卷 142 篇）
scripts/           一键脚本 + exe 打包入口
tests/             解析器、计划完整性、PDF 排版快照测试
```

想调字号、行距、页边距：

- 三种产物共用的规则在 `wiki_transporter/style.py`，改一处三处都变；
- PDF 的字体 / 行距 / 页边距 / 封面版式在 `wiki_transporter/pdf_html.py` 顶部的 `CSS`（`@page`、`body`、`p.d`、`p.cues`、`.cover`）；
- Word 的在 `wiki_transporter/docx_build.py` 的 `setup_doc()`（`line_spacing`、`Pt(11)`、`Cm(2.9)`）；
- 占位封面的配色与几何在 `wiki_transporter/images.py` 的 `art_svg()`。

改完重跑 `build` / `pdf`，不用重新抓。

## 打包 exe

```powershell
pwsh -File scripts\build_exe.ps1            # 不带 plans：exe 是纯工具
pwsh -File scripts\build_exe.ps1 -WithPlans  # 连 plans/ 和 builtin_plans/ 一起内嵌
```

一次出两个 exe，各约 68 MB：

| 产物 | 配置 | 入口 | 控制台 |
|---|---|---|---|
| `dist\wiki-transporter.exe` | `wiki-transporter.spec` | `scripts/entry.py`（走 `cli.main`，不带参数就开界面） | 有 |
| `dist\WikiTransporter-GUI.exe` | `WikiTransporter-GUI.spec` | `scripts/entry_gui.py`（直接 `run_gui`，崩溃写 `gui-error.log`） | 无 |

两个 spec 都默认不内嵌 `plans/`：发出去的 exe 是纯工具。`-WithPlans` 会让脚本设 `WT_WITH_PLANS=1`，spec 才把 `plans/` 和 `wiki_transporter/builtin_plans/` 打进去。原理是 PyInstaller 把 `wiki_transporter` 包（含 `gui/web`）和 onnxruntime / tokenizers 二进制塞进单个可执行文件，运行时解压到临时目录；入口单独成文件是为了避开相对导入在脚本模式下失效。

发布走 `scripts\release.ps1`：校验两个 exe、算 SHA256 并写回上面 README 的校验和段、用 `docs\release-<tag>.md` 作正文建 Release、再传附件（同名先删再传）。令牌只从 `$env:GITHUB_TOKEN` 读。

```powershell
$env:GITHUB_TOKEN = 'ghp_...'                 # 需要 repo 权限
pwsh -File scripts\build_exe.ps1              # 两个 exe
pwsh -File scripts\release.ps1 -Tag v1.6.0 -DryRun   # 先只看哈希、不调 API
pwsh -File scripts\release.ps1 -Tag v1.6.0           # 建/更新 Release 并上传
```

岁家成品的 `sui-story.zip` 由 `scripts\pack_release.ps1` 生成（把 `out\sui\` 连同 `plans\sui.json` 打包），单独发在 v1.3.0 Release，不随每个版本重发。exe 与成品 zip 都不进仓库。

## 更新日志

- **1.6.0**：搜索重做。新增正文全文索引（FTS5 trigram + 短词 LIKE 兜底）、概念层（别名表 / 共现扩展 / 可选向量融合）、主题召回（活动 → 整卷篇目）与角色实体页；结果按 CN 服实装时间线排序，同活动篇目连续。**修掉「搜一个词冒出一堆无关整卷」**：活动补全改为需命中 ≥ 总篇数一半（向上取整），不达标的散落命中整条丢弃，全空则面板不渲染。图形界面同步：卷级全选、正文命中分组折叠、共现实体快捷换词、搜索结果与成品书库统一卡片样式。
- **1.5.0**：只保留 PRTS。移除 BWIKI 三个来源、通用解析 `generic.py`、`wikitext.py` 与 `source add/rm/probe` 命令；`search` 去掉 `--source`。
- **1.4.0**：项目改名 Wiki Transporter，包 `wiki_transporter`、exe `wiki-transporter`。
- **1.3.x**：EPUB 内联样式加 `!important`，兼容不加载出版方 CSS 的阅读器。

## 常见问题

抓取失败或某些页面缺？
PRTS 偶发 429 或超时，重跑 `fetch` 会跳过已下载的页面补抓。长期抓不到可以手动打开 `https://prts.wiki/w/页面名`，复制编辑框里的 wikitext，存成 `cache/raw/sui/<页面名把 / : * ? " < > | 换成 _>.txt`，再 `build`。在图形界面里同样可以对单页重试。

封面是空白 / 没有立绘？
先看该卷主角在 PRTS 有没有立绘文件（命名见上面的「封面」一节），没有就会用本地占位图。
想换掉占位图，把图放到 `cache/img/立绘_<角色>.png` 再跑即可（已有文件优先复用）。

能只出 EPUB 不出 PDF 吗？
可以。`build` 只生成 Word + EPUB；或者 `build --volumes ...` 后挑要导出的卷再跑 `pdf`。

合订本太大、太慢？
`--no-combined` 跳过合订本。合订本 PDF 现在 1200 页出头，Chromium 要渲染两遍（第二遍为了回填目录页码），约 4 分钟；分卷单本一般十几秒。

手机上能看吗？
EPUB 直接丢进 Apple Books / 微信读书 / Koodo Reader；PDF 适合平板和电脑。
