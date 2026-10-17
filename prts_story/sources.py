# -*- coding: utf-8 -*-
"""Wiki 来源（source）注册表与 MediaWiki API 客户端。

一个 source 描述"从哪里取文本、怎么解析"：
    id            短标识，缓存目录名
    name          显示名（中文）
    api           MediaWiki api.php 地址
    home          站点主页（界面里可点开）
    allowed_hosts 出站白名单（交给 net.safe_get 做 SSRF 校验）
    parser        "prts"（PRTS 专用模板解析）/ "generic"（通用 wikitext 解析）
    categories    用来建立页面索引的分类名
    prefixes      用来建立页面索引的标题前缀（没有分类时用）
    page_regex    进一步过滤页面标题的正则（可选）
    accent        界面主色

内置来源写在 BUILTIN 里；用户自定义来源存进用户配置目录的 sources.json，
可用 GUI 或 `wiki-translater source add` 添加。用户来源默认 parser=generic。
"""
from __future__ import annotations

import json
import os
import re
import urllib.parse
from dataclasses import asdict, dataclass, field

from .config import user_data_dir
from .net import NetError, safe_get, safe_get_json

_UNSAFE_NAME = re.compile(r"[^0-9A-Za-z_.-]+")

# Wiki 站点的图片通常放在同域的子域或公共 CDN 上（不在 api.php 的域名下），
# 这些是显式放行的公共图床；其余出站仍受 SSRF 校验与域名白名单约束。
IMAGE_CDNS = (
    "upload.wikimedia.org",
    "static.wikia.nocookie.net",
    "media.prts.wiki",
)


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
    parser: str = "generic"
    allowed_hosts: list[str] = field(default_factory=list)
    categories: list[str] = field(default_factory=list)
    prefixes: list[str] = field(default_factory=list)
    page_regex: str = ""
    accent: str = "#d9a441"
    note: str = ""
    hero_templates: list[str] = field(default_factory=list)
    hero_search: str = ""
    builtin: bool = False
    order: int = 100

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
# 内置来源
# --------------------------------------------------------------------------

BUILTIN: list[Source] = [
    Source(
        id="prts",
        name="明日方舟 · PRTS",
        api="https://prts.wiki/api.php",
        home="https://prts.wiki",
        parser="prts",
        categories=["剧情", "干员密录"],
        accent="#d9a441",
        order=10,
        note="剧情模板完整，解析质量最好。",
        hero_templates=["文件:立绘_%s_1.png", "文件:立绘_%s_2.png", "文件:半身像_%s_1.png"],
        hero_search="%s立绘",
        builtin=True,
    ),
    Source(
        id="ys-wiki",
        name="原神 · BWIKI",
        api="https://wiki.biligame.com/ys/api.php",
        home="https://wiki.biligame.com/ys",
        parser="generic",
        categories=["任务剧情", "魔神任务", "传说任务", "活动剧情"],
        prefixes=["剧情", "任务"],
        accent="#9ac7a0",
        order=30,
        hero_templates=["文件:%s立绘.png", "文件:%s抽卡立绘.png", "文件:%s头像.png"],
        hero_search="%s立绘",
        note="通用解析：对话表格会按行拆成纯文本，可读但不是剧本排版。",
        builtin=True,
    ),
    Source(
        id="reverse1999-wiki",
        name="重返未来：1999 · BWIKI",
        api="https://wiki.biligame.com/reverse1999/api.php",
        home="https://wiki.biligame.com/reverse1999",
        parser="generic",
        categories=["活动章节", "主线章节", "角色剧情", "剧情"],
        prefixes=["NS"],
        accent="#7ac7c4",
        order=60,
        hero_templates=["文件:立绘 %s 01.png", "文件:立绘 %s 02.png", "文件:角色图标 %s 01.png"],
        hero_search="立绘 %s",
        note="章节页以「NS.01 启程的祷声」这类编号命名，通用解析可直接读。",
        builtin=True,
    ),
    Source(
        id="mc-wiki",
        name="鸣潮 · BWIKI",
        api="https://wiki.biligame.com/wutheringwaves/api.php",
        home="https://wiki.biligame.com/wutheringwaves",
        parser="generic",
        categories=["任务回顾"],
        prefixes=["任务回顾"],
        accent="#8fb8c9",
        order=70,
        hero_templates=["文件:角色 %s 立绘.png", "文件:角色 %s 超立绘.png", "文件:角色 %s 头像.png"],
        hero_search="角色 %s 立绘",
        note="剧情收在「任务回顾/…」子页里，按任务名即可搜到。",
        builtin=True,
    ),
]

BUILTIN_BY_ID = {s.id: s for s in BUILTIN}


# --------------------------------------------------------------------------
# 用户来源存储
# --------------------------------------------------------------------------

