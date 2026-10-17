# -*- coding: utf-8 -*-
"""通用 wikitext → 可读文本行的清洗器（不依赖任何站点模板）。

目标和 PRTS 专用解析的区别：PRTS 的剧情页有完整语义模板（[Dialog name=""] 等），
可以精确还原舞台提示；其他 wiki 没有，只能退一步：
    模板 → 取 text/内容/台词 等参数，其余丢弃
    表格 → 每行拆成 "单元格：单元格"，保住对话表里的「角色 | 台词」
    链接/加粗/注释/脚注 → 去掉标记留文字
    标题 → 输出 `@@h<level>:标题` 标记行
产物是纯文本行列表，再交给 generic.blocks_from_lines 分类。
"""
from __future__ import annotations

import html
import re

# 内容里出现这些参数名时，模板按「正文」保留
_KEEP_PARAMS = ("text", "内容", "台词", "描述", "desc", "title", "名称", "name")
# 这些模板整体丢弃（导航、信息框、剧透折叠之类）
_DROP_TEMPLATES = {
    "剧情导航", "导航", "navbox", "信息框", "infobox", "系列任务", "任务信息",
    "cbox2", "参阅", "提示刷新", "ads", "tabs", "tabber", "clear", "reflist",
    "参考", "ref", "注释", "notelist", "目录", "toc",
}

_TAG_RE = re.compile(r"<[^>]+>")
_HEADING_RE = re.compile(r"^(={1,6})\s*(.+?)\s*\1\s*$")
_COMMENT_RE = re.compile(r"<!--.*?-->", re.S)
_REF_RE = re.compile(r"<ref[^>]*?>.*?</ref>|<ref[^>]*?/\s*>", re.S | re.I)
_MEDIA_RE = re.compile(r"\[\[(?:file|image|文件|图像|图片|media)[^\[\]]*(?:\[\[[^\]]*\]\][^\[\]]*)*\]\]",
                       re.I)
_LINK_RE = re.compile(r"\[\[([^\[\]|]*)\|([^\[\]]*)\]\]|\[\[([^\[\]]*)\]\]")
_TEMPLATE_RE = re.compile(r"\{\{([^{}]*)\}\}")
_EXT_LINK_RE = re.compile(r"\[(?:https?:)?//[^\s\]]+\s*([^\]]*)\]")
_TABLE_RE = re.compile(r"^\s*\{\|.*?^\s*\|\}\s*$", re.S | re.M)


def _strip_template(inner: str) -> str:
    """把一个已去掉花括号的模板内容转成正文（或空串）。"""
    parts = inner.split("|")
    name = parts[0].strip().lower()
    if name in _DROP_TEMPLATES or any(name.startswith(t) for t in ("navbox", "infobox")):
        return ""
    kv = {}
    positional = []
    for part in parts[1:]:
        if "=" in part:
            k, v = part.split("=", 1)
            kv[k.strip().lower()] = v.strip()
        else:
            positional.append(part.strip())
    if name in ("color", "colour", "ruby", "字体", "颜色", "bgcolor"):
        # {{color|red|文本}} / {{ruby|汉字|hànzì}}
        return positional[-1] if positional else ""
    if name in ("lang", "nowrap", "small", "big", "center", "居中", "加粗", "b"):
        return positional[0] if positional else ""
    for key in _KEEP_PARAMS:
        if key in kv and kv[key]:
            return kv[key]
    # 未知模板：只有一个不带等号的长参数（像 {{注音|文字}}）时保留它
    if len(positional) == 1 and len(positional[0]) > 1:
        return positional[0]
    return ""


def expand_templates(text: str, rounds: int = 12) -> str:
    for _ in range(rounds):
        new = _TEMPLATE_RE.sub(lambda m: _strip_template(m.group(1)), text)
        if new == text:
            break
        text = new
    return text


def _table_to_lines(block: str) -> list[str]:
    """把 {| ... |} 表格转成文本行：整行用 || 分隔时拼成「A：B」。"""
    lines = []
    for raw in block.split("\n"):
        t = raw.strip()
        if not t or t.startswith(("{|", "|}", "|-", "|+", "!")):
            continue
        if not t.startswith("|"):
            continue
        t = t.lstrip("|").strip()
        if t.startswith("}"):
            continue
        cells = [c.strip() for c in t.split("||")]
        cells = [c for c in cells if c and not c.startswith(("style=", "class=", "colspan", "rowspan", "align="))]
        if not cells:
            continue
        # 去掉单元格属性（| style="..." | 内容）
        cleaned = []
        for c in cells:
            if "|" in c and re.match(r"^[a-zA-Z-]+\s*=", c.split("|", 1)[0].strip()):
                c = c.split("|", 1)[1].strip()
            cleaned.append(c)
        if len(cleaned) == 2:
            lines.append("%s：%s" % (cleaned[0], cleaned[1]))
        else:
            lines.append("，".join(cleaned))
    return lines


def clean_wikitext(text: str) -> list[str]:
    """返回清洗后的行列表；标题行以 `@@h<level>:` 前缀标记。"""
    if not text:
        return []
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = _COMMENT_RE.sub("", text)
    text = _REF_RE.sub("", text)
    text = re.sub(r"<noinclude>.*?</noinclude>", "", text, flags=re.S | re.I)
    text = re.sub(r"</?(?:includeonly|onlyinclude|nowiki|pre|syntaxhighlight)[^>]*>", "", text, flags=re.I)

    # 表格单独处理（保留对话表里的两列结构）
    out: list[str] = []
    pos = 0
    for m in _TABLE_RE.finditer(text):
        out.extend(_split_simple(text[pos:m.start()]))
        out.extend(_table_to_lines(m.group(0)))
        pos = m.end()
    out.extend(_split_simple(text[pos:]))
    return out


def _split_simple(chunk: str) -> list[str]:
    chunk = expand_templates(chunk)
    chunk = _MEDIA_RE.sub("", chunk)
    chunk = _LINK_RE.sub(lambda m: (m.group(2) if m.group(1) else m.group(3) or "").strip(), chunk)
    chunk = _EXT_LINK_RE.sub(lambda m: m.group(1).strip(), chunk)
    chunk = re.sub(r"<br\s*/?>", "\n", chunk, flags=re.I)
    chunk = re.sub(r"</(p|div|li|tr|dd)>", "\n", chunk, flags=re.I)
    chunk = _TAG_RE.sub("", chunk)
    chunk = html.unescape(chunk)
    chunk = chunk.replace("'''", "").replace("''", "")
    chunk = re.sub(r"[ \t\u00a0]+", " ", chunk)

    lines: list[str] = []
    for raw in chunk.split("\n"):
        t = raw.strip()
        if not t:
            continue
        if t in ("|", "{{", "}}", "|-", "⧼") or t.startswith(("[[分类:", "[[Category:")):
            continue
        m = _HEADING_RE.match(t)
        if m:
            lines.append("@@h%d:%s" % (min(6, len(m.group(1))), m.group(2).strip()))
            continue
        # 列表项 / 缩进
        t2 = re.sub(r"^[*#:;]+\s*", "", t)
        if t2:
            lines.append(t2)
    return lines
