# -*- coding: utf-8 -*-
"""用 Chromium（Chrome / Edge）无头打印生成 PDF —— 不依赖 Word / LibreOffice。

为什么不走 Word：`ExportAsFixedFormat` / `SaveAs2` 在部分机器上会永久挂起
（实测连一页空白文档存成 .rtf 都卡死，重启、换打印机、停杀软都无效），
脚本层修不了。Chromium 的打印管线稳定、跨平台，还能顺带产出真正的 PDF 书签。

流程（两遍渲染）：
1. 第一遍：目录页码留占位符，渲染 PDF；
2. 用 PyMuPDF 扫隐藏标记，定位每个标题所在页；
3. 第二遍：把真实页码写回目录，重新渲染；
4. 收尾：叠加页脚页码、写入书签树（PDF 大纲）与文档属性。

PyMuPDF 只在收尾阶段用到，缺失时自动降级（没有页码/书签，页面本身照常出）。
"""
from __future__ import annotations

import html
import os
import re
import shutil
import subprocess
import tempfile
import urllib.parse
from functools import lru_cache

from .config import Plan, author_name, ensure_dir, safe_filename, source_short
from .images import cached_portrait
from .parse import book_stats
from .style import (color_index, crumb_redundant, cue_parts, is_beat, section_label,
                    speaker_column, story_colors, volume_parts)

# 目录条目的锚点：空 span 不产生任何文字，只给 Chromium 一个内部链接目标。
# Chromium 会把 `<a href="#hN">` 打成真正的 PDF 内部链接，目标页写在链接注释里，
# 所以定位页码不需要在正文里留任何可见/可复制的标记。

