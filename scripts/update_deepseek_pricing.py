#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from datetime import datetime
from datetime import timedelta
from datetime import timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.request import Request
from urllib.request import urlopen


SOURCE_URL = "https://api-docs.deepseek.com/zh-cn/quick_start/pricing/"
MODEL_NAMES = (
    "deepseek-v4-flash",
    "deepseek-v4-pro",
    "deepseek-v4-flash-vision-exp",
)
RATE_FIELDS = {
    "缓存命中": "cacheHitInput",
    "缓存未命中": "cacheMissInput",
    "百万tokens输出": "output",
}
NUMBER_PATTERN = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*元")
WINDOW_PATTERN = re.compile(
    r"(\d{1,2}:\d{2})\s*(?:-|–|—|至)\s*(\d{1,2}:\d{2})"
)
WEEKDAY_PATTERN = re.compile(r"周([一二三四五六日])\s*至\s*周([一二三四五六日])")
WEEKDAYS = {"一": 1, "二": 2, "三": 3, "四": 4, "五": 5, "六": 6, "日": 7}


class PricingPageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.rows: list[list[str]] = []
        self.text: list[str] = []
        self._row: list[str] | None = None
        self._cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "tr":
            self._row = []
        elif tag in {"td", "th"} and self._row is not None:
            self._cell = []

    def handle_endtag(self, tag: str) -> None:
        if tag in {"td", "th"} and self._cell is not None and self._row is not None:
            self._row.append(" ".join(self._cell))
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self.rows.append(self._row)
            self._row = None

    def handle_data(self, data: str) -> None:
        normalized = " ".join(data.split())
        if not normalized:
            return
        self.text.append(normalized)
        if self._cell is not None:
            self._cell.append(normalized)


def fetch_pricing_page() -> str:
    request = Request(SOURCE_URL, headers={"User-Agent": "zotero-pdf2zh-pro-pricing-check"})
    with urlopen(request, timeout=15) as response:
        if response.status != 200:
            raise RuntimeError(f"DeepSeek pricing page returned HTTP {response.status}")
        payload = response.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("DeepSeek pricing page is unexpectedly large")
    return payload.decode("utf-8")


def normalize_time(value: str) -> str:
    hour, minute = (int(part) for part in value.split(":"))
    if not 0 <= hour <= 24 or not 0 <= minute < 60 or (hour == 24 and minute):
        raise ValueError(f"invalid pricing time: {value}")
    return f"{hour:02d}:{minute:02d}"


def parse_pricing_page(html: str) -> dict[str, Any]:
    parser = PricingPageParser()
    parser.feed(html)
    page_text = " ".join(parser.text)
    if any(model not in page_text for model in MODEL_NAMES):
        raise ValueError("expected DeepSeek models were not found")

    extracted: dict[str, dict[str, list[float]]] = {
        "offPeak": {},
        "peak": {},
    }
    current_field: str | None = None
    for row in parser.rows:
        row_text = " ".join(row).replace(" ", "")
        for label, field in RATE_FIELDS.items():
            if label in row_text:
                current_field = field
                break
        tier = None
        if "空闲时段" in row_text:
            tier = "offPeak"
        elif "高峰时段" in row_text:
            tier = "peak"
        if tier is None or current_field is None:
            continue
        values = [float(value) for value in NUMBER_PATTERN.findall(" ".join(row))]
        if values:
            if len(values) != len(MODEL_NAMES):
                raise ValueError(f"unexpected price column count for {current_field}/{tier}")
            extracted[tier][current_field] = values

    required_fields = set(RATE_FIELDS.values())
    if any(set(extracted[tier]) != required_fields for tier in extracted):
        raise ValueError("pricing table is incomplete")
    all_values = [
        value
        for tier in extracted.values()
        for values in tier.values()
        for value in values
    ]
    if any(value < 0 or value > 10_000 for value in all_values):
        raise ValueError("pricing value failed the sanity check")

    schedule_match = WEEKDAY_PATTERN.search(page_text)
    if schedule_match is None:
        raise ValueError("peak weekday range was not found")
    first_day, last_day = (WEEKDAYS[value] for value in schedule_match.groups())
    if first_day > last_day:
        raise ValueError("wrapped peak weekday ranges are not supported")
    schedule_start = page_text.find(schedule_match.group(0))
    schedule_text = page_text[schedule_start : schedule_start + 200]
    windows = [
        [normalize_time(start), normalize_time(end)]
        for start, end in WINDOW_PATTERN.findall(schedule_text)
    ]
    if not windows:
        raise ValueError("peak time windows were not found")

    models: dict[str, Any] = {}
    aliases = {
        "deepseek-v4-flash": ["deepseek-chat", "deepseek-reasoner"],
        "deepseek-v4-pro": [],
        "deepseek-v4-flash-vision-exp": [],
    }
    for index, model in enumerate(MODEL_NAMES):
        models[model] = {
            "aliases": aliases[model],
            "offPeak": {
                field: extracted["offPeak"][field][index]
                for field in ("cacheHitInput", "cacheMissInput", "output")
            },
            "peak": {
                field: extracted["peak"][field][index]
                for field in ("cacheHitInput", "cacheMissInput", "output")
            },
        }
    return {
        "peakWeekdays": list(range(first_day, last_day + 1)),
        "peakWindows": windows,
        "models": models,
    }


def update_manifest(manifest: dict[str, Any], extracted: dict[str, Any]) -> bool:
    current = {
        "peakWeekdays": manifest["schedule"]["peakWeekdays"],
        "peakWindows": manifest["schedule"]["peakWindows"],
        "models": manifest["models"],
    }
    if current == extracted:
        return False
    now = datetime.now(timezone(timedelta(hours=8)))
    manifest["version"] = f"{now.date().isoformat()}-cny-tiered"
    manifest["updatedAt"] = now.replace(microsecond=0).isoformat()
    manifest["sourceUrl"] = SOURCE_URL
    manifest["schedule"]["peakWeekdays"] = extracted["peakWeekdays"]
    manifest["schedule"]["peakWindows"] = extracted["peakWindows"]
    manifest["models"] = extracted["models"]
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Update bundled DeepSeek pricing")
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("server/pdf2zh_next/deepseek_pricing.json"),
    )
    parser.add_argument("--html", type=Path, help="Read a saved pricing page")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    html = args.html.read_text(encoding="utf-8") if args.html else fetch_pricing_page()
    changed = update_manifest(manifest, parse_pricing_page(html))
    if changed:
        args.manifest.write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print("DeepSeek pricing changed; manifest updated")
    else:
        print("DeepSeek pricing unchanged")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
