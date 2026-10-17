# -*- coding: utf-8 -*-
"""由品牌矢量图生成规范命名的图标资产。

来源：E:\\Picture\\Homewardbird\\LOGO实心.svg（矢量优先，位图兜底）
产物（assets/icon/）：
    app-1024.png / app-512.png / app-256.png   正方不透明图标
    app.ico                                    16/24/32/48/64/128/256 多尺寸
    logo-white.png / logo-black.png            透明底单色标志，供界面使用
    logo-source.svg / logo-source.png          原始素材留档

用法：py -X utf8 scripts/make_icons.py [--source <svg 或 png>]
"""
from __future__ import annotations

import argparse
import os
import shutil
import sys

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
OUT = os.path.join(ROOT, "assets", "icon")

DEFAULT_SVG = r"E:\Picture\Homewardbird\LOGO实心.svg"
DEFAULT_PNG = r"E:\Picture\Homewardbird\LOGO实心.png"

ICO_SIZES = [16, 24, 32, 48, 64, 128, 256]
PNG_SIZES = [1024, 512, 256]
WEB_DIR = os.path.join(ROOT, "prts_story", "gui", "web", "img")


def rasterize_svg(path: str, size: int = 1024) -> Image.Image:
    """用 PyMuPDF 把 SVG 光栅化到正方形画布。"""
    import fitz

    doc = fitz.open(path)
    page = doc[0]
    rect = page.rect
    # 等比放进正方形，居中
    scale = min(size / rect.width, size / rect.height)
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), alpha=True)
    img = Image.frombytes("RGBA", (pix.width, pix.height), pix.samples)
    doc.close()
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    canvas.paste(img, ((size - img.width) // 2, (size - img.height) // 2), img)
    return canvas


def flatten(img: Image.Image) -> Image.Image:
    """透明底铺白，得到可直接做 exe 图标的方图。"""
    bg = Image.new("RGBA", img.size, (255, 255, 255, 255))
    bg.alpha_composite(img)
    return bg.convert("RGB")


def knockout(img: Image.Image, rgb: tuple[int, int, int]) -> Image.Image:
    """把单色标志的亮度转成透明度，供深/浅两种背景使用。

    同时乘上原图自带的 alpha：矢量光栅化后透明区域的 RGB 常常是 0（黑），
    只看亮度会把它当成实心黑块，必须先与 alpha 相乘。
    """
    from PIL import ImageChops

    grey = img.convert("L")
    alpha = Image.eval(grey, lambda v: 255 - v)
    alpha = ImageChops.multiply(alpha, img.getchannel("A"))
    out = Image.new("RGBA", img.size, rgb + (0,))
    out.putalpha(alpha)
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", help="SVG（优先）或 PNG 源文件")
    args = ap.parse_args(argv)

    svg = args.source or DEFAULT_SVG
    png = DEFAULT_PNG
    os.makedirs(OUT, exist_ok=True)
    os.makedirs(WEB_DIR, exist_ok=True)

    base: Image.Image | None = None
    if os.path.isfile(svg) and svg.lower().endswith(".svg"):
        try:
            base = rasterize_svg(svg, 1024)
            print("光栅化 SVG → 1024×1024")
        except Exception as e:
            print("SVG 光栅化失败（%s），改用内置 PNG" % e)
    if base is None:
        src = args.source if (args.source and args.source.lower().endswith(".png")) else png
        if not os.path.isfile(src):
            print("找不到源图：%s" % src, file=sys.stderr)
            return 1
        base = Image.open(src).convert("RGBA")
        if max(base.size) < 1024:
            base = base.resize((1024, 1024), Image.LANCZOS)
        print("使用位图 → %d×%d" % base.size)

    flat = flatten(base)
    for size in PNG_SIZES:
        flat.resize((size, size), Image.LANCZOS).save(
            os.path.join(OUT, "app-%d.png" % size), "PNG")
    flat.save(os.path.join(OUT, "app.ico"), "ICO",
              sizes=[(s, s) for s in ICO_SIZES])
    print("写出 %s（%d 尺寸）" % (os.path.join(OUT, "app.ico"), len(ICO_SIZES)))

    for name, rgb in (("logo-white.png", (255, 255, 255)),
                      ("logo-black.png", (0, 0, 0))):
        k = knockout(base, rgb)
        for target in (OUT, WEB_DIR):
            k.save(os.path.join(target, name), "PNG")
    # 界面直接用 512 尺寸，省流量
    Image.open(os.path.join(OUT, "logo-white.png")).resize(
        (256, 256), Image.LANCZOS).save(os.path.join(WEB_DIR, "logo.png"), "PNG")
    print("写出 logo-white / logo-black（含界面 img/）")

    if os.path.isfile(svg):
        shutil.copyfile(svg, os.path.join(OUT, "logo-source.svg"))
    if os.path.isfile(png):
        shutil.copyfile(png, os.path.join(OUT, "logo-source.png"))
    print("完成：%s" % OUT)
    return 0


if __name__ == "__main__":
    sys.exit(main())
