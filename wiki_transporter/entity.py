# -*- coding: utf-8 -*-
"""实体浏览页：把已有的别名表 + 活动表 + 正文共现树组装成可浏览的交叉链接页面。

为什么需要这个模块
------------------
搜索返回的是"篇目列表"，但玩家想看的是"萨卡兹这条线有哪些章节、先读什么"。
ArkPlots 和泰拉纪事靠手写关系数据实现这个体验；这里复用已有数据自动组装：
  - ALIASES（deep.py）：岁家→年/夕/令…  提供实体定义
  - themes.json：活动代号 → 章节清单  提供章节分组
  - smart._tree：正文共现统计  提供交叉链接实体

不引入 LLM：所有数据离线已有，零额外成本。
"""
from __future__ import annotations

import json
import os
import re
import sqlite3
from collections import defaultdict

from . import deep, smart, themes
from .config import user_data_dir


# 章节号 → (类型, 序号) 用于排序
_CH_RE = re.compile(r"^(\d+)-")
_ACT_RE = re.compile(r"^([A-Za-z]{1,4})[-\d]")


def _route_key(code: str) -> tuple:
    """阅读顺序：主线按章号 → 活动按代号 → 其它。"""
    m = _CH_RE.match(code or "")
    if m:
        return (0, int(m.group(1)), _num_after_dash(code))
    a = _ACT_RE.match(code or "")
    if a:
        return (1, sum(ord(c) for c in a.group(1)), _num_after_dash(code))
    return (2, 0, 0)


def _num_after_dash(code: str) -> int:
    m = re.search(r"-(\d+)", code or "")
    return int(m.group(1)) if m else 0


def _arc_label(code: str, arc_map: dict | None = None) -> str:
    """一篇剧情归到哪条线：主线按章号，活动按代号查活动表。"""
    m = _CH_RE.match(code or "")
    if m:
        return "第 %s 章 · 主线" % m.group(1)
    head = (code or "").split("-")[0].upper()
    if not head:
        return "其它剧情"
    if arc_map and head in arc_map:
        return "活动 · " + arc_map[head]
    return "剧情 · " + head


def _build_arc_map() -> dict:
    """活动代号 → 活动短名（从 themes.json 读）。"""
    out: dict[str, str] = {}
    try:
        for a in themes.load() or []:
            for cd in (a.get("codes") or []):
                out.setdefault(str(cd).upper(), a.get("short") or a.get("name") or cd)
    except Exception:
        pass
    return out


def _known_entities() -> dict[str, list[str]]:
    """所有已定义实体（ALIASES 的 key + 值里出现的词）。"""
    out: dict[str, list[str]] = {}
    for key, vals in deep.ALIASES.items():
        out[key] = list(vals)
    return out


def entity_page(name: str, limit: int = 300) -> dict:
    """实体 → {name, aliases, groups, related, total}

    groups: [{label, pages: [{title, code, ...}]}]  按章节分组
    related: [{term, docs}]  交叉链接实体（共现树 top）
    """
    name = (name or "").strip()
    if not name:
        return {"name": "", "aliases": [], "groups": [], "related": [], "total": 0}

    und = deep.understand(name)
    concept = und.get("concept") or name
    terms = und.get("terms") or [name]
    aliases = [t for t in terms if t != concept]

    arc_map = _build_arc_map()

    # 从正文索引召回所有提到该实体的篇目
    hits: list[dict] = []
    scored = [concept] + terms
    try:
        con = smart.connect()
        try:
            ids: set[int] = set()
            for t in scored:
                if not t or len(t) < 2:
                    continue
                for row in con.execute(
                    "select d.id from docs d join docs_fts b on b.rowid=d.id "
                    "where b.body like ?", ("%" + t + "%",)).fetchall():
                    ids.add(row[0])
            for doc_id in ids:
                row = con.execute(
                    "select id, title, name, code, plan from docs where id=?",
                    (doc_id,)).fetchone()
                if not row:
                    continue
                hits.append({"title": row[1], "name": row[2] or row[1],
                             "code": row[3] or "", "plan": row[4] or ""})
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass

    # 按章节分组
    hits.sort(key=lambda h: _route_key(h.get("code") or ""))
    groups_map: dict[str, list[dict]] = defaultdict(list)
    for h in hits:
        label = _arc_label(h.get("code") or "", arc_map)
        groups_map[label].append(h)

    groups = [{"label": label, "pages": pages} for label, pages in groups_map.items()]

    # 共现树 → 交叉链接实体
    related: list[dict] = []
    try:
        con = smart.connect()
        try:
            tree = smart._tree(con, concept, 24, [h["title"] for h in hits][:200])
            for t in tree:
                related.append({"term": t["term"], "docs": t.get("docs", 0)})
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass

    return {
        "name": name, "concept": concept, "aliases": aliases,
        "groups": groups[:40], "related": related[:20],
        "total": len(hits),
    }


def prereq(code: str) -> dict:
    """章节代号 → {before: [...], after: [...], same_activity: [...]}

    自动推导：同活动内按序号排（AF-1→AF-2→AF-3），同章节按序号排（10-1→10-2）。
    跨活动前置关系从 prereqs.json 读（手写，可选）。
    """
    code = (code or "").strip()
    if not code:
        return {"code": "", "before": [], "after": [], "same_activity": []}

    m_ch = _CH_RE.match(code)
    m_act = _ACT_RE.match(code)
    if not m_ch and not m_act:
        return {"code": code, "before": [], "after": [], "same_activity": []}

    same_activity: list[dict] = []
    try:
        con = smart.connect()
        try:
            if m_ch:
                ch_num = m_ch.group(1)
                rows = con.execute(
                    "select distinct code, title, name from docs "
                    "where code like ? and code <> ? "
                    "order by code collate nocase",
                    (ch_num + "-%", code)).fetchall()
            else:
                prefix = m_act.group(1)
                rows = con.execute(
                    "select distinct code, title, name from docs "
                    "where code like ? and code <> ? "
                    "order by code collate nocase",
                    (prefix + "-%", code)).fetchall()
            for r in rows:
                same_activity.append({"code": r[0] or "", "title": r[1],
                                      "name": r[2] or r[1]})
        finally:
            con.close()
    except sqlite3.DatabaseError:
        pass

    same_activity.sort(key=lambda x: _route_key(x.get("code") or ""))
    my_seq = _num_after_dash(code)
    before = [x for x in same_activity if _num_after_dash(x["code"]) < my_seq]
    after = [x for x in same_activity if _num_after_dash(x["code"]) > my_seq]

    # 跨活动前置（手写，可选）
    cross = _cross_prereqs()
    entry = cross.get(code)
    cross_before = entry.get("before", []) if entry else []
    cross_after = entry.get("after", []) if entry else []

    return {
        "code": code,
        "before": before[-6:] + cross_before,
        "after": after[:6] + cross_after,
        "same_activity": same_activity,
    }


_PREREQ_PATH = os.path.join(user_data_dir(), "prereqs.json")


def _cross_prereqs() -> dict:
    """跨活动前置关系（手写 JSON，可选文件）。"""
    if not os.path.isfile(_PREREQ_PATH):
        return {}
    try:
        with open(_PREREQ_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}
