# -*- coding: utf-8 -*-
"""页面索引与搜索：输入「剧情名字」，找到对应的 PRTS 页面。

索引来源：source.categories（分类成员，PRTS 的「分类:剧情」「分类:干员密录」）。
索引缓存在用户目录，默认 7 天刷新一次，可按需强制重建。

搜索分三种：
    exact   用户用中/英文双引号括起来（"千年一叹" / “千年一叹”），只认完全命中；
            找不到就是找不到，不做猜测。
    fuzzy   默认。先本地打分（完全相等 > 前缀 > 包含 > 相似度），
            本地没有像样的结果时再问站内搜索 API。
    结果里如果最佳候选只是「模糊相似」，会回一个 did_you_mean，
    由界面问一句「是不是 XX？」。
"""
from __future__ import annotations

import json
import os
import re
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field

from .sources import Source, category_members, search_pages

INDEX_VERSION = 5
CACHE_TTL = 7 * 24 * 3600

QUOTES = ('"', "“", "”", "'", "‘", "’")


# --------------------------------------------------------------------------
# 文本归一化与打分
# --------------------------------------------------------------------------

def norm(text: str) -> str:
    """全角转半角、去空格、统一大小写与引号，便于比较。"""
    if not text:
        return ""
    text = unicodedata.normalize("NFKC", text)
    for q in QUOTES:
        text = text.replace(q, "")
    text = re.sub(r"\s+", "", text)
    return text.lower()


_EXACT_PAIRS = {'"': '"', "“": "”"}


def is_quoted(q: str) -> bool:
    """只有中英文双引号算精确搜索；「」这类书引号是作品名的一部分，不算。"""
    q = q.strip()
    return len(q) >= 2 and _EXACT_PAIRS.get(q[0]) == q[-1]


def unquote(q: str) -> str:
    q = q.strip()
    if is_quoted(q):
        return q[1:-1].strip()
    return q.strip('"“”').strip()


_CODE_RE = re.compile(r"^([A-Za-z]{0,4}\d{0,2}(?:-[A-Za-z0-9]+){0,2})\s+(.*)$")
_SUFFIX_RE = re.compile(r"/(?:BEG|END|NBT|BEG2|END2)$", re.I)


def split_title(title: str) -> tuple[str, str]:
    """`AF-ST1 洪炉示岁·迎春/NBT` → ("AF-ST1", "洪炉示岁·迎春")。"""
    t = _SUFFIX_RE.sub("", title).strip()
    m = _CODE_RE.match(t)
    if m and re.search(r"\d", m.group(1)):
        return m.group(1).upper(), m.group(2).strip()
    return "", t


@dataclass
class Entry:
    title: str
    name: str
    code: str = ""
    kind: str = "page"

    def to_json(self) -> list[str]:
        return [self.title, self.name, self.code, self.kind]

    @classmethod
    def from_json(cls, row: list) -> "Entry":
        row = list(row) + ["", "", "page"]
        return cls(title=row[0], name=row[1] or row[0], code=row[2] or "", kind=row[3] or "page")


def _classify(title: str, source: Source) -> Entry:
    code, name = split_title(title)
    kind = "page"
    if "/干员密录/" in title or title.startswith("干员密录/"):
        kind = "secret"
    elif code:
        kind = "story"
    if not name:
        name = title
    return Entry(title=title, name=name, code=code, kind=kind)


def _skip_title(title: str) -> bool:
    if not title or ":" in title.split("/")[0][:6]:
        return True
    bad = ("/data", "/文档", "/敌方情报", "/攻略", "/简介", "一览", "/沙盒", "沙盒/")
    return any(b in title for b in bad)


