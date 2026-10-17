# -*- coding: utf-8 -*-
"""生成 EPUB 3（分卷 + 全一册合订本），零第三方依赖，纯 zipfile 手写。"""
from __future__ import annotations

import datetime
import os
import uuid
import zipfile

from .config import Plan, author_name, ensure_dir, safe_filename
from .images import cached_portrait, cover_art
from .style import color_index, crumb_redundant, cue_parts, is_beat, story_colors

CSS = """@charset "utf-8";
body { font-family: "Songti SC", "SimSun", serif; line-height: 1.65; margin: 0 1.2em;
       color: #1a1a1a; }
h2 { font-family: "Heiti SC", "SimHei", sans-serif; font-size: 1.6em; text-align: center;
     margin: 0.4em 0 0.3em; letter-spacing: 0.04em; }
h3 { font-family: "Heiti SC", "SimHei", sans-serif; font-size: 1em; color: #4a4a4a;
     text-align: center; margin: 1.6em 0 0.8em; letter-spacing: 0.08em; }
p.crumb { font-family: "Heiti SC", "SimHei", sans-serif; font-size: 0.78em; color: #8c8c8c;
          letter-spacing: 0.16em; text-align: center; margin: 1.4em 0 0.6em; text-indent: 0; }
hr.rule { width: 26%; margin: 0.2em auto 1.8em; border: 0; border-top: 1px solid #d8d8d8; }
p.dlg { margin: 0 0 0.18em; text-indent: 0; }
p.dlg.chg { margin-top: 0.5em; }
p.dlg.beat { margin-top: 0.8em; margin-bottom: 0.35em; }
p.dlg .s { font-family: "Heiti SC", "SimHei", sans-serif; }
p.nar { margin: 0.5em 0; text-indent: 2em; color: #333333; }
p.cues { margin: 0.8em 0; text-align: center; text-indent: 0; }
p.cues .cue { display: block; font-size: 0.8em; letter-spacing: 0.06em; color: #8a8a8a; }
p.cues .cl { color: #b3b3b3; }
p.opt { margin: 0.6em 0; font-size: 0.86em; color: #6b6b6b; text-indent: 2em; }
p.portrait { text-align: center; margin: 0.3em 0 1em; text-indent: 0; }
p.portrait img { width: 34%; max-width: 150px; height: auto; }
p.tp { text-align: center; }
.c1 { color: #8a4b41; } .c2 { color: #3a5680; } .c3 { color: #77602b; } .c4 { color: #3b6355; }
.c5 { color: #6a4b78; } .c6 { color: #7c5533; } .c7 { color: #2f6669; } .c8 { color: #4b4b55; }
/* 封面页：深色底、居中立绘、书名在下 */
.epubcover { background: #0d0f14; color: #ffffff; min-height: 92vh; margin: -0.6em -1.2em 0;
             padding: 2.2em 1.2em 2em; text-align: center; }
.epubcover .coverart { margin: 0 auto 0.9em; }
.epubcover .coverart img { width: 90%; max-width: 380px; height: auto; }
.epubcover h1 { font-family: "Songti SC", "SimSun", serif; font-size: 1.75em;
                letter-spacing: 0.08em; margin: 0.9em 0 0.4em; color: #f4efe6; }
.epubcover .csub { color: #b9b3a6; font-size: 0.86em; letter-spacing: 0.08em; margin: 0.2em 0 0; }
.epubcover .cbrand { color: #c8a462; font-size: 0.72em; letter-spacing: 0.3em;
                     margin-top: 1.6em; text-indent: 0; }
"""

XHTML_HEAD = (
    '<?xml version="1.0" encoding="utf-8"?>\n'
    '<!DOCTYPE html>\n'
    '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN" lang="zh-CN">\n'
    "<head><meta charset=\"utf-8\"/><title>%s</title>"
    '<link rel="stylesheet" type="text/css" href="style.css"/></head><body>\n'
)


