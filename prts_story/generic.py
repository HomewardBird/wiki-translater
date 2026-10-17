# -*- coding: utf-8 -*-
"""通用（非 PRTS）Wiki 页面的剧情解析。

大多数文字 wiki 的剧情段落是「角色：台词」这样的行，加上少量舞台提示和旁白。
这里用一组可读性优先的启发式把它切成与 PRTS 解析器相同的 block 结构，
上层排版（Word / PDF / EPUB）完全复用，不需要改。

局限（会写在界面上）：
    * 表格类对话会被拉平成「角色：台词」，失去原表格的分栏；
    * 特殊的站内模板（折叠、选项卡）只能取到文本，取不到语义。
"""
from __future__ import annotations

import os
import re

from .config import Plan
from .fetch import cache_path
from .wikitext import clean_wikitext

# 说话人：1~14 个字，不含句读与括号
SPEAKER_RE = re.compile(
    r"^(?P<s>[^\s：:，。！？；、“”\"'「」『』【】（）()\[\]<>/\\|]{1,14})[：:]\s*(?P<t>.+)$"
)
# 舞台提示：整行被括号/方头括号包住，或以提示符号开头
CUE_RES = (
    re.compile(r"^【(?P<t>[^】]{1,60})】$"),
    re.compile(r"^[（(](?P<t>[^）)]{1,60})[）)]$"),
    re.compile(r"^(?:※|——|▷|◆|♪|◇|→)\s*(?P<t>.+)$"),
)
SKIP_RE = re.compile(r"^[-=_*~\s]+$")
HEADING_MARK = "@@h"


def _clean_line(line: str) -> str:
    line = line.replace("\u3000", " ").strip()
    line = re.sub(r"[ \t]{2,}", " ", line)
    return line


def _looks_like_dialogue(text: str) -> tuple[str, str] | None:
    m = SPEAKER_RE.match(text)
    if not m:
        return None
    speaker, body = m.group("s").strip(), m.group("t").strip()
    if not body or not speaker:
        return None
    # 「任务奖励：」这类冒号结尾的标签行不是台词
    if body.startswith(("|", "}", "{")) or speaker.endswith(("。", "份")):
        return None
    if len(body) < 1:
        return None
    # 排除时间/数值行，如「12:30」「HP：100」
    if re.fullmatch(r"[\d.:：]+", speaker):
        return None
    return speaker, body


def blocks_from_lines(lines: list[str], keep_headings: bool = True) -> tuple[list[dict], str | None]:
    blocks: list[dict] = []
    header_title: str | None = None
    for raw in lines:
        line = _clean_line(raw)
        if not line or SKIP_RE.match(line):
            continue
        if line.startswith(HEADING_MARK):
            try:
                _, rest = line.split(":", 1)
            except ValueError:
                continue
            title = rest.strip()
            if not title:
                continue
            if header_title is None:
                header_title = title
            if keep_headings:
                blocks.append({"k": "h4", "t": title})
            continue
        for pattern in CUE_RES:
            m = pattern.match(line)
            if m:
                cue = m.group("t").strip()
                if cue and not (blocks and blocks[-1].get("k") == "q" and blocks[-1]["t"] == cue):
                    blocks.append({"k": "q", "t": cue})
                break
        else:
            hit = _looks_like_dialogue(line)
            if hit:
                blocks.append({"k": "d", "s": hit[0], "t": hit[1]})
            else:
                if blocks and blocks[-1].get("k") == "n" and len(line) < 24:
                    # 短行与上一条旁白合并，避免一行一断
                    blocks[-1]["t"] = blocks[-1]["t"] + line
                else:
                    blocks.append({"k": "n", "t": line})
    return _tidy(blocks), header_title


def _tidy(blocks: list[dict]) -> list[dict]:
    """去掉连续重复、首尾多余的小标题。"""
    out: list[dict] = []
    for b in blocks:
        if out and out[-1].get("k") == b["k"] and out[-1].get("t") == b.get("t"):
            continue
        out.append(b)
    while out and out[0].get("k") == "h4":
        out.pop(0)
    return out


def parse_page(text: str) -> tuple[list[dict], str | None]:
    return blocks_from_lines(clean_wikitext(text))


def build_books(plan: Plan, strict: bool = True) -> list[dict]:
    """按 plan 读取通用 wiki 缓存页，组装成书籍数据（结构与 PRTS 版一致）。"""
    books: list[dict] = []
    missing: list[str] = []
    for vol in plan.volumes:
        book = {"title": vol["title"], "sections": []}
        for sec in vol.get("sections", []):
            section = {
                "name": sec["name"],
                "kind": sec.get("kind", "story"),
                "portrait": sec.get("portrait"),
                "stories": [],
            }
            for title in sec.get("pages", []):
                path = cache_path(plan, title)
                if not os.path.isfile(path):
                    missing.append(title)
                    continue
                with open(path, encoding="utf-8") as f:
                    text = f.read()
                blocks, header = parse_page(text)
                if not blocks:
                    print("  !! 页面无有效内容：%s" % title)
                    continue
                chapter = header or _title_of(title)
                section["stories"].append(
                    {"title": chapter, "src": title, "blocks": blocks})
            if section["stories"]:
                book["sections"].append(section)
        if book["sections"]:
            books.append(book)
    if missing and strict:
        raise SystemExit("缺少 %d 个页面的缓存：\n  %s" % (len(missing), "\n  ".join(missing[:20])))
    return books


def _title_of(page_title: str) -> str:
    from .catalog import split_title

    code, name = split_title(page_title)
    return name or page_title
