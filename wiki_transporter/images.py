# -*- coding: utf-8 -*-
"""头像 / 立绘（封面主图）的下载与缓存。

两类图，都存进 `cache/img/`，构建时只读本地文件：

* **干员头像**——只给 PRTS 的「干员档案 / 干员密录」页配一张，
  命名固定（`文件:头像_<干员名>.png`，180×180）。
* **封面立绘**——每卷一张。命名模板写在 `sources.py` 的 `hero_templates` 里
  （PRTS `立绘_年_1`），模板猜不中时在文件命名空间搜一次兜底。都拿不到时本地生成一张占位图。

下载走 net.safe_get（域名白名单 + 私网拒绝）。
"""
from __future__ import annotations

import json
import os
import time

from .config import Plan, ensure_dir, safe_filename, user_data_dir
from .net import NetError
from .sources import PRTS, Source, download_image, image_url, search_files


class ImageError(RuntimeError):
    pass


def _source(plan: Plan) -> Source:
    return PRTS


def portrait_url(operator: str, source: Source | None = None) -> str | None:
    """查 `文件:头像_<名>.png` 的实际下载地址。"""
    source = source or PRTS
    try:
        return image_url(source, "文件:头像_%s.png" % operator)
    except NetError as e:
        raise ImageError("%s：查询失败 %s" % (operator, e)) from e


def portrait_path(plan: Plan, operator: str) -> str:
    return os.path.join(plan.root, "cache", "img", "头像_%s.png" % safe_filename(operator))


def cached_portrait(plan: Plan, operator: str) -> str | None:
    p = portrait_path(plan, operator)
    if os.path.isfile(p) and os.path.getsize(p) > 512:
        return p
    return None


def fetch_portrait(plan: Plan, operator: str, force: bool = False) -> str | None:
    """下载一张头像；已有缓存则复用。返回本地路径或 None。"""
    if not force:
        hit = cached_portrait(plan, operator)
        if hit:
            return hit
    source = _source(plan)
    ensure_dir(os.path.join(plan.root, "cache", "img"))
    try:
        # 先确认文件页存在，再走安全下载
        url = portrait_url(operator, source)
        if not url:
            return None
        from .net import safe_get

        resp = safe_get(url, allowed_hosts=source.allow(), timeout=60,
                        max_bytes=6 * 1024 * 1024)
        if not resp.content_type.startswith("image/"):
            raise ImageError("%s：返回的不是图片（%s）" % (operator, resp.content_type))
        if len(resp.content) < 512:
            raise ImageError("%s：图片内容异常（%d 字节）" % (operator, len(resp.content)))
    except NetError as e:
        raise ImageError("%s：下载失败 %s" % (operator, e)) from e
    path = portrait_path(plan, operator)
    with open(path, "wb") as f:
        f.write(resp.content)
    return path


def plan_operators(plan: Plan) -> list[str]:
    """计划里声明了 portrait 的干员，按出现顺序去重。"""
    seen, out = set(), []
    for v in plan.volumes:
        for s in v.get("sections", []):
            name = s.get("portrait")
            if name and name not in seen:
                seen.add(name)
                out.append(name)
    return out


def fetch_portraits(plan: Plan, delay: float = 0.2, force: bool = False,
                    progress=None) -> dict:
    """抓取计划里所有干员头像。返回 {干员: 本地路径或 None}。"""
    names = plan_operators(plan)
    if not names:
        return {}
    if _source(plan).parser != "prts":
        return {}
    print("抓取 %d 张干员头像 → %s"
          % (len(names), os.path.join(plan.root, "cache", "img")))
    result = {}
    for i, name in enumerate(names, 1):
        had = cached_portrait(plan, name) is not None
        try:
            path = fetch_portrait(plan, name, force=force)
        except ImageError as e:
            print("  !! %s" % e)
            path = None
        result[name] = path
        flag = "缺失" if not path else ("复用缓存" if had and not force else "已下载")
        print("  [%d/%d] %s %s" % (i, len(names), name, flag))
        if progress:
            progress(i, len(names), "头像 %s" % name)
        if had and not force:
            continue
        time.sleep(delay)
    return result


# ---------------------------------------------------------------- 封面立绘
# 默认模板只用于没有在 sources.py 声明命名的来源（自定义来源）。
HERO_TEMPLATES = ("文件:立绘_%s_1.png", "文件:立绘_%s_2.png", "文件:半身像_%s_1.png")
HERO_SEARCH = "%s立绘"
HERO_SKIP_SPEAKERS = {"旁白", "选项", "系统提示", "？？？", "???"}
# 小于这个尺寸的多半是图标 / 头像 / 技能图，当封面会糊
MIN_HERO_BYTES = 40 * 1024
MIN_HERO_WIDTH = 500
# 文件名里带这些词的搜到了也不用（图标、技能、道具、选项按钮）
HERO_BAD_WORDS = ("图标", "技能", "道具", "选项", "头像", "物品", "素材", "徽章", "logo")
# 一轮构建里的「问过了、没有」：同一个角色出现在多卷时不重复发请求
_MISS: dict[tuple[str, str], bool] = {}
# 站点风控（HTTP 567）之后不再试，直接全部走占位图，别继续打人家站点
_BLOCKED: dict[str, bool] = {}


