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

    def test_hidden_void_elements_do_not_hide_following_body(self):
        for tag in ('input', 'img', 'br', 'meta', 'source'):
            with self.subTest(tag=tag):
                page = evidence.extract_page('<body><' + tag + ' hidden><h1>Visible</h1><p>Body</p></body>', 'https://example.com/')
                self.assertEqual(page['h1'], 'Visible')
                self.assertIn('Body', page['text_excerpt'])

    def test_void_element_does_not_end_hidden_parent(self):
        page = evidence.extract_page('<body><div hidden><input hidden><p>Secret</p></div><p>Visible</p></body>', 'https://example.com/')
        self.assertNotIn('Secret', page['text_excerpt'])
        self.assertIn('Visible', page['text_excerpt'])
