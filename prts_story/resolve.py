# -*- coding: utf-8 -*-
"""把「一行主题」展开成一组页面。

用户的清单是按内容写的，不是按页面标题写的：

    将进酒                     → 该活动全部剧情页（IW-ST-1、IW-1/BEG、IW-1/END…）
    年、夕、令、重岳的干员密录   → 四个干员的密录页
    五星术师惊蛰干员档案、密录   → 惊蛰干员页 + 该干员密录页
    官号上的特别映像pv【炎：劫争】 → 站外视频，PRTS 没有页面，单独报「不在本站」

展开活动靠活动页本身：抓一次 wikitext，从 {{关卡链接|IW-1}} 与剧情跳转链接里
拿到整批关卡代号/页面标题，再回到索引里取页面。结果按名字缓存，不重复抓。
"""
from __future__ import annotations

import json
import os
import re
import unicodedata
import urllib.parse
from dataclasses import dataclass, field

from .catalog import Entry, search
from .sources import Source, search_pages

# ---------------------------------------------------------------- 意图识别

_ROLES = ("先锋", "近卫", "重装", "狙击", "术师", "医疗", "辅助", "特种")
_RARITY_RE = re.compile(r"^[一二三四五六]星")
_VIDEO_WORDS = ("pv", "映像", "官号", "小故事", "视频", "录像", "宣传片")
_STRIP_WORDS = ("官号上的", "官号", "官方账号的", "特别映像", "映像", "小故事")
_BRACKETS = "【】‹›《》〈〉「」『』“”‘’\"'"
_FILLERS = ("的所有", "所有", "全部", "相关", "系列")
_SPLIT_RE = re.compile(r"[、,，;；/＋+·・|]+")

_SUFFIX_ORDER = {"BEG": 0, "BEG2": 1, "END": 2, "END2": 3, "NBT": 4}
_CODE_SUFFIX_RE = re.compile(r"/([A-Za-z0-9]+)$")
_LINK_RE = re.compile(r"/(?:wiki|w)/([^?#\s]+)")
_STAGE_LINK_RE = re.compile(r"\{\{关卡链接\|([A-Za-z0-9\-]+)")
_STORY_TITLE_RE = re.compile(
    r"([A-Z][A-Za-z0-9]*(?:-[A-Za-z0-9]+){0,3} [^|\[\]{}<>\n]{1,40}?/(?:BEG|END|BEG2|END2|NBT))")
# 「RO5-END-1/NBT」这种标题里没有篇名的页面，也要能认出来
_BARE_TITLE_RE = re.compile(r"(?<![\w/])([A-Z]{2,4}\d?(?:-[A-Za-z0-9]+)*/(?:BEG|END|BEG2|END2|NBT))")
_FAMILY_RE = re.compile(r"([A-Z]{2,4}\d?)-")


@dataclass
class Group:
    """一段连续内容，对应「卷」里的一个 section。"""
    label: str
    kind: str                  # story / secret / archive / page
    pages: list[Entry] = field(default_factory=list)
    line: str = ""

    def to_json(self) -> dict:
        return {
            "label": self.label,
            "kind": self.kind,
            "line": self.line,
            "pages": [{"title": e.title, "name": e.name, "code": e.code,
                       "kind": e.kind} for e in self.pages],
        }


@dataclass
class Miss:
    line: str
    reason: str
    kind: str = "none"         # none / offwiki / ambiguous / unknown-ops
    candidates: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {"line": self.line, "reason": self.reason, "kind": self.kind,
                "candidates": self.candidates[:4]}


@dataclass
class Topic:
    text: str = ""             # 去掉装饰后的正文
    ops: list[str] = field(default_factory=list)
    want_secret: bool = False
    want_archive: bool = False
    video: bool = False
    raw: str = ""


def clean_line(line: str) -> tuple[str, bool]:
    """去掉「官号上的」「特别映像pv」这类装饰，返回 (正文, 是否像是站外视频)。"""
    t = unicodedata.normalize("NFKC", line).strip()
    video = any(w in t.lower() for w in _VIDEO_WORDS)
    for w in _STRIP_WORDS:
        t = t.replace(w, "")
    t = re.sub(r"(?i)pv(?![a-z])", "", t)
    for ch in _BRACKETS:
        t = t.replace(ch, "")
    for w in _FILLERS:
        t = t.replace(w, "")
    t = re.sub(r"\s+", " ", t)
    return t.strip(" 的、,，·:：-—"), video


