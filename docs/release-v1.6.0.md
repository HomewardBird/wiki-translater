# wiki-transporter v1.6.0

把 PRTS（明日方舟 Wiki）的剧情 wikitext 抓下来、清洗、排成分卷的 Word / PDF / EPUB，再附一份「全一册」合订本。
这个 Release 装的是**工具本体**：两个单文件 exe，不含任何剧情文本，也不含别人的书单。

- 上一个 Release（v1.3.0）还叫 wiki-translater，只有一个带控制台的 exe；这版改了包名与命令名，并把图形界面单独拆出来。
- 仓库自带的「岁家剧情」计划（10 卷 / 142 篇 / 约 67 万字 / 合订本 PDF 1208 页）不含在 exe 里，见文末「想要现成成品」。

## 下载哪个

| 文件 | 大小 | 适合谁 |
|---|---|---|
| `WikiTransporter-GUI.exe` | {{SIZE:WikiTransporter-GUI.exe}} | 只想用界面：双击就开，不弹黑框，本机起服务 + 用 Chrome/Edge 开应用窗口 |
| `wiki-transporter.exe` | {{SIZE:wiki-transporter.exe}} | 想在终端里跑 `doctor` / `search` / `all`；双击不带参数同样是图形界面，只是多一个控制台窗口 |

两个 exe 功能完全一致，都是单文件、免安装：不装 Python、不写注册表，删掉文件就算卸载。
唯一差别是 `console` 开关——图形版没有控制台，所以崩溃会把 traceback 写进 exe 同目录的 `gui-error.log`；命令行版在终端里直接看得到报错。

## 校验和

下载后先核对，值不对就别运行：

```text
{{SHA256:wiki-transporter.exe}}  wiki-transporter.exe
{{SHA256:WikiTransporter-GUI.exe}}  WikiTransporter-GUI.exe
```

## 这版新东西（v1.3.0 → v1.6.0）

**1.6.0 搜索重做**（本次发布重点）
- 正文全文索引：FTS5 trigram，2 字短词走 `LIKE` 兜底。搜「萨卡兹」这类世界观词，以前一条都搜不到，现在能命中真正提到它的篇目。
- 概念层：别名表展开（`特蕾西娅` ↔ `特蕾西亚`）、共现扩展、可选向量融合（本地 bge-small-zh，RRF 合并，不联网）。
- 主题召回：从 PRTS 结构化数据生成「活动 = 一卷篇目」，可以按整卷召回。
- 结果按 CN 服实装时间线排序，同活动的篇目连续，不会跨活动乱跳。
- **降噪**：修掉「搜一个词冒出一堆无关整卷」。旧逻辑命中 1 篇就补全整卷，实测搜「岁家」会把火蓝之心 1→25、众生行记 1→24、落叶逐火 1→19 全拉进来（都是 0.40 分的 2 字角色名蹭到一句台词）。现在要求真命中篇数 ≥ ⌈总篇数/2⌉ 才补全整卷；活动名本身出现在查询别名里才无条件整卷补全；不达标的散落命中整条丢弃，全空则面板不渲染。实测：岁家 200→128 篇（8 条岁家线全留，5 个无关卷归零）；萨卡兹 200→183。
- 图形界面同步：卷级全选、正文命中分组折叠、共现实体快捷换词、搜索结果与书库统一卡片样式。

**1.5.0 只做 PRTS**
- 移除 BWIKI 三个来源（原神 / 重返未来：1999 / 鸣潮）、通用解析 `generic.py`、`wikitext.py` 与 `source add/rm/probe`。
- 旧计划文件里残留的 `source_id` 会被忽略，一律按 PRTS 抓取解析。

**1.4.x 改名收尾**
- 项目改名 Wiki Transporter，包 `wiki_transporter`，命令 `wiki-transporter`（旧名 `wiki-translater` 不再可用）。
- EPUB 内联样式全部加 `!important`，兼容不加载出版方 CSS 的阅读器（KoodoReader 这类）。

## 用法

双击任意一个 exe，界面里三步：

1. **建立索引** —— 只抓页面标题，几秒钟；
2. **搜索并勾选** 要收进书里的篇目（可以用整卷、正文命中、共现实体来挑）；
3. **整理成书** —— 一次跑完 抓取 → 解析 → Word/EPUB → PDF。

勾好的篇目存成计划文件：`%LOCALAPPDATA%\WikiTransporter\plans\<id>.json`，之后可以反复生成。

命令行等价操作：

```powershell
wiki-transporter.exe doctor                       # 自检：Python / python-docx / Chromium / pymupdf
wiki-transporter.exe index                        # 只抓标题建索引
wiki-transporter.exe search 萨卡兹                 # 命令行搜索
wiki-transporter.exe all --plan sui               # 抓取 + 生成 Word/EPUB/PDF
wiki-transporter.exe build --plan sui             # 只要 Word + EPUB
wiki-transporter.exe pdf --plan sui               # 单独导出 PDF
```

不联网、不登录、不采集任何数据；所有缓存与产物都在本机（`cache/`、`out/`、`%LOCALAPPDATA%\WikiTransporter\`）。

## 想要现成成品

「岁家剧情」整套已排好的 PDF / Word / EPUB（10 卷 + 全一册）在 **v1.3.0 Release 的 `sui-story.zip`** 里，不装 exe 也能直接读。
想用这版 exe 重新印：把压缩包里的 `sui.json` 放到 exe 同级的 `plans\` 目录（没有就新建），重启 exe，书架里会出现这本书。

## 已知限制

- 只支持 Windows。打包用 PyInstaller onefile，首次启动要解压到临时目录，冷启动约 3–5 秒。
- 图形版没有控制台：界面起不来时看 exe 同目录的 `gui-error.log`。
- 向量检索是可选的：exe 里已收 onnxruntime / tokenizers 二进制，但模型权重需自行下载到 `%LOCALAPPDATA%\WikiTransporter\models\`，没有模型时自动退回纯关键词检索。
- PDF 依赖本机 Chromium 内核（Chrome 或 Edge 均可）；两者都没有时只出 Word/EPUB。
- 抓取默认 0.35 秒/页，PRTS 偶发 429 / 5xx 会退避重试 3 次；重跑会跳过已下载页面。

## 权利与许可

代码 MIT。剧情文字与美术的权利归其权利方所有，wiki 文字来自对应 Wiki 的贡献者；本仓库与 Release 附件均不含剧情原文，仅供个人阅读存档，请勿商用或付费分发。逐站说明见 [NOTICE.md](https://github.com/HomewardBird/wiki-transporter/blob/main/NOTICE.md)。
