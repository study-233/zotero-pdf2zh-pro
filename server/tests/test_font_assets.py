from __future__ import annotations

import asyncio
import hashlib
import multiprocessing
import sys
import tempfile
import unittest
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path
from unittest.mock import patch

import httpx
from tenacity import stop_after_attempt
from tenacity import wait_none

SERVER_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVER_DIR))

from babeldoc.assets import assets


def metadata_for(content: bytes) -> dict[str, int | str]:
    return {
        "sha3_256": hashlib.sha3_256(content).hexdigest(),
        "size": len(content),
    }


def prepare_in_worker(directory):
    """Exercise both task preparation and the subsequent renderer font lookup."""
    content = {"first.ttf": b"first-font", "second.ttf": b"second-font"}
    metadata = {name: metadata_for(data) for name, data in content.items()}
    downloads = []
    events = []

    def serve(request):
        name = request.url.path.lstrip("/")
        downloads.append(name)
        return httpx.Response(200, content=content[name])

    async def prepare():
        async with httpx.AsyncClient(transport=httpx.MockTransport(serve)) as client:
            await assets.download_all_fonts_async(client, lambda *event: events.append(event))
            for name in content:
                await assets.get_font_and_metadata_async(name, client)

    with (
        patch.object(assets, "EMBEDDING_FONT_METADATA", metadata),
        patch.object(assets, "get_cache_file_path", side_effect=lambda name, _: directory / name),
        patch.object(assets, "get_fastest_upstream_for_font", return_value=("test", metadata)),
        patch.dict(assets.FONT_URL_BY_UPSTREAM, test=lambda name: f"https://assets.test/{name}"),
        patch.object(assets, "verify_file", wraps=assets.verify_file) as verify,
    ):
        asyncio.run(prepare())
    return [call.args[0].name for call in verify.call_args_list], downloads, events


class FontCacheWorkerTests(unittest.TestCase):
    def test_independent_workers_reuse_cache_and_repair_one_damaged_font(self):
        context = multiprocessing.get_context("spawn")
        with tempfile.TemporaryDirectory() as temp_dir:
            directory = Path(temp_dir)
            first = directory / "first.ttf"
            first.write_bytes(b"first-font")
            (directory / "second.ttf").write_bytes(b"second-font")

            def run_worker():
                with ProcessPoolExecutor(max_workers=1, mp_context=context) as pool:
                    return pool.submit(prepare_in_worker, directory).result(timeout=30)

            checked, downloaded, _ = run_worker()
            self.assertEqual(checked, ["first.ttf", "second.ttf"])
            self.assertEqual(downloaded, [])
            self.assertEqual(run_worker(), ([], [], []))
            first.write_bytes(b"wrong-font")
            checked, downloaded, events = run_worker()
            self.assertEqual(checked, ["first.ttf"])
            self.assertEqual(downloaded, ["first.ttf"])
            self.assertIn((assets.FONT_CACHE_CHECK_STAGE, 1, 1), events)
            self.assertIn((assets.FONT_DOWNLOAD_STAGE, 10, 10), events)
            self.assertEqual(first.read_bytes(), b"first-font")
            self.assertEqual(run_worker(), ([], [], []))


