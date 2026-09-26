"""Repository contract validation regression tests."""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import validate  # noqa: E402


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.write("SKILL.md", '---\nname: su-presence\ndescription: "Audit: sites"\n---\n# Start\n')
        self.write("en/SKILL.md", "---\nname: 'su-presence'\ndescription: 'Audit: sites'\n---\n# Start\n")
        self.write(".claude-plugin/plugin.json", json.dumps({"name": "su-presence", "version": "2.1.0"}))
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "su-presence", "plugins": [{"name": "su-presence", "version": "2.1.0"}]}))
        self.write("CHANGELOG.md", "# Changes\n\n## [Unreleased]\n\n## [2.1.0] — today\n")
        self.write("lanes/seo.md", "# Search\n")
        self.write("en/lanes/seo.md", "# Search\n")
        self.write("ops/measure.md", "# Measure\n")
        self.write("en/ops/measure.md", "# Measure\n")

    def write(self, relative, content):
        path = self.root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def errors(self):
        return "\n".join(validate.validate(self.root))

    def test_clean_fixture_and_cli(self):
        self.write("README.md", "# A heading\n# A heading\n[second](#a-heading-1)\n[guide](lanes/seo.md)\n[web](https://example.com)\n````md\n[example](missing.md)\n````\n")
        self.assertEqual("", self.errors())
        result = subprocess.run([sys.executable, str(Path(validate.__file__)), "--root", str(self.root)], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)

    def test_unquoted_colon_space_is_rejected_without_yaml_dependency(self):
        self.write("SKILL.md", "---\nname: su-presence\ndescription: Audit: sites\n---\n")
        self.assertIn("quote a value containing colon-space", self.errors())

    def test_name_and_description_contract(self):
        self.write("SKILL.md", '---\nname: Bad_Name\ndescription: "two\\nlines"\n---\n')
        result = self.errors()
        self.assertIn("name must be kebab-case", result)
        self.assertIn("description must be one line", result)
        self.write("SKILL.md", "---\nname: " + "a" * 65 + "\ndescription: " + "x" * 1025 + "\n---\n")
        result = self.errors()
        self.assertIn("at most 64 characters", result)
        self.assertIn("at most 1024 characters", result)

    def test_malformed_frontmatter_and_duplicate_key(self):
        self.write("SKILL.md", "---\nname: su-presence\nname: extra\ndescription:\n  nested: invalid\n---\n")
        result = self.errors()
        self.assertIn("duplicate frontmatter", result)
        self.assertIn("empty frontmatter", result)
        self.assertIn("expected flat key", result)

    def test_metadata_drift_and_changelog_version(self):
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "wrong", "plugins": [{"name": "wrong", "version": "2.2.0"}]}))
        result = self.errors()
        self.assertIn("name differs", result)
        self.assertIn("versions must match", result)
        self.write(".claude-plugin/marketplace.json", json.dumps({"name": "su-presence", "plugins": [{"name": "su-presence", "version": "2.1.0"}]}))
        self.write("CHANGELOG.md", "## [Unreleased]\n## [2.0.0]\n")
        self.assertIn("latest released version", self.errors())

    def test_broken_links_and_anchor_but_ignores_fenced_example(self):
        self.write("README.md", "[missing](absent.md)\n[anchor](lanes/seo.md#absent)\n```md\n[example](also-absent.md)\n```\n")
        result = self.errors()
        self.assertIn("missing link target: absent.md", result)
        self.assertIn("missing anchor", result)
        self.assertNotIn("also-absent", result)

    def test_inline_code_heading_anchor_and_bad_url(self):
        self.write("README.md", "# Use `collect`\n[good](#use-collect)\n[bad](http://[invalid)\n")
        result = self.errors()
        self.assertNotIn("missing anchor", result)
        self.assertIn("malformed link target", result)

    def test_missing_mirror(self):
        (self.root / "en/ops/measure.md").unlink()
        self.assertIn("missing English mirror", self.errors())

    def test_cli_fails_on_invalid_repository(self):
        self.write("SKILL.md", "---\nname: su-presence\ndescription: Audit: sites\n---\n")
        result = subprocess.run([sys.executable, str(Path(validate.__file__)), "--root", str(self.root)], capture_output=True, text=True)
        self.assertNotEqual(0, result.returncode)


if __name__ == "__main__":
    unittest.main()
