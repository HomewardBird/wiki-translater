# -*- coding: utf-8 -*-
"""PRTS（明日方舟 Wiki）来源与 MediaWiki API 客户端。

来源固定为 PRTS，字段说明：
    id             短标识，缓存目录名
    name           显示名
    api            MediaWiki api.php 地址
    home           站点主页（界面里可点开）
    categories     用来建立页面索引的分类名
    hero_templates 封面立绘的命名模板
    hero_search    模板落空时在文件命名空间里搜一次的查询词

本站的图片放在 media.prts.wiki，出站白名单按「本站 + 同一可注册域的子域 + 公共图床」放行，
其余仍受 net.safe_get 的 SSRF 校验约束。
"""
from __future__ import annotations

import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field

from .config import user_data_dir
from .net import NetError, safe_get, safe_get_json

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_.-]+")

IMAGE_CDNS = ("media.prts.wiki",)


def _base_domain(host: str) -> str:
    """取可注册域名（example.com / moegirl.org.cn），用于放宽同站子域。"""
    parts = [p for p in (host or "").split(".") if p]
    if len(parts) <= 2:
        return ".".join(parts)
    if len(parts[-1]) == 2 and parts[-2] in ("com", "net", "org", "gov", "edu", "co"):
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


@dataclass
class Source:
    id: str
    name: str
    api: str
    home: str = ""
    allowed_hosts: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    accent: str = "#d9a441"
    hero_templates: list[str] = field(default_factory=list)
    hero_search: str = ""

    # ---------- 派生属性 ----------
    @property
    def host(self) -> str:
        return (urllib.parse.urlsplit(self.api).hostname or "").lower()

    def allow(self) -> list[str]:
        """出站白名单：本站 + 同一可注册域的子域（图片/静态资源）+ 公共图床。"""
        hosts = {self.host, *[h.lower() for h in self.allowed_hosts if h]}
        base = _base_domain(self.host)
        if base and base.count(".") >= 1:
            hosts.add("*." + base)
        hosts.update(IMAGE_CDNS)
        hosts.discard("")
        return sorted(hosts)

    def fetch_text(self, title: str, timeout: float = 40) -> str:
        q = {
            "action": "query",
            "format": "json",
            "formatversion": "2",
            "prop": "revisions",
            "rvprop": "content",
            "rvslots": "main",
            "titles": title,
        }
        data = self.api_json(**q, timeout=timeout)
        page = _first_page(data)
        if not page or "missing" in page:
            raise NetError("页面不存在：%s" % title)
        content = _page_content(page)
        if not content:
            raise NetError("页面没有内容：%s" % title)
        return content

    def api_json(self, timeout: float = 30, **params) -> dict:
        params.setdefault("format", "json")
        params.setdefault("formatversion", "2")
        url = self.api + "?" + urllib.parse.urlencode(params)
        return safe_get_json(url, allowed_hosts=self.allow(), timeout=timeout)

    def page_url(self, title: str) -> str:
        base = self.home or ("https://" + self.host)
        return base.rstrip("/") + "/" + urllib.parse.quote(title.replace(" ", "_"))

    def cache_dir(self, *parts: str) -> str:
        safe = _UNSAFE_NAME.sub("_", self.id) or "source"
        path = os.path.join(user_data_dir(), "sources", safe, *parts)
        os.makedirs(path, exist_ok=True)
        return path

    def to_json(self) -> dict:
        return asdict(self)


def _first_page(data: dict) -> dict | None:
    pages = data.get("query", {}).get("pages") or []
    if isinstance(pages, dict):
        pages = list(pages.values())
    return pages[0] if pages else None


def _page_content(page: dict) -> str:
    revs = page.get("revisions") or []
    if not revs:
        return ""
    rev = revs[0]
    slots = rev.get("slots")
    if isinstance(slots, dict) and slots:
        main = slots.get("main") or next(iter(slots.values()))
        if isinstance(main, dict):
            return main.get("content") or main.get("*") or ""
        return main or ""
    return rev.get("content") or rev.get("*") or ""


