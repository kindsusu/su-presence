# -*- coding: utf-8 -*-
"""이름을 바꿔도 옛 스냅샷을 읽을 수 있어야 한다 — 네트워크를 쓰지 않는다.

`su-multi-geo/...` 스키마는 이미 만들어진 audit.json·log.jsonl·history.json 에 박혀 있다.
읽기 호환을 잃으면 **기준선이 조용히 사라진다** — 도구가 파일을 "우리 것이 아니다" 로
판정하고 버리기 때문이다. 에러도 안 난다. 그게 이 파일이 존재하는 이유다.

실행: python -m unittest discover tests
"""
import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import crawl  # noqa: E402
import drift  # noqa: E402
import generate  # noqa: E402
import measure  # noqa: E402
import verify  # noqa: E402

OLD = "su-multi-geo"
NEW = "su-presence"


class TestWritesUseTheNewName(unittest.TestCase):
    """새로 만드는 산출물은 새 이름이어야 한다."""

    def test_every_written_schema_is_renamed(self):
        for name, value in (("crawl.SCHEMA", crawl.SCHEMA),
                            ("measure.SCHEMA_QUERIES", measure.SCHEMA_QUERIES),
                            ("measure.SCHEMA_ROW", measure.SCHEMA_ROW),
                            ("measure.SCHEMA_SUMMARY", measure.SCHEMA_SUMMARY),
                            ("verify.SCHEMA", verify.SCHEMA),
                            ("drift.SCHEMA_HISTORY", drift.SCHEMA_HISTORY),
                            ("drift.SCHEMA_DRIFT", drift.SCHEMA_DRIFT)):
            self.assertTrue(value.startswith(NEW + "/"), "%s = %r" % (name, value))

    def test_crawler_ua_and_manifest_follow_the_name(self):
        self.assertTrue(crawl.UA.startswith(NEW + "-audit/"), crawl.UA)
        self.assertEqual(generate.OWNERSHIP_MANIFEST, "." + NEW + "-generated.json")


class TestReadsStillAcceptTheOldName(unittest.TestCase):
    """옛 이름을 못 읽으면 기존 기준선이 사라진다."""

    def test_audit_prefix_accepts_both(self):
        for prefixes in (measure.AUDIT_SCHEMA_PREFIX, verify.AUDIT_SCHEMA_PREFIX,
                         drift.AUDIT_SCHEMA_PREFIX, generate.SCHEMA_PREFIX):
            self.assertTrue((OLD + "/audit/1").startswith(prefixes), prefixes)
            self.assertTrue((NEW + "/audit/1").startswith(prefixes), prefixes)

    def test_queries_and_rows_accept_both(self):
        accepted_q = (measure.SCHEMA_QUERIES,) + measure.LEGACY_QUERIES_SCHEMAS
        accepted_r = (measure.SCHEMA_ROW,) + measure.LEGACY_ROW_SCHEMAS
        for old in (OLD + "/queries/1", OLD + "/queries/2"):
            self.assertIn(old, accepted_q)
        for old in (OLD + "/measure-row/1", OLD + "/measure-row/2"):
            self.assertIn(old, accepted_r)

    def test_history_index_accepts_the_old_name(self):
        """여기가 깨지면 history.json 이 버려지고 기준선이 초기화된다."""
        accepted = (drift.SCHEMA_HISTORY,) + drift.LEGACY_HISTORY_SCHEMAS
        self.assertIn(OLD + "/history/1", accepted)

    def test_snapshot_validation_accepts_old_payloads(self):
        for kind, schema in (("audit", OLD + "/audit/1"),
                             ("measure", OLD + "/measure/2"),
                             ("measure", OLD + "/measure/1"),
                             ("verify", OLD + "/verify/1")):
            payload = {"schema": schema, "target": {"host": "example.com"}}
            drift.validate_snapshot_payload(kind, payload, "example.com", "t.json")

    def test_snapshot_validation_accepts_new_payloads(self):
        for kind, schema in (("audit", NEW + "/audit/1"),
                             ("measure", NEW + "/measure/2"),
                             ("verify", NEW + "/verify/1")):
            payload = {"schema": schema, "target": {"host": "example.com"}}
            drift.validate_snapshot_payload(kind, payload, "example.com", "t.json")

    def test_snapshot_validation_still_rejects_junk(self):
        with self.assertRaises(SystemExit):
            drift.validate_snapshot_payload(
                "audit", {"schema": "something/else/1", "target": {"host": "example.com"}},
                "example.com", "t.json")

    def test_ownership_manifest_accepts_both_schemas(self):
        for schema in (OLD + "/generated-files/1", NEW + "/generated-files/1"):
            self.assertIn(schema, generate.OWNERSHIP_SCHEMAS)

    def test_jsonld_manifest_accepts_both_schemas(self):
        for schema in (OLD + "/jsonld-manifest/1", NEW + "/jsonld-manifest/1"):
            self.assertIn(schema, verify.JSONLD_MANIFEST_SCHEMAS)

    def test_v2_summary_family_detected_under_either_name(self):
        for schema in (OLD + "/measure/2", NEW + "/measure/2"):
            self.assertIn(schema, drift.SUMMARY_V2_SCHEMAS)


class TestCurrentNameRoundTrip(unittest.TestCase):
    """현행 이름으로 쓴 파일을 현행 도구가 다시 읽어야 한다 — 옛 이름 호환의 대칭."""

    def test_queries_and_log_written_now_are_read_back(self):
        with tempfile.TemporaryDirectory() as mdir:
            measure.write_json(os.path.join(mdir, "queries.json"),
                               {"schema": measure.SCHEMA_QUERIES,
                                "queries": [{"id": "Q01", "text": "요금은 얼마인가요",
                                             "type": "brand", "note": ""}]})
            queries = measure.load_queries(mdir)
            self.assertEqual([q["id"] for q in queries], ["Q01"])

            log = os.path.join(mdir, "log.jsonl")
            row = measure.make_row("2026-09-05", "Q01", "chatgpt", 1, "manual", True,
                                   True, [], True, [],
                                   query_fingerprint_value=queries[0]["fingerprint"])
            self.assertEqual(row["schema"], measure.SCHEMA_ROW)
            measure.append_rows(log, [row])
            self.assertEqual(len(measure.load_log(log)), 1)

    def test_snapshot_written_now_is_read_back(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = os.path.join(tmp, "out", "example.com")
            audit_path = os.path.join(out, "audit.json")
            drift.write_json(audit_path, {"schema": crawl.SCHEMA,
                                          "target": {"host": "example.com"}, "stats": {}})
            drift.main(["snapshot", audit_path, "--date", "2026-09-05"])
            index = drift.load_index(out, "example.com")
            self.assertEqual(index["schema"], drift.SCHEMA_HISTORY)
            self.assertEqual(drift.snapshot_json(out, index["snapshots"][0])["schema"],
                             crawl.SCHEMA)


if __name__ == "__main__":
    unittest.main()