def _strip_role(op: str) -> str:
    """`五星术师惊蛰` → `惊蛰`。"""
    m = _RARITY_RE.match(op)
    if m:
        op = op[m.end():]
    for role in _ROLES:
        if op.startswith(role) and len(op) > len(role):
            op = op[len(role):]
            break
    return op.strip()


def parse_topic(line: str) -> Topic:
    text, video = clean_line(line)
    topic = Topic(text=text, video=video, raw=line)
    if not text:
        return topic

    tail = text
    m = re.search(r"(?:的)?(?:干员)?密录$", tail)
    if m:
        topic.want_secret = True
        tail = tail[:m.start()]
    if "档案" in tail:
        topic.want_archive = True
        tail = re.sub(r"(?:的)?(?:干员)?档案$", "", tail)
        tail = tail.replace("干员档案", "").replace("档案", "")
    head = re.match(r"^(?:干员)?密录[ ·:：]*(.+)$", tail)
    if head:
        topic.want_secret = True
        tail = head.group(1)

    tail = tail.strip(" 的、,，·:：-—")
    ops = []
    for part in _SPLIT_RE.split(tail):
        op = _strip_role(part.strip(" 的"))
        if op:
            ops.append(op)
    topic.ops = ops if (topic.want_secret or topic.want_archive) else []
    return topic


# ---------------------------------------------------------------- 展开：干员密录 / 档案

def _secret_pages(cat, op: str) -> list[Entry]:
    pat = re.compile(r"^%s/干员密录/(\d+)$" % re.escape(op))
    found = []
    for e in cat.entries:
        m = pat.match(e.title)
        if m:
            found.append((int(m.group(1)), e))
    return [e for _, e in sorted(found)]


def _operator_pages(cat, ops: list[str]) -> list[Entry]:
    """干员主页面（档案）。索引只收剧情/密录，所以按标题直接合成 Entry。"""
    out = []
    for op in ops:
        hit = next((e for e in cat.entries if e.title == op), None)
        out.append(hit or Entry(title=op, name=op, code="", kind="page"))
    return out


def _expand_ops(cat, topic: Topic) -> list[Group]:
    groups: list[Group] = []
    if topic.want_archive:
        pages = _operator_pages(cat, topic.ops)
        groups.append(Group(label="干员档案·%s" % "、".join(topic.ops),
                            kind="archive", pages=pages, line=topic.raw))
    if topic.want_secret:
        for op in topic.ops:
            pages = _secret_pages(cat, op)
            if pages:
                groups.append(Group(label="干员密录·%s" % op, kind="secret",
                                    pages=pages, line=topic.raw))
    return groups


# ---------------------------------------------------------------- 展开：活动 → 整卷剧情

@dataclass
class EventSpec:
    page: str = ""
    titles: list[str] = field(default_factory=list)    # 剧情页标题，按出现顺序
    codes: list[str] = field(default_factory=list)     # 关卡代号，按出现顺序
    families: list[str] = field(default_factory=list)  # 代号族，如 IW / FM


def _safe(name: str) -> str:
    return re.sub(r"[^\w\u3400-\u9fff-]+", "_", name)[:80] or "page"


def _read_cache(path: str) -> dict | None:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def _write_cache(path: str, data: dict) -> None:
    try:
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(data, f, ensure_ascii=False)
    except OSError:
        pass


def event_spec(source: Source, name: str) -> EventSpec | None:
    """活动名 → 该活动的剧情页标题 / 关卡代号（带缓存）。"""
    path = os.path.join(source.cache_dir("resolved"), _safe(name) + ".json")
    cached = _read_cache(path)
    if cached and (cached.get("codes") or cached.get("titles")):
        return EventSpec(page=cached.get("page") or "",
                         titles=cached.get("titles") or [],
                         codes=cached.get("codes") or [],
                         families=cached.get("families") or [])

    for title in _search_candidates(source, name):
        try:
            text = source.fetch_text(title)
        except Exception:  # noqa: BLE001
            continue
        codes = list(dict.fromkeys(c.upper() for c in _STAGE_LINK_RE.findall(text)))
        titles = list(dict.fromkeys(_STORY_TITLE_RE.findall(text)
                                    + _BARE_TITLE_RE.findall(text)))
        families = list(dict.fromkeys(
            m.group(1) for m in (re.match(r"([A-Z]{2,4}\d?)-", x)
                                 for x in list(codes) + list(titles)) if m))
        if codes or titles:
            spec = EventSpec(page=title, titles=titles, codes=codes, families=families)
            _write_cache(path, {"name": name, "page": spec.page, "titles": spec.titles,
                                "codes": spec.codes, "families": spec.families})
            return spec
    return None


