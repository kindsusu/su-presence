# -*- coding: utf-8 -*-
"""비공개 주소 관련 finding이 영문 보고서에서 국문 그대로 새지 않는지."""
import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import report  # noqa: E402


class NonpublicEnglishMessages(unittest.TestCase):

    def en(self, code, data, urls=None):
        return report.finding_message(
            {"code": code, "message": "국문 원문", "data": data, "urls": urls or []}, "en")

    def test_alt_host_nonpublic(self):
        msg = self.en("ALT_HOST_NONPUBLIC", {"host": "www.example.com", "result": "nonpublic_address",
                                             "address_class": "private"})
        self.assertIn("www.example.com", msg)
        self.assertIn("private", msg)
        self.assertNotIn("국문", msg)

    def test_mirror_nonpublic_dns(self):
        msg = self.en("MIRROR_NONPUBLIC_DNS", {"count": 2, "hosts": []})
        self.assertTrue(msg.startswith("2 "), msg)
        self.assertNotIn("국문", msg)

    def test_robots_unreachable_nonpublic(self):
        msg = self.en("ROBOTS_UNREACHABLE", {"status": None, "error": "nonpublic_address"})
        self.assertIn("non-public address", msg)
        self.assertIn("unknown, not allowed", msg)


if __name__ == "__main__":
    unittest.main()
