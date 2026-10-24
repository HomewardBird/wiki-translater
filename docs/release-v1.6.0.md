# wiki-transporter v1.6.0

抓 PRTS（明日方舟 Wiki）剧情 wikitext，排成分卷 Word / PDF / EPUB，再出「全一册」合订本。本 Release 是工具本体，不含剧情原文。

## 下载

| 文件 | 说明 |
|---|---|
| `WikiTransporter-GUI.exe` | 图形版，{{SIZE:WikiTransporter-GUI.exe}}。双击开界面，不弹黑框 |
| `wiki-transporter.exe` | 命令行版，{{SIZE:wiki-transporter.exe}}。双击也是界面，终端里可跑 `doctor` / `search` / `all` |
| `sui-story.zip` | 「岁家剧情」成品：`pdf/`（13 个 PDF）、`docx/`（13 个）、`epub/`（12 个），含全一册。不装 exe 也能直接读 |

两个 exe 功能相同，只是 `console` 开关不同（图形版无控制台，崩溃栈写 exe 同目录 `gui-error.log`）。单文件免安装，删掉即卸载。

SHA256 见仓库 README 的校验和段。

## 变更（v1.3.0 → v1.6.0）

- **1.6.0 搜索重做**：正文 FTS5 trigram 索引（2 字词 LIKE 兜底）、概念层（别名 / 共现 / 可选向量融合）、主题召回（活动 → 整卷）、按 CN 服实装时间线排序。
- **降噪**：修掉「搜一个词冒出一堆无关整卷」。旧逻辑命中 1 篇就补全整卷（搜「岁家」把火蓝之心 1→25、众生行记 1→24 全拉进来）；现在需真命中 ≥ ⌈总篇数/2⌉ 才补全，活动名在查询别名里才无条件整卷。实测岁家 200→128 篇、萨卡兹 200→183。
- **1.5.0**：只做 PRTS，移除 BWIKI 三来源、`generic.py` 与 `source add/rm/probe`。
- **1.4.x**：改名 Wiki Transporter（包 `wiki_transporter`、命令 `wiki-transporter`）；EPUB 内联样式加 `!important`，兼容 KoodoReader 这类不加载出版方 CSS 的阅读器。

## 用法

双击 exe → 建立索引 → 搜索勾选 → 整理成书（抓取 → 解析 → Word/EPUB → PDF）。计划存 `%LOCALAPPDATA%\WikiTransporter\plans\<id>.json`。

```powershell
wiki-transporter.exe doctor                 # 自检
wiki-transporter.exe search 萨卡兹           # 命令行搜索
wiki-transporter.exe all --plan sui         # 全部产物
```

## 限制

- 仅 Windows；onefile 冷启动约 3–5 秒。
- 向量检索可选：exe 内含 onnxruntime / tokenizers 运行时，但**不含模型权重**，需下载到 `%LOCALAPPDATA%\WikiTransporter\models\bge-small-zh-v1.5\`；没有模型时自动退回关键词检索。
- PDF 需本机 Chrome / Edge；两者都没有只出 Word/EPUB。
- 抓取 0.35 秒/页，429/5xx 退避重试 3 次，重跑跳过已下载页面。

## 权利

代码 MIT。剧情文字与美术权利归其权利方，wiki 文字来自对应 Wiki 贡献者；本仓库与附件不含剧情原文，仅供个人阅读存档，请勿商用或付费分发。见 [NOTICE.md](https://github.com/HomewardBird/wiki-transporter/blob/main/NOTICE.md)。
