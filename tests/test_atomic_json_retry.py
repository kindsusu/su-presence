import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
import seo_geo


class AtomicWriteTests(unittest.TestCase):
    def test_windows_transient_replace_retains_previous_until_success(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'state.json'
            target.write_text('{"old":true}', encoding='utf-8')
            real_replace = seo_geo.os.replace
            calls = []

            def replace(source, destination):
                calls.append(1)
                if len(calls) < 3:
                    self.assertEqual(json.loads(target.read_text()), {'old': True})
                    error = PermissionError('sharing violation')
                    error.winerror = 32
                    raise error
                return real_replace(source, destination)

            with patch.object(seo_geo.os, 'replace', side_effect=replace), patch.object(seo_geo.time, 'sleep'):
                seo_geo.atomic_json(target, {'new': True})
            self.assertEqual(len(calls), 3)
            self.assertEqual(json.loads(target.read_text()), {'new': True})
            self.assertEqual(list(Path(root).iterdir()), [target])

    def test_permanent_denial_preserves_old_file(self):
        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'state.json'
            target.write_text('{"old":true}', encoding='utf-8')
            with patch.object(seo_geo.os, 'replace', side_effect=PermissionError('denied')) as replace:
                with self.assertRaises(PermissionError):
                    seo_geo.atomic_json(target, {'new': True})
            self.assertEqual(replace.call_count, 1)
            self.assertEqual(json.loads(target.read_text()), {'old': True})
            self.assertEqual(list(Path(root).iterdir()), [target])
