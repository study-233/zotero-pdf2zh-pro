import unittest

from build_glossary_data import Headings, prepare_snapshot, sha256


class SnapshotTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