CSS = r"""@charset "utf-8";
@page { size: A4; margin: 2.5cm 2.9cm 2.6cm 2.9cm; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
html { font-size: 11pt; }
body {
  margin: 0; padding: 0; color: #1a1a1a;
  font-family: "宋体", SimSun, "Songti SC", "STSong", serif;
  font-size: 11pt; line-height: 1.55;
  text-align: justify;
}
.anc { font-size: 0; line-height: 0; }

/* ---------- 封面（满版深色 + 右侧立绘） ---------- */
@page cover { size: A4; margin: 0; }
.cover { page: cover; position: relative; box-sizing: border-box;
         width: 21cm; height: 29.7cm; margin: 0; overflow: hidden;
         page-break-after: always; display: flex; flex-direction: column;
         justify-content: flex-end; padding: 0 2.4cm 3.4cm 2.4cm;
         background: radial-gradient(125% 95% at 74% 26%, #1d2331 0%, #12151c 46%, #0a0c10 100%); }
.cover::before { content: ""; position: absolute; inset: 0; z-index: 0;
  background-image: var(--hero, none); background-repeat: no-repeat;
  background-position: right -2.3cm bottom -1cm; background-size: auto 102%; }
.cover .veil { position: absolute; inset: 0; z-index: 1;
  background:
    linear-gradient(90deg, rgba(9,10,13,.97) 0%, rgba(9,10,13,.92) 24%,
                    rgba(9,10,13,.60) 46%, rgba(9,10,13,.08) 68%, rgba(9,10,13,0) 84%),
    linear-gradient(0deg, rgba(9,10,13,.95) 0%, rgba(9,10,13,.55) 22%, rgba(9,10,13,0) 46%),
    radial-gradient(58% 38% at 16% 82%, rgba(200,164,98,.16), transparent 62%); }
.cover .cap { position: relative; z-index: 2; width: 12.4cm; color: #ffffff; }
.cover .series { font-size: 10pt; letter-spacing: .42em; color: #c8a462; }
.cover .vol { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-weight: bold;
              font-size: 34pt; line-height: 1.26; letter-spacing: .04em;
              margin: .55cm 0 0; text-shadow: 0 2px 18px rgba(0,0,0,.7); }
.cover .rule { width: 2.4cm; height: 2pt; background: #c8a462; margin: .58cm 0 .5cm; }
.cover .sub { font-family: "楷体", KaiTi, "Kaiti SC", serif; font-size: 12pt;
              color: #e2e2e2; letter-spacing: .08em; }
.cover .meta { margin-top: .75cm; font-size: 9.5pt; color: #a5abb4; line-height: 2;
               letter-spacing: .06em; }
.cover .brand { position: absolute; right: 2.2cm; bottom: 1.4cm; z-index: 2; font-size: 9pt;
                letter-spacing: .28em; color: #e6d9bd; }
.cover.plain .cap { padding-bottom: 3cm; }
/* 不透明图（自带底色 / 画面里带字）与本地占位图：右下一块竖版画框，别压到标题 */
.cover.framed { padding-right: 12.2cm; }
.cover.framed::before { top: auto; left: auto; bottom: 0; right: -2.4cm;
  width: 10.9cm; height: 16.4cm; background-size: cover; background-position: center top;
  box-shadow: -1px 0 0 rgba(200,164,98,.40), 0 0 70px rgba(0,0,0,.55); }
.cover.framed .veil { background:
    linear-gradient(90deg, rgba(9,10,13,.97) 0%, rgba(9,10,13,.92) 46%,
                    rgba(9,10,13,.42) 68%, rgba(9,10,13,.06) 100%),
    linear-gradient(0deg, rgba(9,10,13,.9) 0%, rgba(9,10,13,.32) 20%, rgba(9,10,13,0) 42%),
    radial-gradient(58% 38% at 16% 82%, rgba(200,164,98,.16), transparent 62%); }
.cover.framed .cap { width: 9.4cm; }
.cover.framed .vol { font-size: 30pt; }
.cover.framed::before { -webkit-mask-image: linear-gradient(to top, #000 76%, transparent 100%);
  mask-image: linear-gradient(to top, #000 76%, transparent 100%); }
.cover.framed .brand { right: auto; left: 2.4cm; }

/* 00 总目录与说明的页头（浅色页，不占满版） */
.tochead { text-align: center; padding-top: 6cm; }
.tochead .series { font-size: 12.5pt; color: #8c8c8c; letter-spacing: .18em; }
.tochead .vol { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-weight: bold;
                font-size: 27pt; letter-spacing: .06em; margin: 1.5cm 0 1cm; }
.tochead .meta { font-size: 10.5pt; color: #555555; }
.tochead .note { font-size: 9.5pt; color: #8c8c8c; margin-top: 7pt; }

/* ---------- 凡例 ---------- */
.fanli { page-break-after: always; }
.fanli h2 { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-size: 15pt; font-weight: bold;
            text-align: center; margin: 0 0 18pt; letter-spacing: 0.2em; }
.fanli p { font-size: 10pt; color: #3c3c3c; margin: 5.5pt 0; text-indent: 0; line-height: 1.75; }

/* ---------- 目录 ---------- */
.toc { page-break-after: always; }
.toc h2 { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-size: 18pt; font-weight: bold;
          text-align: center; margin: 0 0 20pt; letter-spacing: 0.2em; }
.toc ul { list-style: none; margin: 0; padding: 0; }
.toc a { color: inherit; text-decoration: none; }
.toc li { display: flex; align-items: baseline; margin: 1.2pt 0; line-height: 1.45; }
.toc .t { text-align: left; }
.toc .dots { flex: 1 1 auto; border-bottom: 1pt dotted #cccccc; margin: 0 4pt 0 6pt;
             position: relative; top: -2pt; min-width: 1em; }
.toc .p { flex: 0 0 auto; font-size: 9pt; color: #666666; }
.toc li.l1 { margin-top: 14pt; margin-bottom: 3pt; }
.toc li.l1 .t { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-weight: bold; font-size: 13pt; }
.toc li.l2 { margin-top: 7pt; }
.toc li.l2 .t { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-weight: bold; font-size: 11pt; }
.toc li.l3 { padding-left: 1.4em; }
.toc li.l3 .t { font-size: 10.5pt; }
.toc .note { font-size: 9pt; color: #999999; margin-bottom: 8pt; text-indent: 0; }

/* ---------- 正文：卷 / 节 / 篇 的层级 ---------- */
/* 合订本里每卷一张扉页：卷次小字、卷名大字、括注再小一号，整体居中 */
.volh { height: 24cm; display: flex; flex-direction: column; justify-content: center;
        align-items: center; page-break-before: always; page-break-inside: avoid;
        font-family: "黑体", SimHei, "Heiti SC", sans-serif; text-align: center; margin: 0; }
.volh .vnum { font-size: 12pt; color: #8c8c8c; letter-spacing: 0.3em; margin-bottom: 14pt; }
.volh .vname { font-size: 26pt; font-weight: bold; letter-spacing: 0.06em; line-height: 1.4; }
.volh .vnote { font-size: 12.5pt; color: #555555; letter-spacing: 0.06em; margin-top: 14pt;
               line-height: 1.6; }
/* 节标题退成一行小字（像书的书眉），把字号让给真正的篇名 */
.sech { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-size: 9pt; color: #8c8c8c;
        letter-spacing: 0.16em; text-align: center;
        page-break-before: always; page-break-after: avoid; margin: 0 0 20pt; }
.story { page-break-before: always; page-break-after: avoid; margin: 0 0 6pt; }
.story.nobreak { page-break-before: auto; }
.crumb { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-size: 9pt; color: #8c8c8c;
         letter-spacing: 0.16em; text-align: center; margin: 0 0 18pt; }
.ptrt { text-align: center; margin: 0 0 14pt; page-break-inside: avoid; }
.ptrt img { width: 3.9cm; height: auto; }
.stitle { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-size: 21pt; font-weight: bold;
          color: #111111; text-align: center; letter-spacing: 0.04em; line-height: 1.35;
          margin: 0 0 11pt; }
.srule { height: 0; line-height: 0; font-size: 0; width: 3cm; margin: 0 auto 17pt;
         border-top: 0.6pt solid #d8d8d8; }
.h4 { font-family: "黑体", SimHei, "Heiti SC", sans-serif; font-size: 12pt; font-weight: bold;
      color: #4a4a4a; letter-spacing: 0.08em; text-align: center; page-break-after: avoid;
      margin: 22pt 0 12pt; text-indent: 0; }

/* ---------- 正文：台词 / 旁白 / 演出提示 ---------- */
p { margin: 0; orphans: 2; widows: 2; }
/* 台词：说话人一行之内的名字，换人不换行；同一个人连说几句保持紧凑，换人才拉开 */
/* 台词：说话人带冒号起头，折行要落在台词的位置上（悬垂缩进），换人才拉开间距 */
p.d { padding-left: var(--nw, 3.3em); text-indent: calc(-1 * var(--nw, 3.3em));
      margin: 0 0 2pt; text-align: justify; }
p.d.chg { margin-top: 5.5pt; }
p.d.beat { margin-top: 9pt; margin-bottom: 4pt; }
p.d .sp { display: inline-block; min-width: var(--nw, 3.3em); text-indent: 0;
          white-space: nowrap;
          font-family: "黑体", SimHei, "Heiti SC", sans-serif; }
.c1 { color: #8a4b41; } .c2 { color: #3a5680; } .c3 { color: #77602b; } .c4 { color: #3b6355; }
.c5 { color: #6a4b78; } .c6 { color: #7c5533; } .c7 { color: #2f6669; } .c8 { color: #4b4b55; }
/* 旁白 / 字幕：楷体，和台词分得开，但仍是正文 */
p.n { font-family: "楷体", KaiTi, "Kaiti SC", serif; font-size: 11pt; color: #333333;
      text-indent: 2em; margin: 6pt 0; line-height: 1.58; }
/* 演出提示：一场戏里的舞台指示，居中、疏字距、浅灰，成组出现时一行一条 */
p.cues { text-align: center; text-indent: 0; margin: 9pt 0 11pt; line-height: 1.5;
         page-break-inside: avoid; }
p.cues .cue { display: block; font-family: "楷体", KaiTi, "Kaiti SC", serif;
              font-size: 9pt; letter-spacing: 0.06em; }
p.cues .cl { color: #b3b3b3; }
p.cues .cs { color: #cccccc; padding: 0 0.1em; }
p.cues .cv { color: #8a8a8a; }
p.c { font-family: "楷体", KaiTi, "Kaiti SC", serif; font-size: 9.5pt; color: #6b6b6b;
      text-indent: 2em; margin: 8pt 0; }
/* 篇末一段跟着上一段走：宁可上一页多挤一行，也不要单独占一页 */
.tail { page-break-before: avoid; }
"""

