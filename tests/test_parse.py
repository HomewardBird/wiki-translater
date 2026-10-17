# -*- coding: utf-8 -*-
"""解析器单元测试：不联网、不需要缓存。"""
import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from prts_story.cli import parse_selection  # noqa: E402
from prts_story.parse import clean, page_title, parse_archive, parse_story  # noqa: E402

SAMPLE = """{{剧情模拟器|图片数据={{Widget:Data_Image}}|文本数据=
[background image="bg_yard"]
[Character name="年" image="char_2014_nian"]
[name="年"] '''我说'''，这炉子得再加把火。
[Delay time="0.5"]
[subtitle text="——三天之后——"]
[sticker text="啪"]
[decision options="再看看; 直接走"]
[playmusic key="m_sui_loop"]
[stopmusic]
[video res="video_01"]
}}
[[分类:剧情]]
"""


class TestClean(unittest.TestCase):
    def test_wikitext_marks(self):
        self.assertEqual(clean("'''粗体'''"), "粗体")
        self.assertEqual(clean("{{color|#fff|红字}}"), "红字")
        self.assertEqual(clean("[[年|年]]与[[夕]]"), "年与夕")
        self.assertEqual(clean("a<br/>b"), "a b")
        self.assertEqual(clean("&nbsp;&amp;&quot;"), '&"')


class TestParseStory(unittest.TestCase):
    def setUp(self):
        self.blocks, self.header = parse_story(SAMPLE)

    def kinds(self):
        return [b["k"] for b in self.blocks]

    def test_order_and_kinds(self):
        self.assertEqual(self.kinds(), ["q", "d", "n", "n", "c", "q", "q", "q"])

    def test_dialogue(self):
        self.assertEqual(self.blocks[1]["s"], "年")
        self.assertEqual(self.blocks[1]["t"], "我说，这炉子得再加把火。")

    def test_choices(self):
        self.assertEqual(self.blocks[4]["t"], ["再看看", "直接走"])

    def test_cues(self):
        # 演出提示已读者化：换景 ◆、音乐 ♪（可带中文曲名）、停乐 ♪ 停、影像 ▷
        self.assertEqual(self.blocks[0]["t"], "◆")
        self.assertEqual(self.blocks[5]["t"], "♪")
        self.assertEqual(self.blocks[6]["t"], "♪ 停")
        self.assertEqual(self.blocks[7]["t"], "▷")

    def test_cues_have_no_asset_ids(self):
        joined = " ".join(str(b.get("t")) for b in self.blocks if b["k"] == "q")
        self.assertNotIn("bg_yard", joined)
        self.assertNotIn("m_sui_loop", joined)
        self.assertNotIn("video_01", joined)

    def test_music_name_mapping(self):
        blocks, _ = parse_story('[playmusic key="$darkness01_loop"]\n[stopmusic]')
        self.assertEqual([b["t"] for b in blocks], ["♪ 幽暗", "♪ 停"])

    def test_directives_dropped(self):
        joined = " ".join(str(b.get("t")) for b in self.blocks)
        self.assertNotIn("char_2014_nian", joined)
        self.assertNotIn("0.5", joined)

    def test_header(self):
        self.assertIsNone(self.header)


class TestTitle(unittest.TestCase):
    def test_suffix(self):
        self.assertEqual(page_title("WR-1 初醒/END", {}), "WR-1 初醒（行动后）")
        self.assertEqual(page_title("WR-1 初醒/BEG", {}), "WR-1 初醒（行动前）")
        self.assertEqual(page_title("AF-ST1 洪炉示岁·迎春/NBT", {}), "AF-ST1 洪炉示岁·迎春")

    def test_titles_override(self):
        sec = {"titles": {"RO5-END-2": "长卷留痕"}}
        self.assertEqual(page_title("RO5-END-2/NBT", sec), "长卷留痕")


class TestArchive(unittest.TestCase):
    def test_archive_blocks(self):
        text = """==干员档案==
{{干员档案
|档案1=基础档案
|档案1文本=【代号】惊蛰\n【种族】黎博利
|档案2=档案资料一
|档案2文本=第一段\n第二段
}}
==语音记录==
"""
        blocks = parse_archive(text)
        self.assertEqual([b["k"] for b in blocks], ["h4", "n", "n", "h4", "n", "n"])
        self.assertEqual(blocks[0]["t"], "基础档案")
        self.assertEqual(blocks[1]["t"], "【代号】惊蛰")
        self.assertEqual(blocks[4]["t"], "第一段")


class TestSelection(unittest.TestCase):
    def test_parse(self):
        self.assertEqual(parse_selection("1-3,5", 6), [0, 1, 2, 4])
        self.assertEqual(parse_selection("", 3), [0, 1, 2])
        self.assertEqual(parse_selection("9", 3), [])


if __name__ == "__main__":
    unittest.main()
