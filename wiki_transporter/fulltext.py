# -*- coding: utf-8 -*-
"""全量剧情正文抓取：让「萨卡兹」这类不写在活动名里的词也能召回。

为什么需要这个模块
-----------------
themes.py 解决了「搜活动名 → 整卷」，但玩家真正会搜的是「萨卡兹」「源石技艺」
「维多利亚」——这些词只出现在正文里。smart.py 的 trigram 索引能召回，前提是
正文在本地：原来只有 plans/sui.json 那 146 篇缓存，覆盖不了 1733 篇剧情。

这里把「分类:剧情」+「分类:干员密录」的全部页面抓到 cache/raw/fulltext/，抓完
顺手调 smart.build() 增量建索引。已缓存（任何 plan 目录下都算）的页面直接跳过，
所以中断后重跑就是续传；GUI 里由「建正文索引」按钮启动同一个函数。
"""
from __future__ import annotations

import os
import time
from concurrent.futures import ThreadPoolExecutor
from threading import Lock

from .catalog import load_catalog
from .config import app_home, safe_filename
from .fetch import _Limiter
from .sources import PRTS

DIR_NAME = "fulltext"


def corpus_dir() -> str:
    path = os.path.join(app_home(), "cache", "raw", DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def crawl_titles() -> list[str]:
    """索引里的全部剧情页面（wiki 标题）。"""
    cat = load_catalog(PRTS)
    return [e.title for e in (cat.entries if cat else [])]


def pending() -> list[str]:
    """还没落盘的页面 —— 任何 plan 目录下的同名缓存都算已有。"""
    from . import smart

    have = set()
    for _plan, path, _mtime in smart.iter_raw():
        have.add(smart.title_of(path))
    return [t for t in crawl_titles() if t not in have]


def crawl(delay: float = 0.3, workers: int = 2, force: bool = False,
          progress=None, batch_limit: int = 0, reindex: bool = True) -> dict:
    """抓全量正文。progress(done, total, msg)。返回统计。"""
    from . import smart

    todo = crawl_titles() if force else pending()
    if batch_limit:
        todo = todo[:batch_limit]
    total = len(todo)
    out_dir = corpus_dir()
    limiter = _Limiter(delay)
    state = {"ok": 0, "skip": 0, "fail": 0, "done": 0}
    missing: list[str] = []
    lock = Lock()
    started = time.time()

    def one(title: str):
        path = os.path.join(out_dir, safe_filename(title)[:120] + ".txt")
        if not force and os.path.isfile(path) and os.path.getsize(path) > 80:
            with lock:
                state["skip"] += 1
                state["done"] += 1
            return
        try:
            limiter.wait()
            text = PRTS.fetch_text(title)
        except Exception as e:  # 单页失败不该中断整批
            with lock:
                state["fail"] += 1
                state["done"] += 1
                missing.append("%s：%s" % (title, str(e)[:100]))
                if progress and state["done"] % 5 == 0:
                    progress(state["done"], total, "失败 %s" % title)
            return
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        except OSError as e:
            with lock:
                state["fail"] += 1
                state["done"] += 1
                missing.append("%s：写盘失败 %s" % (title, str(e)[:80]))
            return
        with lock:
            state["ok"] += 1
            state["done"] += 1
            if progress and state["done"] % 5 == 0:
                progress(state["done"], total, title)

    if progress:
        progress(0, total, "待抓 %d 篇" % total)
    if todo:
        with ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            list(pool.map(one, todo))

    result = {"todo": total, "fetched": state["ok"], "reused": state["skip"],
              "failed": state["fail"], "seconds": round(time.time() - started, 1),
              "missing": missing}
    if reindex:
        if progress:
            progress(total, total, "重建正文索引…")
        result["index"] = smart.build()
    return result