HEAD = ('<!DOCTYPE html>\n<html lang="zh-CN"><head><meta charset="utf-8"/>'
        "<title>%s</title>\n<style>%s</style></head><body>\n")

# 字号倍率：只放大/缩小所有 pt（字号、行距、段距），cm 这类版心尺寸与页边距保持不动，
# 于是「放大字号」= 每行字数变少、页数变多，版面比例和设计不变。
# 封面与卷扉页例外：它们是按 21×29.7cm 画布排死的设计稿（书名 26–34pt 压在金色分隔线
# 上方），跟着放大就会书名折行、压线，所以这两块的尺寸始终按 100% 出。
_PT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)pt")
_RULE = re.compile(r"([^{}]*)\{([^{}]*)\}")
_NO_SCALE = (".cover", ".volh")   # 选择器里带这些字样的规则不缩放


def _scale_pt(css_text: str, scale: float) -> str:
    if abs(scale - 1.0) < 1e-6:
        return css_text

    def one(m: re.Match) -> str:
        head, body = m.group(1), m.group(2)
        if any(tok in head for tok in _NO_SCALE):
            return m.group(0)
        scaled = _PT.sub(lambda mm: "%gpt" % round(float(mm.group(1)) * scale, 2), body)
        return head + "{" + scaled + "}"

    return _RULE.sub(one, css_text)


@lru_cache(maxsize=8)
def css_for(scale: float = 1.0) -> str:
    """按倍率缩放后的整份 CSS（1.0 原样返回，不产生额外开销）。"""
    return _scale_pt(CSS, scale)


def clamp_scale(scale: float | None) -> float:
    """只接受 1.0 / 1.15 / 1.3 三档，别的一律回落到 1.0。"""
    try:
        v = round(float(scale or 1.0), 2)
    except (TypeError, ValueError):
        return 1.0
    return v if v in (1.0, 1.15, 1.3) else 1.0



def esc(s: str) -> str:
    return html.escape(str(s), quote=False)


def _mk(idx: int) -> str:
    return '<span class="anc" id="h%d"></span>' % idx


def file_uri(path: str) -> str:
    # 用 as_uri 保证空格 / 中文都正确编码
    return urllib.parse.urljoin("file:", urllib.request.pathname2url(os.path.abspath(path)))


# ---------------------------------------------------------------- HTML 组装

def _series_label(plan: Plan, title: str) -> str:
    """封面顶端那行小字：分卷印来源，合订本印书名。"""
    if title == "全一册":
        return plan.title
    return (plan.data.get("source") or "").split("（")[0].strip() or plan.title