def store_path() -> str:
    return os.path.join(user_data_dir(), "sources.json")


def load_user_sources() -> list[Source]:
    path = store_path()
    if not os.path.isfile(path):
        return []
    try:
        with open(path, encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        return []
    out = []
    for item in raw.get("sources", []) if isinstance(raw, dict) else []:
        try:
            out.append(Source(**{k: v for k, v in item.items() if k in Source.__dataclass_fields__}))
        except TypeError:
            continue
    return out


def save_user_sources(sources: list[Source]) -> None:
    path = store_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump({"version": 1, "sources": [s.to_json() for s in sources]},
                  f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


def all_sources() -> list[Source]:
    """内置 + 用户自定义；同 id 时用户定义覆盖内置，按 order 排（内置先、自定义在后）。"""
    merged = {s.id: s for s in BUILTIN}
    for s in load_user_sources():
        merged[s.id] = s
    return sorted(merged.values(), key=lambda s: (s.order, 0 if s.builtin else 1, s.name))


def get_source(source_id: str) -> Source:
    for s in all_sources():
        if s.id == source_id:
            return s
    raise KeyError("未知来源：%s" % source_id)


def normalize_source(data: dict) -> Source:
    """把界面提交的表单/JSON 变成合法 Source，并做基础校验。"""
    sid = (data.get("id") or "").strip().lower()
    api = (data.get("api") or "").strip()
    if not api:
        raise ValueError("请填写 api.php 地址")
    if not api.startswith(("http://", "https://")):
        raise ValueError("api 地址必须以 http:// 或 https:// 开头")
    if not sid:
        sid = re.sub(r"[^a-z0-9]+", "-", (data.get("name") or "custom").lower()).strip("-")[:32]
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{1,40}", sid or ""):
        raise ValueError("来源 id 只能用小写字母、数字、点、下划线、减号")
    parser = data.get("parser") if data.get("parser") in ("prts", "generic") else "generic"
    cats = data.get("categories") or []
    prefixes = data.get("prefixes") or []
    if isinstance(cats, str):
        cats = [c.strip() for c in re.split(r"[,，\n]", cats) if c.strip()]
    if isinstance(prefixes, str):
        prefixes = [c.strip() for c in re.split(r"[,，\n]", prefixes) if c.strip()]
    return Source(
        id=sid,
        name=(data.get("name") or sid).strip(),
        api=api,
        home=(data.get("home") or "").strip(),
        parser=parser,
        allowed_hosts=[urllib.parse.urlsplit(api).hostname or ""],
        categories=list(cats),
        prefixes=list(prefixes),
        page_regex=(data.get("page_regex") or "").strip(),
        accent=(data.get("accent") or "#d9a441").strip(),
        note=(data.get("note") or "").strip(),
        builtin=False,
        order=int(data.get("order") or 100),
    )


def probe(source: Source) -> dict:
    """连通性自检：站点信息 + 索引规模。"""
    info = source.api_json(action="query", meta="siteinfo",
                           siprop="general|statistics", timeout=25)
    general = info.get("query", {}).get("general", {})
    stats = info.get("query", {}).get("statistics", {})
    writable = bool(general.get("writeapi"))
    return {
        "ok": True,
        "sitename": general.get("sitename"),
        "generator": general.get("generator"),
        "pages": stats.get("pages"),
        "writeapi": writable,
        "api": source.api,
    }


# --------------------------------------------------------------------------
# 检索
# --------------------------------------------------------------------------

def search_pages(source: Source, query: str, limit: int = 20) -> list[dict]:
    """站内全文搜索。部分 wiki 关闭了搜索索引，失败时返回空表。"""
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
        key = json.dumps(nxt, sort_keys=True)
        if key in seen_cursor:
            break
        seen_cursor.add(key)
        cont = nxt
    return titles


def prefix_pages(source: Source, prefix: str, limit: int = 3000) -> list[str]:
    titles: list[str] = []
    cont: dict = {}
    while True:
        data = source.api_json(action="query", list="allpages", apprefix=prefix,
                               aplimit="500", **cont)
        titles += [p.get("title", "") for p in data.get("query", {}).get("allpages") or []]
        nxt = data.get("continue")
        if not nxt or len(titles) >= limit:
            break
        cont = nxt
    return [t for t in titles if t]


def search_files(source: Source, query: str, limit: int = 10) -> list[str]:
    """在文件命名空间（ns=6）里搜图片，返回「文件:…」标题。

    各 Wiki 的立绘命名不统一（PRTS 是 `立绘_年_1`，鸣潮是 `角色 秧秧 立绘`），
    命名模板猜不中时用搜索兜底；自定义来源没有模板也能靠它拿到封面图。
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
