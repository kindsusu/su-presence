# -*- coding: utf-8 -*-
"""문서와 도구가 어긋나는 것을 잡는다 — 네트워크를 쓰지 않는다.

문서만 고치고 도구를 안 고치는(또는 그 반대) 사고가 실제로 있었다.
`ops/crawlers.md` 에는 다음(Daum) 크롤러가 한 글자도 없는데 `crawl.py` 는 두 토큰을
검사하고 `generate.py` 는 두 토큰을 써넣고 있었다. 문서만 보고 robots.txt 를 쓰면
다음 크롤러가 빠진다.

실행: python -m unittest discover tests
"""
import io
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import generate  # noqa: E402

UA_LINE = re.compile(r"^User-agent:\s*(\S+)\s*$", re.M)


def robots_block_uas(path):
    """문서의 'robots.txt 완본' 코드블록에서 User-agent 목록을 뽑는다."""
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    blocks = re.findall(r"```\n(.*?)```", text, re.S)
    for block in blocks:
        if "User-agent:" in block and "Sitemap:" in block:
            return [m.group(1) for m in UA_LINE.finditer(block)]
    raise AssertionError("robots.txt 완본 블록을 찾지 못했다: %s" % path)


def tool_uas():
    return [ua for _label, uas in generate.UA_GROUPS for ua in uas]


class TestCrawlersDocMatchesGenerator(unittest.TestCase):
    """`ops/crawlers.md` 의 완본 == `generate.py` 가 실제로 써넣는 목록."""

    def setUp(self):
        self.doc = os.path.join(ROOT, "ops", "crawlers.md")

    def test_doc_and_generator_list_the_same_uas(self):
        doc, tool = robots_block_uas(self.doc), tool_uas()
        missing_in_doc = sorted(set(tool) - set(doc))
        missing_in_tool = sorted(set(doc) - set(tool))
        self.assertEqual(
            missing_in_doc, [],
            "generate.py 는 써넣는데 ops/crawlers.md 완본에 없다: %s" % missing_in_doc)
        self.assertEqual(
            missing_in_tool, [],
            "문서 완본에 있는데 generate.py 는 안 써넣는다: %s" % missing_in_tool)

    def test_no_duplicate_ua_in_doc(self):
        doc = robots_block_uas(self.doc)
        dupes = sorted({ua for ua in doc if doc.count(ua) > 1})
        self.assertEqual(dupes, [], "완본에 중복된 UA: %s" % dupes)

    def test_daum_needs_both_tokens_everywhere(self):
        """`Daumoa` 만 적으면 `DAUM` 요청이 `*` 그룹으로 떨어진다. 세 곳이 같아야 한다."""
        for name, uas in (("crawl.KEO_UAS", crawl.KEO_UAS),
                          ("generate.UA_GROUPS", tool_uas()),
                          ("ops/crawlers.md", robots_block_uas(self.doc))):
            for token in ("DAUM", "Daumoa"):
                self.assertIn(token, uas, "%s 에 %s 가 없다" % (name, token))


class TestProbedCrawlersAreDocumented(unittest.TestCase):
    """crawl.py 가 robots 정책을 검사하는 UA 는 문서 어딘가에 설명이 있어야 한다.

    완본에 넣을 필요는 없다 — Googlebot·Bingbot 은 기본 허용이 전제라 '차단돼 있지
    않은지'만 본다. 다만 **아무 데도 안 적힌 UA 를 검사하면** 사용자는 그 판정이
    어디서 왔는지 알 수 없다.
    """

    def test_every_probed_ua_appears_in_the_doc(self):
        with io.open(os.path.join(ROOT, "ops", "crawlers.md"), encoding="utf-8") as fh:
            text = fh.read()
        undocumented = [ua for ua in crawl.ALL_UAS if ua not in text]
        self.assertEqual(
            undocumented, [],
            "crawl.py 가 검사하는데 ops/crawlers.md 에 없다: %s" % undocumented)


class TestLaneDocsExistForEveryLane(unittest.TestCase):
    """레인을 늘리면 그 레인 문서도 있어야 한다. KEO 를 세울 때 영문 미러를 빠뜨렸었다."""

    LANE_DOC = {"SEO": "seo", "AEO": "aeo", "GEO": "geo",
                "LLMO": "llmo", "NEO": "naver", "KEO": "daum",
                "reputation": "reputation"}

    def test_korean_and_english_lane_docs_exist(self):
        for lane in crawl.LANES:
            stem = self.LANE_DOC.get(lane)
            self.assertIsNotNone(stem, "레인 %s 의 문서 이름이 등록돼 있지 않다" % lane)
            for sub in ("lanes", os.path.join("en", "lanes")):
                path = os.path.join(ROOT, sub, stem + ".md")
                self.assertTrue(os.path.isfile(path),
                                "레인 %s 의 문서가 없다: %s" % (lane, path))


if __name__ == "__main__":
    unittest.main()
