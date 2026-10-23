# -*- coding: utf-8 -*-
"""把 PRTS 剧情页的 wikitext 解析成结构化书籍数据。

产物结构：
    books = [
      {title, sections: [
        {name, kind, portrait, stories: [
          {title, src, blocks: [
             {k:'d', s:'说话人', t:'台词'},
             {k:'n', t:'旁白/字幕'},
             {k:'h4', t:'小标题'},
             {k:'q', t:'◆ / ♪ 曲名 / ♪ 停 / ▷'},  # 演出提示（已读者化）
             {k:'c', t:['选项A','选项B']},  # 玩家选项
          ]}
        ]}
      ]}
    ]
"""
from __future__ import annotations

import os
import re

from .config import Plan
from .cues import music_cue, scene_cue, stop_music_cue, video_cue
from .fetch import cache_path
from .style import same_title

# 不属于台词的「演出参数」指令，直接丢弃（时间轴 / 淡入淡出 / 音效 / 镜头特效等）
SKIP_PAGE_MARKS = (
    "{{剧情导航}}",
    "{{剧情模拟器",
)

ROLE_MAP = {"name", "multiline"}


def _push_cue(blocks: list[dict], text: str) -> None:
    """追加一条演出提示；空串丢弃，与前一条重复的合并。"""
    if not text:
        return
    if blocks and blocks[-1].get("k") == "q" and blocks[-1]["t"] == text:
        return
    blocks.append({"k": "q", "t": text})


def clean(s: str | None) -> str:
    """去掉 wikitext 标记，只留下可读文本。"""
    if not s:
        return ""
    s = re.sub(r"<br\s*/?>", " ", s, flags=re.I)
    s = re.sub(r"\{\{color\|[^|{}]*\|([^{}]*)\}\}", r"\1", s)
    s = re.sub(r"\{\{术语\|[^|{}]*\|([^{}]*)\}\}", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\|([^{}]*)\}\}", r"\1", s)
    s = re.sub(r"\{\{[^{}]*\}\}", "", s)
    s = re.sub(r"<p=\d+>", "", s, flags=re.I)
    s = re.sub(r"'{2,}", "", s)
    s = re.sub(r"\[\[[^\]|]*\|([^\]]*)\]\]", r"\1", s)
    s = re.sub(r"\[\[([^\]]*)\]\]", r"\1", s)
    s = s.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    s = s.replace("\u00a0", " ")
    s = re.sub(r"[ \t]+", " ", s)
    return s.strip()


def parse_attrs(s: str) -> dict:
    """解析 `[xxx name="张三" text="你好"]` 里的属性。"""
    out = {}
    for m in re.finditer(r'([A-Za-z_]+)\s*=\s*"([^"]*)"', s):
        out[m.group(1).lower()] = m.group(2)
    return out


def page_title(src_title: str, section: dict) -> str:
    """把 wiki 页面名转成读者看到的小节标题。

    `WR-1 初醒/END` → `WR-1 初醒（行动后）`；`RO5-END-2/NBT` 走 section 里的 titles 映射。
    """
    key = re.sub(r"/NBT$", "", src_title)
    titles = section.get("titles") or {}
    if key in titles:
        return titles[key]
    base = re.sub(r"/(BEG|END)$", "", key)
    kind = "（行动后）" if re.search(r"/END$", src_title) else (
        "（行动前）" if re.search(r"/BEG$", src_title) else ""
    )
    return base + kind


def parse_story(text: str, root: str | None = None) -> tuple[list[dict], str | None]:
    """解析一页剧情，返回 (blocks, 密录书名或 None)。"""
    blocks: list[dict] = []
    body = text
    head = body.find("文本数据=")
    if head >= 0:
        body = body[head + len("文本数据="):]

    for raw in body.split("\n"):
        line = raw.rstrip()
        t = line.strip()
        if not t:
            continue
        if t == "}}":
            continue
        if t.startswith(SKIP_PAGE_MARKS):
            continue
        if t.startswith("[[分类:"):
            continue

        m = re.match(r"^\[([A-Za-z_]+)([\s\S]*?)\]([\s\S]*)$", t)
        if m:
            name = m.group(1)
            attrs = parse_attrs(t[1:t.index("]")])
            rest = clean(m.group(3) or "")
            low = name.lower()
            if low in ROLE_MAP and attrs.get("name"):
                if rest:
                    blocks.append({"k": "d", "s": clean(attrs["name"]), "t": rest})
                continue
            if low in ("subtitle", "sticker"):
                x = clean(attrs.get("text") or rest)
                if x:
                    blocks.append({"k": "n", "t": x})
                continue
            if low == "animtext":
                if rest:
                    blocks.append({"k": "n", "t": rest})
                continue
            if low == "header":
                x = rest or clean(attrs.get("text") or "")
                if x:
                    blocks.append({"k": "h4", "t": x})
                continue
            if low == "decision":
                opts = [clean(x) for x in (attrs.get("options") or "").split(";")]
                opts = [x for x in opts if x]
                if opts:
                    blocks.append({"k": "c", "t": opts})
                continue
            if low == "background":
                img = attrs.get("image") or attrs.get("imagegroup") or ""
                _push_cue(blocks, scene_cue(img))
                continue
            if low in ("image", "cgitem", "showitem", "charactercutin"):
                # 镜头 / 立绘 / CG 构图指令：不属于文字稿，直接略过
                continue
            if low == "playmusic":
                key = attrs.get("key") or attrs.get("intro") or ""
                if key:
                    _push_cue(blocks, music_cue(key, root))
                continue
            if low == "stopmusic":
                _push_cue(blocks, stop_music_cue())
                continue
            if low == "video":
                if attrs.get("res"):
                    _push_cue(blocks, video_cue())
                continue
            # charslot / Character / Blocker / Delay / Dialog / 音效 / 特效 / 镜头 … 丢弃
            continue

        x = clean(t)
        if x and not x.startswith("|"):
            blocks.append({"k": "n", "t": x})

    header_title = None
    for b in blocks:
        if b["k"] == "h4":
            header_title = b["t"]
            break
    return blocks, header_title