def _cover_html(plan: Plan, title: str, subtitle: str, meta_lines: list[str],
                art: tuple[str | None, str] | None = None) -> str:
    """封面。art =（本地图片路径, 版式）：bleed 满版 / framed 画框 / plain 纯字。"""
    meta = "".join("<div>%s</div>" % esc(x) for x in meta_lines)
    if subtitle and (subtitle == title or subtitle in title):
        subtitle = ""
    path, kind = art if art else (None, "plain")
    if not path:
        kind = "plain"
    cls = "" if kind == "bleed" else " " + kind
    style = (' style="--hero:url(&quot;%s&quot;)"' % esc(file_uri(path))) if path else ""
    brand = ('<div class="brand">%s</div>' % esc(author_name(plan))) if author_name(plan) else ""
    return ('<div class="cover%s"%s><div class="veil"></div>'
            '<div class="cap"><div class="series">%s</div>'
            '<div class="vol">%s</div><div class="rule"></div>'
            '<div class="sub">%s</div><div class="meta">%s</div></div>'
            '%s</div>\n'
            % (cls, style, esc(_series_label(plan, title)),
               esc(title), esc(subtitle), meta, brand))


def _fanli_html(plan: Plan) -> str:
    body = "".join("<p>%s</p>\n" % esc(line) for line in plan.fanli)
    return '<div class="fanli"><h2>凡　例</h2>%s</div>\n' % body


def _toc_html(entries: list[dict], page_map: dict[int, int] | None, show_pages: bool) -> str:
    items = []
    for e in entries:
        if e["level"] not in (1, 2, 3):
            continue
        pg = ""
        if show_pages:
            pg = str(page_map.get(e["idx"], "")) if page_map else ""
        items.append('<li class="l%d"><span class="t"><a href="#h%d">%s</a></span>'
                     '<span class="dots"></span><span class="p">%s</span></li>\n'
                     % (e["level"], e["idx"], esc(e["title"]), esc(pg)))
    note = "" if show_pages else '<p class="note">（页码见页脚；此页在定稿渲染时回填）</p>'
    return '<div class="toc"><h2>目　录</h2>%s<ul>%s</ul></div>\n' % (note, "".join(items))


def _cue_run_html(texts: list[str]) -> str:
    """把连续几条演出提示排成一组（一行一条），像剧本开头的舞台指示。"""
    items = []
    for t in texts:
        p = cue_parts(t)
        if not p:
            continue
        if p["kind"] == "other":
            items.append('<span class="cue cv">%s</span>' % esc(p["value"]))
        elif p["value"]:
            items.append('<span class="cue"><span class="cl">%s</span>'
                         '<span class="cs">·</span><span class="cv">%s</span></span>'
                         % (esc(p["label"]), esc(p["value"])))
        else:
            items.append('<span class="cue cl">%s</span>' % esc(p["label"]))
    if not items:
        return ""
    return '<p class="cues">%s</p>\n' % "".join(items)


def _blocks_html(blocks: list[dict]) -> str:
    out = []
    colors = story_colors(blocks)
    prev_speaker: str | None = None
    i = 0
    while i < len(blocks):
        b = blocks[i]
        k = b["k"]
        if k == "q":
            run = []
            while i < len(blocks) and blocks[i]["k"] == "q":
                run.append(blocks[i].get("t") or "")
                i += 1
            out.append(_cue_run_html(run))
            prev_speaker = None          # 演出提示之后重新起一段对话
            continue
        if k == "h4":
            out.append('<div class="h4">%s</div>\n' % esc(b["t"]))
        elif k == "d":
            name = b.get("s") or ""
            text = b.get("t") or ""
            cls = ["d"]
            if prev_speaker != name:
                cls.append("chg")
            if is_beat(text):
                cls.append("beat")
            out.append('<p class="%s"><span class="sp c%d">%s：</span>%s</p>\n'
                       % (" ".join(cls), color_index(name, colors), esc(name), esc(text)))
            prev_speaker = name
        elif k == "n":
            out.append('<p class="n">%s</p>\n' % esc(b["t"]))
            prev_speaker = None
        elif k == "c":
            out.append('<p class="c">【选项】%s</p>\n' % esc("　／　".join(b["t"])))
            prev_speaker = None
        i += 1
    if out:
        # 篇末最后两段跟着上一段走：宁可上一页少挤两行，也不要留一页只有一两行字
        for j in range(1, min(3, len(out)) + 1):
            out[-j] = out[-j].replace('class="', 'class="tail ', 1)
    return "".join(out)


def _portrait_html(plan: Plan, operator: str | None) -> str:
    if not operator:
        return ""
    path = cached_portrait(plan, operator)
    if not path:
        return ""
    return '<div class="ptrt"><img src="%s" alt="%s"/></div>\n' % (
        esc(file_uri(path)), esc(operator))


