# -*- coding: utf-8 -*-
"""Wiki Transporter 图形界面（本地 Web 应用）。

不引入任何 Web 框架：标准库 http.server 起一个只监听 127.0.0.1 的服务，
用 Chrome/Edge 的 `--app` 模式开一个没有地址栏的窗口，看起来就是一个桌面软件。

安全约束：
    * 只绑定 127.0.0.1（不对外监听）；
    * 每个进程生成一次性 token，写进 index.html，所有 /api/* 请求必须带
      `X-Token` 头（SSE 用查询参数），阻止其他网页跨站调用；
    * 校验 Host 头，防 DNS 重绑定；
    * 静态文件做路径穿越检查。
"""
from __future__ import annotations

import base64
import contextlib
import io
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from .. import __version__
from ..catalog import (Catalog, build_catalog, build_names, load_catalog,
                       needs_names, norm, search)
from ..compose import compose_groups, compose_plan
from ..resolve import resolve_lines, topic_search
from .. import smart as text_index
from .. import deep as deep_search
from .. import themes as theme_index
from .. import fulltext as full_index
from .. import entity as entity_index
from ..config import (Plan, app_home, author_name, builtin_plans,
                      ensure_dir, load_settings, safe_filename,
                      save_settings, settings_path, user_data_dir, workspace_dir)
from ..fetch import fetch_titles
from ..images import (cover_fetch_enabled, fetch_portraits, set_user_cover_config,
                      user_cover_dir, user_covers)
from ..parse import book_stats, build_books
from ..sources import PRTS
from ..net import NetError, UnsafeURLError

WEB_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "web")
APP_NAME = "Wiki Transporter"
APP_CN = "安巢鸟"


def plan_dirs() -> list[str]:
    """计划文件的两个位置：用户目录优先，其次是程序目录（exe 旁边的 plans/）。"""
    dirs = [os.path.join(user_data_dir(), "plans")]
    extra = os.path.join(app_home(), "plans")
    if os.path.abspath(extra) not in {os.path.abspath(d) for d in dirs}:
        dirs.append(extra)
    return dirs


def plan_files() -> list[tuple[str, str]]:
    """[(完整路径, 文件名)]，同一个计划 id 只取先找到的那份。"""
    seen: set[str] = set()
    found: list[tuple[str, str]] = []
    for d in plan_dirs():
        if not os.path.isdir(d):
            continue
        for name in sorted(os.listdir(d)):
            if not name.endswith(".json") or name[:-5] in seen:
                continue
            seen.add(name[:-5])
            found.append((os.path.join(d, name), name))
    return found

# 系统“选择文件夹”对话框：%s 依次是初始目录（两处，均需转义单引号）
DIR_DIALOG_SCRIPT = (
    "Add-Type -AssemblyName System.Windows.Forms;"
    "$d=New-Object System.Windows.Forms.FolderBrowserDialog;"
    "$d.Description='选择书籍的存放位置';"
    "$d.ShowNewFolderButton=$true;"
    "if('%s' -ne ''){$d.SelectedPath='%s'};"
    "if($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK)"
    "{[Console]::Out.Write($d.SelectedPath)}"
)

MIME = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "application/javascript; charset=utf-8",
    ".png": "image/png",
    ".svg": "image/svg+xml",
    ".ico": "image/x-icon",
    ".json": "application/json; charset=utf-8",
    ".woff2": "font/woff2",
}


# --------------------------------------------------------------------------
# 任务（抓取 / 索引 / 生成）
# --------------------------------------------------------------------------

class Job:
    def __init__(self, job_id: str, kind: str, title: str):
        self.id = job_id
        self.kind = kind
        self.title = title
        self.status = "running"      # running | done | error | canceled
        self.progress = 0.0
        self.message = "准备中…"
        self.phase = ""              # 当前阶段名（抓取原文 / 渲染 PDF …）
        self.done = 0                # 当前阶段已完成数
        self.total = 0               # 当前阶段总数（0 = 未知）
        self.log: list[dict] = []
        self.result: dict = {}
        self.error = ""
        self.started = time.time()
        self.finished = 0.0
        self._lock = threading.Lock()
        self._version = 0

    def emit(self, message: str, progress: float | None = None, *,
             phase: str | None = None, done: int | None = None,
             total: int | None = None) -> None:
        with self._lock:
            self.message = message
            if phase is not None:
                self.phase = phase
            if done is not None:
                self.done = done
            if total is not None:
                self.total = total
            if progress is not None:
                self.progress = max(0.0, min(1.0, progress))
            self.log.append({"t": round(time.time() - self.started, 2), "m": message})
            if len(self.log) > 500:
                self.log = self.log[-400:]
            self._version += 1

    def step(self, done: int, total: int, message: str,
             lo: float = 0.0, hi: float = 1.0, phase: str | None = None) -> None:
        """把抽水式进度（done/total）映射到区间 [lo, hi]，界面据此画真实进度条。"""
        frac = (done / total) if total else 0.0
        self.emit(message, lo + (hi - lo) * frac, phase=phase, done=done, total=total)

    def finish(self, result: dict | None = None, error: str = "") -> None:
        with self._lock:
            self.status = "error" if error else "done"
            self.error = error
            self.result = result or {}
            self.progress = 1.0 if not error else self.progress
            self.finished = time.time()
            self.message = error or "完成"
            self._version += 1

    def snapshot(self) -> dict:
        with self._lock:
            return {
                "id": self.id,
                "kind": self.kind,
                "title": self.title,
                "status": self.status,
                "progress": round(self.progress, 4),
                "message": self.message,
                "phase": self.phase,
                "done": self.done,
                "total": self.total,
                "log": self.log[-80:],
                "result": self.result,
                "error": self.error,
                "elapsed": round((self.finished or time.time()) - self.started, 1),
                "version": self._version,
            }


class _LogSink(io.TextIOBase):
    """把构建过程中的 print 收进任务日志。"""

    def __init__(self, job: Job):
        self.job = job

    def write(self, text: str) -> int:
        for line in str(text).splitlines():
            line = line.strip()
            if line:
                self.job.emit(line)
        return len(text)

    def flush(self) -> None:
        return None


# --------------------------------------------------------------------------
# 应用状态
# --------------------------------------------------------------------------

