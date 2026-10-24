# -*- coding: utf-8 -*-
"""生成 Word 文档：分卷正文、总目录、全一册合订本。

排版规则和 PDF 版同源（见 `style.py`）：台词悬垂缩进、说话人按出场顺序着色、
演出提示收成居中的浅灰小字、停顿单独留白。目标是长时间阅读不累眼，
而不是把页数压到最少。
"""
from __future__ import annotations

import os

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_LINE_SPACING, WD_TAB_ALIGNMENT
from docx.oxml import OxmlElement
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from .config import (Plan, author_name, ensure_dir, safe_filename, source_accent,
                     source_short)
from .images import cached_portrait, cover_art
from .parse import book_stats
from .style import (crumb_redundant, cue_parts, is_beat, section_label,
                    speaker_column, story_colors, volume_parts)

SONG = "宋体"
HEI = "黑体"
KAI = "楷体"

# 正文 11pt 时 1em 是多少厘米
EM_CM = 11 * 2.54 / 72

TOC_FIELD = 'TOC \\o "1-3" \\h \\z \\u'


def set_run(run, font=SONG, size=11, bold=False, color=None, italic=False):
    run.font.name = font
    run.font.size = Pt(size)
    run.bold = bold
    run.italic = italic
    rPr = run._element.get_or_add_rPr()
    rf = rPr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rPr.append(rf)
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi", "w:cs"):
        rf.set(qn(attr), font)
    if color:
        run.font.color.rgb = RGBColor.from_string(color)


def style_east_asia(doc, style_name, font, size, bold=False, color=None):
    st = doc.styles[style_name]
    st.font.name = font
    st.font.size = Pt(size)
    st.font.bold = bold
    if color:
        st.font.color.rgb = RGBColor.from_string(color)
    rPr = st.element.get_or_add_rPr()
    rf = rPr.find(qn("w:rFonts"))
    if rf is None:
        rf = OxmlElement("w:rFonts")
        rPr.append(rf)
    for attr in ("w:eastAsia", "w:ascii", "w:hAnsi", "w:cs"):
        rf.set(qn(attr), font)


def add_field(paragraph, instr, placeholder=""):
    """插入 Word 域（页码 / 目录）。"""
    run = paragraph.add_run()
    begin = OxmlElement("w:fldChar")
    begin.set(qn("w:fldCharType"), "begin")
    instr_el = OxmlElement("w:instrText")
    instr_el.set(qn("xml:space"), "preserve")
    instr_el.text = instr
    sep = OxmlElement("w:fldChar")
    sep.set(qn("w:fldCharType"), "separate")
    text = OxmlElement("w:t")
    text.text = placeholder
    end = OxmlElement("w:fldChar")
    end.set(qn("w:fldCharType"), "end")
    for el in (begin, instr_el, sep, text, end):
        run._r.append(el)
    return run


