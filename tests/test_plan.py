# -*- coding: utf-8 -*-
"""计划文件完整性测试：确认 sui 计划能被正确加载。"""
import json
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from wiki_transporter.config import Plan  # noqa: E402


class TestSuiPlan(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plan = Plan(ROOT, "sui")

    def test_volumes(self):
        self.assertEqual(len(self.plan.volumes), 10)

    def test_pages_unique(self):
        pages = self.plan.pages()
        self.assertEqual(len(pages), len(set(pages)))
        self.assertEqual(len(pages), 142)

    def test_section_kinds(self):
        kinds = {s["kind"] for v in self.plan.volumes for s in v["sections"]}
        self.assertEqual(kinds, {"story", "secret", "archive"})

    def test_fanli_present(self):
        self.assertTrue(self.plan.fanli)

    def test_builtin_plan_in_sync(self):
        """仓库里的 plans/sui.json 与包内副本必须一致（防止两处漂移）。"""
        builtin = os.path.join(ROOT, "wiki_transporter", "builtin_plans", "sui.json")
        with open(os.path.join(ROOT, "plans", "sui.json"), encoding="utf-8") as f:
            a = json.load(f)
        with open(builtin, encoding="utf-8") as f:
            b = json.load(f)
        self.assertEqual(a, b)


if __name__ == "__main__":
    unittest.main()
