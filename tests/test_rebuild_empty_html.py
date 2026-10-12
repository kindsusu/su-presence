import sys
from pathlib import Path
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import rebuild_evidence as evidence

class EmptyHtmlTests(unittest.TestCase):
    def test_javascript_shell_is_not_observed_content(self):
        responses = [(404, {}, b""), (200, {"content-type":"text/html"}, b"<html><head><title>App</title></head><body><script>render()</script></body></html>")]
        with patch.object(evidence, "_request_once", side_effect=responses):
            result = evidence.fetch_page("https://example.com/")
        self.assertEqual(result["outcome"], "error")
        self.assertIn("rendering", result["error"])
