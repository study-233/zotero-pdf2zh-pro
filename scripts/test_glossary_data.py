import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from build_glossary_data import Headings, prepare_snapshot, sha256, source_entries, strip_script_elements


class SnapshotTests(unittest.TestCase):
    def test_parser_preserves_bytes_around_scripts_and_quoted_attributes(self):
        prefix = '<h2 id="x">中文 &amp;术语</h2>\r\n'.encode()
        suffix = b'<p>CC BY 4.0</p>'
        raw = prefix + b'<ScRiPt data-x=">">one\n two</sCrIpT \t> ' + b'<script>three</script>' + suffix
        self.assertEqual(strip_script_elements(raw), prefix + b' ' + suffix)
        self.assertEqual(strip_script_elements(b'\xff<p>license</p>'), b'\xff<p>license</p>')

    def test_ambiguous_or_incomplete_scripts_fail_closed(self):
        for raw in [b'<script>key</script\t\n bar>', b'<script>key',
                    b'<script/>', b'<script', b'</script>']:
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                strip_script_elements(raw)

    def test_removes_client_scripts_without_changing_terms_or_licensing(self):
        token = b"AIza" + b"0" * 35
        content = b'<h2 id="airway">airway</h2><p>CC BY 4.0</p>'
        raw = content + b'<SCRIPT type="text/javascript">' + token + b'</SCRIPT>'
        item = {"file": "source.html", "upstreamSha256": sha256(raw),
                "sha256": sha256(content), "transform": "strip-script-elements-v1"}
        result = prepare_snapshot(raw, item)
        self.assertEqual(result, content)
        parser = Headings("h2")
        parser.feed(result.decode())
        self.assertEqual(parser.entries, {"airway": "airway"})

    def test_rejects_changed_upstream_or_output(self):
        raw = b"source"
        for item in [
            {"file": "source.html", "sha256": sha256(b"changed")},
            {"file": "source.html", "upstreamSha256": sha256(raw),
             "sha256": sha256(b"changed"), "transform": "strip-script-elements-v1"},
        ]:
            with self.subTest(item=item), self.assertRaises(ValueError):
                prepare_snapshot(raw, item)

    def test_rejects_remaining_client_key_or_unknown_transform(self):
        for raw, transform in [(b"AIza" + b"0" * 35, "strip-script-elements-v1"),
                               (b"source", "unknown")]:
            with self.subTest(transform=transform), self.assertRaises(ValueError):
                prepare_snapshot(raw, {"file": "source.html", "sha256": sha256(raw),
                                       "transform": transform})

    def test_unchanged_non_html_sources(self):
        raw = b"source,target\nairway,example\n"
        self.assertEqual(prepare_snapshot(raw, {"file": "source.csv", "sha256": sha256(raw)}), raw)

    def test_existing_archives_also_reject_client_keys(self):
        raw = b'<script>' + b'AIza' + b'0' * 35 + b'</script>'
        with TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'source.html').write_bytes(raw)
            source = {'format': 'google-glossary', 'inputs': [
                {'file': 'source.html', 'sha256': sha256(raw)}]}
            with self.assertRaisesRegex(ValueError, 'Client API configuration'):
                source_entries(source, root)


if __name__ == "__main__":
    unittest.main()
