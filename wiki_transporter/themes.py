# -*- coding: utf-8 -*-
"""自动主题表：把「活动」变成「一卷篇目」，不需要人工整理清单。

为什么需要这个模块
-----------------
用户不看剧情、也没法手写清单，但「搜一个词 → 该看哪些剧情、按什么顺序」必须
成立。plans/sui.json 那种整理是人工产物，覆盖不了全部 1733 篇剧情；而 PRTS 的
活动数据是结构化的：

    1. 活动一览页面是 Semantic MediaWiki，`action=ask` 直接给出 298 条活动的
       名称 / 名称缩短 / 活动开始时间（实测一次请求）；
    2. 每个活动页的 wikitext 里有 `{{关卡链接|IW-1}}`，正则取出关卡代号；
    3. 分类:剧情 的篇目标题自带代号（`IW-1 化物/BEG`），按代号前缀归到活动名下。

于是得到与 plans/sui.json 同形的「活动 → 篇目」表：搜「将进酒」就能整卷摆出
IW 系列，搜任何活动名/简称/代号都一样，全程不需要用户整理。

数据落在 user_data_dir/themes.json，搜索时由 gui/server.py 的 _theme_recall 读取。
"""
from __future__ import annotations

import json
import os
import re
import time

from .catalog import load_catalog
from .config import user_data_dir
from .sources import PRTS

THEMES_NAME = "themes.json"
THEMES_VERSION = 1

# 活动页里的关卡链接：{{关卡链接|IW-1}} / {{关卡链接|AF-ST1}} / 主线 {{关卡链接|7-9}}
_STAGE_RE = re.compile(r"\{\{关卡链接\|\s*([A-Za-z0-9]{1,4})-")
# 篇目标题的活动代号前缀：IW-1 化物/BEG、AF-ST1 洪炉示岁·迎春/NBT、7-9 萨卡兹……
_CODE_RE = re.compile(r"^([A-Za-z0-9]{1,4})-")
# 篇目内顺序：ST 剧情关排在编号关之前，同一编号按 BEG→NBT→END
_SUFFIX_ORDER = {"BEG": 1, "NBT": 2, "END": 3}
_NUM_RE = re.compile(r"^([A-Za-z0-9]{1,4})-(ST-)?(\d+)")


def themes_path() -> str:
    return os.path.join(user_data_dir(), THEMES_NAME)


def load() -> list[dict]:
    """读缓存；没有就返回空表（搜索退化成原来的标题 + 正文两层）。"""
    try:
        with open(themes_path(), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict) or data.get("version") != THEMES_VERSION:
        return []
    return [a for a in (data.get("activities") or []) if a.get("pages")]


def stats() -> dict:
    acts = load()
    try:
        built_at = int(os.path.getmtime(themes_path()))
    except OSError:
        built_at = 0
    return {"activities": len(acts),
            "pages": sum(len(a.get("pages") or []) for a in acts),
            "built_at": built_at}


def _activity_rows(source) -> list[dict]:
    """SMW ask：298 条活动的名称/简称/开始时间/分类。"""
    d = source.api_json(
        action="ask",
        query="[[分类:有活动信息的页面]]|?名称|?名称缩短|?活动开始时间cn|?分类"
              "|limit=500|format=json",
    )
    out = []
    for key, val in (d.get("query", {}).get("results") or {}).items():
        po = val.get("printouts") or {}
        name = (po.get("名称") or [None])[0] or key
        short = (po.get("名称缩短") or [None])[0] or key
        start = 0
        stamps = po.get("活动开始时间cn") or []
        if stamps and isinstance(stamps[0], dict):
            try:
                start = int(stamps[0].get("timestamp") or 0)
            except (TypeError, ValueError):
                start = 0
        out.append({"page": key, "name": name, "short": short, "start": start})
    return out


def _page_key(title: str) -> tuple:
    """按章节顺序排：主线数字在前（0-1 → 7-9 → 14-21），活动代号按字母。"""
    m = _NUM_RE.match(title)
    suffix = title.rsplit("/", 1)[-1].upper() if "/" in title else ""
    if not m:
        return ((2, 0, ""), 0, 0, 9)
    head = m.group(1).upper()
    hk = (0, int(head), "") if head.isdigit() else (1, len(head), head)
    return (hk, 0 if m.group(2) else 1, int(m.group(3)), _SUFFIX_ORDER.get(suffix, 9))


def build(force: bool = False, progress=None) -> dict:
    """抓活动表 → 写 themes.json。progress(done, total, msg)。"""
    if not force and load():
        return {"skipped": True, **stats()}

    def say(done, total, msg):
        if progress:
            progress(done, total, msg)

    rows = _activity_rows(PRTS)
    cat = load_catalog(PRTS)
    by_prefix: dict[str, list] = {}
    for e in (cat.entries if cat else []):
        m = _CODE_RE.match(e.title)
        if m:
            by_prefix.setdefault(m.group(1).upper(), []).append(e.title)

    total = len(rows)
    acts: list[dict] = []
    seen: set[tuple] = set()
    for i, r in enumerate(rows):
        say(i, total, r["short"])
        try:
            text = PRTS.fetch_text(r["page"])
        except Exception:
            continue  # 复刻/签到这类没有独立页面，跳过即可
        codes = {m.group(1).upper() for m in _STAGE_RE.finditer(text)}
        codes &= set(by_prefix)
        if not codes:
            continue
        sig = tuple(sorted(codes))
        if sig in seen:  # 复刻活动与本体共用关卡，只留本体
            continue
        seen.add(sig)
        pages = sorted((t for c in codes for t in by_prefix[c]), key=_page_key)
        acts.append({"name": r["short"], "title": r["name"], "start": r["start"],
                     "codes": sorted(codes), "pages": pages})
    # 主线剧情不在活动表里（它是「关卡一览/主线」），单独归成一卷：篇目标题以
    # 数字开头（0-1 … 17-x），不抓页面也能按章节号排好顺序。
    main_pages = sorted((t for c, ts in by_prefix.items() if c.isdigit()
                         for t in ts), key=_page_key)
    if main_pages:
        acts.insert(0, {"name": "主线剧情", "title": "主线剧情·全部章节",
                        "start": 0, "codes": ["主线"], "pages": main_pages})
    acts.sort(key=lambda a: (a["start"] or 0, a["name"]))

    data = {"version": THEMES_VERSION, "built_at": int(time.time()),
            "activities": acts}
    os.makedirs(os.path.dirname(themes_path()), exist_ok=True)
    tmp = themes_path() + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    os.replace(tmp, themes_path())
    say(total, total, "主题表完成：%d 个活动" % len(acts))
    return {"skipped": False, "activities": len(acts),
            "pages": sum(len(a["pages"]) for a in acts)}