def parse_archive(text: str) -> list[dict]:
    """解析干员档案页（模组/档案文本），返回按档案段落分节的内容。"""
    blocks: list[dict] = []
    start = text.find("==干员档案==")
    end = text.find("==语音记录==")
    if start < 0:
        return blocks
    seg = text[start:end if end > start else len(text)]

    names = {}
    for n in re.finditer(r"\|档案(\d)=([^\n|]*)", seg):
        names[n.group(1)] = clean(n.group(2))

    for m in re.finditer(r"\|档案(\d)文本=([\s\S]*?)(?=\||\}\})", seg):
        idx = m.group(1)
        if idx not in names:
            continue
        body_text = re.sub(r"\n\}\}$", "", m.group(2)).strip()
        blocks.append({"k": "h4", "t": names[idx]})
        for line in body_text.split("\n"):
            x = clean(line)
            if x:
                blocks.append({"k": "n", "t": x})
    return blocks


def drop_dup_head(blocks: list[dict], story_title: str) -> list[dict]:
    """篇首的小标题如果就是篇名本身，删掉——否则页面上会出现两次同一个书名。

    干员密录的正文第一个 `[header]` 就是密录标题（`《千年一叹》`），而篇名也取自它，
    读者看到的会是「篇名 + 立绘 + 又一次居中的篇名」，层级反而糊了。
    """
    if blocks and blocks[0].get("k") == "h4" and same_title(blocks[0].get("t", ""), story_title):
        return blocks[1:]
    return blocks


def block_chars(blocks: list[dict]) -> int:
    total = 0
    for b in blocks:
        t = b.get("t") or ""
        total += len(" / ".join(t) if isinstance(t, list) else t)
        total += len(b.get("s") or "")
    return total


def build_books(plan: Plan, strict: bool = True) -> list[dict]:
    """读取缓存，按 plan 组装成分卷书籍数据。

    PRTS 走专用模板解析；其他 Wiki 走通用 wikitext 解析（generic.py）。
    """
    if getattr(plan, "source_id", "prts") != "prts":
        from .generic import build_books as build_generic

        return build_generic(plan, strict=strict)

    books: list[dict] = []
    missing: list[str] = []

    for vol in plan.volumes:
        book = {"title": vol["title"], "sections": []}
        for sec in vol.get("sections", []):
            kind = sec.get("kind", "story")
            section = {
                "name": sec["name"],
                "kind": kind,
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

                if kind == "archive":
                    blocks = parse_archive(text)
                    story_title = sec.get("storyTitle") or "干员档案"
                else:
                    blocks, header = parse_story(text, plan.root)
                    if kind == "secret":
                        story_title = header or sec["name"]
                    else:
                        story_title = page_title(title, sec)

                if not blocks:
                    print("  !! 页面无有效内容：%s" % title)
                    continue
                blocks = drop_dup_head(blocks, story_title)
                section["stories"].append(
                    {"title": story_title, "src": title, "blocks": blocks}
                )
            if section["stories"]:
                book["sections"].append(section)
        books.append(book)

    if missing:
        msg = "缺少 %d 个页面的缓存，请先运行 fetch：\n  %s" % (
            len(missing),
            "\n  ".join(missing[:20]),
        )
        if strict:
            raise SystemExit(msg)
        print("警告：" + msg)
    return books


def book_stats(book: dict) -> tuple[int, int]:
    """返回 (篇数, 字数)。"""
    n = 0
    chars = 0
    for s in book["sections"]:
        for st in s["stories"]:
            n += 1
            chars += block_chars(st["blocks"])
    return n, chars