def hero_path(plan: Plan, operator: str) -> str:
    return os.path.join(plan.root, "cache", "img", "立绘_%s.png" % safe_filename(operator))


def cached_hero(plan: Plan, operator: str) -> str | None:
    p = hero_path(plan, operator)
    if os.path.isfile(p) and os.path.getsize(p) > MIN_HERO_BYTES:
        return p
    return None


def hero_templates(source: Source) -> list[str]:
    return list(source.hero_templates or HERO_TEMPLATES)


def _drop(path: str) -> None:
    try:
        os.remove(path)
    except OSError:
        pass


def _usable(path: str | None) -> bool:
    """够不够当封面：字节数和宽度卡一道，避免把图标当成封面。"""
    if not path or not os.path.isfile(path):
        return False
    if os.path.getsize(path) < MIN_HERO_BYTES:
        _drop(path)
        return False
    try:
        import pymupdf

        doc = pymupdf.open(path)
        width = doc[0].rect.width
        doc.close()
    except Exception:  # noqa: BLE001  没装 PyMuPDF / 不是图片：按字节数放行
        return True
    if width < MIN_HERO_WIDTH:
        _drop(path)
        return False
    return True


def _pick_file(source: Source, operator: str) -> str | None:
    """命名模板全落空时，在文件命名空间里搜一张像立绘的。"""
    query = (source.hero_search or HERO_SEARCH) % operator
    for title in search_files(source, query, limit=10):
        if any(w in title for w in HERO_BAD_WORDS):
            continue
        return title
    return None


def _blocked(source: Source) -> bool:
    return source.id in _BLOCKED


def _note_net_error(source: Source, err: Exception) -> None:
    if "567" in str(err) and source.id not in _BLOCKED:
        _BLOCKED[source.id] = True
        print("  !! %s 触发了站点风控（HTTP 567），本次构建的封面改用占位图" % source.name)


def fetch_hero(plan: Plan, operator: str, force: bool = False) -> str | None:
    """下载一张封面立绘；拿不到就返回 None（不抛错）。"""
    if not operator:
        return None
    if not force:
        hit = cached_hero(plan, operator)
        if hit:
            return hit
    source = _source(plan)
    if _blocked(source):
        return None
    key = (source.id, operator)
    if not force and key in _MISS:
        return None
    ensure_dir(os.path.join(plan.root, "cache", "img"))
    dest = hero_path(plan, operator)
    for tpl in hero_templates(source):
        try:
            path = download_image(source, tpl % operator, dest,
                                  max_bytes=14 * 1024 * 1024)
        except NetError as e:
            _note_net_error(source, e)
            if _blocked(source):
                _MISS[key] = True
                return None
            continue
        if _usable(path):
            return path
    title = _pick_file(source, operator)
    if not title:
        _MISS[key] = True
        return None
    try:
        path = download_image(source, title, dest, max_bytes=14 * 1024 * 1024)
    except NetError as e:
        _note_net_error(source, e)
        _MISS[key] = True
        return None
    if not _usable(path):
        _MISS[key] = True
        return None
    return path


def hero_candidates(book: dict) -> list[str]:
    """封面主角候选：密录声明的干员排最前，其余按出场次数从多到少。"""
    names: list[str] = []
    for sec in book.get("sections", []):
        portrait = sec.get("portrait")
        if portrait and portrait not in names:
            names.append(portrait)
    counts: dict[str, int] = {}
    for sec in book.get("sections", []):
        for story in sec.get("stories", []):
            for block in story.get("blocks", []):
                who = block.get("s") if block.get("k") == "d" else None
                if who and who not in HERO_SKIP_SPEAKERS:
                    counts[who] = counts.get(who, 0) + 1
    for name, _n in sorted(counts.items(), key=lambda kv: -kv[1]):
        if name not in names:
            names.append(name)
    return names


def volume_hero_name(book: dict) -> str | None:
    """一卷的封面主角（首选候选）。"""
    names = hero_candidates(book)
    return names[0] if names else None