class App:
    def __init__(self):
        self.root = workspace_dir()
        self.token = secrets.token_urlsafe(24)
        self.jobs: dict[str, Job] = {}
        self.catalog: Catalog | None = None
        self.thumbs: dict[str, bytes] = {}
        self._lock = threading.Lock()
        self._build_lock = threading.Lock()
        self.started = time.time()

    # ---------- 索引 ----------
    def get_catalog(self) -> Catalog:
        with self._lock:
            if self.catalog is not None:
                return self.catalog
        cat = load_catalog(PRTS)
        with self._lock:
            self.catalog = cat
        return cat

    def refresh_catalog(self, job: Job) -> Catalog:
        job.emit("读取页面索引：%s" % PRTS.name, 0.01, phase="建立索引")
        cat = build_catalog(PRTS, progress=lambda d, t, m: job.step(
            d, t, m, 0.01, 0.88, "建立索引"))
        if needs_names(cat):
            need = sum(1 for e in cat.entries if "/干员密录/" in e.title)
            job.emit("补全篇名索引（%d 页）…" % need, 0.88, phase="补全篇名")
            cat = build_names(cat, progress=lambda d, t, m: job.step(
                d, t, m, 0.88, 0.99, "补全篇名"))
        with self._lock:
            self.catalog = cat
        return cat

    def start_job(self, kind: str, title: str, fn) -> Job:
        job = Job(secrets.token_hex(6), kind, title)
        with self._lock:
            self.jobs[job.id] = job

        def runner():
            try:
                result = fn(job)
                job.finish(result)
            except (SystemExit, Exception) as e:  # noqa: BLE001
                detail = traceback.format_exc(limit=4) if not isinstance(e, (NetError, UnsafeURLError, ValueError)) else str(e)
                job.emit(str(e))
                job.finish({}, error=str(e) or "未知错误")
                if detail and str(e) not in detail:
                    job.emit(detail)

        threading.Thread(target=runner, name="job-" + job.id, daemon=True).start()
        return job

    def recent(self, limit: int = 12) -> list[dict]:
        items = []
        for path, name in plan_files():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            pid = data.get("id") or name[:-5]
            out = os.path.join(self.root, "out", pid)
            files = []
            for kind in ("pdf", "docx", "epub"):
                d = os.path.join(out, kind)
                if os.path.isdir(d):
                    files += [os.path.join(d, f) for f in sorted(os.listdir(d))
                              if not f.startswith(".")]
            pages = sum(1 for v in data.get("volumes", []) for s in v.get("sections", [])
                        for p in s.get("pages", []))
            items.append({
                "id": pid,
                "title": data.get("title") or pid,
                "source_id": data.get("source_id") or "prts",
                "pages": pages,
                "mtime": max([os.path.getmtime(p) for p in files], default=os.path.getmtime(path)),
                "out_dir": out,
                "plan_id": data.get("id") or pid,
                "files": [{"path": p, "name": os.path.basename(p),
                           "kind": os.path.basename(os.path.dirname(p)),
                           "size": os.path.getsize(p)} for p in files[:200]],
            })
        items.sort(key=lambda x: -x["mtime"])
        return items[:limit]


# --------------------------------------------------------------------------
# HTTP
# --------------------------------------------------------------------------

