# -*- coding: utf-8 -*-
"""封面素材：占位图的生成规则、来源立绘命名、透明通道判断。全部不联网。"""
import json
import os
import tempfile
import unittest

from prts_story import images
from prts_story.config import Plan, source_accent, source_short
from prts_story.sources import BUILTIN, Source


class ArtSvgTest(unittest.TestCase):
    def test_bands_and_shapes(self):
        svg = images.art_svg("#7ac7c4")
        self.assertTrue(svg.startswith("<svg "))
        self.assertIn("#7ac7c4", svg)
        self.assertGreaterEqual(svg.count("<rect"), 50)      # 渐变色带
        self.assertGreaterEqual(svg.count("<ellipse"), 16)   # 光晕
        # 不用线性/径向渐变标签：PyMuPDF 与部分阅读器对它的支持不一致
        self.assertNotIn("linearGradient", svg)
        self.assertNotIn("radialGradient", svg)

    def test_unknown_accent_falls_back(self):
        svg = images.art_svg("")
        self.assertIn("#d9a441", svg)

    def test_mix(self):
        self.assertEqual(images._mix("#ffffff", 1.0), "#000000")
        self.assertEqual(images._mix("#ffffff", 0.0), "#ffffff")


class SourceHeroTest(unittest.TestCase):
    def test_every_builtin_declares_hero_templates(self):
        for src in BUILTIN:
            self.assertTrue(src.hero_templates, src.id)
            self.assertTrue(src.hero_search, src.id)

    def test_prts_and_bwiki_naming(self):
        by_id = {s.id: s for s in BUILTIN}
        self.assertIn("文件:立绘_%s_1.png", by_id["prts"].hero_templates)
        self.assertIn("文件:角色 %s 立绘.png", by_id["mc-wiki"].hero_templates)
        self.assertIn("文件:立绘 %s 01.png", by_id["reverse1999-wiki"].hero_templates)
        self.assertIn("文件:%s立绘.png", by_id["ys-wiki"].hero_templates)

    def test_custom_source_without_templates_uses_default(self):
        src = Source(id="x", name="x", api="https://x.invalid/api.php")
        self.assertEqual(images.hero_templates(src), list(images.HERO_TEMPLATES))


class FallbackArtTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wt-cover-test-")
        os.makedirs(os.path.join(self.tmp, "plans"))
        with open(os.path.join(self.tmp, "plans", "t.json"), "w", encoding="utf-8") as f:
            json.dump({"title": "t", "volumes": []}, f)
        self.plan = Plan.load(self.tmp, "t")

    def test_fallback_art_is_local_and_reused(self):
        path = images.fallback_art(self.plan)
        self.assertIsNotNone(path)
        self.assertTrue(os.path.isfile(path))
        self.assertGreater(os.path.getsize(path), 4096)
        self.assertIn(os.path.join("cache", "img"), path)
        again = images.fallback_art(self.plan)
        self.assertEqual(path, again)

    def test_generated_art_is_opaque(self):
        path = images.fallback_art(self.plan)
        if path.endswith(".png"):
            self.assertFalse(images.has_alpha(path))


class SourceLabelTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="wt-label-test-")
        os.makedirs(os.path.join(self.tmp, "plans"))

    def _plan(self, data):
        with open(os.path.join(self.tmp, "plans", "t.json"), "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        return Plan.load(self.tmp, "t")

    def test_label_uses_plan_source_without_api_url(self):
        plan = self._plan({"title": "t", "source": "原神 · BWIKI（https://wiki.biligame.com/ys/api.php）",
                           "volumes": []})
        self.assertEqual(source_short(plan), "原神 · BWIKI")

    def test_label_falls_back_to_registry(self):
        plan = self._plan({"title": "t", "source_id": "mc-wiki", "volumes": []})
        self.assertEqual(source_short(plan), "鸣潮 · BWIKI")

    def test_accent_follows_source(self):
        plan = self._plan({"title": "t", "source_id": "reverse1999-wiki", "volumes": []})
        self.assertEqual(source_accent(plan), "#7ac7c4")


class CoverHtmlTest(unittest.TestCase):
    """封面三档版式：满版 / 画框 / 纯字。"""

    def setUp(self):
        from prts_story.pdf_html import _cover_html

        self._cover = _cover_html
        self.tmp = tempfile.mkdtemp(prefix="wt-cover-html-")
        os.makedirs(os.path.join(self.tmp, "plans"))
        with open(os.path.join(self.tmp, "plans", "t.json"), "w", encoding="utf-8") as f:
            json.dump({"title": "书", "source": "原神 · BWIKI（https://x/api.php）",
                       "volumes": []}, f, ensure_ascii=False)
        self.plan = Plan.load(self.tmp, "t")
        self.img = os.path.join(self.tmp, "art.png")
        with open(self.img, "wb") as f:
            f.write(b"placeholder")   # 这里只用到路径，不解析图片内容

    def test_bleed_has_no_extra_class(self):
        html = self._cover(self.plan, "卷一", "", ["第 1 卷"], (self.img, "bleed"))
        self.assertIn('<div class="cover"', html)
        self.assertIn("--hero:url", html)
        self.assertIn("原神 · BWIKI", html)          # 系列名取来源，不写死明日方舟

    def test_framed_class(self):
        html = self._cover(self.plan, "卷一", "", [], (self.img, "framed"))
        self.assertIn('<div class="cover framed"', html)

    def test_plain_when_no_art(self):
        html = self._cover(self.plan, "卷一", "", [], None)
        self.assertIn('<div class="cover plain"', html)
        self.assertNotIn("--hero:url", html)

    def test_missing_path_degrades_to_plain(self):
        html = self._cover(self.plan, "卷一", "", [], (None, "bleed"))
        self.assertIn('<div class="cover plain"', html)


if __name__ == "__main__":
    unittest.main()