def _story_head_html(plan: Plan, story: dict, idx: int, crumb: str | None,
                     operator: str | None, nobreak: bool) -> str:
    """篇首：一行小字交代所属（节/卷）+ 立绘 + 篇名 + 一条细线。

    篇名只出现这一次——正文里同名的居中标题已经在 parse 阶段去掉了。
    """
    parts = ['<div class="story%s">%s' % (" nobreak" if nobreak else "", _mk(idx))]
    if not crumb_redundant(crumb, story["title"]):
        parts.append('<div class="crumb">%s</div>' % esc(crumb))
    parts.append(_portrait_html(plan, operator))
    parts.append('<div class="stitle">%s</div>' % esc(story["title"]))
    parts.append('<div class="srule"></div></div>\n')
    return "".join(parts)


def _volume_body_html(plan: Plan, vol: dict, entries: list[dict], volume_heading: bool,
                      crumb: str | None = None) -> str:
    out = []
    if volume_heading:
        idx = len(entries)
        entries.append({"idx": idx, "level": 1, "title": vol["title"]})
        num, name, note = volume_parts(vol["title"])
        parts = ['<div class="volh">%s' % _mk(idx)]
        if num:
            parts.append('<div class="vnum">%s</div>' % esc(num))
        parts.append('<div class="vname">%s</div>' % esc(name))
        if note:
            parts.append('<div class="vnote">%s</div>' % esc(note))
        parts.append("</div>\n")
        out.append("".join(parts))
    multi = len(vol["sections"]) > 1
    used: set[str] = set()
    for sec in vol["sections"]:
        stories = sec["stories"]
        if not stories:
            continue
        operator = sec.get("portrait")
        if operator in used:
            operator = None
        if operator:
            used.add(operator)
        same = len(stories) == 1 and stories[0]["title"] == sec["name"]
        show_sec = multi and not same
        if show_sec:
            idx = len(entries)
            entries.append({"idx": idx, "level": 2, "title": sec["name"]})
            out.append('<div class="sech">%s%s</div>\n'
                       % (_mk(idx), esc(section_label(sec["name"], stories[0]["title"]))))
        for i, story in enumerate(stories):
            idx = len(entries)
            # 有节标题时篇是第 3 层，否则篇直接挂在卷下（第 2 层）——
            # 这样合订本里同一卷的篇目永远处于同一层，书签不会深浅不一。
            entries.append({"idx": idx, "level": 3 if show_sec else 2,
                            "title": story["title"]})
            # 节标题已另起一页时，紧随其后的第一篇不能再分一次页，否则会留下空白页
            nobreak = show_sec and i == 0
            head_crumb = None if nobreak else (crumb or sec["name"])
            out.append(_story_head_html(plan, story, idx, head_crumb,
                                        operator if i == 0 else None, nobreak))
            out.append('<div class="bd" style="--nw:%.2fem">\n' % speaker_column(story["blocks"]))
            out.append(_blocks_html(story["blocks"]))
            out.append("</div>\n")
    return "".join(out)


def _vol_meta(vol: dict) -> str:
    return "·".join(s["name"] for s in vol["sections"])


def build_volume_html(plan: Plan, books: list[dict], index: int,
                      page_map: dict[int, int] | None = None,
                      show_pages: bool = True,
                      covers: dict[int, tuple[str | None, str]] | None = None,
                      font_scale: float = 1.0
                      ) -> tuple[str, list[dict]]:
    vol = books[index]
    _n, chars = book_stats(vol)
    entries: list[dict] = []
    body = _volume_body_html(plan, vol, entries, volume_heading=False)
    doc = [HEAD % (esc(vol["title"]), css_for(font_scale))]
    subtitle = _vol_meta(vol)
    if subtitle == vol["title"]:      # 单节卷：副标题与书名相同就不重复印
        subtitle = ""
    doc.append(_cover_html(plan, vol["title"], subtitle,
                           ["第 %d 卷 / 共 %d 卷" % (index + 1, len(books)),
                            "约 %d 万字" % round(chars / 10000)],
                           art=(covers or {}).get(index)))
    doc.append(_fanli_html(plan))
    doc.append(_toc_html(entries, page_map, show_pages))
    doc.append(body)
    doc.append("</body></html>")
    return "".join(doc), entries