# --------------------------------------------------------------------------
# 唯一来源：明日方舟 · PRTS
# --------------------------------------------------------------------------

PRTS = Source(
    id="prts",
    name="明日方舟 · PRTS",
    api="https://prts.wiki/api.php",
    home="https://prts.wiki",
    categories=["剧情", "干员密录"],
    accent="#d9a441",
    hero_templates=["文件:立绘_%s_1.png", "文件:立绘_%s_2.png", "文件:半身像_%s_1.png"],
    hero_search="%s立绘",
)


def get_source(source_id: str = "prts") -> Source:
    """本项目只接 PRTS；旧计划里的 source_id 缺省或写成 prts 都落到同一个来源。"""
    if source_id not in ("", "prts"):
        raise KeyError("未知来源：%s（Wiki Transporter 只支持 PRTS）" % source_id)
    return PRTS


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------

def search_pages(source: Source, query: str, limit: int = 20) -> list[dict]:
    """站内全文搜索。PRTS 关闭搜索索引时会失败，返回空表。"""
    try:
        data = source.api_json(action="query", list="search", srsearch=query,
                               srlimit=str(min(50, max(1, limit))), timeout=20)
    except (NetError, ValueError):
        return []
    hits = data.get("query", {}).get("search") or []
    return [{"title": h.get("title", ""), "snippet": _strip_tags(h.get("snippet", "")),
             "size": h.get("size", 0)} for h in hits if h.get("title")]


def _strip_tags(html: str) -> str:
    return re.sub(r"<[^>]+>", "", html or "").replace("&quot;", '"').strip()


def category_members(source: Source, category: str, limit: int = 5000) -> list[str]:
    titles: list[str] = []
    cont: dict = {}
    seen_cursor = set()
    while True:
        params = dict(action="query", list="categorymembers",
                      cmtitle=category if category.startswith(("Category:", "分类:"))
                      else "Category:" + category,
                      cmlimit="500", **cont)
        data = source.api_json(**params)
        for item in data.get("query", {}).get("categorymembers") or []:
            title = item.get("title", "")
            if title.startswith(("Category:", "分类:")):
                continue
            titles.append(title)
        nxt = data.get("continue")
        if not nxt or len(titles) >= limit:
            break
        key = repr(sorted(nxt.items()))
        if key in seen_cursor:
            break
        seen_cursor.add(key)
        cont = nxt
    return titles


def search_files(source: Source, query: str, limit: int = 10) -> list[str]:
    """在文件命名空间（ns=6）里搜图片，返回「文件:…」标题。

    PRTS 立绘命名是 `立绘_年_1`；模板猜不中时用搜索兜底。
    """
    try:
        data = source.api_json(action="query", list="search", srsearch=query,
                               srnamespace="6", srlimit=str(min(50, max(1, limit))),
                               timeout=20)
    except (NetError, ValueError):
        return []
    out = []
    for hit in data.get("query", {}).get("search") or []:
        title = hit.get("title") or ""
        if re.search(r"\.(png|jpe?g|webp)$", title, re.I):
            out.append(title)
    return out


def image_url(source: Source, title: str, timeout: float = 25) -> str | None:
    data = source.api_json(action="query", prop="imageinfo", iiprop="url",
                           titles=title, timeout=timeout)
    page = _first_page(data)
    if not page:
        return None
    info = (page.get("imageinfo") or [{}])[0]
    return info.get("url")


def download_image(source: Source, title: str, dest: str,
                   max_bytes: int = 6 * 1024 * 1024) -> str | None:
    """按文件页标题取一张图并存到 dest；已存在则复用。"""
    if os.path.isfile(dest) and os.path.getsize(dest) > 512:
        return dest
    url = image_url(source, title)
    if not url:
        return None
    resp = safe_get(url, allowed_hosts=source.allow(), timeout=60,
                    max_bytes=max_bytes)
    if not resp.content_type.startswith("image/"):
        raise NetError("不是图片：%s（%s）" % (url, resp.content_type))
    if len(resp.content) < 512:
        return None
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(resp.content)
    return dest