def score_entry(query_norm: str, entry: Entry) -> float:
    """0 表示不相关；越高越像。"""
    if not query_norm:
        return 0.0
    name_n = norm(entry.name)
    title_n = norm(entry.title)
    best = 0.0
    for cand, weight in ((name_n, 1.0), (title_n, 0.92)):
        if not cand:
            continue
        # 查询的头一个字都不在候选里，就不是同一个东西：
        # 否则「黍干员密录」会把「余/干员密录/1」也算成命中（共享「干员密录」四字）
        if query_norm[0] not in cand:
            continue
        if cand == query_norm:
            best = max(best, 1000 * weight)
            continue
        if cand.startswith(query_norm):
            best = max(best, (520 + 120 * len(query_norm) / max(1, len(cand))) * weight)
            continue
        pos = cand.find(query_norm)
        if pos >= 0:
            best = max(best, (330 + 90 * len(query_norm) / max(1, len(cand))
                               - pos * 2) * weight)
            continue
        # 查询的每个字是否按顺序出现在候选里（允许跳字，如「千年叹」→「千年一叹」）
        coverage = _subsequence_ratio(query_norm, cand)
        if coverage >= 0.75:
            best = max(best, (150 + 60 * coverage) * weight)
        else:
            ratio = _ratio(query_norm, cand)
            if ratio >= 0.55:
                best = max(best, (90 + 80 * ratio) * weight)
    if entry.code and norm(entry.code) == query_norm:
        best = max(best, 900)
    return round(best, 2)


def _subsequence_ratio(q: str, c: str) -> float:
    if not q:
        return 0.0
    i = 0
    for ch in c:
        if i < len(q) and ch == q[i]:
            i += 1
    return i / len(q)


def _ratio(a: str, b: str) -> float:
    from difflib import SequenceMatcher

    return SequenceMatcher(None, a, b).ratio()


# --------------------------------------------------------------------------
# 索引
# --------------------------------------------------------------------------

@dataclass
class Catalog:
    source: Source
    entries: list[Entry] = field(default_factory=list)
    built_at: float = 0.0
    names_built: bool = False

    @property
    def index_path(self) -> str:
        return os.path.join(self.source.cache_dir(), "catalog.json")

    @property
    def fresh(self) -> bool:
        return bool(self.entries) and (time.time() - self.built_at) < CACHE_TTL

    def save(self) -> None:
        payload = {
            "version": INDEX_VERSION,
            "source": self.source.id,
            "built_at": self.built_at or time.time(),
            "names_built": self.names_built,
            "entries": [e.to_json() for e in self.entries],
        }
        tmp = self.index_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, self.index_path)

    def titles(self) -> list[str]:
        return [e.title for e in self.entries]


def load_catalog(source: Source) -> Catalog:
    cat = Catalog(source=source)
    path = cat.index_path
    if not os.path.isfile(path):
        return cat
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return cat
    if data.get("version") != INDEX_VERSION:
        return cat
    cat.entries = [Entry.from_json(row) for row in data.get("entries", [])]
    cat.built_at = float(data.get("built_at") or 0)
    cat.names_built = bool(data.get("names_built"))
    return cat


def build_catalog(source: Source, progress=None, force: bool = False) -> Catalog:
    """抓取索引（分类 + 前缀）。progress(done, total, msg)。"""
    cat = load_catalog(source)
    if cat.fresh and not force:
        return cat

    def say(msg: str, done: float = 0, total: float = 0):
        if progress:
            progress(done, total, msg)

    titles: list[str] = []
    for cat_name in source.categories:
        say("读取分类：%s" % cat_name)
        try:
            titles += category_members(source, cat_name)
        except Exception as e:  # 分类不存在 / 网络问题都不致命
            say("分类 %s 读取失败：%s" % (cat_name, str(e)[:80]))

    seen, entries = set(), []
    for title in titles:
        if title in seen:
            continue
        seen.add(title)
        if _skip_title(title):
            continue
        entries.append(_classify(title, source))
    entries.sort(key=lambda e: (e.kind, e.code, e.name))

    cat.entries = entries
    cat.built_at = time.time()
    cat.save()
    say("索引完成：%d 个页面" % len(entries), 1, 1)
    return cat


def ensure_catalog(source: Source, progress=None) -> Catalog:
    cat = load_catalog(source)
    if cat.entries:
        return cat
    return build_catalog(source, progress=progress)