def build_toc_html(plan: Plan, books: list[dict],
                   page_map: dict[int, int] | None = None,
                   show_pages: bool = True,
                   font_scale: float = 1.0) -> tuple[str, list[dict]]:
    """00_总目录与说明：全书概览 + 每卷篇目。"""
    entries: list[dict] = []
    total_stories = sum(book_stats(b)[0] for b in books)
    total_chars = sum(book_stats(b)[1] for b in books)
    head = ('<div class="tochead"><div class="series">%s</div>'
            '<div class="vol">总目录与说明</div>'
            '<div class="meta">全书共 %d 卷、%d 篇、约 %d 万字。</div>'
            '<div class="note">文字取自 %s，版权归原作方所有，仅供个人阅读存档。</div>'
            '</div>\n'
            % (esc(plan.title), len(books), total_stories, round(total_chars / 10000),
               esc(source_short(plan))))
    fanli = _fanli_html(plan)
    rows = []
    for i, vol in enumerate(books):
        n, c = book_stats(vol)
        rows.append('<div class="tocvol">%s</div>\n<div class="tocmeta">%s　（%d 篇，约 %d 字）</div>\n'
                    % (esc(vol["title"]), esc(_vol_meta(vol)), n, c))
        for sec in vol["sections"]:
            for st in sec["stories"]:
                rows.append('<div class="tocstory">· %s</div>\n' % esc(st["title"]))
    extra = ("<style>.tocvol{font-family:'黑体',SimHei,sans-serif;font-weight:bold;font-size:13pt;"
             "margin:12pt 0 3pt}.tocmeta{font-size:9pt;color:#999;margin-bottom:5pt}"
             ".tocstory{font-size:9.5pt;margin:0.4pt 0 0.4pt 0.9cm}</style>")
    doc = [HEAD % (esc(plan.title + " · 总目录与说明"),
                   css_for(font_scale) + _scale_pt(extra, font_scale))]
    doc.append(head)
    doc.append('<div style="page-break-before:always"></div>')
    doc.append('<h2 style="font-family:\'黑体\',SimHei,sans-serif;font-size:18pt;'
               'text-align:center;margin:0 0 16pt">总目录</h2>\n')
    doc.append("".join(rows))
    doc.append("</body></html>")
    # 这份是全书概览，不做页码回填、也不写书签：卷标题就在这一页上，指向自己没意义。
    return "".join(doc), entries


def build_combined_html(plan: Plan, books: list[dict],
                        page_map: dict[int, int] | None = None,
                        show_pages: bool = True,
                        covers: dict[int, tuple[str | None, str]] | None = None,
                        font_scale: float = 1.0
                        ) -> tuple[str, list[dict]]:
    total_stories = sum(book_stats(b)[0] for b in books)
    total_chars = sum(book_stats(b)[1] for b in books)
    entries: list[dict] = []
    body = []
    for vol in books:
        body.append(_volume_body_html(plan, vol, entries, volume_heading=True))
    doc = [HEAD % (esc(plan.title + " · 全一册"), css_for(font_scale))]
    doc.append(_cover_html(plan, "全一册",
                           "共 %d 卷 · %d 篇 · 约 %d 万字" % (
                               len(books), total_stories, round(total_chars / 10000)),
                           ["%d 卷合订" % len(books)],
                           art=(covers or {}).get(0)))
    doc.append(_fanli_html(plan))
    doc.append(_toc_html(entries, page_map, show_pages))
    doc.append("".join(body))
    doc.append("</body></html>")
    return "".join(doc), entries


# ---------------------------------------------------------------- 渲染

def find_chromium() -> str | None:
    cands = [
        os.environ.get("CHROME_PATH") or "",
        os.environ.get("PRTS_CHROME") or "",
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        "/usr/bin/google-chrome", "/usr/bin/google-chrome-stable",
        "/usr/bin/chromium", "/usr/bin/chromium-browser", "/usr/bin/microsoft-edge",
    ]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    for name in ("chrome", "google-chrome", "chromium", "msedge", "microsoft-edge"):
        p = shutil.which(name)
        if p:
            return p
    return None


def render_pdf(browser: str, html_path: str, pdf_path: str, profile_dir: str,
               timeout: int = 300) -> None:
    if os.path.isfile(pdf_path):
        os.remove(pdf_path)
    url = file_uri(html_path)
    cmd = [
        browser,
        "--headless=new",
        "--disable-gpu",
        "--no-first-run",
        "--no-default-browser-check",
        "--disable-extensions",
        "--disable-sync",
        "--disable-background-networking",
        "--no-pdf-header-footer",
        "--user-data-dir=" + profile_dir,
        "--virtual-time-budget=15000",
        "--run-all-compositor-stages-before-draw",
        "--print-to-pdf=" + pdf_path,
        url,
    ]
    r = subprocess.run(cmd, capture_output=True, timeout=timeout)
    if not os.path.isfile(pdf_path) or os.path.getsize(pdf_path) < 800:
        tail = (r.stderr or r.stdout or b"").decode("utf-8", "replace")[-600:]
        raise RuntimeError("Chromium 打印失败：%s" % (tail or "没有产出 PDF"))