def xesc(s: str) -> str:
    return (s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def _story_xhtml(story: dict, crumb: str | None, portrait_href: str | None = None) -> str:
    parts = [XHTML_HEAD % xesc(story["title"])]
    if crumb and not crumb_redundant(crumb, story["title"]):
        parts.append('<p class="crumb">%s</p>\n' % xesc(crumb))
    parts.append("<h2>%s</h2>\n" % xesc(story["title"]))
    if portrait_href:
        parts.append('<p class="portrait"><img src="%s" alt="%s"/></p>\n'
                     % (xesc(portrait_href), xesc(story["title"])))
    parts.append('<hr class="rule"/>\n')
    parts.append(_blocks_xhtml(story["blocks"]))
    parts.append("</body></html>")
    return "".join(parts)


def _cue_line(text: str) -> str:
    c = cue_parts(text)
    if not c:
        return ""
    if c["kind"] == "other":
        return '<span class="cue cv">%s</span>' % xesc(c["value"])
    if c["value"]:
        return ('<span class="cue"><span class="cl">%s</span> · '
                '<span class="cv">%s</span></span>' % (xesc(c["label"]), xesc(c["value"])))
    return '<span class="cue cl">%s</span>' % xesc(c["label"])


def _blocks_xhtml(blocks: list[dict]) -> str:
    """正文：台词两列不挤、旁白楷体、演出提示居中成组。

    EPUB 主要给手机看，屏幕本来就窄，所以不做 PDF 那种「说话人一列」的悬垂缩进，
    只保留颜色和间距——窄屏上多缩进一格就少两个字。
    """
    parts: list[str] = []
    colors = story_colors(blocks)
    prev_speaker: str | None = None
    i = 0
    while i < len(blocks):
        b = blocks[i]
        kind = b["k"]
        if kind == "q":
            lines = []
            while i < len(blocks) and blocks[i]["k"] == "q":
                one = _cue_line(blocks[i].get("t") or "")
                if one:
                    lines.append(one)
                i += 1
            if lines:
                parts.append('<p class="cues">%s</p>\n' % "".join(lines))
            prev_speaker = None
            continue
        if kind == "h4":
            parts.append("<h3>%s</h3>\n" % xesc(b["t"]))
        elif kind == "d":
            name = b.get("s") or ""
            text = b.get("t") or ""
            cls = ["dlg"]
            if prev_speaker != name:
                cls.append("chg")
            if is_beat(text):
                cls.append("beat")
            parts.append('<p class="%s"><span class="s c%d">%s：</span>%s</p>\n'
                         % (" ".join(cls), color_index(name, colors), xesc(name), xesc(text)))
            prev_speaker = name
        elif kind == "n":
            parts.append('<p class="nar">%s</p>\n' % xesc(b["t"]))
            prev_speaker = None
        elif kind == "c":
            parts.append('<p class="opt">【选项】%s</p>\n' % xesc("　／　".join(b["t"])))
            prev_speaker = None
        i += 1
    return "".join(parts)


def _fanli_xhtml(plan: Plan) -> str:
    parts = [XHTML_HEAD % "凡例", "<h2>凡例</h2>\n"]
    for line in plan.fanli:
        parts.append('<p class="nar">%s</p>\n' % xesc(line))
    parts.append("</body></html>")
    return "".join(parts)


def _write_epub(path: str, book_id: str, title: str, chapters: list[tuple[str, str, str]],
                nav_tree: list[dict], images: dict[str, bytes] | None = None) -> None:
    """chapters: [(filename, title, xhtml)]；nav_tree: [{title, href?, children?}]"""
    images = images or {}
    nav = ['<?xml version="1.0" encoding="utf-8"?>', "<!DOCTYPE html>",
           '<html xmlns="http://www.w3.org/1999/xhtml" '
           'xmlns:epub="http://www.idpf.org/2007/ops" xml:lang="zh-CN">',
           '<head><meta charset="utf-8"/><title>目录</title></head><body>',
           '<nav epub:type="toc" id="toc"><h1>目录</h1><ol>']

    def nav_items(nodes):
        for node in nodes:
            if node.get("children"):
                label = xesc(node["title"])
                nav.append('<li><span>%s</span><ol>' % label)
                nav_items(node["children"])
                nav.append("</ol></li>")
            else:
                nav.append('<li><a href="%s">%s</a></li>'
                           % (node["href"], xesc(node["title"])))

    nav_items(nav_tree)
    nav.append("</ol></nav></body></html>")
    nav_x = "\n".join(nav)

    ncx = ['<?xml version="1.0" encoding="utf-8"?>',
           '<ncx xmlns="http://www.daisy.org/z3986/2005/ncx/" version="2005-1"><head>',
           '<meta name="dtb:uid" content="%s"/></head><docTitle><text>%s</text></docTitle><navMap>'
           % (book_id, xesc(title))]
    counter = {"n": 0}

    def ncx_items(nodes):
        for node in nodes:
            counter["n"] += 1
            i = counter["n"]
            ncx.append('<navPoint id="np%d" playOrder="%d"><navLabel><text>%s</text></navLabel>'
                       % (i, i, xesc(node["title"])))
            if node.get("href"):
                ncx.append('<content src="%s"/>' % node["href"])
            if node.get("children"):
                ncx_items(node["children"])
            ncx.append("</navPoint>")

    ncx_items(nav_tree)
    ncx.append("</navMap></ncx>")
    ncx_x = "\n".join(ncx)

    items = ['<item id="nav" href="nav.xhtml" media-type="application/xhtml+xml" properties="nav"/>',
             '<item id="ncx" href="toc.ncx" media-type="application/x-dtbncx+xml"/>',
             '<item id="css" href="style.css" media-type="text/css"/>']
    for n, href in enumerate(images, 1):
        items.append('<item id="img%d" href="%s" media-type="%s"/>'
                     % (n, xesc(href), _media_type(href)))
    spine = []
    for i, (fn, _t, _x) in enumerate(chapters, 1):
        items.append('<item id="c%d" href="%s" media-type="application/xhtml+xml"/>' % (i, fn))
        spine.append('<itemref idref="c%d"/>' % i)

    opf = ['<?xml version="1.0" encoding="utf-8"?>',
           '<package xmlns="http://www.idpf.org/2007/opf" version="3.0" unique-identifier="bookid">',
           '<metadata xmlns:dc="http://purl.org/dc/elements/1.1/">',
           '<dc:identifier id="bookid">%s</dc:identifier>' % book_id,
           "<dc:title>%s</dc:title>" % xesc(title),
           "<dc:creator>%s</dc:creator>" % xesc(author_name()),
           "<dc:language>zh-CN</dc:language>",
           "<dc:description>%s；演出指令仅保留场景/画面/音乐提示。</dc:description>"
           % xesc(title),
           '<meta property="dcterms:modified">%s</meta>'
           % datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
           "</metadata><manifest>"] + items + ['</manifest><spine toc="ncx">'] + spine + ["</spine></package>"]

    ensure_dir(os.path.dirname(path))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/epub+zip",
                   compress_type=zipfile.ZIP_STORED)
        z.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0" encoding="utf-8"?>\n<container version="1.0" '
            'xmlns="urn:oasis:names:tc:opendocument:xmlns:container"><rootfiles><rootfile '
            'full-path="OEBPS/content.opf" media-type="application/oebps-package+xml"/>'
            "</rootfiles></container>",
            compress_type=zipfile.ZIP_DEFLATED,
        )
        z.writestr("OEBPS/content.opf", "\n".join(opf), compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/nav.xhtml", nav_x, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/toc.ncx", ncx_x, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("OEBPS/style.css", CSS, compress_type=zipfile.ZIP_DEFLATED)
        for href, blob in images.items():
            z.writestr("OEBPS/" + href, blob, compress_type=zipfile.ZIP_STORED)
        for fn, _t, xhtml in chapters:
            z.writestr("OEBPS/" + fn, xhtml, compress_type=zipfile.ZIP_DEFLATED)


def _book_id(plan: Plan, suffix: str) -> str:
    # 命名空间前缀保持 prts-story：改它会让同一本书在阅读器里变成另一本书
    return "urn:uuid:" + str(uuid.uuid5(uuid.NAMESPACE_URL, "prts-story/%s/%s" % (plan.id, suffix)))


def _media_type(href: str) -> str:
    return "image/svg+xml" if href.lower().endswith(".svg") else "image/png"


def _hero_href(plan: Plan, book: dict, images: dict[str, bytes], index: int = 0) -> str | None:
    """把该卷的封面图（本地图片 / 立绘 / 占位图）读进 images，返回 EPUB 内部相对路径。"""
    path, _kind = cover_art(plan, book, index)
    if not path:
        return None
    stem = safe_filename(os.path.basename(path).rsplit(".", 1)[0])
    ext = os.path.splitext(path)[1].lower() or ".png"
    href = "img/cover-%s%s" % (stem, ext)
    if href not in images:
        try:
            with open(path, "rb") as f:
                images[href] = f.read()
        except OSError:
            return None
    return href


def _cover_xhtml(title: str, subtitle: str, hero: str | None) -> str:
    art = ('<div class="coverart"><img src="%s" alt=""/></div>' % hero) if hero else ""
    sub = '<p class="csub">%s</p>' % subtitle if subtitle else ""
    return ('<?xml version="1.0" encoding="utf-8"?><!DOCTYPE html>'
            '<html xmlns="http://www.w3.org/1999/xhtml" xml:lang="zh-CN">'
            '<head><meta charset="utf-8"/><title>封面</title>'
            '<link rel="stylesheet" type="text/css" href="style.css"/></head>'
            '<body><div class="epubcover">%s<h1>%s</h1>%s'
            '<p class="cbrand">安巢鸟 · 整理</p></div></body></html>'
            % (art, title, sub))


def _portrait_href(plan: Plan, sec: dict, images: dict[str, bytes]) -> str | None:
    """把该节的干员头像读进 images，返回 EPUB 内部相对路径。"""
    operator = sec.get("portrait")
    if not operator:
        return None
    path = cached_portrait(plan, operator)
    if not path:
        return None
    href = "img/%s.png" % safe_filename(operator)
    if href not in images:
        try:
            with open(path, "rb") as f:
                images[href] = f.read()
        except OSError:
            return None
    return href


def build_volume_epub(plan: Plan, books: list[dict], index: int, out_dir: str) -> str:
    book = books[index]
    images: dict[str, bytes] = {}
    hero = _hero_href(plan, book, images, index) or _hero_href(plan, books[0], images, 0)
    chapters: list[tuple[str, str, str]] = [
        ("ch001.xhtml", "封面", _cover_xhtml(book["title"], "", hero)),
        ("ch002.xhtml", "凡例", _fanli_xhtml(plan)),
    ]
    nav_tree: list[dict] = [{"title": "凡例", "href": "ch002.xhtml"}]
    multi = len(book["sections"]) > 1
    for sec in book["sections"]:
        href = _portrait_href(plan, sec, images)
        for j, story in enumerate(sec["stories"]):
            fn = "ch%03d.xhtml" % (len(chapters) + 1)
            crumb = sec["name"] if multi else None
            chapters.append((fn, story["title"],
                             _story_xhtml(story, crumb, href if j == 0 else None)))
            nav_tree.append({"title": story["title"], "href": fn})

    path = os.path.join(out_dir, "%02d_%s.epub" % (
        index + 1, safe_filename(book["title"].replace("　", "_"))))
    _write_epub(path, _book_id(plan, book["title"]), book["title"], chapters, nav_tree, images)
    return path


def build_combined_epub(plan: Plan, books: list[dict], out_dir: str,
                        out_name: str = "全一册") -> str:
    title = plan.title + " · 全一册"
    images: dict[str, bytes] = {}
    hero = _hero_href(plan, books[0], images) if books else None
    chapters: list[tuple[str, str, str]] = [
        ("ch001.xhtml", "封面", _cover_xhtml(title, "", hero)),
        ("ch002.xhtml", "凡例", _fanli_xhtml(plan)),
    ]
    nav_tree: list[dict] = [{"title": "凡例", "href": "ch002.xhtml"}]

    for vol in books:
        vol_node: dict = {"title": vol["title"], "children": []}
        for sec in vol["sections"]:
            sec_node: dict = {"title": sec["name"], "children": []}
            href = _portrait_href(plan, sec, images)
            for j, story in enumerate(sec["stories"]):
                fn = "ch%03d.xhtml" % (len(chapters) + 1)
                chapters.append((fn, story["title"],
                                 _story_xhtml(story, vol["title"],
                                              href if j == 0 else None)))
                sec_node["children"].append({"title": story["title"], "href": fn})
            if len(vol["sections"]) > 1:
                vol_node["children"].append(sec_node)
            else:
                vol_node["children"].extend(sec_node["children"])
        nav_tree.append(vol_node)

    path = os.path.join(out_dir, "%s.epub" % safe_filename(out_name))
    _write_epub(path, _book_id(plan, "combined"), title, chapters, nav_tree, images)
    return path