def setup_doc() -> Document:
    doc = Document()
    sec = doc.sections[0]
    sec.page_height = Cm(29.7)
    sec.page_width = Cm(21.0)
    sec.top_margin = Cm(2.5)
    sec.bottom_margin = Cm(2.6)
    sec.left_margin = Cm(2.9)
    sec.right_margin = Cm(2.9)

    style_east_asia(doc, "Normal", SONG, 11)
    pf = doc.styles["Normal"].paragraph_format
    pf.line_spacing_rule = WD_LINE_SPACING.MULTIPLE
    pf.line_spacing = 1.55
    pf.space_before = Pt(0)
    pf.space_after = Pt(0)

    for name in ("Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        doc.styles[name].font.color.rgb = RGBColor(0x00, 0x00, 0x00)
        doc.styles[name].font.bold = True
        doc.styles[name].font.italic = False
    style_east_asia(doc, "Heading 1", HEI, 22, True, "000000")
    style_east_asia(doc, "Heading 2", HEI, 16, True, "000000")
    style_east_asia(doc, "Heading 3", HEI, 13.5, True, "000000")
    style_east_asia(doc, "Heading 4", HEI, 12, True, "595959")
    for name in ("Heading 1", "Heading 2", "Heading 3", "Heading 4"):
        f = doc.styles[name].paragraph_format
        f.space_before = Pt(14)
        f.space_after = Pt(8)
        f.line_spacing = 1.2
        f.keep_with_next = True

    footer = doc.sections[0].footer
    p = footer.paragraphs[0]
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    add_field(p, " PAGE ", "1")
    for r in p.runs:
        set_run(r, SONG, 9, color="777777")
    return doc


def para(doc, text="", font=SONG, size=11, bold=False, color=None, indent=0.0,
         first_indent=None, align=None, space_before=0, space_after=0, italic=False):
    p = doc.add_paragraph()
    pf = p.paragraph_format
    if indent:
        pf.left_indent = Cm(indent)
    if first_indent:
        pf.first_line_indent = Cm(first_indent)
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    if align is not None:
        p.alignment = align
    if text:
        set_run(p.add_run(text), font, size, bold, color, italic)
    return p


def _series_label(plan: Plan, title: str) -> str:
    """扉页顶端那行小字：分卷印来源，合订本印书名。"""
    if title == "全一册":
        return plan.title
    return (plan.data.get("source") or "").split("（")[0].strip() or plan.title


def _docx_cover_art(plan: Plan, book: dict, index: int = 0) -> str | None:
    """Word 只能用位图：拿不到位图就没图封面，退回纯字版式。"""
    path, _kind = cover_art(plan, book, index)
    if not path or path.lower().endswith(".svg"):
        return None
    return path


def add_cover(doc, plan: Plan, title: str, subtitle: str, meta_lines: list[str],
              hero: str | None = None):
    """扉页：上半页立绘，下半页书名与卷次。没有立绘就退回纯字封面。"""
    if hero:
        para(doc, "", size=9, space_before=6)
        pic = doc.add_paragraph()
        pic.alignment = WD_ALIGN_PARAGRAPH.CENTER
        pic.add_run().add_picture(hero, width=Cm(10.2))
        para(doc, "", size=11, space_before=10)
    else:
        para(doc, "", size=11, space_before=90)
    series = _series_label(plan, title)
    p = para(doc, series, HEI, 10.5, False, "8C8C8C", align=WD_ALIGN_PARAGRAPH.CENTER)
    _letter_spacing(p, 0.32, 10.5)
    p = para(doc, title, HEI, 28, True, "000000",
             align=WD_ALIGN_PARAGRAPH.CENTER, space_before=14)
    _letter_spacing(p, 0.05, 28)
    _add_rule(doc, width_cm=2.4, space_before=8, space_after=10,
              color=source_accent(plan).lstrip("#").upper() or "C8A462")
    if subtitle and subtitle != title and subtitle not in title:
        para(doc, subtitle, KAI, 12.5, False, "555555", align=WD_ALIGN_PARAGRAPH.CENTER)
    if not hero:
        para(doc, "", size=11, space_before=110)
    else:
        para(doc, "", size=11, space_before=26)
    for line in meta_lines:
        para(doc, line, SONG, 10, False, "999999", align=WD_ALIGN_PARAGRAPH.CENTER)
    if plan.credit:
        p = para(doc, author_name(plan), HEI, 9, False, "A8A29A",
                 align=WD_ALIGN_PARAGRAPH.CENTER, space_before=16)
        _letter_spacing(p, 0.28, 9)
    doc.add_page_break()


def add_fanli(doc, plan: Plan):
    para(doc, "凡　例", HEI, 15, True, "000000",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=16)
    for line in plan.fanli:
        para(doc, line, SONG, 10, False, "3C3C3C", space_before=4, space_after=4)
    doc.add_page_break()


def add_toc_page(doc):
    para(doc, "目　录", HEI, 18, True, "000000",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=18)
    p = doc.add_paragraph()
    add_field(p, TOC_FIELD, "（目录将在打开时由 Word 更新）")
    for r in p.runs:
        set_run(r, SONG, 11)
    doc.add_page_break()


def _letter_spacing(p, em: float, size_pt: float):
    """字间距（Word 里叫「字符间距」，单位是 1/20 磅）。

    w:spacing 在 rPr 里必须排在 color / sz 之前，所以不能直接 append。
    """
    val = str(int(round(em * size_pt * 20)))
    for r in p.runs:
        rPr = r._element.get_or_add_rPr()
        sp = OxmlElement("w:spacing")
        sp.set(qn("w:val"), val)
        rPr.insert_element_before(
            sp, "w:w", "w:kern", "w:position", "w:sz", "w:szCs", "w:highlight", "w:u",
            "w:effect", "w:bdr", "w:shd", "w:fitText", "w:vertAlign", "w:rtl", "w:cs",
            "w:em", "w:lang", "w:eastAsianLayout", "w:specVanish", "w:oMath")


def _add_rule(doc, width_cm: float = 3.0, space_before: float = 2,
              space_after: float = 12, color: str = "D8D8D8"):
    """篇名底下那条细横线（用一个只有下边框的空段落画）。"""
    p = doc.add_paragraph()
    pf = p.paragraph_format
    side = max((21.0 - 2 * 2.9 - width_cm) / 2, 0)
    pf.left_indent = Cm(side)
    pf.right_indent = Cm(side)
    pf.space_before = Pt(space_before)
    pf.space_after = Pt(space_after)
    pPr = p._p.get_or_add_pPr()
    pBdr = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in (("w:val", "single"), ("w:sz", "4"), ("w:space", "1"), ("w:color", color)):
        bottom.set(qn(k), v)
    pBdr.append(bottom)
    pPr.insert_element_before(
        pBdr, "w:shd", "w:tabs", "w:suppressAutoHyphens", "w:kinsoku", "w:wordWrap",
        "w:overflowPunct", "w:topLinePunct", "w:autoSpaceDE", "w:autoSpaceDN", "w:bidi",
        "w:adjustRightInd", "w:snapToGrid", "w:spacing", "w:ind", "w:contextualSpacing",
        "w:mirrorIndents", "w:suppressOverlap", "w:jc", "w:textDirection",
        "w:textAlignment", "w:textboxTightWrap", "w:outlineLvl", "w:divId", "w:cnfStyle",
        "w:rPr", "w:sectPr", "w:pPrChange")
    run = p.add_run("\u200b")
    run.font.size = Pt(1)
    return p


def _add_cues(doc, texts: list[str]):
    """一组演出提示：居中、浅灰、一行一条，像剧本的舞台指示。"""
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    pf = p.paragraph_format
    pf.space_before = Pt(8)
    pf.space_after = Pt(8)
    pf.keep_together = True
    first = True
    for t in texts:
        c = cue_parts(t)
        if not c:
            continue
        if not first:
            p.add_run().add_break()
        first = False
        if c["kind"] == "other":
            set_run(p.add_run(c["value"]), KAI, 9, False, "8A8A8A")
        elif c["value"]:
            set_run(p.add_run(c["label"]), KAI, 9, False, "B3B3B3")
            set_run(p.add_run("·"), KAI, 9, False, "CCCCCC")
            set_run(p.add_run(c["value"]), KAI, 9, False, "8A8A8A")
        else:
            set_run(p.add_run(c["label"]), KAI, 9, False, "B3B3B3")


def _add_blocks(doc, blocks):
    colors = story_colors(blocks)
    col = Cm(speaker_column(blocks) * EM_CM)
    prev_speaker = None
    i = 0
    while i < len(blocks):
        b = blocks[i]
        kind = b["k"]
        if kind == "q":
            run = []
            while i < len(blocks) and blocks[i]["k"] == "q":
                run.append(blocks[i].get("t") or "")
                i += 1
            _add_cues(doc, run)
            prev_speaker = None
            continue
        if kind == "h4":
            p = para(doc, b["t"], HEI, 12, True, "4A4A4A",
                     align=WD_ALIGN_PARAGRAPH.CENTER, space_before=18, space_after=10)
            p.paragraph_format.keep_with_next = True
        elif kind == "d":
            name = b.get("s") or ""
            text = b.get("t") or ""
            p = doc.add_paragraph()
            pf = p.paragraph_format
            # 悬垂缩进 + 制表位 = 台词两列对齐，折行落在台词那一列
            pf.left_indent = col
            pf.first_line_indent = -col
            pf.tab_stops.add_tab_stop(col, WD_TAB_ALIGNMENT.LEFT)
            beat = is_beat(text)
            pf.space_before = Pt(9 if beat else (5.5 if prev_speaker != name else 2))
            pf.space_after = Pt(4 if beat else 2)
            set_run(p.add_run(name + "："), HEI, 11, False, colors.get(name))
            p.add_run("\t")
            set_run(p.add_run(text), SONG, 11)
            prev_speaker = name
        elif kind == "n":
            para(doc, b["t"], KAI, 11, False, "333333",
                 first_indent=2 * EM_CM, space_before=3, space_after=3)
            prev_speaker = None
        elif kind == "c":
            para(doc, "【选项】" + "　／　".join(b["t"]), KAI, 9.5, False, "6B6B6B",
                 first_indent=2 * EM_CM, space_before=4, space_after=4)
            prev_speaker = None
        i += 1


def _add_portrait(doc, plan: Plan, operator: str | None):
    """在干员档案 / 密录页顶部放一张头像（每节最多一张）。"""
    if not operator:
        return None
    path = cached_portrait(plan, operator)
    if not path:
        return None
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    p.paragraph_format.space_before = Pt(2)
    p.paragraph_format.space_after = Pt(12)
    p.add_run().add_picture(path, width=Cm(3.9))
    return p


def _add_story(doc, plan: Plan, story: dict, crumb: str | None = None,
               operator: str | None = None, break_before: bool = True):
    """篇首：所属小字 → 立绘 → 篇名 → 细线。篇名只出现这一次。"""
    head = []
    if crumb and not crumb_redundant(crumb, story["title"]):
        p = para(doc, crumb, HEI, 9, False, "8C8C8C",
                 align=WD_ALIGN_PARAGRAPH.CENTER, space_after=14)
        _letter_spacing(p, 0.16, 9)
        head.append(p)
    picture = _add_portrait(doc, plan, operator)
    if picture is not None:
        head.append(picture)
    # 用 Heading 3 承载篇名，Word 的「导航窗格 / 目录域」才认得出这一级
    h = doc.add_paragraph(style="Heading 3")
    h.alignment = WD_ALIGN_PARAGRAPH.CENTER
    h.paragraph_format.space_before = Pt(2)
    h.paragraph_format.space_after = Pt(2)
    set_run(h.add_run(story["title"]), HEI, 21, True, "111111")
    head.append(h)
    for p in head[:-1]:
        p.paragraph_format.keep_with_next = True
    head[0].paragraph_format.page_break_before = break_before
    _add_rule(doc)


def _add_volume_body(doc, plan: Plan, vol: dict, volume_heading: bool,
                     at_page_top: bool = False) -> bool:
    """写入一卷正文；返回「写完后是否仍在页首」。

    `at_page_top` 用来避免**连续两次分页**——那是空白页的来源：
    节标题已另起一页时，紧随其后的篇标题就不能再分一次。
    """
    if volume_heading:
        # 卷扉页：Word 会忽略页首段落的「段前距」，所以用空行把标题压到页面中部
        spacers = [para(doc, "", SONG, 24) for _ in range(6)]
        spacers[0].paragraph_format.page_break_before = not at_page_top
        num, name, note = volume_parts(vol["title"])
        if num:
            p = para(doc, num, HEI, 12, False, "8C8C8C",
                     align=WD_ALIGN_PARAGRAPH.CENTER, space_after=14)
            _letter_spacing(p, 0.3, 12)
            p.paragraph_format.keep_with_next = True
        h = doc.add_paragraph(style="Heading 1")
        h.alignment = WD_ALIGN_PARAGRAPH.CENTER
        h.paragraph_format.space_before = Pt(0)
        h.paragraph_format.space_after = Pt(6)
        set_run(h.add_run(name), HEI, 26, True, "000000")
        if note:
            para(doc, note, HEI, 12.5, False, "555555",
                 align=WD_ALIGN_PARAGRAPH.CENTER, space_before=14)
        at_page_top = True

    multi = len(vol["sections"]) > 1
    used_portrait: set[str] = set()   # 同一卷里同一干员只配一张头像
    for sec in vol["sections"]:
        stories = sec["stories"]
        if not stories:
            continue
        operator = sec.get("portrait")
        if operator in used_portrait:
            operator = None
        if operator:
            used_portrait.add(operator)
        # 单篇且篇名与节名相同（如「干员密录·夕」）时不重复印两行标题
        same = len(stories) == 1 and stories[0]["title"] == sec["name"]
        show_sec = multi and not same
        if show_sec:
            h = doc.add_paragraph(style="Heading 2")
            h.paragraph_format.page_break_before = not at_page_top
            h.alignment = WD_ALIGN_PARAGRAPH.CENTER
            h.paragraph_format.space_before = Pt(0)
            h.paragraph_format.space_after = Pt(18)
            h.paragraph_format.keep_with_next = True
            set_run(h.add_run(section_label(sec["name"], stories[0]["title"])),
                    HEI, 9, False, "8C8C8C")
            _letter_spacing(h, 0.16, 9)
            at_page_top = True

        for i, story in enumerate(stories):
            nobreak = show_sec and i == 0
            _add_story(doc, plan, story,
                       crumb=None if nobreak else sec["name"],
                       operator=operator if i == 0 else None,
                       break_before=not (nobreak or at_page_top))
            _add_blocks(doc, story["blocks"])
            at_page_top = False
    return at_page_top


def _vol_meta(book: dict) -> str:
    names = "·".join(s["name"] for s in book["sections"])
    return names


def build_volume_docx(plan: Plan, books: list[dict], index: int, out_dir: str) -> str:
    vol = books[index]
    _, chars = book_stats(vol)
    doc = setup_doc()
    doc.core_properties.title = vol["title"]
    doc.core_properties.author = author_name(plan) or None
    doc.core_properties.comments = (
        "文字稿由 %s 的剧情页清洗生成；演出指令仅保留场景/画面/音乐提示，删去时间轴与特效参数。"
        % source_short(plan)
    )
    add_cover(
        doc, plan, vol["title"], _vol_meta(vol),
        ["第 %d 卷 / 共 %d 卷" % (index + 1, len(books)), "约 %d 万字" % round(chars / 10000)],
        hero=_docx_cover_art(plan, vol, index),
    )
    add_fanli(doc, plan)
    add_toc_page(doc)
    # 目录页末尾已有分页符，正文第一页不能再分一次
    _add_volume_body(doc, plan, vol, volume_heading=False, at_page_top=True)

    ensure_dir(out_dir)
    fn = os.path.join(out_dir, "%02d_%s.docx" % (
        index + 1, safe_filename(vol["title"].replace("　", "_"))))
    doc.save(fn)
    return fn


def build_toc_docx(plan: Plan, books: list[dict], out_dir: str) -> str:
    """00_总目录与说明.docx：全书概览 + 每卷篇目。"""
    doc = setup_doc()
    doc.core_properties.title = plan.title + " · 总目录与说明"
    doc.core_properties.author = author_name(plan) or None

    total_stories = 0
    total_chars = 0
    rows = []
    for v in books:
        n, c = book_stats(v)
        total_stories += n
        total_chars += c
        rows.append((n, c))

    para(doc, "", size=11, space_before=80)
    para(doc, plan.title, SONG, 13, False, "888888", align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "总目录与说明", HEI, 26, True, "000000",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_before=26)
    para(doc, "", size=11, space_before=30)
    para(doc, "全书共 %d 卷、%d 篇、约 %d 万字。" % (
        len(books), total_stories, round(total_chars / 10000)),
        SONG, 11, False, "444444", align=WD_ALIGN_PARAGRAPH.CENTER)
    para(doc, "文字取自 %s，版权归原作方所有，仅供个人阅读存档。" % source_short(plan),
         SONG, 9.5, False, "8C8C8C", align=WD_ALIGN_PARAGRAPH.CENTER, space_before=6)
    para(doc, "", size=11, space_before=24)
    for line in ["凡例"] + list(plan.fanli):
        para(doc, line, SONG, 9.5, False, "5A5A5A", space_before=1, space_after=1)

    doc.add_page_break()
    para(doc, "总目录", HEI, 18, True, "000000",
         align=WD_ALIGN_PARAGRAPH.CENTER, space_after=16)

    for i, v in enumerate(books):
        h = doc.add_paragraph(style="Heading 2")
        set_run(h.add_run(v["title"]), HEI, 13, True, "000000")
        h.paragraph_format.space_before = Pt(14)
        h.paragraph_format.space_after = Pt(4)
        meta = "　".join(s["name"] for s in v["sections"])
        para(doc, meta + "　（%d 篇，约 %d 字）" % rows[i],
             SONG, 9, False, "999999", space_after=6)
        for s in v["sections"]:
            for st in s["stories"]:
                p = doc.add_paragraph()
                p.paragraph_format.left_indent = Cm(0.9)
                p.paragraph_format.space_before = Pt(1)
                p.paragraph_format.space_after = Pt(1)
                set_run(p.add_run("· " + st["title"]), SONG, 10)

    ensure_dir(out_dir)
    fn = os.path.join(out_dir, "00_总目录与说明.docx")
    doc.save(fn)
    return fn


def build_combined_docx(plan: Plan, books: list[dict], out_dir: str,
                        out_name: str = "全一册") -> str:
    """把全部卷合成一本书（单文件，目录横跨全书，页码连续）。"""
    total_stories = 0
    total_chars = 0
    for v in books:
        n, c = book_stats(v)
        total_stories += n
        total_chars += c

    doc = setup_doc()
    doc.core_properties.title = plan.title + " · 全一册"
    doc.core_properties.author = author_name(plan) or None
    doc.core_properties.comments = (
        "文字稿由 %s 的剧情页清洗生成；演出指令仅保留场景/画面/音乐提示，删去时间轴与特效参数。"
        % source_short(plan)
    )
    add_cover(
        doc, plan, "全一册",
        "共 %d 卷 · %d 篇 · 约 %d 万字" % (len(books), total_stories, round(total_chars / 10000)),
        ["%d 卷合订" % len(books)],
        hero=_docx_cover_art(plan, books[0]) if books else None,
    )
    add_fanli(doc, plan)
    add_toc_page(doc)
    at_top = True
    for vol in books:
        at_top = _add_volume_body(doc, plan, vol, volume_heading=True,
                                  at_page_top=at_top)

    ensure_dir(out_dir)
    fn = os.path.join(out_dir, "%s.docx" % safe_filename(out_name))
    doc.save(fn)
    return fn
