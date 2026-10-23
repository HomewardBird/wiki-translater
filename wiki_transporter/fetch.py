# -*- coding: utf-8 -*-
"""页面抓取：磁盘缓存 + 断点续抓 + 限速，出站请求全部经过 net.safe_get。"""
from __future__ import annotations

import json
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from .config import Plan, ensure_dir, safe_filename
from .net import NetError, UnsafeURLError
from .sources import Source, get_source


class FetchError(RuntimeError):
    pass


def cache_path(plan: Plan, title: str) -> str:
    name = safe_filename(title)[:120] + ".txt"
    return os.path.join(plan.cache_dir, name)


class _Limiter:
    """保证两次请求之间至少间隔 delay 秒（多线程共享）。"""

    def __init__(self, delay: float):
        self.delay = max(0.0, delay)
        self._lock = threading.Lock()
        self._next = 0.0

    def wait(self) -> None:
        if not self.delay:
            return
        with self._lock:
            now = time.monotonic()
            sleep = self._next - now
            self._next = max(now, self._next) + self.delay
        if sleep > 0:
            time.sleep(sleep)


def _source_for(plan: Plan) -> Source:
    return get_source(plan.data.get("source_id") or "prts")


def fetch_titles(plan: Plan, titles: list[str], source: Source | None = None,
                 delay: float = 0.25, force: bool = False, workers: int = 1,
                 progress=None) -> dict:
    """抓取一批页面的 wikitext 到 plan.cache_dir。

    progress(done, total, message) 可选；workers>1 时并发但共用同一个限速器。
    """
    source = source or _source_for(plan)
    ensure_dir(plan.cache_dir)
    limiter = _Limiter(delay)
    total = len(titles)
    state = {"ok": 0, "skipped": 0, "done": 0}
    missing: list[str] = []
    lock = threading.Lock()

    def one(title: str):
        path = cache_path(plan, title)
        if not force and os.path.isfile(path) and os.path.getsize(path) > 80:
            with lock:
                state["skipped"] += 1
                state["done"] += 1
                if progress:
                    progress(state["done"], total, "缓存命中 %d/%d" % (state["done"], total))
            return None
        try:
            limiter.wait()
            text = source.fetch_text(title)
        except (NetError, UnsafeURLError) as e:
            with lock:
                state["done"] += 1
                missing.append("%s：%s" % (title, str(e)[:120]))
                if progress:
                    progress(state["done"], total, "失败 %s" % title)
            return None
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        with lock:
            state["ok"] += 1
            state["done"] += 1
            if progress:
                progress(state["done"], total, "已下载 %s" % title)
        return text

    if workers and workers > 1 and total > 4:
        with ThreadPoolExecutor(max_workers=min(8, workers)) as pool:
            list(pool.map(one, titles))
    else:
        for title in titles:
            one(title)

    if missing:
        print("抓取失败 %d 页：%s" % (len(missing), "；".join(missing[:5])))
    return {"ok": not missing, "fetched": state["ok"], "reused": state["skipped"],
            "missing": missing}


def fetch_plan(plan: Plan, delay: float = 0.35, force: bool = False,
               progress=None, workers: int = 2) -> dict:
    """抓取 plan 里声明的所有页面（命令行兼容入口）。"""
    titles = plan.pages()
    print("抓取 %d 页 wikitext（限速 %.2fs/页）→ %s"
          % (len(titles), delay, plan.cache_dir))
    res = fetch_titles(plan, titles, delay=delay, force=force,
                       progress=progress, workers=workers)
    index = {
        "plan": plan.id,
        "api": _source_for(plan).api,
        "fetched_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "fetched": res["fetched"],
        "reused": res["reused"],
        "missing": res["missing"],
    }
    with open(os.path.join(plan.cache_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=1)
    print("抓取完成：新增 %d，复用 %d，失败 %d"
          % (res["fetched"], res["reused"], len(res["missing"])))
    return {"ok": res["ok"], "missing": res["missing"]}