def fetch_book_heroes(plan: Plan, books: list[dict], progress=None) -> dict[int, str | None]:
    """给每卷抓一张封面立绘，返回 {卷下标: 本地路径或 None}。"""
    out: dict[int, str | None] = {}
    for i, book in enumerate(books):
        path = fetch_book_hero(plan, book)
        out[i] = path
        name = volume_hero_name(book) or "—"
        print("  [%d/%d] %s %s" % (i + 1, len(books), name, "已就绪" if path else "无立绘"))
        if progress:
            progress(i + 1, len(books), "封面 %s" % name)
    return out


def fetch_book_hero(plan: Plan, book: dict) -> str | None:
    """取一卷的封面立绘：候选按出场次数依次试，直到找到一张能用的。"""
    for name in hero_candidates(book)[:6]:
        try:
            path = fetch_hero(plan, name)
        except ImageError:
            path = None
        if path:
            return path
    return None


# ---------------------------------------------------------------- 封面版式
def has_alpha(path: str) -> bool:
    """立绘是不是透明底：透明底可以满版叠在深色封面上，不透明的要放进画框。"""
    try:
        import pymupdf

        doc = pymupdf.open(path)
        # alpha=True 才会保留透明通道；默认 pixmap 是 RGB，永远看不出透明底
        pix = doc[0].get_pixmap(alpha=True)
        alpha = bool(pix.alpha)
        transparent = False
        if alpha:
            w, h = pix.width, pix.height
            for x, y in ((0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)):
                if pix.pixel(x, y)[3] < 32:
                    transparent = True
                    break
        doc.close()
    except Exception:  # noqa: BLE001
        return False
    return transparent


def _mix(color: str, k: float) -> str:
    """把颜色往黑里混 k（0=原色，1=全黑）。"""
    c = (color or "#d9a441").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        r, g, b = int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        r, g, b = 217, 164, 65
    f = max(0.0, min(1.0, 1.0 - k))
    return "#%02x%02x%02x" % (int(r * f), int(g * f), int(b * f))


def _lerp(c1: tuple[int, int, int], c2: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    return (int(c1[0] + (c2[0] - c1[0]) * t),
            int(c1[1] + (c2[1] - c1[1]) * t),
            int(c1[2] + (c2[2] - c1[2]) * t))


def _hex(color: str) -> tuple[int, int, int]:
    c = (color or "#d9a441").lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return 217, 164, 65


def art_svg(accent: str, width: int = 900, height: int = 1200) -> str:
    """占位封面图（SVG）：来源主色渐变 + 剪影。

    渐变与光晕都用大量色带/同心椭圆叠出来，不用 <linearGradient>——
    PyMuPDF 与部分 EPUB 阅读器对内联渐变支持不一致，色带处处都渲染得对。
    """
    top, mid, bottom = _mix(accent, 0.34), _mix(accent, 0.62), "#07080b"
    c_top, c_mid, c_bottom = _hex(top), _hex(mid), _hex(bottom)
    bands = []
    n = 56
    for i in range(n):
        t = i / (n - 1)
        if t < 0.46:
            r, g, b = _lerp(c_top, c_mid, t / 0.46)
        else:
            r, g, b = _lerp(c_mid, c_bottom, (t - 0.46) / 0.54)
        y = int(round(t * height))
        h = int(round(height / n)) + 2
        bands.append('<rect x="0" y="%d" width="%d" height="%d" fill="#%02x%02x%02x"/>'
                     % (y, width, h, r, g, b))
    c_accent = _hex(accent)
    glow = []
    steps = 16
    for i in range(steps):
        t = (i + 1) / steps
        rx = int(0.62 * width * t)
        ry = int(0.52 * height * t)
        glow.append('<ellipse cx="%d" cy="%d" rx="%d" ry="%d" fill="#%02x%02x%02x" '
                    'opacity="%.3f"/>' % (int(width * 0.66), int(height * 0.24),
                                          rx, ry, *c_accent, 0.026))
    return ('<svg xmlns="http://www.w3.org/2000/svg" width="%d" height="%d" '
            'viewBox="0 0 %d %d">%s%s'
            '<g fill="#05070a" opacity="0.58">'
            '<ellipse cx="450" cy="474" rx="112" ry="128"/>'
            '<path d="M176 1200 C182 826 276 704 450 694 C624 704 718 826 724 1200 Z"/></g>'
            '<g fill="#%02x%02x%02x" opacity="0.55">'
            '<path d="M570 352 C662 470 656 634 568 694 C612 552 602 446 542 380 Z"/>'
            '<path d="M340 362 C292 470 300 554 330 636 C278 554 284 446 326 352 Z"/></g>'
            '<g stroke="#%02x%02x%02x" stroke-width="1.6" opacity="0.30">'
            '<path d="M0 1030 H900"/><path d="M0 1048 H620"/><path d="M0 1066 H392"/></g>'
            '<rect x="0" y="0" width="8" height="%d" fill="#%02x%02x%02x" opacity="0.75"/>'
            '</svg>'
            % (width, height, width, height, "".join(bands), "".join(glow),
               *c_accent, *c_accent, height, *c_accent))


def fallback_art(plan: Plan) -> str | None:
    """本地生成一张占位封面图（各来源各自的强调色渐变 + 剪影），不联网。"""
    source = _source(plan)
    img_dir = ensure_dir(os.path.join(plan.root, "cache", "img"))
    png = os.path.join(img_dir, "占位_%s.png" % safe_filename(source.id))
    if os.path.isfile(png) and os.path.getsize(png) > 4096:
        return png
    svg = art_svg(source.accent or "#d9a441")
    svg_path = os.path.join(img_dir, "占位_%s.svg" % safe_filename(source.id))
    with open(svg_path, "w", encoding="utf-8", newline="\n") as f:
        f.write(svg)
    try:
        import pymupdf

        doc = pymupdf.open(svg_path)
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(1.2, 1.2))
        pix.save(png)
        doc.close()
    except Exception:  # noqa: BLE001  没装 PyMuPDF：返回 SVG，PDF/EPUB 仍能用
        return svg_path if os.path.isfile(svg_path) else None
    return png if os.path.isfile(png) else (svg_path if os.path.isfile(svg_path) else None)


