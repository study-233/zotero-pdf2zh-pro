import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import numpy as np
import pymupdf
from sklearn.cluster import DBSCAN
from threadpoolctl import threadpool_limits

import babeldoc.format.pdf.high_level
from babeldoc.format.pdf.document_il.il_version_1 import Box
from babeldoc.format.pdf.document_il.utils import extract_char
from babeldoc.format.pdf.document_il.utils.fontmap import FontMapper


class PreprocessingPerformanceTests(unittest.TestCase):
    def test_tree_clustering_preserves_line_membership_and_order(self):
        rng = np.random.default_rng(17)
        def summarize(chars):
            return [(line.text, [(c[0].x, c[0].y, c[0].x2, c[0].y2, c[1], c[2]) for c in line.chars])
                    for line in extract_char.process_page_chars_to_lines(chars)]
        def brute(*args, **kwargs):
            kwargs['algorithm'] = 'brute'
            return DBSCAN(*args, **kwargs)
        # Explicitly constrain the reference's tiny brute-force jobs in this test.
        with threadpool_limits(limits=1):
            for vertical in (False, True):
                for count in (1, 8, 80, 260):
                    chars = []
                    for index in range(count):
                        x, y = rng.integers(0, 24, 2) * 6
                        chars.append((Box(float(x), float(y), float(x+5), float(y+9)), chr(65+index%26), vertical))
                    with patch.object(extract_char, 'DBSCAN', side_effect=brute):
                        expected = summarize(list(chars))
                    self.assertEqual(summarize(list(chars)), expected)

    def test_fonts_are_loaded_once_per_task_and_not_shared_between_tasks(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)/'font.ttf'
            font = pymupdf.Font('helv')
            path.write_bytes(font.buffer)
            family = {key: ['font.ttf'] for key in ('normal', 'script', 'fallback', 'base')}
            metadata = dict(ascent=800, descent=-200, encoding_length=2)
            first = SimpleNamespace(primary_font_family=None, lang_out='en')
            second = SimpleNamespace(primary_font_family=None, lang_out='en')
            with patch('babeldoc.assets.assets.get_font_family', return_value=family), patch(
                'babeldoc.assets.assets.get_font_and_metadata', return_value=(path, metadata)
            ) as assets:
                a, b, c = FontMapper(first), FontMapper(first), FontMapper(second)
            self.assertEqual(assets.call_count, 2)
            self.assertIs(a.base_font, b.base_font)
            self.assertIsNot(a.base_font, c.base_font)
            self.assertTrue(b.has_char('A'))
