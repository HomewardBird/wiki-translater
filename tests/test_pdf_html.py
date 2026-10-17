# -*- coding: utf-8 -*-
"""PDF（Chromium 引擎）排版层测试：纯 HTML 组装 + 工具函数，不启动浏览器。"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prts_story.config import Plan
from prts_story.pdf_html import (build_combined_html, build_toc_html,
                                 build_volume_html, esc, find_chromium,
                                 _blocks_html, _toc_html)


def make_plan(tmp: str) -> Plan:
    os.makedirs(os.path.join(tmp, "plans"), exist_ok=True)
    data = {
        "title": "测试书",
        "fanli": ["第一条凡例", "第二条凡例"],
        "volumes": [{"title": "卷一　测试卷", "sections": [
            {"name": "测试节", "kind": "secret", "pages": ["测试/干员密录/1"]}]}],
    }
    with open(os.path.join(tmp, "plans", "t.json"), "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False)
    return Plan(tmp, "t")


def make_books() -> list[dict]:
    return [{
        "title": "卷一　测试卷",
        "sections": [
            {"name": "干员密录·甲", "portrait": None, "stories": [
                {"title": "《第一篇》", "blocks": [
                    {"k": "h4", "t": "第一关"},
                    {"k": "d", "s": "甲", "t": "台词一"},
                    {"k": "n", "t": "旁白一"},
                    {"k": "q", "t": "♪ 幽暗"},
                    {"k": "c", "t": ["A", "B"]},
                ]},
            ]},
            {"name": "干员密录·乙", "portrait": None, "stories": [
                {"title": "干员密录·乙", "blocks": [{"k": "n", "t": "旁白二"}]},
            ]},
        ],
    }]


class TestEscaping(unittest.TestCase):
    def test_esc(self):
        self.assertEqual(esc('<a & "b">'), '&lt;a &amp; "b"&gt;')


class TestBlocks(unittest.TestCase):
    def test_all_kinds(self):
        html = _blocks_html([
            {"k": "h4", "t": "标题"},
            {"k": "d", "s": "甲", "t": "台词"},
            {"k": "n", "t": "旁白"},
            {"k": "q", "t": "◆"},
            {"k": "c", "t": ["A", "B"]},
        ])
        self.assertIn('<div class="h4">标题</div>', html)
        self.assertIn('<span class="sp c1">甲：</span>台词', html)
        # 篇末几段会带 tail 前缀（避免孤行），这里只关心旁白本身
        self.assertRegex(html, r'<p class="(?:tail )?n">旁白</p>')
        self.assertIn("换景", html)
        self.assertIn("A　／　B", html)

    def test_speaker_colors_follow_appearance(self):
        html = _blocks_html([
            {"k": "d", "s": "甲", "t": "一"},
            {"k": "d", "s": "乙", "t": "二"},
            {"k": "d", "s": "甲", "t": "三"},
        ])
        self.assertIn("sp c1", html)      # 甲 先出场
        self.assertIn("sp c2", html)      # 乙 第二个

    def test_same_speaker_keeps_tight(self):
        html = _blocks_html([
            {"k": "d", "s": "甲", "t": "一"},
            {"k": "d", "s": "甲", "t": "二"},
            {"k": "d", "s": "乙", "t": "三"},
        ])
        # 同一人连说两句不加间距；换人才加 chg
        self.assertEqual(html.count("chg"), 2)   # 甲乙两段各一次（首段也算）

    def test_beat_gets_breathing_room(self):
        html = _blocks_html([{"k": "d", "s": "甲", "t": "……"}])
        self.assertIn("beat", html)
        self.assertNotIn("beat", _blocks_html([{"k": "d", "s": "甲", "t": "……不对"}]))

    def test_cue_run_becomes_one_band(self):
        html = _blocks_html([
            {"k": "q", "t": "♪ 停"},
            {"k": "q", "t": "◆"},
            {"k": "q", "t": "♪ 幽暗"},
        ])
        self.assertEqual(html.count("cues"), 1)      # 三条合成一组（篇末那组还会带 tail 类）
        self.assertIn("音乐", html)
        self.assertIn("停", html)
        self.assertIn("换景", html)
        self.assertIn("幽暗", html)


class TestTitleNoise(unittest.TestCase):
    def test_leading_dup_title_is_dropped(self):
        from prts_story.parse import drop_dup_head
        blocks = [{"k": "h4", "t": "《千年一叹》"}, {"k": "d", "s": "甲", "t": "一"}]
        out = drop_dup_head(blocks, "《千年一叹》")
        self.assertEqual([b["k"] for b in out], ["d"])

    def test_keeps_different_heading(self):
        from prts_story.parse import drop_dup_head
        blocks = [{"k": "h4", "t": "第一关（前）"}, {"k": "n", "t": "旁白"}]
        self.assertEqual(len(drop_dup_head(blocks, "AF-ST1 洪炉示岁·迎春")), 2)

    def test_crumb_not_repeated(self):
        from prts_story.style import crumb_redundant
        self.assertTrue(crumb_redundant("洪炉示岁", "AF-ST1 洪炉示岁·迎春"))
        self.assertFalse(crumb_redundant("干员密录·年", "《千年一叹》"))

    def test_section_label_drops_repeated_title(self):
        from prts_story.style import section_label
        self.assertEqual(section_label("干员档案·惊蛰（五星术师）", "干员档案"), "惊蛰（五星术师）")
        self.assertEqual(section_label("干员密录·年", "《千年一叹》"), "干员密录·年")


class TestToc(unittest.TestCase):
    def test_page_numbers_from_map(self):
        entries = [{"idx": 0, "level": 1, "title": "卷"}, {"idx": 1, "level": 3, "title": "篇"}]
        html = _toc_html(entries, {0: 8, 1: 9}, True)
        self.assertIn('<a href="#h0">卷</a>', html)
        self.assertIn('<span class="p">8</span>', html)
        self.assertIn('<span class="p">9</span>', html)

    def test_placeholder_before_pagination(self):
        entries = [{"idx": 0, "level": 1, "title": "卷"}]
        html = _toc_html(entries, None, False)
        self.assertIn("回填", html)          # 第一遍渲染时给一句提示
        self.assertNotIn('<span class="p">0</span>', html)


class TestVolumeHtml(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prts-test-")
        self.plan = make_plan(self.tmp)
        self.books = make_books()

    def test_structure_and_entries(self):
        html, entries = build_volume_html(self.plan, self.books, 0, {0: 4, 1: 5, 2: 6})
        self.assertIn('<div class="cover plain">', html)
        self.assertIn("凡　例", html)
        self.assertIn("目　录", html)
        self.assertIn("卷一　测试卷", html)
        self.assertIn("《第一篇》", html)
        # 有节标题时节是 2 级、篇是 3 级；篇名与节名相同时只印篇，篇直接挂在卷下（2 级）
        self.assertEqual([e["level"] for e in entries], [2, 3, 2])
        self.assertEqual(entries[0]["title"], "干员密录·甲")

    def test_first_story_after_section_does_not_break_page(self):
        html, _ = build_volume_html(self.plan, self.books, 0, None)
        # 节标题后的第一篇不加 page-break，避免产生空白页
        self.assertIn('class="story nobreak"', html)

    def test_second_story_starts_new_page(self):
        books = make_books()
        books[0]["sections"][0]["stories"].append(
            {"title": "《第二篇》", "blocks": [{"k": "n", "t": "旁白三"}]})
        html, entries = build_volume_html(self.plan, books, 0, None)
        self.assertIn('<div class="story">', html)
        self.assertEqual(len(entries), 4)


class TestCombinedAndTocDoc(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="prts-test-")
        self.plan = make_plan(self.tmp)
        self.books = make_books()

    def test_combined_has_volume_heading(self):
        html, entries = build_combined_html(self.plan, self.books, {0: 8, 1: 9, 2: 10})
        self.assertIn('class="volh"', html)
        self.assertIn("全一册", html)
        self.assertEqual(entries[0]["level"], 1)
        self.assertEqual(entries[0]["title"], "卷一　测试卷")

    def test_toc_doc_lists_stories(self):
        html, entries = build_toc_html(self.plan, self.books, {0: 1}, True)
        self.assertIn("总目录", html)
        self.assertIn("《第一篇》", html)
        # 全书概览不写书签，因此不产出书签条目
        self.assertEqual(entries, [])


class TestBrowserLookup(unittest.TestCase):
    def test_find_chromium_returns_path_or_none(self):
        p = find_chromium()
        self.assertTrue(p is None or os.path.isfile(p))


if __name__ == "__main__":
    unittest.main()