class Handler(BaseHTTPRequestHandler):
    app: App = None  # type: ignore[assignment]
    server_version = "Wiki-Translater/" + __version__
    protocol_version = "HTTP/1.1"

    # ---------- 基础 ----------
    def log_message(self, fmt, *args):  # 安静一点，日志进任务里
        return

    def _send(self, code: int, body: bytes, ctype: str = "application/json; charset=utf-8",
              extra: dict | None = None):
        headers = {
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        headers.update(extra or {})
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, data, code: int = 200):
        self._send(code, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def _error(self, message: str, code: int = 400):
        self._json({"ok": False, "error": message}, code)

    def _body(self) -> dict:
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return {}
        if length <= 0 or length > 4 * 1024 * 1024:
            return {}
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except ValueError:
            return {}

    def _host_ok(self) -> bool:
        host = (self.headers.get("Host") or "").split(":")[0].lower()
        return host in ("127.0.0.1", "localhost", "[::1]", "::1")

    def _token_ok(self, query: dict | None = None) -> bool:
        token = self.headers.get("X-Token")
        if not token and query:
            token = (query.get("token") or [""])[0]
        return secrets.compare_digest(token or "", self.app.token)

    @staticmethod
    def _plan_item(pid: str, data: dict) -> dict:
        vols = data.get("volumes") or []
        pages = sum(len(s.get("pages") or []) for v in vols for s in (v.get("sections") or []))
        return {
            "id": pid,
            "title": data.get("title") or pid,
            "volumes": len(vols),
            "pages": pages,
            "source": data.get("source") or "",
        }

    # ---------- 路由 ----------
    def do_GET(self):
        if not self._host_ok():
            return self._error("非法 Host", 403)
        parsed = urllib.parse.urlsplit(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)
        if path.startswith("/api/"):
            if not self._token_ok(query):
                return self._error("缺少访问令牌", 403)
            return self._api_get(path, query)
        return self._static(path, query)

    def do_HEAD(self):
        return self.do_GET()

    def do_POST(self):
        if not self._host_ok():
            return self._error("非法 Host", 403)
        if not self._token_ok():
            return self._error("缺少访问令牌", 403)
        parsed = urllib.parse.urlsplit(self.path)
        try:
            return self._api_post(parsed.path, self._body())
        except (NetError, UnsafeURLError) as e:
            return self._error(str(e), 400)
        except KeyError as e:
            return self._error("找不到：%s" % e, 404)
        except ValueError as e:
            return self._error(str(e), 400)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            return self._error("服务内部错误：%s" % e, 500)

    # ---------- 静态 ----------
    def _static(self, path: str, query: dict):
        if path in ("/", "/index.html"):
            return self._index()
        rel = urllib.parse.unquote(path.lstrip("/"))
        target = os.path.abspath(os.path.join(WEB_DIR, rel))
        if not target.startswith(os.path.abspath(WEB_DIR) + os.sep) or not os.path.isfile(target):
            return self._error("Not found", 404)
        ext = os.path.splitext(target)[1].lower()
        with open(target, "rb") as f:
            body = f.read()
        self._send(200, body, MIME.get(ext, "application/octet-stream"),
                   {"Cache-Control": "public, max-age=60"})

    def _index(self):
        with open(os.path.join(WEB_DIR, "index.html"), encoding="utf-8") as f:
            html = f.read()
        html = html.replace("__TOKEN__", self.app.token)
        html = html.replace("__VERSION__", __version__)
        html = html.replace("__APP__", APP_NAME)
        self._send(200, html.encode("utf-8"), MIME[".html"],
                   {"Content-Security-Policy":
                    "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
                    "script-src 'self'; connect-src 'self'"})

    # ---------- API: GET ----------
    def _api_get(self, path: str, query: dict):
        app = self.app
        if path == "/api/state":
            return self._json({
                "ok": True,
                "app": {"name": APP_NAME, "cn": APP_CN, "version": __version__},
                "root": app.root,
                "settings": self._settings_json(),
                "indexed": len(app.get_catalog().entries),
                "recent": app.recent(),
                "uptime": round(time.time() - app.started, 1),
            })
        if path == "/api/settings":
            return self._json({"ok": True, "settings": self._settings_json()})
        if path == "/api/model/status":
            from .. import localembed
            from .. import llm as _llm

            return self._json({"ok": True, "model": localembed.status(),
                               "enabled": bool(_llm.config().get("embed_local"))})
        if path == "/api/recent":
            return self._json({"ok": True, "items": app.recent()})
        if path == "/api/plans":
            items = []
            seen: set[str] = set()
            for pid, data in builtin_plans():
                seen.add(pid)
                items.append(self._plan_item(pid, data))
            for fpath, name in plan_files():
                try:
                    with open(fpath, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    continue
                pid = data.get("id") or name[:-5]
                if pid in seen or not isinstance(data, dict):
                    continue
                seen.add(pid)
                items.append(self._plan_item(pid, data))
            return self._json({"ok": True, "items": items})
        m = re.fullmatch(r"/api/job/([0-9a-f]+)", path)
        if m:
            job = app.jobs.get(m.group(1))
            if not job:
                return self._error("任务不存在", 404)
            return self._json({"ok": True, "job": job.snapshot()})
        m = re.fullmatch(r"/api/job/([0-9a-f]+)/stream", path)
        if m:
            return self._stream(m.group(1))
        if path == "/api/thumb":
            return self._thumb(query)
        if path == "/api/cover":
            return self._cover_get(query)
        if path == "/api/entity":
            name = (query.get("name") or [""])[0].strip()
            if not name:
                return self._error("缺少实体名", 400)
            return self._json({"ok": True, "entity": entity_index.entity_page(name)})
        if path == "/api/prereq":
            code = (query.get("code") or [""])[0].strip()
            if not code:
                return self._error("缺少章节代号", 400)
            return self._json({"ok": True, "prereq": entity_index.prereq(code)})
        return self._error("未知接口", 404)

    def _thumb(self, query: dict):
        """把 PDF 的某一页渲染成 PNG，用作封面预览。只允许访问 out/ 下的 PDF。"""
        path = (query.get("path") or [""])[0]
        try:
            page = max(0, int((query.get("page") or ["0"])[0]))
            width = min(1000, max(160, int((query.get("w") or ["520"])[0])))
        except ValueError:
            return self._error("参数不合法", 400)
        out_root = os.path.abspath(os.path.join(self.app.root, "out"))
        target = os.path.abspath(path)
        if (not target.startswith(out_root + os.sep) or not os.path.isfile(target)
                or not target.lower().endswith(".pdf")):
            return self._error("不允许访问该文件", 403)
        key = "%s|%d|%d" % (target, page, width)
        cached = self.app.thumbs.get(key)
        if cached:
            return self._send(200, cached, "image/png", {"Cache-Control": "public, max-age=600"})
        try:
            import pymupdf

            doc = pymupdf.open(target)
            index = min(page, doc.page_count - 1)
            pg = doc[index]
            zoom = width / max(1.0, pg.rect.width)
            pix = pg.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
            data = pix.tobytes("png")
            doc.close()
        except Exception as e:  # noqa: BLE001
            return self._error("渲染预览失败：%s" % e, 500)
        if len(self.app.thumbs) > 40:
            self.app.thumbs.clear()
        self.app.thumbs[key] = data
        return self._send(200, data, "image/png", {"Cache-Control": "public, max-age=600"})

    def _stream(self, job_id: str):
        app = self.app
        job = app.jobs.get(job_id)
        if not job:
            return self._error("任务不存在", 404)
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        last_version = -1
        try:
            while True:
                snap = job.snapshot()
                if snap["version"] != last_version:
                    last_version = snap["version"]
                    payload = json.dumps(snap, ensure_ascii=False)
                    self.wfile.write(("data: %s\n\n" % payload).encode("utf-8"))
                    self.wfile.flush()
                if snap["status"] != "running":
                    break
                time.sleep(0.12)
        except (BrokenPipeError, ConnectionResetError):
            return

    # ---------- API: POST ----------
    def _api_post(self, path: str, body: dict):
        app = self.app
        if path == "/api/search":
            return self._search(body)
        if path == "/api/index":
            return self._start_index(body)
        if path == "/api/preview":
            return self._preview(body)
        if path == "/api/resolve":
            return self._resolve(body)
        if path == "/api/build":
            return self._build(body)
        if path == "/api/open":
            return self._open(body)
        if path == "/api/book/delete":
            return self._book_delete(body)
        if path == "/api/settings/save":
            return self._save_settings(body)
        if path == "/api/settings/test":
            return self._llm_test(body)
        if path == "/api/llm/embed-build":
            return self._start_embed()
        if path == "/api/model/download":
            return self._start_model_download()
        if path == "/api/model/remove":
            return self._model_remove()
        if path == "/api/pick-dir":
            return self._pick_dir(body)
        if path == "/api/cover":
            return self._cover_post(body)
        if path == "/api/shutdown":
            threading.Timer(0.3, os._exit, args=(0,)).start()
            return self._json({"ok": True})
        return self._error("未知接口", 404)

    # ---------- 具体接口 ----------
    def _search(self, body: dict):
        q = (body.get("q") or "").strip()
        limit = int(body.get("limit") or 200)
        cat = self.app.get_catalog()
        if not cat.entries:
            return self._json({"ok": True, "mode": "noindex", "results": [],
                               "did_you_mean": None, "total": 0,
                               "text": self._text_search(q, limit)})
        remote = bool(body.get("remote", True))
        res = search(cat, q, limit=limit, remote=remote)
        res = self._with_topic(cat, q, res, remote=remote)
        # 主题召回压在最前面：搜「岁家」该把 plans/sui.json 那份 10 卷 142 篇整批摆出来
        theme = self._theme_recall(q)
        if theme:
            res = self._merge_theme(cat, res, theme)
        text = self._text_search(q, max(limit, 40))
        # 有主题清单时正文命中全是噪声（共现扩展召回的不相关活动），
        # 清单已完整，不需要再补充
        if text and not res.get("theme"):
            res = self._merge_text_rows(cat, res, text)
        res.update({"ok": True, "indexed": len(cat.entries), "text": text})
        # 实体信号：命中别名表里的实体时，给前端一个入口跳转实体页
        und = deep_search.understand(q)
        concept = und.get("concept") or ""
        if concept and concept in deep_search.ALIASES:
            res["entity_hint"] = {"name": concept, "aliases": und.get("terms", [])[:8]}
        return self._json(res)

    def _text_search(self, q: str, limit: int) -> dict | None:
        """正文检索兜底：标题索引里根本没有「萨卡兹」这种世界观词。"""
        if not q:
            return None
        try:
            st = text_index.stats()
            if not st["docs"] and text_index.iter_raw():
                text_index.build()  # 首次搜索顺手建索引，146 篇约 1 秒
            # 概念层：岁家 → 别名/共现/LLM 扩展词，再进 trigram 词法层
            return deep_search.search(q, limit=min(limit, 200))
        except Exception as e:
            return {"error": str(e)[:140], "hits": [], "tree": [],
                    "timeline": [], "corpus": {"docs": 0}}

    def _arc_label(self, code: str) -> str:
        """一篇剧情归到哪条线：主线按章号，活动按代号查活动表。

        有了这个，「萨卡兹」的 47 篇才是一条条「第 10 章 → 第 11 章 → 覆潮之下」
        的阅读线，而不是一屏乱序篇名。
        """
        m = re.match(r"^(\d+)-", code or "")
        if m:
            return "第 %s 章 · 主线" % m.group(1)
        head = (code or "").split("-")[0].upper()
        if not head:
            return "其它剧情"
        cache = getattr(self.app, "_arc_map", None)
        if cache is None:
            cache = {}
            try:
                for a in theme_index.load():
                    for cd in (a.get("codes") or []):
                        cache.setdefault(str(cd).upper(),
                                         a.get("short") or a.get("name") or cd)
            except Exception:
                pass
            self.app._arc_map = cache
        name = cache.get(head)
        return ("活动 · " + name) if name else ("剧情 · " + head)

    def _merge_text_rows(self, cat: Catalog, res: dict, text: dict) -> dict:
        """正文命中优先：标题层只按字面相似度排，会把 spine/档案垫在最前面。

        规则：专题行（group）和精确同名行保持最高分；其余标题相似行压到 700 以下，
        让正文命中（800 起）排上去——但正文一篇都没命中时什么都不动。
        """
        hits = text.get("hits") or []
        if not hits:
            return res
        rows = res.get("results") or []
        out = []
        for r in rows:
            if not r.get("group") and not r.get("exact"):
                title = r.get("title") or ""
                if title.endswith("/spine") or "/spine/" in title or "/档案/" in title:
                    continue
                if float(r.get("score") or 0) > 700:
                    r["score"] = 700
            out.append(r)
        have = {r.get("title") for r in out}
        # 已经有专题清单时，正文命中只做补充：否则清单后面会再拖一整串噪声
        cap = 12 if any(r.get("group") for r in out) else 200
        extra = [h for h in hits if h.get("title") not in have][:cap]
        for i, h in enumerate(extra):
            have.add(h.get("title"))
            out.append({"title": h["title"], "name": h.get("name") or h["title"],
                        "code": h.get("code") or "", "kind": "story",
                        "score": 800 - i, "exact": False, "from": "text",
                        "group": self._arc_label(h.get("code") or "")})
        # 排序：发布时间线为主——同活动篇目连在一起、活动间按 CN 服实装时间。
        # 主题召回行（专题清单）保持原计划卷序在前，正文补充行按 _route_key 排在后。
        themed = [r for r in out if r.get("group") and r.get("exact")]
        plain = [r for r in out if not (r.get("group") and r.get("exact"))]
        from ..smart import _route_key
        plain.sort(key=_route_key)
        res["results"] = themed + plain
        res["total"] = len(res["results"])
        return res

    def _with_topic(self, cat: Catalog, q: str, res: dict, remote: bool = True) -> dict:
        """单独搜索也按主题展开：输入「将进酒」直接把整卷剧情摆出来。"""
        if res.get("mode") != "fuzzy" or not q:
            return res
        qn = norm(q)
        # 查询本身就是索引里的一个页面（IW-1 化物/BEG、黍/干员密录/1）就不用展开
        if any(norm(e.title) == qn or norm(e.name) == qn for e in cat.entries):
            return res
        groups = topic_search(cat, q, remote=remote)
        if not groups:
            return res
        grouped = {e.title for g in groups for e in g.pages}
        # 活动主页本身（「将进酒」那个索引外的页面）不该混在整卷剧情里
        merged = [r for r in res.get("results") or []
                  if not (norm(r["title"]) == qn and r["title"] not in grouped)]
        have = {r["title"]: r for r in merged}
        order = {}
        for g in groups:
            for e in g.pages:
                order.setdefault(e.title, len(order))
                if e.title in have:
                    have[e.title].update({"score": 960, "exact": True, "group": g.label})
                    continue
                row = {"title": e.title, "name": e.name, "code": e.code,
                       "kind": e.kind, "score": 960, "exact": True,
                       "group": g.label}
                have[e.title] = row
                merged.append(row)
        merged.sort(key=lambda r: (-r["score"], order.get(r["title"], 10 ** 6)))
        res["results"] = merged
        res["total"] = len(merged)
        res["groups"] = [{"label": g.label, "kind": g.kind,
                          "pages": len(g.pages)} for g in groups]
        res["did_you_mean"] = None
        return res

    # ---------- 主题召回：认出「自己整理的计划 / 卷」 ----------
    # plans/*.json 就是用户亲手整理的篇目清单（如 plans/sui.json = 岁家 10 卷 142
    # 篇）。以前搜索只查页面标题和正文，从不读这些文件，所以搜「岁家」召回 0。
    # 这一层把计划文件当主题表：查询命中计划名或卷名，就把对应卷的篇目按原顺序整批
    # 返回，而不是靠标题模糊去猜。
    _PLAN_NOISE = ("明日方舟", "剧情", "文字稿", "剧本", "整理", "合集", "文字",
                   "全一册", "相关", "故事", "全篇")
    _CLEAN_RE = re.compile(r"[^\w\u4e00-\u9fff]+")

    def _clean(self, text) -> str:
        s = norm(str(text or ""))
        for w in self._PLAN_NOISE:
            s = s.replace(norm(w), "")
        return self._CLEAN_RE.sub("", s)

    def _plan_index(self) -> list[dict]:
        cache = getattr(self.app, "_plan_theme_cache", None)
        if cache is not None:
            return cache
        out = []
        for path, fn in plan_files():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue
            vols = []
            for i, v in enumerate(data.get("volumes") or []):
                pages = [p for s in (v.get("sections") or [])
                         for p in (s.get("pages") or []) if p]
                if pages:
                    label = str(v.get("title") or "第 %d 卷" % (i + 1))
                    vols.append({"label": label, "clean": self._clean(label),
                                 "pages": pages})
            if not vols:
                continue
            title = str(data.get("title") or data.get("name") or fn[:-5])
            out.append({"id": fn[:-5], "title": title, "clean": self._clean(title),
                        "volumes": vols})
        self.app._plan_theme_cache = out
        return out

    # 「岁的历史发展」这类查询：先剥掉方面词，剩下的单字「岁」也要能命中计划名
    _PLAN_ASPECTS = ("历史", "发展", "来历", "起源", "关系", "人物", "事件",
                     "结局", "设定", "背景", "介绍", "世界观", "时间线", "顺序")

    def _plan_recall(self, q: str) -> list[dict]:
        """查询命中计划名/卷名 → [{label, plan, pages}]，保持计划里的卷序。

        「岁的历史发展」这种问法要先过概念层：方面词剥掉、单字概念补全成
        「岁家」，否则单字永远匹配不到计划名，142 篇清单就摆不出来。
        """
        und = deep_search.understand(q)
        keys = [und.get("concept"), und.get("core")] + list(und.get("terms") or [])
        keys = [k for k in keys if k]
        if not keys:
            return []
        out = []
        for p in self._plan_index():
            pc = p["clean"]
            hit_plan = any(k in pc or (len(k) >= 2 and pc in k) for k in keys)
            for v in p["volumes"]:
                vc = v["clean"]
                hit_vol = any(k in vc or (len(k) >= 2 and vc in k) for k in keys)
                if hit_plan or hit_vol:
                    out.append({"label": v["label"], "plan": p["title"],
                                "source": "plan", "pages": v["pages"]})
        return out

    def _theme_recall(self, q: str) -> list[dict]:
        """先认用户整理的计划，再认自动活动表（298 个活动 → 1733 篇剧情）。"""
        groups = self._plan_recall(q)
        if groups:
            return [g for g in groups if len(g["pages"]) >= 2]
        core = self._clean(q)
        if len(core) < 2:
            return []
        qn = norm(q).lower()
        acts = theme_index.load()
        if not acts:
            self._start_themes()  # 首次搜索顺手建一次（约 1 分钟，后台跑）
            return []
        out = []
        for a in acts:
            keys = [self._clean(a.get("name")), self._clean(a.get("short"))]
            hit = any(k and len(k) >= 2 and (core in k or k in core) for k in keys)
            if not hit:
                for c in (a.get("codes") or []):
                    c = norm(c).lower()
                    if c and qn and (qn == c or qn.startswith(c + "-")):
                        hit = True
                        break
            if hit:
                out.append({"label": a.get("short") or a.get("name"),
                            "plan": a.get("title") or a.get("short"),
                            "source": "activity", "pages": a.get("pages") or []})
        return out

    def _start_themes(self):
        if getattr(self.app, "_themes_job", None) is not None:
            return

        def work(job: Job):
            return theme_index.build(progress=lambda d, t, m: job.step(d, t, m))

        self.app._themes_job = self.app.start_job("themes", "活动主题表", work)

    def _merge_theme(self, cat: Catalog, res: dict, groups: list[dict]) -> dict:
        from ..catalog import split_title

        by_norm: dict[str, object] = {}
        for e in cat.entries:
            by_norm.setdefault(norm(e.title), e)
        rows: list[dict] = []
        seen: set[str] = set()
        for g in groups:
            for t in g["pages"]:
                key = norm(t)
                if key in seen:
                    continue
                seen.add(key)
                e = by_norm.get(key)
                if e is not None:
                    row = {"title": e.title, "name": e.name or e.title,
                           "code": e.code or "", "kind": e.kind or "story"}
                else:
                    # 索引里暂时没有这一页也要列出来：清单完整性优先于索引完整性
                    code, name = split_title(t)
                    row = {"title": t, "name": name or t,
                           "code": code or "", "kind": "story"}
                row.update({"score": 1000.0, "exact": True,
                            "group": g["label"], "plan": g["plan"]})
                rows.append(row)
        if not rows:
            return res
        keep = [r for r in (res.get("results") or [])
                if norm(r.get("title") or "") not in seen]
        counts: dict[str, int] = {}
        for r in rows:
            counts[r["group"]] = counts.get(r["group"], 0) + 1
        res["results"] = rows + keep
        res["total"] = len(res["results"])
        res["groups"] = [{"label": g, "kind": "story", "pages": n}
                         for g, n in counts.items()]
        res["theme"] = {"title": groups[0].get("plan") or "",
                        "source": groups[0].get("source") or "plan",
                        "volumes": len(counts), "pages": len(rows)}
        res["did_you_mean"] = None
        return res

    def _start_index(self, body: dict):
        force = bool(body.get("force"))
        # fulltext=true：抓「分类:剧情」全部正文再建 FTS 索引（搜世界观词用）
        if body.get("fulltext"):
            def crawl_job(job: Job):
                res = full_index.crawl(
                    progress=lambda d, t, m: job.step(d, t, m))
                res.pop("missing", None)
                return res

            job = self.app.start_job("fulltext", "抓取全量剧情正文", crawl_job)
            return self._json({"ok": True, "job": job.id})

        def work(job: Job):
            if force:
                with self.app._lock:
                    self.app.catalog = None
            cat = self.app.refresh_catalog(job)
            return {"count": len(cat.entries)}

        job = self.app.start_job("index", "索引 %s" % PRTS.name, work)
        return self._json({"ok": True, "job": job.id})

    def _plan_json(self, plan: Plan) -> dict:
        total_pages = sum(len(sec.get("pages") or [])
                          for v in plan.volumes for sec in (v.get("sections") or []))
        cached = sum(1 for t in plan.pages()
                     if os.path.isfile(os.path.join(plan.cache_dir,
                                                    safe_filename(t)[:120] + ".txt")))
        return {
            "id": plan.id,
            "title": plan.title,
            "volumes": [{
                "title": v["title"],
                "sections": [{"name": s["name"], "pages": s.get("pages") or [],
                              "kind": s.get("kind")} for s in (v.get("sections") or [])],
            } for v in plan.volumes],
            "pages": total_pages,
            "cached": cached,
            "source_id": plan.source_id,
            "cover": self._cover_state(plan),
        }

    # ---------- 封面（本地图片 / 联网抓取开关） ----------
    def _cover_state(self, plan: Plan) -> dict:
        files = user_covers(plan.id)
        return {
            "files": [{"name": os.path.basename(p), "size": os.path.getsize(p)} for p in files],
            "plan_cover": plan.data.get("cover"),
            "fetch": cover_fetch_enabled(plan),
        }

    def _cover_get(self, query: dict):
        plan_id = (query.get("plan_id") or [""])[0].strip()
        if not plan_id:
            raise ValueError("缺少计划名")
        return self._json({"ok": True, "plan_id": plan_id,
                           **self._cover_state(Plan(self.app.root, plan_id))})

    def _cover_post(self, body: dict):
        plan_id = str(body.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("缺少计划名")
        plan = Plan(self.app.root, plan_id)
        action = str(body.get("action") or "upload")
        d = user_cover_dir(plan.id)

        if action == "clear":
            if os.path.isdir(d):
                for name in os.listdir(d):
                    if name == "config.json":   # 只清图片，保留「是否联网抓取」这类设置
                        continue
                    try:
                        os.remove(os.path.join(d, name))
                    except OSError:
                        pass
            return self._json({"ok": True, **self._cover_state(plan)})

        if action == "fetch":
            value = bool(body.get("value"))
            set_user_cover_config(plan.id, fetch=value)
            return self._json({"ok": True, **self._cover_state(plan)})

        raw = str(body.get("data") or "")
        if not raw:
            raise ValueError("没有收到图片")
        name = safe_filename(str(body.get("name") or "cover.png"))[:80]
        ext = os.path.splitext(name)[1].lower()
        if ext not in (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp"):
            raise ValueError("只支持 png / jpg / webp / gif / bmp")
        try:
            payload = base64.b64decode(raw.split(",")[-1])
        except Exception:  # noqa: BLE001
            raise ValueError("图片数据读不出来")
        if not payload:
            raise ValueError("图片是空的")
        if len(payload) > 24 * 1024 * 1024:
            raise ValueError("图片太大（上限 24 MB）")
        ensure_dir(d)
        if body.get("replace", True):
            for old in os.listdir(d):
                try:
                    os.remove(os.path.join(d, old))
                except OSError:
                    pass
        # 文件名带序号：01_xx.png、02_xx.png —— 按卷顺序取用
        idx = len([f for f in os.listdir(d) if not f.startswith(".")]) + 1
        target = os.path.join(d, "%02d_%s" % (idx, name))
        with open(target, "wb") as f:
            f.write(payload)
        return self._json({"ok": True, **self._cover_state(plan)})

    def _resolve_lines(self, cat: Catalog, lines: list[str],
                       remote: bool = True) -> tuple[list, list[dict]]:
        """把用户粘贴的每行解析成「页面组」。返回 (groups, missing)。"""
        groups, misses = resolve_lines(cat, lines, remote=remote)
        return groups, [m.to_json() for m in misses]

    def _require_index(self) -> Catalog:
        cat = self.app.get_catalog()
        if not cat.entries:
            raise ValueError("还没有建索引，先在「整理」页里建一次")
        return cat

    def _resolve(self, body: dict):
        text = str(body.get("text") or "")
        lines = [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]
        if not lines:
            raise ValueError("粘贴一段清单：每行写一个主题或页面——活动名（将进酒）、"
                             "干员密录（年、夕、令、重岳的干员密录）、标题、代号或 Wiki 链接")
        cat = self._require_index()
        groups, missing = self._resolve_lines(cat, lines)
        flat = [e for g in groups for e in g.pages]
        return self._json({
            "ok": True,
            "groups": [g.to_json() for g in groups],
            "matched": [{"title": e.title, "name": e.name, "code": e.code,
                         "kind": e.kind} for e in flat],
            "missing": missing,
            "total": len(flat) + len(missing),
        })

    def _preview(self, body: dict):
        plan_file = str(body.get("plan") or "").strip()
        if plan_file:
            try:
                plan = Plan(self.app.root, plan_file)
            except SystemExit:
                raise ValueError("找不到这套计划：%s" % plan_file)
            return self._json({"ok": True, "plan": self._plan_json(plan), "missing": []})

        titles = [t for t in (body.get("titles") or []) if t]
        text = str(body.get("text") or "")
        missing: list[dict] = []
        data = None

        if text.strip() and not titles:
            lines = [ln for ln in re.split(r"[\r\n]+", text) if ln.strip()]
            cat = self._require_index()
            groups, missing = self._resolve_lines(cat, lines)
            entries = [e for g in groups for e in g.pages]
            if not entries:
                raise ValueError("这些行都没有匹配到已索引的页面")
            data = compose_groups(PRTS, groups, title=body.get("title"))
        else:
            cat = self.app.get_catalog()
            by_title = {e.title: e for e in cat.entries}
            entries = [by_title.get(t) for t in titles]
            entries = [e for e in entries if e]

        if not entries:
            raise ValueError("请先选择要整理的篇目")
        if data is None:
            data = compose_plan(PRTS, entries, title=body.get("title"))
        plan = Plan.from_dict(self.app.root, data)
        plan.save()
        return self._json({"ok": True, "plan": self._plan_json(plan), "missing": missing})

    def _build(self, body: dict):
        plan_id = body.get("plan_id") or ""
        formats = [f for f in (body.get("formats") or ["pdf"]) if f in ("pdf", "docx", "epub")]
        if not formats:
            formats = ["pdf"]
        # PDF 正文字号倍率：只有 100% / 115% / 130% 三档，别的值一律回落到 100%
        from ..pdf_html import clamp_scale

        font_scale = clamp_scale(body.get("font_scale"))
        plan = Plan(self.app.root, plan_id)
        wanted = str(body.get("title") or "").strip()
        if wanted and wanted != plan.title:
            data = dict(plan.data)
            data["title"] = wanted[:60]
            data["filePrefix"] = safe_filename(wanted[:60])
            plan = Plan.from_dict(self.app.root, data, plan_id=plan.id)
        # 整理者署名默认写进产物，body 里 credit=false 才不写
        plan.apply_credit(body.get("credit") is not False)
        # 内置计划只在包里，用户目录没有存档；落一份盘，书库里才看得见这本书
        try:
            plan.save()
        except OSError:
            pass
        if not self.app._build_lock.acquire(blocking=False):
            raise ValueError("已经有一个生成任务在跑，等它结束再试")
        try:
            job = self.app.start_job("build", plan.title,
                                     lambda j: self._run_build(j, plan, formats, font_scale))
        except Exception:
            self.app._build_lock.release()
            raise
        return self._json({"ok": True, "job": job.id})

    def _run_build(self, job: Job, plan: Plan, formats: list[str], font_scale: float = 1.0) -> dict:
        sink = contextlib.redirect_stdout(_LogSink(job))
        try:
            with sink:
                return self._build_inner(job, plan, formats, font_scale)
        finally:
            self.app._build_lock.release()

    # 生成任务的阶段区间（进度条按这个区间逐段推进，不跳格）
    P_FETCH = (0.02, 0.56)
    P_PARSE = (0.56, 0.62)
    P_DOCX = (0.62, 0.76)
    P_PDF = (0.76, 0.965)
    P_FINAL = (0.965, 0.995)

    def _build_inner(self, job: Job, plan: Plan, formats: list[str],
                     font_scale: float = 1.0) -> dict:
        source = PRTS
        titles = plan.pages()
        n = len(titles)
        job.emit("来源：%s · %d 个页面" % (source.name, n), self.P_FETCH[0],
                 phase="抓取原文", done=0, total=n)
        lo, hi = self.P_FETCH
        fetch_titles(plan, titles, source=source, delay=0.12, workers=4,
                     progress=lambda d, t, m: job.step(d, t, m, lo, hi, "抓取原文"))

        lo, hi = self.P_PARSE
        job.emit("解析文本…", lo, phase="解析文本", done=0, total=n)
        books = build_books(plan, strict=False)
        if not books:
            raise ValueError("没有解析出内容，可能是页面结构不被支持，或抓取失败")
        from ..cli import COMBINED_NAME

        outputs: list[str] = []
        selection = list(range(len(books)))
        total_chars = sum(book_stats(b)[1] for b in books)
        job.emit("共 %d 卷 / 约 %.1f 万字" % (len(books), total_chars / 10000),
                 hi, phase="解析文本", done=n, total=n)

        if "docx" in formats or "epub" in formats:
            from ..docx_build import build_combined_docx, build_toc_docx, build_volume_docx
            from ..epub_build import build_combined_epub, build_volume_epub

            ensure_dir(plan.out_dir("docx"))
            ensure_dir(plan.out_dir("epub"))
            lo, hi = self.P_DOCX
            # 每卷 Word / EPUB 各算一个工序：2 * 卷数 + 目录 + 合订
            steps = max(1, 2 * len(selection) + 2)
            k = 0
            for i in selection:
                if "docx" in formats:
                    job.emit("排版 Word：%s" % books[i]["title"],
                             lo + (hi - lo) * (k / steps), phase="排版 Word",
                             done=k, total=steps)
                    outputs.append(build_volume_docx(plan, books, i, plan.out_dir("docx")))
                    k += 1
                if "epub" in formats:
                    job.emit("生成 EPUB：%s" % books[i]["title"],
                             lo + (hi - lo) * (k / steps), phase="生成 EPUB",
                             done=k, total=steps)
                    outputs.append(build_volume_epub(plan, books, i, plan.out_dir("epub")))
                    k += 1
            if "docx" in formats:
                job.emit("总目录（Word）", lo + (hi - lo) * (k / steps),
                         phase="排版 Word", done=k, total=steps)
                outputs.append(build_toc_docx(plan, books, plan.out_dir("docx")))
                k += 1
            if len(books) > 1:
                if "docx" in formats:
                    job.emit("合并全一册（Word）", lo + (hi - lo) * (k / steps),
                             phase="排版 Word", done=k, total=steps)
                    outputs.append(build_combined_docx(plan, books, plan.out_dir("docx"), COMBINED_NAME))
                    k += 1
                if "epub" in formats:
                    job.emit("合并全一册（EPUB）", lo + (hi - lo) * (k / steps),
                             phase="生成 EPUB", done=k, total=steps)
                    outputs.append(build_combined_epub(plan, books, plan.out_dir("epub"), COMBINED_NAME))
                    k += 1
            job.emit("Word / EPUB 完成", hi, phase="排版 Word", done=steps, total=steps)

        if "pdf" in formats:
            from ..pdf_html import export_pdfs_html

            lo, hi = self.P_PDF
            job.emit("准备头像与排版…", lo, phase="渲染 PDF", done=0, total=1)
            try:
                fetch_portraits(plan, progress=lambda d, t, m: None)
            except Exception as e:  # 头像失败不影响正文
                job.emit("头像跳过：%s" % str(e)[:100])
            pdf_dir = ensure_dir(plan.out_dir("pdf"))
            pages = export_pdfs_html(
                plan, books, selection, pdf_dir, combined=True, combined_name=COMBINED_NAME,
                font_scale=font_scale,
                progress=lambda frac, label, d=None, t=None: job.emit(
                    label, lo + (hi - lo) * frac, phase="渲染 PDF",
                    done=d, total=t))
            for name in pages:
                outputs.append(os.path.join(pdf_dir, name + ".pdf"))

        lo, hi = self.P_FINAL
        job.emit("整理产物…", lo, phase="整理产物")
        files = []
        for path in dict.fromkeys(outputs):
            if os.path.isfile(path):
                files.append({"path": path, "name": os.path.basename(path),
                              "kind": os.path.basename(os.path.dirname(path)),
                              "size": os.path.getsize(path)})
        job.emit("完成，共 %d 个文件" % len(files), 1.0, phase="完成",
                 done=len(files), total=len(files))
        return {"plan_id": plan.id, "title": plan.title, "files": files,
                "out_dir": os.path.join(self.app.root, "out", plan.id)}

    def _settings_json(self) -> dict:
        s = load_settings()
        from .. import deep as _deep
        from .. import llm as _llm
        from .. import localembed
        vec = _deep.vec_stats()
        return {
            "author": author_name(),
            "out_root": s.get("out_root") or "",
            "root": self.app.root,
            "default_root": app_home(),
            "settings_path": settings_path(),
            "llm": {"enabled": _llm.status()["enabled"],
                    "base_url": s.get("llm_base_url") or "",
                    "has_key": bool(s.get("llm_api_key")),
                    "model": s.get("llm_model") or "",
                    "embed_model": s.get("llm_embed_model") or "",
                    "embed_local": bool(_llm.config().get("embed_local")),
                    "local": localembed.status(),
                    "vectors": vec.get("vectors", 0),
                    "vector_model": vec.get("model") or "",
                    "status": _llm.status()},
        }

    def _save_settings(self, body: dict):
        patch: dict[str, str] = {}
        if "out_root" in body:
            raw = str(body.get("out_root") or "").strip()
            root = ""
            if raw:
                root = os.path.abspath(os.path.expandvars(os.path.expanduser(raw)))
                try:
                    os.makedirs(root, exist_ok=True)
                except OSError as e:
                    raise ValueError("无法创建该目录：%s" % e)
                probe_file = os.path.join(root, ".wikitransporter-write-test")
                try:
                    with open(probe_file, "w", encoding="utf-8") as f:
                        f.write("ok")
                    os.unlink(probe_file)
                except OSError:
                    raise ValueError("这个位置不能写文件，请换一个目录（例如文档目录下）")
            patch["out_root"] = root
        save_settings(patch)
        if any(str(k).startswith("llm_") for k in (body or {})):
            from .. import llm
            llm.save_settings_patch(body)
        self.app.root = workspace_dir()
        return self._json({"ok": True, "settings": self._settings_json()})

    def _llm_test(self, body: dict):
        """连通性探测：真发一次 chat，有向量模型再发一次 embeddings。"""
        from .. import llm
        cfg = llm.config()
        if not llm.configured(cfg):
            return self._json({"ok": False, "error": "先填接口地址和模型名再保存"})
        t0 = time.time()
        txt = llm.chat("只回复两个字：可用", timeout=15)
        if not txt:
            return self._json({"ok": False,
                               "error": "连不上或没有返回：%s" % cfg["base_url"]})
        emb = ""
        if cfg["embed_model"]:
            vs = llm.embed(["测试"])
            emb = ("向量可用（%d 维）" % len(vs[0])) if vs else "向量接口不可用"
        return self._json({"ok": True, "reply": (txt or "").strip()[:40],
                           "embed": emb, "seconds": round(time.time() - t0, 2)})

    def _start_embed(self):
        from .. import deep, llm as _llm
        if not _llm.config()["embed_model"]:
            return self._json({"ok": False,
                               "error": "先下载本地模型，或配置 API 的向量模型（llm_embed_model）"})
        if getattr(self.app, "_embed_job", None) is not None:
            return self._json({"ok": True, "job": self.app._embed_job.id, "running": True})

        def work(job: Job):
            return deep.build_vectors(progress=lambda d, t, m: job.step(d, t, m))

        self.app._embed_job = self.app.start_job("embed", "向量索引", work)
        return self._json({"ok": True, "job": self.app._embed_job.id})

    def _start_model_download(self):
        """下载本地向量模型（约 24MB）。下完自动切到本地模型，并提示重建向量索引。"""
        from .. import localembed

        running = getattr(self.app, "_model_job", None)
        if running is not None and running.status == "running":
            return self._json({"ok": True, "job": running.id, "running": True})

        def work(job: Job):
            job.emit("开始下载本地向量模型…", 0.01, phase="下载模型", done=0, total=2)

            def progress(i, n, msg):
                job.step(i, n, msg)

            res = localembed.download(progress=progress)
            if not res.get("ok"):
                raise ValueError(res.get("error") or "下载失败")
            save_settings({"llm_embed_local": "1", "llm_embed_model": localembed.NAME})
            st = localembed.status()
            job.emit("模型已就绪，可以去建向量索引了", 1.0, phase="完成")
            return {"ok": True, "ready": True, "size_mb": st["size_mb"]}

        self.app._model_job = self.app.start_job("model", "本地向量模型", work)
        return self._json({"ok": True, "job": self.app._model_job.id})

    def _model_remove(self):
        from .. import localembed

        localembed.remove()
        save_settings({"llm_embed_local": "", "llm_embed_model": ""})
        return self._json({"ok": True, "model": localembed.status(),
                           "settings": self._settings_json()})

    def _pick_dir(self, body: dict):
        """弹出系统「选择文件夹」对话框（Windows 用 PowerShell 的 WinForms）。"""
        if sys.platform != "win32":
            raise ValueError("当前系统不支持弹出选择器，请手动填写路径")
        initial = str(body.get("initial") or self.app.root).replace("'", "''")
        script = DIR_DIALOG_SCRIPT % (initial, initial)
        try:
            proc = subprocess.run(["powershell", "-NoProfile", "-STA", "-Command", script],
                                  capture_output=True, text=True, timeout=300)
        except subprocess.TimeoutExpired:
            raise ValueError("选择超时，请手动填写路径")
        except OSError as e:
            raise ValueError("打不开系统选择器：%s" % e)
        return self._json({"ok": True, "path": (proc.stdout or "").strip()})

    def _open(self, body: dict):
        path = body.get("path") or ""
        reveal = bool(body.get("reveal"))
        if not path:
            raise ValueError("缺少路径")
        target = path if os.path.isabs(path) else os.path.join(self.app.root, path)
        if not os.path.exists(target) and reveal and os.path.isdir(os.path.dirname(target)):
            target = os.path.dirname(target)  # 文件还没生成：打开所在目录
        if not os.path.exists(target):
            raise ValueError("路径不存在：%s" % target)
        if sys.platform == "win32":
            if reveal or os.path.isdir(target):
                if os.path.isdir(target):
                    os.startfile(target)  # noqa: S606
                else:
                    subprocess.Popen(["explorer", "/select,", os.path.normpath(target)])
            else:
                os.startfile(target)  # noqa: S606
        else:
            opener = "open" if sys.platform == "darwin" else "xdg-open"
            arg = target if os.path.isdir(target) else os.path.dirname(target)
            subprocess.Popen([opener, arg])
        return self._json({"ok": True})

    def _book_delete(self, body: dict):
        """删掉一本书：计划文件 + out/<id> 下的全部导出。

        书目 id 里带中文（prts-千年一叹），所以不能按 ASCII 白名单校验；
        只挡掉路径分隔符、点段和控制字符，再核对拼出来的路径仍在 out/ 里。
        """
        pid = str(body.get("id") or "").strip()
        if (not pid or pid in (".", "..") or len(pid) > 160
                or any(ord(c) < 32 or c in '/\\:*?"<>|' for c in pid)):
            raise ValueError("书目 id 不合法")
        plan_path = None
        for path, name in plan_files():
            try:
                with open(path, encoding="utf-8") as f:
                    data = json.load(f)
            except (OSError, ValueError):
                continue
            if (data.get("id") or name[:-5]) == pid:
                plan_path = path
                break
        out_root = os.path.join(self.app.root, "out")
        out_dir = os.path.join(out_root, pid)
        if os.path.realpath(os.path.dirname(out_dir)) != os.path.realpath(out_root):
            raise ValueError("书目 id 不合法")
        removed = []
        if plan_path and os.path.isfile(plan_path):
            os.unlink(plan_path)
            removed.append(os.path.join("plans", os.path.basename(plan_path)))
        if os.path.isdir(out_dir):
            shutil.rmtree(out_dir)
            removed.append(os.path.relpath(out_dir, self.app.root))
        if not removed:
            raise ValueError("这本书已经不存在了")
        return self._json({"ok": True, "removed": removed})


# --------------------------------------------------------------------------
# 启动
# --------------------------------------------------------------------------

def find_browser() -> str | None:
    """优先用 Chrome/Edge 的 app 模式开窗口。"""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        os.path.join(os.environ.get("LOCALAPPDATA", ""), "Google", "Chrome", "Application", "chrome.exe"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        "/usr/bin/google-chrome", "/usr/bin/chromium", "/usr/bin/chromium-browser",
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    ]
    for path in candidates:
        if path and os.path.isfile(path):
            return path
    return None


def hide_console() -> None:
    """双击 exe 时把附带的控制台窗口藏起来（从终端运行则不动）。"""
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        user32 = ctypes.windll.user32
        procs = (ctypes.c_uint * 8)()
        count = kernel32.GetConsoleProcessList(procs, 8)
        if count == 1:  # 控制台只有自己 → 是双击启动的
            hwnd = kernel32.GetConsoleWindow()
            if hwnd:
                user32.ShowWindow(hwnd, 0)
    except Exception:
        pass


def serve(host: str = "127.0.0.1", port: int = 0) -> ThreadingHTTPServer:
    app = App()
    handler = type("BoundHandler", (Handler,), {"app": app})
    httpd = ThreadingHTTPServer((host, port), handler)
    httpd.daemon_threads = True
    httpd.app = app  # type: ignore[attr-defined]
    return httpd


def run_gui(port: int = 0, browser: bool = True) -> int:
    hide_console()
    httpd = serve(port=port)
    app: App = httpd.app  # type: ignore[attr-defined]
    url = "http://127.0.0.1:%d/?token=%s" % (httpd.server_address[1], app.token)
    print("%s %s 已启动：%s" % (APP_NAME, __version__, url))
    print("输出目录：%s" % app.root)

    if browser:
        exe = find_browser()
        opened = False
        if exe:
            profile = os.path.join(user_data_dir(), "browser")
            os.makedirs(profile, exist_ok=True)
            try:
                subprocess.Popen([exe, "--app=" + url, "--window-size=1280,900",
                                  "--user-data-dir=" + profile,
                                  "--no-first-run", "--no-default-browser-check",
                                  "--disable-features=Translate"])
                opened = True
            except OSError:
                opened = False
        if not opened:
            import webbrowser

            webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return 0
