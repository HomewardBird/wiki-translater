# -*- coding: utf-8 -*-
"""命令行入口。

用法：
    wiki-transporter doctor                       # 检查环境
    wiki-transporter fetch    [--force]           # 抓取 / 续抓原始 wikitext
    wiki-transporter build    [--volumes 1-3,5]   # 生成 Word + EPUB（默认同时生成合订本）
    wiki-transporter pdf      [--volumes ...]     # 导出 PDF（默认 Chromium 引擎）
    wiki-transporter all                          # 一条龙：抓取 → Word/EPUB → PDF
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
import unicodedata

from . import __version__
from .config import (Plan, ensure_dir, output_root, safe_filename,
                     setup_console)
from .docx_build import build_combined_docx, build_toc_docx, build_volume_docx
from .epub_build import build_combined_epub, build_volume_epub
from .fetch import fetch_plan
from .images import fetch_portraits
from .parse import book_stats, build_books
from .pdf import doctor, export_pdfs

COMBINED_NAME = "全一册"


def _disp_width(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1 for c in s)


def _pad(s, width: int, align: str = "left") -> str:
    """按终端显示宽度对齐（中文占 2 列）。"""
    s = str(s)
    if _disp_width(s) > width:
        kept, w = "", 0
        for ch in s:
            cw = 2 if unicodedata.east_asian_width(ch) in "WF" else 1
            if w + cw > width:
                break
            kept += ch
            w += cw
        s = kept
    fill = " " * max(0, width - _disp_width(s))
    return fill + s if align == "right" else s + fill



def parse_selection(spec: str | None, total: int) -> list[int]:
    """把 "1-3,5" 解析成 0 基下标列表；空值表示全部。"""
    if not spec:
        return list(range(total))
    picked: set[int] = set()
    for part in spec.replace("，", ",").split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            picked.update(range(int(a) - 1, int(b)))
        else:
            picked.add(int(part) - 1)
    return sorted(i for i in picked if 0 <= i < total)


def _print_table(rows: list[tuple[str, int, int, int]]):
    print("")
    print("  %s %s %s %s" % (_pad("卷", 36), _pad("篇数", 6, "right"),
                            _pad("字数", 10, "right"), _pad("页数", 7, "right")))
    print("  " + "-" * 63)
    for title, n, chars, pages in rows:
        print("  %s %s %s %s" % (
            _pad(title, 36), _pad(n, 6, "right"),
            _pad("%.1f万" % (chars / 10000), 10, "right"),
            _pad(pages if pages else "-", 7, "right")))
    print("")


def expected_docx(plan: Plan, books: list[dict], selection: list[int],
                  combined: bool) -> list[str]:
    out = plan.out_dir("docx")
    files = [os.path.join(out, "00_总目录与说明.docx")]
    for i in selection:
        files.append(os.path.join(out, "%02d_%s.docx" % (
            i + 1, safe_filename(books[i]["title"].replace("　", "_")))))
    if combined:
        files.append(os.path.join(out, COMBINED_NAME + ".docx"))
    return files


def load_books(plan: Plan, strict: bool = True):
    print("解析缓存 → 结构化文本 …")
    books = build_books(plan, strict=strict)
    ensure_dir(plan.build_dir)
    with open(plan.books_json, "w", encoding="utf-8") as f:
        json.dump(books, f, ensure_ascii=False, indent=1)
    n = sum(book_stats(b)[0] for b in books)
    chars = sum(book_stats(b)[1] for b in books)
    print("  %d 卷 / %d 篇 / 约 %.1f 万字（结构化数据：%s）"
          % (len(books), n, chars / 10000, plan.books_json))
    return books


def make_documents(plan: Plan, books: list[dict], selection: list[int],
                   combined: bool) -> dict:
    out_docx_dir = plan.out_dir("docx")
    out_epub_dir = plan.out_dir("epub")
    ensure_dir(out_docx_dir)
    ensure_dir(out_epub_dir)

    docx_files, epub_files = [], []
    for i in selection:
        docx = build_volume_docx(plan, books, i, out_docx_dir)
        epub = build_volume_epub(plan, books, i, out_epub_dir)
        docx_files.append(docx)
        epub_files.append(epub)
        print("  已生成 %s" % os.path.basename(docx))

    toc = build_toc_docx(plan, books, out_docx_dir)
    docx_files.insert(0, toc)

    combined_paths = {}
    if combined:
        out_root = os.path.join(plan.root, "out", plan.id)
        combined_paths["docx"] = build_combined_docx(
            plan, books, out_docx_dir, COMBINED_NAME)
        combined_paths["epub"] = build_combined_epub(
            plan, books, out_epub_dir, COMBINED_NAME)
        print("  已生成合订本 %s（Word / EPUB）" % COMBINED_NAME)
    return {"docx": docx_files, "epub": epub_files, "combined": combined_paths}


def make_pdfs(plan: Plan, docx_files: list[str], combined: bool,
              write_back: bool = False) -> dict:
    pdf_dir = ensure_dir(plan.out_dir("pdf"))
    print("导出 PDF → %s" % pdf_dir)
    target = list(docx_files)
    if combined:
        combined_docx = os.path.join(plan.out_dir("docx"), COMBINED_NAME + ".docx")
        if os.path.isfile(combined_docx):
            target.append(combined_docx)
    started = time.time()
    pages = export_pdfs(target, pdf_dir, write_back=write_back)
    if pages:
        total = sum(pages.values())
        print("  PDF 完成，合计 %d 页（用时 %.1fs）" % (total, time.time() - started))
    else:
        print("  PDF 完成（用时 %.1fs）" % (time.time() - started))
    return pages


def pick_engine(engine: str) -> str:
    """auto：优先 Chromium（不依赖 Office，还能生成书签），否则 Word / LibreOffice。"""
    from .pdf_html import find_chromium

    if engine == "auto":
        if find_chromium():
            return "chrome"
        from .pdf import soffice_available, word_available
        if word_available():
            return "word"
        if soffice_available():
            return "soffice"
        return "chrome"
    return engine


def make_pdfs_html(plan: Plan, books: list[dict], selection: list[int],
                   combined: bool, font_scale: float = 1.0) -> dict:
    from .pdf_html import export_pdfs_html

    pdf_dir = ensure_dir(plan.out_dir("pdf"))
    print("导出 PDF → %s" % pdf_dir)
    started = time.time()
    pages = export_pdfs_html(plan, books, selection, pdf_dir, combined=combined,
                             combined_name=COMBINED_NAME, font_scale=font_scale)
    print("  PDF 完成，合计 %d 页（用时 %.1fs）" % (sum(pages.values()), time.time() - started))
    return pages


def cmd_doctor(args) -> int:
    info = doctor()
    print("Wiki Transporter %s" % __version__)
    for k, v in info.items():
        print("  %-14s %s" % (k, v))
    if not info.get("python-docx"):
        print("\n缺少 python-docx：请先执行  pip install python-docx")
        return 1
    if not info.get("chromium") and not info.get("word") and not info.get("libreoffice"):
        print("\n提示：没有 Chromium / Word / LibreOffice，只能生成 Word/EPUB，无法导出 PDF。")
    if info.get("chromium") and not info.get("pymupdf"):
        print("\n提示：装了 PyMuPDF（pip install pymupdf）才能回填目录页码、写入 PDF 书签。")
    return 0


def cmd_fetch(plan: Plan, args) -> int:
    res = fetch_plan(plan, delay=args.delay, force=args.force)
    if not args.no_images:
        fetch_portraits(plan, force=args.force)
    return 0 if res["ok"] else 1


def cmd_build(plan: Plan, args) -> int:
    books = load_books(plan, strict=not args.allow_missing)
    selection = parse_selection(args.volumes, len(books))
    print("生成 Word / EPUB …")
    make_documents(plan, books, selection, combined=not args.no_combined)
    rows = []
    for i, b in enumerate(books):
        n, chars = book_stats(b)
        rows.append((b["title"], n, chars, 0))
    _print_table(rows)
    return 0


def cmd_pdf(plan: Plan, args) -> int:
    books = load_books(plan, strict=not args.allow_missing)
    selection = parse_selection(args.volumes, len(books))
    combined = not args.no_combined
    engine = pick_engine(getattr(args, "engine", "auto"))

    if engine == "chrome":
        pages = make_pdfs_html(plan, books, selection, combined,
                               font_scale=getattr(args, "font_scale", 1.0))
    else:
        paths = expected_docx(plan, books, selection, combined)
        if not args.rebuild and all(os.path.isfile(p) for p in paths):
            print("复用已有 Word 文件（加 --rebuild 可强制重排）")
            docs = {"docx": paths}
        else:
            docs = make_documents(plan, books, selection, combined=combined)
        pages = make_pdfs(plan, docs["docx"], combined=combined,
                          write_back=getattr(args, "write_back", False))
    rows = []
    for i, b in enumerate(books):
        n, chars = book_stats(b)
        key = "%02d_%s" % (i + 1, safe_filename(b["title"].replace("　", "_")))
        rows.append((b["title"], n, chars, pages.get(key, 0)))
    _print_table(rows)
    if combined and pages:
        print("  合订本 %s：%d 页" % (COMBINED_NAME, pages.get(COMBINED_NAME, 0)))
    return 0


def cmd_all(plan: Plan, args) -> int:
    if not fetch_plan(plan, delay=args.delay, force=False)["ok"]:
        print("抓取有失败页面，继续用已有缓存构建。")
    if not args.no_images:
        fetch_portraits(plan)
    args.rebuild = True
    return cmd_pdf(plan, args)


def build_parser() -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--root", help="项目根目录（含 plans/），默认自动查找")
    common.add_argument("--plan", default="sui", help="计划名，对应 plans/<name>.json（默认 sui）")
    common.add_argument("--volumes", help='只处理指定卷，如 "1-3,5"')
    common.add_argument("--no-combined", action="store_true", help="不生成全一册合订本")
    common.add_argument("--allow-missing", action="store_true", help="缺页时只警告不中止")
    common.add_argument("--rebuild", action="store_true", help="强制重新排版（默认复用已有 docx）")
    common.add_argument("--no-images", action="store_true", help="不下载干员头像")
    common.add_argument("--no-cover", action="store_true",
                        help="不联网抓封面立绘，只用本机占位图")
    common.add_argument("--cover-image", action="append", metavar="PATH",
                        help="用自己的图片做封面（可给多次，按卷顺序依次使用）")
    common.add_argument("--no-credit", action="store_true",
                        help="不写整理者署名（PDF 文档属性 / Word 作者 / EPUB dc:creator / 封面 / 凡例）")

    p = argparse.ArgumentParser(
        prog="wiki-transporter",
        description="抓取 PRTS（明日方舟 Wiki）的剧情 wikitext，排版成 Word / PDF / EPUB 分卷书。",
    )
    p.add_argument("--version", action="version", version="Wiki Transporter " + __version__)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", parents=[common], help="检查运行环境").set_defaults(func=cmd_doctor)
    f = sub.add_parser("fetch", parents=[common], help="抓取原始 wikitext（可断点续抓）")
    f.add_argument("--delay", type=float, default=0.35, help="每页间隔秒数（默认 0.35）")
    f.add_argument("--force", action="store_true", help="忽略缓存重新下载")
    f.set_defaults(func=cmd_fetch)
    b = sub.add_parser("build", parents=[common], help="生成 Word 与 EPUB")
    b.set_defaults(func=cmd_build)
    d = sub.add_parser("pdf", parents=[common], help="生成 PDF（默认用 Chromium 引擎）")
    d.add_argument("--delay", type=float, default=0.35)
    d.add_argument("--engine", choices=("auto", "chrome", "word", "soffice"), default="auto",
                   help="PDF 引擎：auto 优先 Chromium，其次 Word / LibreOffice")
    d.add_argument("--write-back", action="store_true",
                   help="把更新后的目录页码回存进 .docx（默认不回存；个别环境可能卡住）")
    d.add_argument("--font-scale", type=float, choices=(1.0, 1.15, 1.3), default=1.0,
                   help="PDF 正文字号倍率：1.0 / 1.15 / 1.3（平板横屏双页看不清时调大）")
    d.set_defaults(func=cmd_pdf)
    a = sub.add_parser("all", parents=[common], help="抓取 → Word/EPUB → PDF 一条龙")
    a.add_argument("--delay", type=float, default=0.35, help="每页间隔秒数（默认 0.35）")
    a.add_argument("--engine", choices=("auto", "chrome", "word", "soffice"), default="auto",
                   help="PDF 引擎：auto 优先 Chromium，其次 Word / LibreOffice")
    a.add_argument("--write-back", action="store_true",
                   help="把更新后的目录页码回存进 .docx（默认不回存）")
    a.add_argument("--font-scale", type=float, choices=(1.0, 1.15, 1.3), default=1.0,
                   help="PDF 正文字号倍率：1.0 / 1.15 / 1.3")
    a.set_defaults(func=cmd_all)

    g = sub.add_parser("gui", help="打开图形界面（不带参数运行时默认走这里）")
    g.add_argument("--port", type=int, default=0, help="监听端口，0 表示自动分配")
    g.add_argument("--no-browser", action="store_true", help="只起服务，不自动开窗口")
    g.set_defaults(func=lambda plan, args: _cmd_gui(args))

    s = sub.add_parser("search", help="在命令行里搜索剧情页面")
    s.add_argument("query", help='关键词；用引号精确：search \'"千年一叹"\'')
    s.add_argument("--limit", type=int, default=12)
    s.add_argument("--remote", action="store_true", help="同时使用站内搜索")
    s.set_defaults(func=lambda plan, args: _cmd_search(args))

    cfg = sub.add_parser("config", help="查看 / 修改书籍存放位置")
    cfg.add_argument("--out", help="书籍存放位置；用 --out default 恢复默认")
    cfg.set_defaults(func=lambda plan, args: _cmd_config(args))
    return p


def _cmd_config(args) -> int:
    from .config import (app_home, author_name, save_settings,
                         settings_path, workspace_dir)

    patch = {}
    if args.out:
        patch["out_root"] = ("" if args.out.strip().lower() in ("default", "-", "reset")
                             else args.out)
    if patch:
        save_settings(patch)
    print("整理者    ：%s" % author_name())
    print("存放位置  ：%s" % workspace_dir())
    print("默认位置  ：%s" % app_home())
    print("配置文件  ：%s" % settings_path())
    return 0


def _cmd_gui(args) -> int:
    from .gui import run_gui

    return run_gui(port=args.port, browser=not args.no_browser)


def _cmd_search(args) -> int:
    from .catalog import build_catalog, load_catalog, search
    from .sources import PRTS

    cat = load_catalog(PRTS)
    if not cat.entries:
        print("首次使用，正在建立索引（%s）…" % PRTS.name)
        cat = build_catalog(PRTS, progress=lambda d, t, m: print("  " + m))
    res = search(cat, args.query, limit=args.limit, remote=args.remote)
    if res.get("did_you_mean"):
        d = res["did_you_mean"]
        print("没有确切匹配，是不是「%s」？" % d["name"])
    if not res["results"]:
        print("没有找到「%s」" % res["query"] if res.get("query") else "没有找到")
        return 1
    print("%s（%d 个结果）" % ("精确匹配" if res["mode"] == "exact" else "搜索结果", res["total"]))
    for r in res["results"]:
        print("  %-28s %-10s %s" % (r["name"][:28], r["code"] or "", r["title"]))
    return 0


def main(argv: list[str] | None = None) -> int:
    setup_console()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        # 双击 exe / 直接运行：打开图形界面
        try:
            from .gui import run_gui

            return run_gui()
        except ImportError as e:  # 没有 GUI 依赖时退回帮助
            print("图形界面启动失败：%s" % e)
            build_parser().print_help()
            return 1
    args = build_parser().parse_args(argv)
    if not hasattr(args, "root"):  # gui / search / config 不需要计划文件
        return args.func(None, args)
    root = output_root(args.root)
    if args.func is cmd_doctor:
        return cmd_doctor(args)
    plan = Plan(root, args.plan)
    # 命令行封面开关覆盖计划文件里的设置（命令行 > 计划文件）
    if getattr(args, "no_cover", False):
        plan.cover_fetch = False
    # 整理者署名默认写；--no-credit 才关掉
    plan.apply_credit(not getattr(args, "no_credit", False))
    covers = getattr(args, "cover_image", None)
    if covers:
        plan.cover = covers if len(covers) > 1 else covers[0]
    from .images import reset_negative_cache

    reset_negative_cache()   # 每条命令重新问一次封面，别继承上一次的「没有」
    print("计划：%s ｜ 根目录：%s" % (args.plan, root))
    return args.func(plan, args)


if __name__ == "__main__":
    sys.exit(main())
