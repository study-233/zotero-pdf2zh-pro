from __future__ import annotations

import asyncio
import sqlite3
import threading
import unittest
from contextlib import ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

import babeldoc.format.pdf.high_level as high_level
import pdf2zh_next_service as service
from babeldoc.format.pdf.translation_config import TranslationConfig
from babeldoc.progress_monitor import ProgressMonitor


class ConfigCancellationTests(unittest.TestCase):
    def test_cancel_before_progress_monitor_exists(self):
        with TemporaryDirectory() as directory:
            config = TranslationConfig(
                translator=SimpleNamespace(),
                input_file=Path(directory) / "paper.pdf",
                lang_in="en", lang_out="zh-CN",
                doc_layout_model=object(),
                working_dir=directory, output_dir=directory,
            )
            self.assertIsNone(config.progress_monitor)
            config.raise_if_cancelled()
            config.cancel_translation()
            self.assertTrue(config.cancel_event.is_set())
            with self.assertRaises(asyncio.CancelledError):
                config.raise_if_cancelled()

    def test_normal_monitor_finish_does_not_cancel(self):
        cancelled = threading.Event()
        callback = Mock()
        monitor = ProgressMonitor([("translation", 1)], cancel_event=cancelled,
                                  finish_callback=callback)
        monitor.on_finish()
        self.assertFalse(cancelled.is_set())
        callback.assert_not_called()
        monitor.cancel()
        monitor.on_finish()
        callback.assert_called_once_with(type="error", error=asyncio.CancelledError)


class ServiceCancellationTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.cancel_event = threading.Event()
        self.lock = threading.Lock()
        self.config = TranslationConfig.__new__(TranslationConfig)
        self.config.cancel_event = threading.Event()
        self.config.progress_monitor = None
        self.config.pool_max_workers = 1
        self.config.report_interval = 0.1
        self.config.translator = SimpleNamespace()
        self.config.cleanup_temp_files = Mock()
        self.payload = {
            "input_path": str(Path(directory.name) / "cancellation-test.pdf"),
            "output_dir": str(Path(directory.name) / "output"),
            "output_modes": ["dual"],
            "service": "openai",
        }
        self.files = {"dual": SimpleNamespace(filename="translated.pdf")}
        self.patches = ExitStack()
        self.addCleanup(self.patches.close)
        self.patches.enter_context(patch.object(service, "_TEXT_CHECK_TRANSLATION_LOCK", self.lock))
        self.patches.enter_context(patch.object(service.Path, "read_bytes", return_value=b"pdf"))
        self.runtime = self.patches.enter_context(patch.object(service, "create_runtime_settings", return_value=object()))
        self.patches.enter_context(patch.object(service, "create_babeldoc_config", return_value=self.config))
        self.fonts = self.patches.enter_context(patch.object(service, "download_all_fonts_async", new_callable=AsyncMock))
        self.collect = self.patches.enter_context(patch.object(service, "collect_output_files", return_value=self.files))
        self.initial_skip = service._SKIP_TEXT_CHECKS_ENABLED
        self.addCleanup(service.set_text_checks_skipped, self.initial_skip)

    def start(self, **kwargs):
        return asyncio.create_task(service.translate_pdf_with_callbacks(
            self.payload, "cancel-test", cancel_event=self.cancel_event, **kwargs,
        ))

    async def test_already_cancelled_skips_preparation(self):
        self.cancel_event.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.start()
        self.runtime.assert_not_called()
        self.fonts.assert_not_called()
        self.assertFalse(self.lock.locked())

    async def test_lock_wait_can_be_cancelled_without_orphan_waiter(self):
        attempted = asyncio.Event()
        original_acquire = self.lock.acquire
        observed_lock = SimpleNamespace(
            acquire=lambda **kwargs: (attempted.set(), original_acquire(**kwargs))[1],
            release=self.lock.release,
        )
        self.lock.acquire()
        try:
            with patch.object(service, "_TEXT_CHECK_TRANSLATION_LOCK", observed_lock):
                task = self.start()
                await asyncio.wait_for(attempted.wait(), 1)
                self.cancel_event.set()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            self.assertTrue(self.lock.locked())
            self.assertTrue(self.cancel_event.is_set())
            self.runtime.assert_not_called()
        finally:
            self.lock.release()
        self.assertTrue(self.lock.acquire(blocking=False))
        self.lock.release()

    async def test_async_task_cancel_while_waiting_for_lock(self):
        entered = asyncio.Event()
        self.lock.acquire()
        try:
            original_sleep = asyncio.sleep

            async def wait_for_lock(delay):
                entered.set()
                await original_sleep(delay)

            with patch.object(service.asyncio, "sleep", side_effect=wait_for_lock):
                task = self.start()
                await asyncio.wait_for(entered.wait(), 1)
                task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await asyncio.wait_for(task, 1)
            self.assertTrue(self.lock.locked())
            self.runtime.assert_not_called()
        finally:
            self.lock.release()

    async def test_font_cancellation_drains_cleanup_before_releasing_lock(self):
        started, cleaning, release_cleanup = asyncio.Event(), asyncio.Event(), asyncio.Event()

        async def prepare_fonts(progress_callback):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaning.set()
                await release_cleanup.wait()

        self.fonts.side_effect = prepare_fonts
        with patch.object(service, "babeldoc_translate") as translate:
            task = self.start()
            try:
                await asyncio.wait_for(started.wait(), 1)
                self.cancel_event.set()
                await asyncio.wait_for(cleaning.wait(), 1)
                self.assertFalse(task.done())
                self.assertTrue(self.lock.locked())
                translate.assert_not_called()
            finally:
                release_cleanup.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertFalse(self.lock.locked())
        self.assertEqual(service._SKIP_TEXT_CHECKS_ENABLED, self.initial_skip)
        self.config.cleanup_temp_files.assert_called_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.config.recovery.connection.execute("SELECT 1")

    async def test_cancel_on_config_ready_prevents_font_preparation(self):
        with self.assertRaises(asyncio.CancelledError):
            await self.start(on_config_ready=lambda config: config.cancel_translation())
        self.fonts.assert_not_called()
        self.assertTrue(self.cancel_event.is_set())
        self.assertFalse(self.lock.locked())
        self.config.cleanup_temp_files.assert_called_once()

    async def test_preparation_failure_closes_recovery_and_releases_lock(self):
        self.fonts.side_effect = RuntimeError("synthetic preparation failure")
        with self.assertRaisesRegex(RuntimeError, "preparation failure"):
            await self.start()
        self.assertFalse(self.lock.locked())
        self.assertFalse(self.cancel_event.is_set())
        self.config.cleanup_temp_files.assert_called_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.config.recovery.connection.execute("SELECT 1")

    async def test_async_cancellation_waits_for_live_worker_exit(self):
        started, cancelled = asyncio.Event(), asyncio.Event()
        release_worker, exited = threading.Event(), threading.Event()
        loop = asyncio.get_running_loop()
        original_cancel = self.config.cancel_translation

        def cancel_translation():
            original_cancel()
            cancelled.set()

        def worker(monitor, config):
            config.progress_monitor = monitor
            loop.call_soon_threadsafe(started.set)
            try:
                if not release_worker.wait(3):
                    raise AssertionError("test did not release worker")
                config.raise_if_cancelled()
            finally:
                monitor.on_finish()
                exited.set()

        with patch.object(high_level, "get_translation_stage", return_value=[("translation", 1)]), \
             patch.object(high_level, "do_translate", side_effect=worker), \
             patch.object(service, "babeldoc_translate", high_level.async_translate), \
             patch.object(self.config, "cancel_translation", side_effect=cancel_translation):
            task = self.start()
            try:
                await asyncio.wait_for(started.wait(), 1)
                task.cancel()
                await asyncio.wait_for(cancelled.wait(), 1)
                self.assertFalse(task.done())
                self.assertFalse(exited.is_set())
                self.assertTrue(self.lock.locked())
            finally:
                release_worker.set()
            with self.assertRaises(asyncio.CancelledError):
                await asyncio.wait_for(task, 1)
        self.assertTrue(exited.is_set())
        self.assertFalse(self.lock.locked())
        self.collect.assert_not_called()

    async def test_success_joins_worker_before_output_commit_without_cancelling(self):
        finish_seen = asyncio.Event()
        release_worker = threading.Event()

        def worker(monitor, config):
            config.progress_monitor = monitor
            try:
                monitor.translate_done(SimpleNamespace())
                if not release_worker.wait(3):
                    raise AssertionError("test did not release worker")
            finally:
                monitor.on_finish()

        def progress(event):
            if event["type"] == "finish":
                finish_seen.set()

        with patch.object(high_level, "get_translation_stage", return_value=[("translation", 1)]), \
             patch.object(high_level, "do_translate", side_effect=worker), \
             patch.object(service, "babeldoc_translate", high_level.async_translate):
            task = self.start(progress_callback=progress)
            try:
                await asyncio.wait_for(finish_seen.wait(), 1)
                self.assertFalse(task.done())
                self.assertFalse(self.cancel_event.is_set())
                self.assertTrue(self.lock.locked())
                self.collect.assert_not_called()
            finally:
                release_worker.set()
            result = await asyncio.wait_for(task, 1)
        self.assertEqual(result.files, self.files)
        self.assertFalse(self.cancel_event.is_set())
        self.assertFalse(self.lock.locked())
        self.collect.assert_called_once()
        with self.assertRaises(sqlite3.ProgrammingError):
            self.config.recovery.connection.execute("SELECT 1")

    async def test_cancellation_at_finish_never_commits_output(self):
        async def translate(config):
            yield {"type": "finish", "translate_result": SimpleNamespace()}

        def progress(event):
            if event["type"] == "finish":
                self.cancel_event.set()

        with patch.object(service, "babeldoc_translate", side_effect=translate):
            with self.assertRaises(asyncio.CancelledError):
                await self.start(progress_callback=progress)
        self.collect.assert_not_called()
        self.assertFalse(self.lock.locked())


if __name__ == "__main__":
    unittest.main()
