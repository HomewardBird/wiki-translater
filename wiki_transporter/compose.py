# -*- coding: utf-8 -*-
"""把「选中的页面」组装成一本可以排版的书（plan）。

规则尽量贴近人的直觉：
    * `XX-1 名字/BEG` + `XX-1 名字/END` 合成一节（行动前 / 行动后），
      和 PRTS 页面本身的组织方式一致；
    * 代码前缀相同的页面（AF-ST1 / AF-ST2 / AF-ST3）归到同一卷；
    * 卷名优先用用户输入的查询词，其次用命中页面的篇名。
"""
from __future__ import annotations

import re

from .catalog import Entry, split_title
from .config import author_name, safe_filename
from .sources import Source

_SPLIT_SUFFIX = re.compile(r"/(BEG|END|NBT)$", re.I)
_CODE_FAMILY = re.compile(r"^([A-Za-z]+|\d+)")


def _base(title: str) -> str:
    return _SPLIT_SUFFIX.sub("", title).strip()


def _family(entry: Entry, title: str) -> str:
    code = entry.code or split_title(title)[0]
    m = _CODE_FAMILY.match(code)
    if m:
        return m.group(1).upper()
    head = title.split("/")[0]
    return head[:2].upper() if head else "其他"


def _common_prefix(items: list[str]) -> str:
    if not items:
        return ""
    shortest = min(items, key=len)
    for i, ch in enumerate(shortest):
        if any(it[i] != ch for it in items if len(it) > i):
            return shortest[:i]
    return shortest


def compose_plan(source: Source, entries: list[Entry], title: str | None = None,
                 plan_id: str | None = None) -> dict:
    """返回 plan 字典（可直接 Plan.from_dict 使用）。"""
    seen, picked = set(), []
    for e in entries:
        if e.title not in seen:
            seen.add(e.title)
            picked.append(e)
    if not picked:
        raise ValueError("没有选中任何页面")

    # 1. 先把 /BEG /END 收进同一节
    sections: dict[str, dict] = {}
    order: list[str] = []
    for e in picked:
        base = _base(e.title)
        if base not in sections:
            display = _section_name(e, base)
            sections[base] = {
                "name": display,
                "kind": e.kind,
                "pages": [],
                "portrait": _portrait_of(e, base),
                # 让 PRTS 解析器用同一个显示名做篇名，页面上不会出现「节名 + 篇名」两遍
                "titles": {base: display} if e.kind != "secret" else {},
            }
            order.append(base)
        sections[base]["pages"].append(e.title)

    # 2. 再按代码族分卷
    volumes: dict[str, dict] = {}
    vol_order: list[str] = []
    for base in order:
        sec = sections[base]
        entry = next(e for e in picked if _base(e.title) == base)
        fam = _family(entry, base)
        if fam not in volumes:
            volumes[fam] = {"title": "", "sections": [], "_names": []}
            vol_order.append(fam)
        volumes[fam]["sections"].append(sec)
        volumes[fam]["_names"].append(sec["name"])

    book_title = (title or "").strip()
    if not book_title:
        names = [e.name for e in picked if e.name]
        common = _common_prefix(names).strip(" ·-—　")
        book_title = common if len(common) >= 2 else (names[0] if names else "未命名")
    book_title = book_title[:60]

    out_volumes = []
    for fam in vol_order:
        vol = volumes[fam]
        names = [n for n in vol["_names"] if n]
        if len(vol_order) == 1:
            vol_title = book_title
        else:
            common = _common_prefix(names).strip(" ·-—")
            vol_title = common if len(common) >= 2 else (names[0] if names else fam)
        out_volumes.append({"title": vol_title[:40], "sections": vol["sections"]})

    if plan_id is None:
        plan_id = "%s-%s" % (
            source.id,
            re.sub(r"[^0-9A-Za-z\u4e00-\u9fa5]+", "-", book_title)[:40].strip("-"))

    return {
        "id": plan_id,
        "title": book_title,
        "filePrefix": safe_filename(book_title),
        "source_id": source.id,
        "source": "%s（%s）" % (source.name, source.api),
        "fanli": [
            "本书由 Wiki Transporter 从 %s 抓取，仅供个人阅读存档，版权属于原作者与原站点。" % source.name,
            "整理与排版：%s。" % author_name(),
            "原文为 Wiki 页面文本，经过自动整理：演出提示用灰色小字，音乐/换场单独成行。",
            "若原文包含表格或特殊模板，自动转换可能丢失部分版式，可在原站对照阅读。",
        ],
        "volumes": out_volumes,
    }


def compose_groups(source: Source, groups, title: str | None = None) -> dict:
    """按「主题组」成卷：一组一卷，卷名用组的标签。

    groups 是 resolve.Group 列表（每组一批页面，如「画中人」17 篇）。
    节内仍然按关卡把 /BEG /END 收成一节。
    """
    entries, seen = [], set()
    for g in groups:
        for e in g.pages:
            if e.title not in seen:
                seen.add(e.title)
                entries.append(e)
    if not entries:
        raise ValueError("没有选中任何页面")
    if len(groups) <= 1:
        only = groups[0].label if groups else ""
        return compose_plan(source, entries, title=(title or only or None))

    base = compose_plan(source, entries, title=title or "合集")
    volumes = []
    for g in groups:
        sub = compose_plan(source, g.pages, title=g.label or None)
        label = (g.label or sub["title"]).split("（")[0].strip()
        for v in sub["volumes"]:
            volumes.append({"title": label[:40], "sections": v["sections"]})
    base["volumes"] = volumes
    return base


def _section_name(entry: Entry, base: str) -> str:
    """节名：密录用「干员密录 · 干员」，剧情用去掉代码的篇名。"""
    if entry.kind == "secret":
        operator = _portrait_of(entry, base)
        return "干员密录 · %s" % operator if operator else entry.name
    name = split_title(base)[1] or entry.name or base
    return name


def _portrait_of(entry: Entry, base: str) -> str | None:
    """PRTS 干员密录页（`年/干员密录/1`）配一张干员头像。"""
    m = re.match(r"^([^/]+)/干员密录/\d+$", entry.title)
    if m:
        return m.group(1)
    return None
