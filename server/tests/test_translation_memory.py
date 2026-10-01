import asyncio
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import server
from pdf2zh_next_service import translate_pdf_with_callbacks
from task_manager import TaskManager, TaskRecord
from translation_memory import TranslationMemory, document_fingerprint, sync_recovery

SOURCE = 'Spatial computing enables users to interact directly with physical devices.'
TARGET = '空间计算使用户能够直接与物理设备进行交互。'


def entry(**changes):
    return dict(source=SOURCE, translation=TARGET, status='succeeded', page=5, paragraphId='p-123', **changes)


class MemoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.path = self.root / 'translation-memory.sqlite3'
        self.memory = TranslationMemory(self.path)

    def test_insert_upsert_reload_normalization_and_isolation(self):
        self.memory.upsert('one', [entry()])
        corrected = entry()
        corrected['translation'] = '最终校对译文'
        self.memory.upsert('one', [corrected])
        memory = TranslationMemory(self.path)
        self.assertEqual(memory.lookup('one', SOURCE)['translation'], '最终校对译文')
        self.assertEqual(memory.lookup('one', '  Spatial\ncomputing enables users to interact directly with physical devices.  ')['matchType'], 'exact')
        self.assertEqual(memory.lookup('one', 'physical devices')['matchType'], 'contained')
        self.assertFalse(memory.lookup('other', SOURCE)['matched'])
        self.assertFalse(memory.lookup('one', SOURCE, target_lang='ja')['matched'])
        self.assertFalse(memory.lookup('one', '%')['matched'])
        with closing(sqlite3.connect(self.path)) as db, db:
            self.assertEqual(db.execute('SELECT count(*) FROM paragraphs').fetchone()[0], 1)

    def test_unicode_page_priority_reverse_lookup_and_final_readable_text(self):
        first = entry()
        first.update(page=2, source='An efﬁcient soft\u00adware system.', readingTranslation='可读译文', translation='{v1}')
        second = dict(first, page=9, readingTranslation='第九页译文')
        self.memory.upsert('one', [first, second])
        self.assertEqual(self.memory.lookup('one', 'efficient software', page=9)['page'], 9)
        self.assertEqual(self.memory.lookup('one', 'An efficient software system.', page=1)['matchType'], 'exact')
        self.assertEqual(self.memory.lookup('one', '第九页', side='translation')['source'], second['source'])

    def test_only_confirmed_nonempty_paragraphs(self):
        entries = [entry()]
        for changes in ({'status': 'failed'}, {'status': 'pending'}, {'status': 'skipped'},
                        {'translation': ''}, {'source': ''}, {'quality': {'status': 'failed'}}):
            entries.append(dict(entry(), **changes))
        self.assertEqual(self.memory.upsert('one', entries), 1)
        self.memory.upsert('one', [dict(entry(), translation='校对后', quality={'status': 'corrected'})])
        self.assertEqual(self.memory.lookup('one', SOURCE)['translation'], '校对后')

    def test_legacy_placeholders_reuse_prose_instead_of_becoming_a_model_miss(self):
        self.memory.upsert('one', [dict(entry(), translation="<style id='1'>已有</style>译文 {v1}")])
        result = self.memory.lookup('one', SOURCE)
        self.assertTrue(result['matched'])
        self.assertTrue(result['formattingIncomplete'])
        self.assertEqual(result['translation'], '已有译文 ⟦原排版内容⟧')
        self.memory.upsert('one', [dict(entry(), readingTranslation=TARGET)])
        self.assertFalse(self.memory.lookup('one', SOURCE)['formattingIncomplete'])

    def checkpoint(self):
        work = self.root / 'task-one'
        work.mkdir()
        pdf = work / 'paper.pdf'
        pdf.write_bytes(b'%PDF-1.4\noriginal bytes')
        checkpoint = work / 'paragraph-recovery.sqlite3'
        with closing(sqlite3.connect(checkpoint)) as db, db:
            db.execute('CREATE TABLE paragraphs (key TEXT PRIMARY KEY, payload TEXT)')
            db.execute('INSERT INTO paragraphs VALUES (?, ?)', ('p', json.dumps(entry())))
        return work, {'input_path': str(pdf), 'target_lang': 'zh-CN'}

    def test_task_deletion_and_restart_preserve_memory_with_legacy_backfill(self):
        work, payload = self.checkpoint()
        fingerprint = document_fingerprint(Path(payload['input_path']))
        manager = TaskManager(self.root / 'tasks.json')
        manager._tasks['task-one'] = TaskRecord('task-one', 'paper.pdf', 'openai', ['dual'], payload, work, status='completed')
        manager._save_persistent_tasks()
        manager.close()
        # Existing completed task checkpoints are migrated when the manager loads.
        manager = TaskManager(self.root / 'tasks.json')
        self.addCleanup(manager.close)
        self.assertTrue(self.memory.lookup(fingerprint, SOURCE)['matched'])
        manager.delete_task('task-one')
        self.assertFalse(work.exists())
        self.assertTrue(TranslationMemory(self.path).lookup(fingerprint, SOURCE)['matched'])

    def test_interrupted_task_without_a_pdf_output_is_not_backfilled(self):
        work, payload = self.checkpoint()
        fingerprint = document_fingerprint(Path(payload['input_path']))
        manager = TaskManager(self.root / 'tasks.json')
        manager._tasks['task-one'] = TaskRecord('task-one', 'paper.pdf', 'openai', ['dual'], payload, work, status='incomplete')
        manager._save_persistent_tasks()
        manager.close()
        restarted = TaskManager(self.root / 'tasks.json')
        self.addCleanup(restarted.close)
        self.assertFalse(self.memory.lookup(fingerprint, SOURCE)['matched'])

    def test_sync_checkpoint_is_read_only(self):
        work, payload = self.checkpoint()
        before = (work / 'paragraph-recovery.sqlite3').read_bytes()
        self.assertEqual(sync_recovery(self.path, payload), 1)
        self.assertEqual((work / 'paragraph-recovery.sqlite3').read_bytes(), before)

    def test_pipeline_syncs_after_output_and_before_return(self):
        work = self.root / 'pipeline'
        work.mkdir()
        pdf = work / 'paper.pdf'
        pdf.write_bytes(b'original bytes')
        payload = {'input_path': str(pdf), 'output_dir': str(work / 'output'),
                   'output_modes': ['dual'], 'service': 'openai', 'translation_memory_path': str(self.path)}
        config = SimpleNamespace(qps=2, pool_max_workers=1, translator=SimpleNamespace())

        async def translate(config):
            with config.recovery.connection:
                config.recovery._upsert('p', entry())
            yield {'type': 'finish', 'translate_result': SimpleNamespace()}

        async def fonts(**kwargs):
            pass

        def output(*args):
            self.assertFalse(self.memory.lookup(document_fingerprint(pdf), SOURCE)['matched'])
            return {'dual': SimpleNamespace(filename='translated.pdf')}

        with patch('pdf2zh_next_service.create_runtime_settings', return_value=object()), \
             patch('pdf2zh_next_service.create_babeldoc_config', return_value=config), \
             patch('pdf2zh_next_service.download_all_fonts_async', side_effect=fonts), \
             patch('pdf2zh_next_service.babeldoc_translate', side_effect=translate), \
             patch('pdf2zh_next_service.collect_output_files', side_effect=output):
            asyncio.run(translate_pdf_with_callbacks(payload, 'pipeline'))
        self.assertEqual(self.memory.lookup(document_fingerprint(pdf), SOURCE)['translation'], TARGET)

    def test_lookup_route_validation_and_zero_provider_calls(self):
        self.memory.upsert('one', [entry()])
        with patch.object(server, 'TRANSLATES_DIR', self.root), \
             patch('pdf2zh_next_service.get_translator') as provider:
            client = server.create_app().test_client()
            base = {'documentFingerprint': 'one', 'text': SOURCE, 'page': 5}
            for changes, kind in (({}, 'exact'), ({'text': 'physical devices'}, 'contained')):
                response = client.post('/translation-lookup', json={**base, **changes})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['matchType'], kind)
                self.assertTrue(response.json['fromTranslationMemory'])
            self.assertFalse(client.post('/translation-lookup', json={**base, 'documentFingerprint': 'absent'}).json['matched'])
            for data in ([], {}, {'text': SOURCE}, {**base, 'text': ''}, {**base, 'page': True},
                         {**base, 'page': 0}, {**base, 'side': 'unknown'}, {**base, 'targetLang': []}):
                self.assertEqual(client.post('/translation-lookup', json=data).status_code, 400)
            provider.assert_not_called()