def _search_candidates(source: Source, name: str) -> list[str]:
    try:
        hits = search_pages(source, name, limit=10)
    except Exception:  # noqa: BLE001
        return []
    bad = ("OST", "家具", "单曲", "贴纸", "活动公告", "复刻", "卡池", "寻访", "时装", "音乐")

    def rank(title: str) -> tuple:
        if title == name:
            return (0, len(title))
        if title.startswith(name):
            return (3 if any(b in title for b in bad) else 1, len(title))
        return (4 if any(b in title for b in bad) else 2, len(title))

    return sorted((h["title"] for h in hits), key=rank)[:4]


def _stage_suffix(title: str) -> str:
    m = _CODE_SUFFIX_RE.search(title)
    return (m.group(1).upper() if m else "NBT")


def _code_key(code: str) -> tuple:
    """IW-ST-1 / IW-1 / FM-ST3 / IW-EX-2 → 剧情顺序。"""
    upper = code.upper()
    num = re.search(r"(\d+)", upper)
    return (1 if "EX" in upper else 0,
            int(num.group(1)) if num else 0,
            1 if "ST" in upper else 0,
            _SUFFIX_ORDER.get(_stage_suffix(code), 5),
            upper)


def pages_for_titles(cat, titles: list[str]) -> list[Entry]:
    by_title = {e.title: e for e in cat.entries}
    return [by_title[t] for t in titles if t in by_title]


def pages_for_codes(cat, codes: list[str], cap: int = 400) -> list[Entry]:
    """代号列表 → 该活动的剧情页，按关卡顺序、同关卡 BEG 在 END 前。"""
    by_code: dict[str, list[Entry]] = {}
    for e in cat.entries:
        if e.kind == "story" and e.code:
            by_code.setdefault(e.code.upper(), []).append(e)
    out = []
    for code in codes:
        stage = by_code.get(code.upper()) or []
        stage.sort(key=lambda e: (_SUFFIX_ORDER.get(_stage_suffix(e.title), 9), e.title))
        out.extend(stage)
        if len(out) >= cap:
            break
    return out


def pages_for_family(cat, family: str, cap: int = 400) -> list[Entry]:
    """代号族 → 该族的全部剧情页。

    有代码的按代码走；「RO5-END-1/NBT」这类没有代码的，直接按标题前缀认。
    """
    head = family.upper() + "-"
    picked = [e for e in cat.entries
              if e.code.upper().startswith(head)
              or (not e.code and e.title.upper().startswith(head))]
    picked.sort(key=lambda e: (_code_key(e.code or e.title),
                               _SUFFIX_ORDER.get(_stage_suffix(e.title), 9), e.title))
    return picked[:cap]


def _name_variants(name: str) -> list[str]:
    """「岁的界园志异结局」→ 先试全名，再试去掉意图词的名字。"""
    out = [name]
    trimmed = re.sub(r"(?:的)?(?:所有|全部)?(?:结局|剧情|CG|访谈录|全集)$", "", name).strip()
    if trimmed and trimmed != name:
        out.append(trimmed)
    return out


def _expand_name(cat, topic: Topic, remote: bool) -> tuple[list[Group], Miss | None]:
    name = topic.text
    if not name:
        return [], Miss(topic.raw, "这一行是空的", "none")

    exact = [e for e in cat.entries if e.title == name]
    if len(exact) == 1:
        return [Group(label=name, kind="page", pages=exact, line=topic.raw)], None

    # 「WR-6 画中」这种关卡写法不要去猜整卷
    looks_stage = bool(re.match(r"(?i)^[a-z]{1,4}\d*[-\s/]", name))
    if remote and not looks_stage:
        for candidate in _name_variants(name):
            spec = event_spec(cat.source, candidate)
            if spec is None:
                continue
            by_titles = pages_for_titles(cat, spec.titles)
            by_codes = pages_for_codes(cat, spec.codes)
            by_family = []
            for fam in spec.families:
                by_family += pages_for_family(cat, fam)
            best = max((by_titles, by_codes, by_family), key=len)
            seen, pages = set(), []
            for e in best:
                if e.title not in seen:
                    seen.add(e.title)
                    pages.append(e)
            if len(pages) >= 2:
                label = spec.page or candidate
                return [Group(label="%s（%d 篇）" % (label, len(pages)),
                              kind="story", pages=pages, line=topic.raw)], None

    hits = (search(cat, name, limit=6, remote=remote).get("results") or [])
    if len(hits) >= 1:
        by_title = {e.title: e for e in cat.entries}
        in_index = [by_title[h["title"]] for h in hits if h["title"] in by_title]
        # 「洪炉示岁」这类：多篇篇名都以这一行开头，就是一组
        same = [e for e in in_index if e.name.startswith(name) or e.title.startswith(name)]
        if len(same) >= 2:
            return [Group(label="%s（%d 篇）" % (name, len(same)), kind="story",
                          pages=same, line=topic.raw)], None
        # 「WR-6 画中」这类：对上的都是同一关的 /BEG /END，一起收
        code_set = {e.code for e in in_index}
        if len(in_index) >= 2 and len(code_set) == 1 and "" not in code_set:
            return [Group(label=in_index[0].name or name, kind="story",
                          pages=in_index, line=topic.raw)], None
        if len(in_index) == 1:
            return [Group(label=in_index[0].name or name, kind="page",
                          pages=in_index, line=topic.raw)], None
        if in_index:
            return [], Miss(topic.raw, "这一行能对上好几篇，但组不成一卷",
                            "ambiguous", [e.title for e in in_index[:4]])
    if topic.video:
        return [], Miss(topic.raw, "PRTS 站内也没有这个标题（像是官号视频/站外内容）",
                        "offwiki")
    return [], Miss(topic.raw, "索引和站内搜索都没有这一行", "none")