def _locate_pages(pdf_path: str, entries: list[dict]) -> dict[int, int]:
    """定位每个目录条目在正文里的页码（1 基）。

    优先用目录链接（Chromium 会把 `<a href="#h3">` 打成真正的 PDF 内部链接，
    目标页就在注释里），这样页面上不必留下任何标记；链接拿不到时退回按标题逐条搜索。
    """
    try:
        import pymupdf
    except ImportError:  # 没有 PyMuPDF 就不回填页码
        return {}

    pages: dict[int, int] = {}
    doc = pymupdf.open(pdf_path)
    try:
        # ---- 方案 A：读目录链接的目标页 ----
        # Chromium 视情况打成 LINK_GOTO 或 LINK_NAMED（命名目标），两种都收。
        kinds = {pymupdf.LINK_GOTO, pymupdf.LINK_NAMED}
        hits: list[tuple[int, float, int]] = []   # (所在页, y 坐标, 目标页)
        for pno in range(doc.page_count):
            for link in doc[pno].get_links():
                if link.get("kind") not in kinds:
                    continue
                tgt = link.get("page", -1)
                if not isinstance(tgt, int) or tgt < 0:
                    continue
                y = (link.get("from") or pymupdf.Rect()).y0
                hits.append((pno, y, tgt + 1))
        if hits:
            hits.sort(key=lambda x: (x[0], x[1]))
            if len(hits) == len(entries):
                for e, h in zip(entries, hits):
                    pages[e["idx"]] = h[2]
                return pages

        # ---- 方案 B：逐条按标题找页（跳过目录页）----
        toc_pages = set()
        for pno in range(min(doc.page_count, 8)):
            if "目　录" in doc[pno].get_text():
                toc_pages.add(pno)
        start = 0
        for e in entries:
            for pno in range(start, doc.page_count):
                if pno in toc_pages and start == 0 and pno < 3:
                    continue
                if doc[pno].search_for(e["title"]):
                    pages[e["idx"]] = pno + 1
                    start = pno
                    break
    finally:
        doc.close()
    return pages


def _finalize(pdf_path: str, entries: list[dict], pages: dict[int, int],
              title: str, number_pages: bool = True, subject: str = "",
              keywords: str = "", credit: str = "") -> int:
    """加页脚页码 + 书签 + 文档属性。返回总页数。"""
    try:
        import pymupdf
    except ImportError:
        return 0
    doc = pymupdf.open(pdf_path)
    try:
        if number_pages:
            for pno in range(doc.page_count):
                if pno == 0:      # 封面不印页码
                    continue
                page = doc[pno]
                rect = page.rect
                box = pymupdf.Rect(0, rect.height - 52, rect.width, rect.height - 20)
                page.insert_textbox(box, str(pno + 1), fontsize=9, fontname="helv",
                                    color=(0.47, 0.47, 0.47), align=1)
        toc = []
        # 书签层级必须从 1 开始、且逐层递进；把实际出现的层级压缩成连续序号，
        # 这样同一卷下的兄弟篇目一定在同一层（不会出现第 1 篇 2 级、第 2 篇 3 级）。
        found = sorted({e["level"] for e in entries if pages.get(e["idx"])})
        rank = {lv: i + 1 for i, lv in enumerate(found)}
        prev = 0
        for e in entries:
            pg = pages.get(e["idx"])
            if not pg:
                continue
            # 首行必须是 1，且每行最多比上一行深一层，否则 PyMuPDF 拒绝写入
            lvl = max(1, min(rank.get(e["level"], 1), prev + 1))
            prev = lvl
            toc.append([lvl, e["title"], pg])
        if toc:
            doc.set_toc(toc)
        meta = doc.metadata or {}
        meta.update({"title": title, "subject": subject or title,
                     "keywords": keywords or "剧情, 文字稿, Wiki"})
        if credit:
            meta["author"] = credit
        else:
            meta.pop("author", None)
        doc.set_metadata(meta)
        tmp = pdf_path + ".tmp"
        # garbage=1 只去掉重复对象；garbage=3/4 会重建全部 xref，一本千页的书要多花
        # 两三分钟，文件却只小 1% 左右，不值得。
        doc.save(tmp, garbage=1, deflate=True)
        doc.close()
        os.replace(tmp, pdf_path)
    finally:
        try:
            doc.close()
        except Exception:
            pass
    return pymupdf.open(pdf_path).page_count


# ---------------------------------------------------------------- 对外入口

def _make_pdf(plan: Plan, builder, out_path: str, browser: str, work: str,
              title: str, on_stage=None) -> tuple[int, int]:
    """两遍渲染。返回 (页数, 目录条目数)。"""
    def stage(frac: float, text: str) -> None:
        if on_stage:
            on_stage(frac, text)

    stage(0.02, "准备排版")
    html1, entries = builder(None, False)
    h1 = os.path.join(work, "pass1.html")
    p1 = os.path.join(work, "pass1.pdf")
    with open(h1, "w", encoding="utf-8") as f:
        f.write(html1)
    stage(0.18, "第一遍渲染")
    render_pdf(browser, h1, p1, os.path.join(work, "prof"))
    stage(0.50, "第一遍完成，定位页码")
    pages = _locate_pages(p1, entries)

    html2, entries2 = builder(pages, True)
    h2 = os.path.join(work, "pass2.html")
    with open(h2, "w", encoding="utf-8") as f:
        f.write(html2)
    ensure_dir(os.path.dirname(out_path))
    stage(0.58, "第二遍渲染")
    render_pdf(browser, h2, out_path, os.path.join(work, "prof"))
    stage(0.88, "定位页码")
    pages2 = _locate_pages(out_path, entries2)
    if len(pages2) >= len(pages):
        pages = pages2
    stage(0.93, "盖页码、写书签")
    label = source_short(plan)
    total = _finalize(out_path, entries2, pages, title,
                      subject="%s · 剧情文字稿" % label,
                      keywords="%s, 剧情, 文字稿" % label,
                      credit=author_name(plan))
    stage(1.0, "完成")
    return total, len(entries2)