def search(catalog: Catalog, query: str, limit: int = 24,
           remote: bool = True) -> dict:
    """返回 {mode, results, did_you_mean, total, source}。"""
    raw = (query or "").strip()
    exact_mode = is_quoted(raw)
    q = unquote(raw) if exact_mode else raw
    qn = norm(q)
    if not qn:
        return {"mode": "empty", "results": [], "did_you_mean": None, "total": 0}

    scored = []
    for e in catalog.entries:
        s = score_entry(qn, e)
        if s > 0:
            scored.append((s, e))
    scored.sort(key=lambda x: (-x[0], len(x[1].name)))

    used_remote = False
    if remote and (not scored or scored[0][0] < 320):
        try:
            remote_hits = search_pages(catalog.source, q, limit=limit)
        except Exception:
            remote_hits = []
        have = {e.title for _, e in scored}
        for hit in remote_hits:
            if hit["title"] in have or _skip_title(hit["title"]):
                continue
            e = _classify(hit["title"], catalog.source)
            s = max(score_entry(qn, e), 210.0 if hit.get("snippet") else 160.0)
            scored.append((s, e))
            have.add(hit["title"])
        if remote_hits:
            used_remote = True
        scored.sort(key=lambda x: (-x[0], len(x[1].name)))

    if exact_mode:
        results = [(s, e) for s, e in scored if s >= 900]
        return {
            "mode": "exact",
            "query": q,
            "results": [_result_json(s, e) for s, e in results[:limit]],
            "did_you_mean": None,
            "total": len(results),
            "remote": used_remote,
        }

    top = scored[:limit]
    results = [_result_json(s, e) for s, e in top]
    dym = None
    if results and top[0][0] < 900:
        best_entry = top[0][1]
        # 查询词本来就出现在篇名里时，这是正常命中，不要多嘴问「是不是」
        if qn not in norm(best_entry.name) and qn not in norm(best_entry.title):
            dym = {"title": best_entry.title, "name": best_entry.name,
                   "code": best_entry.code,
                   "confidence": round(min(0.95, top[0][0] / 1000), 2)}
    return {
        "mode": "fuzzy",
        "query": q,
        "results": results,
        "did_you_mean": dym,
        "total": len(scored),
        "remote": used_remote,
    }


def _result_json(score: float, entry: Entry) -> dict:
    return {
        "title": entry.title,
        "name": entry.name,
        "code": entry.code,
        "kind": entry.kind,
        "score": score,
        "exact": score >= 900,
    }


# --------------------------------------------------------------------------
# 篇名索引：像 PRTS「XX/干员密录/1」这类标题里没有篇名的页面，
# 需要抓一次正文才能知道它叫什么（如「千年一叹」）。这一步并发抓取，结果缓存。
# --------------------------------------------------------------------------

_SECRET_TITLE_RE = re.compile(r"/干员密录/\d+$")
_HEADER_RE = re.compile(r"\[header[^\]]*\]\s*([^\n\[]+)", re.I)
_H2_RE = re.compile(r"^==\s*([^=]+?)\s*==\s*$", re.M)


def needs_names(catalog: Catalog) -> bool:
    return any(_SECRET_TITLE_RE.search(e.title) for e in catalog.entries) and not catalog.names_built


def build_names(catalog: Catalog, progress=None, workers: int = 8) -> Catalog:
    """给标题里没有篇名的页面补上篇名（PRTS 干员密录）。"""
    targets = [e for e in catalog.entries if _SECRET_TITLE_RE.search(e.title)]
    if not targets:
        catalog.names_built = True
        catalog.save()
        return catalog

    total = len(targets)
    done = 0

    def work(entry: Entry):
        try:
            text = catalog.source.fetch_text(entry.title, timeout=30)
        except Exception:
            return entry, None
        return entry, _extract_name(text)

    with ThreadPoolExecutor(max_workers=max(1, min(16, workers))) as pool:
        for entry, name in pool.map(work, targets):
            done += 1
            if name and name.strip():
                entry.name = name.strip()
                if not entry.code:
                    entry.code = entry.title.rsplit("/", 2)[0]
            if progress and (done % 10 == 0 or done == total):
                progress(done, total, "读取篇名 %d/%d" % (done, total))

    catalog.names_built = True
    catalog.save()
    return catalog


def _extract_name(text: str) -> str | None:
    m = _HEADER_RE.search(text)
    if m:
        name = re.sub(r"[\[\]'\"《》（）()]", "", m.group(1)).strip()
        if 1 <= len(name) <= 40:
            return name
    m = _H2_RE.search(text)
    if m:
        name = m.group(1).strip()
        if 1 <= len(name) <= 40:
            return name
    return None
