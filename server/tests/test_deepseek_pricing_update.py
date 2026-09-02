from __future__ import annotations

import copy
import importlib.util
import json
import sys
import unittest
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "server"))

from observability import parse_deepseek_pricing_manifest


SCRIPT_PATH = REPO_ROOT / "scripts" / "update_deepseek_pricing.py"
SPEC = importlib.util.spec_from_file_location("update_deepseek_pricing", SCRIPT_PATH)
assert SPEC is not None and SPEC.loader is not None
update_script = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(update_script)


class DeepSeekPricingUpdateTests(unittest.TestCase):
    def setUp(self) -> None:
        self.fixture = (
            Path(__file__).parent / "fixtures" / "deepseek_pricing.html"
        ).read_text(encoding="utf-8")
        self.manifest = json.loads(
            (
                Path(__file__).resolve().parents[1]
                / "pdf2zh_next"
                / "deepseek_pricing.json"
            ).read_text(encoding="utf-8")
        )

    def test_official_page_fixture_matches_bundled_manifest(self) -> None:
        extracted = update_script.parse_pricing_page(self.fixture)
        manifest = copy.deepcopy(self.manifest)
        manifest["schedule"]["peakWeekdays"] = extracted["peakWeekdays"]
        manifest["schedule"]["peakWindows"] = extracted["peakWindows"]
        manifest["models"] = extracted["models"]
        self.assertFalse(update_script.update_manifest(manifest, extracted))

    def test_bundled_manifest_passes_runtime_validation(self) -> None:
        policies = parse_deepseek_pricing_manifest(
            self.manifest,
            source="bundled",
        )
        self.assertIn("deepseek-v4-flash-vision-exp", policies)

    def test_price_change_produces_manifest_candidate(self) -> None:
        changed_html = self.fixture.replace("0.05元", "0.06元", 1)
        extracted = update_script.parse_pricing_page(changed_html)
        manifest = copy.deepcopy(self.manifest)
        self.assertTrue(update_script.update_manifest(manifest, extracted))
        self.assertEqual(
            manifest["models"]["deepseek-v4-flash"]["offPeak"]["cacheHitInput"],
            0.06,
        )
        self.assertEqual(manifest["sourceUrl"], update_script.SOURCE_URL)

    def test_incomplete_page_is_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "models"):
            update_script.parse_pricing_page("<html>pricing unavailable</html>")


if __name__ == "__main__":
    unittest.main()