def _make_pdf_once(builder, out_path: str, browser: str, work: str, title: str,
                   on_stage=None) -> tuple[int, int]:
    """单遍渲染（没有分页目录的文档用）：只盖页脚页码。"""
    if on_stage:
        on_stage(0.05, "准备排版")
    html, entries = builder(None, False)
    h = os.path.join(work, "once.html")
    with open(h, "w", encoding="utf-8") as f:
        f.write(html)
    ensure_dir(os.path.dirname(out_path))
    if on_stage:
        on_stage(0.25, "渲染")
    render_pdf(browser, h, out_path, os.path.join(work, "prof"))
    if on_stage:
        on_stage(0.85, "盖页码")
    pages = _finalize(out_path, [], {}, title)
    if on_stage:
        on_stage(1.0, "完成")
    return pages, len(entries)


def export_pdfs_html(plan: Plan, books: list[dict], selection: list[int], pdf_dir: str,
                     combined: bool = True, combined_name: str = "全一册",
                     with_toc_page: bool = True, progress=None,
                     font_scale: float = 1.0) -> dict[str, int]:
    """用 Chromium 把结构化数据直接排版成 PDF。返回 {文件名主体: 页数}。

    font_scale 只缩放字号（pt），A4 版心不变：选 130% 会让每行字数变少、页数变多。
    产物写进 pdf_dir 并覆盖同名文件——同一套书不要指望两档字号并存。

    progress(frac, label, done, total)：frac 是整段 PDF 工序的完成比例（0–1），
    label 是当前在做的文件/步骤，done/total 是已完成/总共的文件数。
    """
    scale = clamp_scale(font_scale)
    browser = find_chromium()
    if not browser:
        raise RuntimeError(
            "找不到 Chromium（Chrome / Edge）。请安装 Chrome，或设置环境变量 "
            "PRTS_CHROME=/path/to/chrome。")
    ensure_dir(pdf_dir)
    print("  PDF 引擎：Chromium（%s）" % os.path.basename(browser))
    if abs(scale - 1.0) > 1e-6:
        print("  正文字号：%d%%（A4 版心不变，每行字数减少、页数相应增加）"
              % round(scale * 100))
    from .images import cover_art

    covers = {i: cover_art(plan, book, i) for i, book in enumerate(books)}
    fill = next((v for v in covers.values() if v[0]), None)
    if fill:  # 个别卷没有图时，用第一张能用的顶上，别留空封面
        covers = {i: (v if v[0] else fill) for i, v in covers.items()}
        covers.setdefault(0, fill)
    result: dict[str, int] = {}

    todo: list[tuple[str, int | None, str]] = []
    if with_toc_page:
        todo.append(("toc", None, "00_总目录与说明"))
    for i in selection:
        todo.append(("vol", i, "%02d_%s" % (i + 1, safe_filename(books[i]["title"].replace("　", "_")))))
    if combined:
        todo.append(("all", None, safe_filename(combined_name)))
    total = len(todo)

    def reporter(k: int, title_text: str):
        def on_stage(frac: float, text: str) -> None:
            if progress:
                progress((k + frac) / max(1, total), "%s · %s" % (title_text, text),
                         k, total)
        return on_stage

    work_root = tempfile.mkdtemp(prefix="prts-pdf-")
    try:
        for k, (kind, index, name) in enumerate(todo):
            work = ensure_dir(os.path.join(work_root, name))
            on_stage = reporter(k, name)
            if kind == "toc":
                pages, _n = _make_pdf_once(
                    lambda pm, sp: build_toc_html(plan, books, pm, sp, font_scale=scale),
                    os.path.join(pdf_dir, name + ".pdf"), browser, work,
                    plan.title + " · 总目录与说明", on_stage=on_stage)
                print("  %-42s %4d 页（全书概览）" % (name, pages))
            elif kind == "vol":
                pages, n = _make_pdf(
                    plan, lambda pm, sp, i=index: build_volume_html(
                        plan, books, i, pm, sp, covers, font_scale=scale),
                    os.path.join(pdf_dir, name + ".pdf"), browser, work,
                    books[index]["title"], on_stage=on_stage)
                print("  %-42s %4d 页（%d 条目录）" % (name, pages, n))
            else:
                pages, n = _make_pdf(
                    plan, lambda pm, sp: build_combined_html(
                        plan, books, pm, sp, covers, font_scale=scale),
                    os.path.join(pdf_dir, name + ".pdf"), browser, work,
                    plan.title + " · 全一册", on_stage=on_stage)
                print("  %-42s %4d 页（%d 条目录）" % (name, pages, n))
            result[name] = pages
    finally:
        shutil.rmtree(work_root, ignore_errors=True)
    return result
