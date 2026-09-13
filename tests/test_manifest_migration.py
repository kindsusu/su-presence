# -*- coding: utf-8 -*-
"""이름 변경 전 생성물 manifest의 실제 파일 경로 이행 회귀 테스트."""

import contextlib
import io
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT))

import generate
import seo_geo
import verify
from tests import test_generate as fixtures
from tests import test_verify as verify_fixtures


def write_json(path: Path, value) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class TestOwnershipManifestMigration(unittest.TestCase):
    def test_legacy_filename_owns_stale_files_during_first_new_run(self):
        with tempfile.TemporaryDirectory() as raw:
            deploy = Path(raw)
            stale = deploy / "jsonld" / "stale.product.json"
            write_json(stale, {})
            write_json(deploy / generate.LEGACY_OWNERSHIP_MANIFESTS[0], {
                "schema": "su-multi-geo/generated-files/1",
                "files": ["jsonld/stale.product.json"],
            })

            generate.run("jsonld", fixtures.audit(), {}, str(deploy))

            self.assertFalse(stale.exists())
            current = json.loads((deploy / generate.OWNERSHIP_MANIFEST).read_text(encoding="utf-8"))
            self.assertEqual(current["schema"], "su-presence/generated-files/1")
            self.assertNotIn("jsonld/stale.product.json", current["files"])
            checked = verify.verify_deploy(fixtures.audit(), str(deploy),
                                           fetch=verify_fixtures.live(), delay=0)
            mapping = next(c for c in checked["checks"] if c["id"] == "jsonld.mapping")
            self.assertEqual(mapping["status"], "pass")

    def test_current_manifest_wins_over_legacy_manifest(self):
        with tempfile.TemporaryDirectory() as raw:
            deploy = Path(raw)
            legacy_owned = deploy / "jsonld" / "legacy-owned.json"
            write_json(legacy_owned, {})
            write_json(deploy / generate.OWNERSHIP_MANIFEST, {
                "schema": "su-presence/generated-files/1", "files": []})
            write_json(deploy / generate.LEGACY_OWNERSHIP_MANIFESTS[0], {
                "schema": "su-multi-geo/generated-files/1",
                "files": ["jsonld/legacy-owned.json"],
            })

            generate.run("jsonld", fixtures.audit(), {}, str(deploy))

            self.assertTrue(legacy_owned.exists())

    def test_status_records_legacy_manifest_when_current_is_absent(self):
        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            write_json(root / "audit.json", fixtures.audit())
            legacy = root / "deploy" / generate.LEGACY_OWNERSHIP_MANIFESTS[0]
            write_json(legacy, {"schema": "su-multi-geo/generated-files/1", "files": []})
            output = io.StringIO()

            with contextlib.redirect_stdout(output):
                code = seo_geo.main(["status", str(root / "audit.json")])

            result = json.loads(output.getvalue())
            self.assertEqual(code, 0)
            self.assertEqual(result["artifacts"]["deploy"]["state"], "recorded")
            self.assertEqual(Path(result["artifacts"]["deploy"]["path"]), legacy.resolve())


if __name__ == "__main__":
    unittest.main()