class FontAssetTests(unittest.IsolatedAsyncioTestCase):
    def test_rechecks_damaged_record_and_changed_expected_hash(self):
        with tempfile.TemporaryDirectory() as temp_dir:
            path = Path(temp_dir) / "font.ttf"
            path.write_bytes(b"font")
            digest = metadata_for(b"font")["sha3_256"]
            self.assertTrue(assets._verify_font(path, digest))
            assets._font_record(path).write_bytes(b"incomplete")
            with patch.object(assets, "verify_file", wraps=assets.verify_file) as verify:
                self.assertTrue(assets._verify_font(path, digest))
                self.assertFalse(assets._verify_font(path, metadata_for(b"updated")["sha3_256"]))
                self.assertEqual(verify.call_count, 2)

    async def test_download_file_streams_to_atomic_temporary_file(self) -> None:
        content = b"font-data" * 200_000
        progress: list[int] = []

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=content, request=request)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "font.ttf"
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                await assets.download_file(
                    client,
                    "https://assets.test/font.ttf",
                    output_path,
                    hashlib.sha3_256(content).hexdigest(),
                    progress.append,
                )

            self.assertEqual(output_path.read_bytes(), content)
            self.assertEqual(list(output_path.parent.glob("*.part")), [])
            self.assertEqual(progress[0], 0)
            self.assertEqual(progress[-1], len(content))
            self.assertEqual(progress, sorted(progress))

    async def test_corrupt_download_keeps_existing_cache_and_removes_part(self) -> None:
        original = b"existing-corrupt-cache"
        downloaded = b"bad-download"

        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, content=downloaded, request=request)

        with tempfile.TemporaryDirectory() as temp_dir:
            output_path = Path(temp_dir) / "font.ttf"
            output_path.write_bytes(original)
            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                single_attempt = assets.download_file.retry_with(
                    stop=stop_after_attempt(1),
                    wait=wait_none(),
                    reraise=True,
                )
                with self.assertRaises(ValueError):
                    await single_attempt(
                        client,
                        "https://assets.test/font.ttf",
                        output_path,
                        hashlib.sha3_256(b"expected").hexdigest(),
                        font=True,
                    )

            self.assertEqual(output_path.read_bytes(), original)
            self.assertFalse(assets._font_record(output_path).exists())
            self.assertEqual(list(output_path.parent.glob("*.part")), [])

    async def test_download_all_fonts_reports_missing_bytes_and_runs_once(self) -> None:
        cached_content = b"cached-font"
        missing_content = b"missing-font" * 100_000
        font_metadata = {
            "cached.ttf": metadata_for(cached_content),
            "missing.ttf": metadata_for(missing_content),
        }
        requested_paths: list[str] = []
        progress: list[tuple[str, int, int]] = []

        def handler(request: httpx.Request) -> httpx.Response:
            requested_paths.append(request.url.path)
            return httpx.Response(200, content=missing_content, request=request)

        with tempfile.TemporaryDirectory() as temp_dir:
            cache_dir = Path(temp_dir)
            (cache_dir / "cached.ttf").write_bytes(cached_content)

            async def fastest_upstream(_client):
                return "test", font_metadata

            async with httpx.AsyncClient(
                transport=httpx.MockTransport(handler)
            ) as client:
                with (
                    patch.object(assets, "EMBEDDING_FONT_METADATA", font_metadata),
                    patch.object(
                        assets,
                        "get_cache_file_path",
                        side_effect=lambda filename, _folder: cache_dir / filename,
                    ),
                    patch.object(
                        assets,
                        "get_fastest_upstream_for_font",
                        side_effect=fastest_upstream,
                    ),
                    patch.object(
                        assets,
                        "get_font_url_by_name_and_upstream",
                        side_effect=lambda name, _upstream: f"https://assets.test/{name}",
                    ),
                ):
                    await assets.download_all_fonts_async(
                        client,
                        lambda stage, current, total: progress.append(
                            (stage, current, total)
                        ),
                    )
                    second_progress: list[tuple[str, int, int]] = []
                    await assets.download_all_fonts_async(
                        client,
                        lambda stage, current, total: second_progress.append(
                            (stage, current, total)
                        ),
                    )

            self.assertEqual(requested_paths, ["/missing.ttf"])
            self.assertEqual((cache_dir / "missing.ttf").read_bytes(), missing_content)
            self.assertEqual(second_progress, [])

        check_events = [
            event for event in progress if event[0] == assets.FONT_CACHE_CHECK_STAGE
        ]
        download_events = [
            event for event in progress if event[0] == assets.FONT_DOWNLOAD_STAGE
        ]
        self.assertEqual(check_events[0], (assets.FONT_CACHE_CHECK_STAGE, 0, 2))
        self.assertEqual(check_events[-1], (assets.FONT_CACHE_CHECK_STAGE, 2, 2))
        self.assertEqual(
            download_events[0],
            (assets.FONT_DOWNLOAD_STAGE, 0, len(missing_content)),
        )
        self.assertEqual(
            download_events[-1],
            (
                assets.FONT_DOWNLOAD_STAGE,
                len(missing_content),
                len(missing_content),
            ),
        )
        downloaded_values = [current for _, current, _ in download_events]
        self.assertEqual(downloaded_values, sorted(downloaded_values))


if __name__ == "__main__":
    unittest.main()
