import asyncio
import importlib
import os
import sqlite3
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import Mock, patch

from peewee import SqliteDatabase

from pdf2zh_pro.translator.cache_lifecycle import cache_connection_scope
from babeldoc.utils.priority_thread_pool_executor import PriorityThreadPoolExecutor


class TranslationCacheLifecycleTests(unittest.TestCase):
    def setUp(self):
        directory = self.enterContext(tempfile.TemporaryDirectory())
        self.enterContext(patch.dict(os.environ, {'PDF2ZH_TRANSLATION_CACHE_DIR': directory}))
        self.caches = []
        for name in ('pdf2zh_pro.translator.cache',):
            module = importlib.import_module(name)
            database = SqliteDatabase(None)
            self.enterContext(patch.object(module, 'db', database))
            self.enterContext(module._TranslationCache.bind_ctx(database))
            self.addCleanup(database.close)
            self.caches.append((module, database))

    def test_initialization_releases_connections(self):
        for module, database in self.caches:
            with self.subTest(cache=module.__name__):
                module.init_db()
                self.assertTrue(database.is_closed())

    def test_cache_roundtrips_release_main_and_worker_connections(self):
        for module, database in self.caches:
            with self.subTest(cache=module.__name__):
                module.init_db()
                database.close()

                def roundtrip(index):
                    cache = module.TranslationCache('lifecycle', {'scope': 'test'})
                    source, target = f'source {index}', f'translation {index}'
                    cache.set(source, target)
                    self.assertTrue(database.is_closed())
                    self.assertEqual(cache.get(source), target)
                    self.assertTrue(database.is_closed())
                    if hasattr(cache, 'delete'):
                        cache.delete(source)
                        self.assertTrue(database.is_closed())
                        self.assertIsNone(cache.get(source))
                        self.assertTrue(database.is_closed())

                roundtrip(0)
                with ThreadPoolExecutor(max_workers=3) as executor:
                    list(executor.map(roundtrip, range(1, 7)))

    def test_failed_reads_release_connections(self):
        for module, database in self.caches:
            with self.subTest(cache=module.__name__):
                module.init_db()
                database.close()
                cache = module.TranslationCache('lifecycle')

                def fail_read(**kwargs):
                    database.execute_sql('SELECT 1')
                    raise ValueError('failed cache read')

                with patch.object(module._TranslationCache, 'get_or_none', side_effect=fail_read):
                    with self.assertRaisesRegex(ValueError, 'failed cache read'):
                        cache.get('source')
                self.assertTrue(database.is_closed())

    def test_failed_initialization_releases_connections(self):
        for module, database in self.caches:
            with self.subTest(cache=module.__name__):
                def fail_creation(*args, **kwargs):
                    database.execute_sql('SELECT 1')
                    raise ValueError('failed cache initialization')

                with patch.object(database, 'create_tables', side_effect=fail_creation):
                    with self.assertRaisesRegex(ValueError, 'failed cache initialization'):
                        module.init_db()
                self.assertTrue(database.is_closed())

    def test_priority_worker_reuses_connection_across_jobs_and_closes_on_exit(self):
        for module, database in self.caches:
            with self.subTest(cache=module.__name__):
                module.init_db()
                cache = module.TranslationCache('lifecycle')
                original_close = database.close
                closed = []

                def close():
                    result = original_close()
                    closed.append(database.is_closed())
                    return result

                def roundtrip():
                    cache.set('source', 'translation')
                    self.assertFalse(database.is_closed())
                    connection = database.connection()
                    self.assertEqual(cache.get('source'), 'translation')
                    self.assertIs(database.connection(), connection)
                    return connection

                with patch.object(database, '_connect', wraps=database._connect) as connect, \
                     patch.object(database, 'close', side_effect=close):
                    with PriorityThreadPoolExecutor(max_workers=1) as executor:
                        first = executor.submit(roundtrip).result(timeout=3)
                        second = executor.submit(roundtrip).result(timeout=3)
                        self.assertIs(first, second)
                        self.assertEqual(connect.call_count, 1)
                        self.assertEqual(closed, [])
                    self.assertEqual(closed, [True])

    def test_task_scope_reuses_cache_and_nested_scopes(self):
        for module, database in self.caches:
            module.init_db()
        with ExitStack() as patches:
            connects = [patches.enter_context(patch.object(database, '_connect', wraps=database._connect))
                        for _, database in self.caches]
            connections = []
            with cache_connection_scope():
                for module, database in self.caches:
                    cache = module.TranslationCache('lifecycle')
                    cache.set('source', 'translation')
                    connection = database.connection()
                    connections.append(connection)
                    with cache_connection_scope():
                        for _ in range(10):
                            self.assertEqual(cache.get('source'), 'translation')
                        if hasattr(cache, 'delete'):
                            cache.delete('source')
                            self.assertIsNone(cache.get('source'))
                    self.assertFalse(database.is_closed())
                    self.assertIs(database.connection(), connection)
            self.assertEqual([connect.call_count for connect in connects], [1])
        for (_, database), connection in zip(self.caches, connections):
            self.assertTrue(database.is_closed())
            with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                connection.execute('SELECT 1')

    def test_task_scope_closes_after_failed_reads_and_cancellation(self):
        for module, database in self.caches:
            module.init_db()
            cache = module.TranslationCache('lifecycle')
            for error in (ValueError('failed read'), asyncio.CancelledError()):
                with self.subTest(cache=module.__name__, error=type(error).__name__):
                    def fail_read(**kwargs):
                        database.execute_sql('SELECT 1')
                        raise error

                    with patch.object(module._TranslationCache, 'get_or_none', side_effect=fail_read):
                        with self.assertRaises(type(error)), cache_connection_scope():
                            cache.get('source')
                    self.assertTrue(database.is_closed())

    def test_concurrent_task_scopes_keep_separate_connections(self):
        for module, database in self.caches:
            with self.subTest(cache=module.__name__):
                module.init_db()
                cache = module.TranslationCache('lifecycle')
                barrier = threading.Barrier(2)

                def roundtrip(index):
                    with cache_connection_scope():
                        cache.set(f'source {index}', 'translation')
                        connection = database.connection()
                        barrier.wait(timeout=3)
                        self.assertEqual(cache.get(f'source {index}'), 'translation')
                        self.assertIs(database.connection(), connection)
                    self.assertTrue(database.is_closed())
                    with self.assertRaisesRegex(sqlite3.ProgrammingError, 'closed database'):
                        connection.execute('SELECT 1')
                    return connection

                with ThreadPoolExecutor(max_workers=2) as executor:
                    connections = list(executor.map(roundtrip, range(2)))
                self.assertIsNot(connections[0], connections[1])

    def test_pdf_task_closes_connections_on_success_error_and_cancellation(self):
        from babeldoc.format.pdf import high_level

        for module, database in self.caches:
            module.init_db()
        for error in (None, ValueError('failed translation'), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                result = SimpleNamespace()

                def translate(monitor, config):
                    for module, database in self.caches:
                        cache = module.TranslationCache('lifecycle')
                        cache.set('source', 'translation')
                        self.assertEqual(cache.get('source'), 'translation')
                        self.assertFalse(database.is_closed())
                    if error is not None:
                        raise error
                    return result

                config = SimpleNamespace(raise_if_cancelled=lambda: None, input_file='paper.pdf',
                                         split_strategy=None, shared_context_cross_split_part=SimpleNamespace(),
                                         debug=False, cleanup_temp_files=Mock())
                monitor = Mock()
                with ExitStack() as patches:
                    for name in ('Document', 'check_metadata', 'MemoryMonitor',
                                 'fix_cmap', 'add_metadata', 'migrate_toc'):
                        patches.enter_context(patch.object(high_level, name))
                    patches.enter_context(patch.object(high_level, '_do_translate_single', side_effect=translate))
                    if error is not None:
                        with self.assertRaises(type(error)):
                            high_level.do_translate(monitor, config)
                    else:
                        self.assertIs(high_level.do_translate(monitor, config), result)
                        monitor.translate_done.assert_called_once_with(result)
                monitor.on_finish.assert_called_once()
                config.cleanup_temp_files.assert_called_once()
                self.assertTrue(all(database.is_closed() for _, database in self.caches))


if __name__ == '__main__':
    unittest.main()