def reset_negative_cache() -> None:
    """一次构建开始前清掉「问过了、没有」的缓存（命令行同一进程里可能连跑多本书）。"""
    _MISS.clear()
    _BLOCKED.clear()


COVER_EXT = (".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg")


def user_cover_dir(plan_id: str) -> str:
    """用户在界面里自己选的封面图存在用户目录，不写进仓库里的计划文件。"""
    return os.path.join(user_data_dir(), "covers", safe_filename(plan_id))


def user_covers(plan_id: str) -> list[str]:
    d = user_cover_dir(plan_id)
    if not os.path.isdir(d):
        return []
    names = sorted(f for f in os.listdir(d) if f.lower().endswith(COVER_EXT))
    return [os.path.join(d, n) for n in names]


def user_cover_config(plan_id: str) -> dict:
    """界面里对这本书的封面设置（是否联网抓取），存在用户目录，不改计划文件。"""
    path = os.path.join(user_cover_dir(plan_id), "config.json")
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def set_user_cover_config(plan_id: str, **kv) -> dict:
    d = user_cover_dir(plan_id)
    ensure_dir(d)
    data = user_cover_config(plan_id)
    data.update(kv)
    with open(os.path.join(d, "config.json"), "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    return data


def cover_fetch_enabled(plan: Plan) -> bool:
    """要不要联网抓封面：界面设置优先，其次计划文件里的 cover_fetch。"""
    cfg = user_cover_config(plan.id)
    if "fetch" in cfg:
        return bool(cfg["fetch"])
    return bool(getattr(plan, "cover_fetch", True))


def local_cover(plan: Plan, index: int = 0) -> str | None:
    """本地封面图片：计划里手填的优先，其次是界面里选过的（用户目录）。"""
    raw = getattr(plan, "cover", None)
    if raw:
        if isinstance(raw, str):
            raw = [raw]
        if isinstance(raw, (list, tuple)):
            items = [str(x) for x in raw if x]
            if items:
                pick = items[index] if index < len(items) else items[-1]
                path = pick if os.path.isabs(pick) else os.path.join(plan.root, pick)
                if os.path.isfile(path):
                    return path
                print("  !! 计划指定的封面图片不存在：%s（改用其它封面）" % path)
    picked = user_covers(plan.id)
    if picked:
        return picked[index] if index < len(picked) else picked[-1]
    return None


def cover_art(plan: Plan, book: dict, index: int = 0) -> tuple[str | None, str]:
    """一卷的封面美术：(本地图片路径, 版式)。

    优先级：计划里手填的本地图片 → （开关打开时）站点立绘 → 本机占位图。

    版式三档：
    * `bleed`  —— 透明底立绘，满版叠在封面右侧（PRTS 默认那套）；
    * `framed` —— 不透明图，放进右侧竖版画框，避免和封面文字打架；
    * `plain`  —— 一张都没有（连占位图都生成失败）。
    """
    path = local_cover(plan, index)
    if path:
        return path, ("bleed" if has_alpha(path) else "framed")
    if not cover_fetch_enabled(plan):
        path = fallback_art(plan)
        return (path, "framed") if path else (None, "plain")
    path = fetch_book_hero(plan, book)
    if not path:
        path = fallback_art(plan)
        return (path, "framed") if path else (None, "plain")
    return path, ("bleed" if has_alpha(path) else "framed")