# ---------------------------------------------------------------- 对外入口

def resolve_line(cat, line: str, remote: bool = True) -> tuple[list[Group], Miss | None]:
    """一行 → (页面组, 失败原因)。"""
    raw = (line or "").strip()
    if not raw:
        return [], Miss(line, "空行", "none")

    # 直接粘 wiki 链接：https://prts.wiki/w/WR-10_夕/BEG
    m = _LINK_RE.search(raw)
    if m:
        title = urllib.parse.unquote(m.group(1)).replace("_", " ")
        hit = next((e for e in cat.entries if e.title == title), None)
        if hit is not None:
            return [Group(label=title, kind="page", pages=[hit], line=raw)], None
        return [], Miss(raw, "链接指向的页面不在索引里：%s" % title, "none")

    topic = parse_topic(raw)
    if topic.ops:
        groups = _expand_ops(cat, topic)
        if groups:
            return groups, None
        return [], Miss(raw, "没找到这些干员的密录/档案：%s" % "、".join(topic.ops),
                        "unknown-ops", [])

    groups, miss = _expand_name(cat, topic, remote)
    if groups:
        return groups, None
    # 一行里写了多个主题：「画中人、将进酒」
    parts = [p for p in _SPLIT_RE.split(topic.text) if len(p) >= 2]
    if len(parts) >= 2:
        merged, misses = [], []
        for part in parts:
            sub = Topic(text=part, video=topic.video, raw=raw)
            got, sub_miss = _expand_name(cat, sub, remote)
            merged += got
            if sub_miss is not None:
                misses.append(sub_miss)
        if merged:
            return merged, None
        if misses:
            reasons = list(dict.fromkeys(m.reason for m in misses))
            return [], Miss(raw, "；".join(reasons), misses[0].kind,
                            misses[0].candidates)
    return [], miss


def resolve_lines(cat, lines: list[str], remote: bool = True):
    """多行 → (页面组, 失败清单)，跨行去重，保持用户书写的顺序。"""
    groups: list[Group] = []
    missing: list[Miss] = []
    seen: set[str] = set()
    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        got, miss = resolve_line(cat, line, remote=remote)
        kept = []
        for g in got:
            pages = [p for p in g.pages if p.title not in seen]
            for p in pages:
                seen.add(p.title)
            if pages:
                kept.append(Group(label=g.label, kind=g.kind, pages=pages, line=g.line))
        if kept:
            groups.extend(kept)
        elif miss is not None:
            missing.append(miss)
        # 全是重复页面的行：静默略过，不当成失败
    return groups, missing


def topic_search(cat, query: str, remote: bool = True) -> list[Group]:
    """单独搜索用：把查询当成主题，能展开就给出整组页面。"""
    q = (query or "").strip()
    if len(q) < 2 or len(q) > 24:
        return []
    topic = parse_topic(q)
    if topic.ops:
        return _expand_ops(cat, topic)
    if not remote or re.match(r"(?i)^[a-z]{1,4}\d*[-\s/]", topic.text) or "/" in topic.text:
        return []
    groups, _ = _expand_name(cat, topic, remote=True)
    return [g for g in groups if len(g.pages) >= 2]
