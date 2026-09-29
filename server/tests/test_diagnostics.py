import json
import logging
import tempfile
import unittest
from pathlib import Path
from diagnostics import DiagnosticStore, SafeLogHandler, exception_frames, safe_fields
from task_manager import TaskManager,TaskRecord

class DiagnosticTests(unittest.TestCase):
    def test_secrets_never_reach_disk_or_export(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DiagnosticStore(Path(temp))
            source={'taskId':'test','attempt':1,'currentPage':4,'apiKey':'sk-secret-key','prompt':'private paper','fileName':'sensitive.pdf','url':'https://private.example/?token=secret','status':'running'}
            store.record('task_state',source)
            logger=logging.getLogger('privacy-test');handler=SafeLogHandler(store.record);logger.addHandler(handler)
            try: logger.warning('sk-secret-key /Users/secret/private.pdf private paper')
            finally: logger.removeHandler(handler)
            self.assertTrue(store.flush(timeout=2))
            exported=json.dumps(store.export())
            disk=''.join(p.read_text() for p in Path(temp).glob('*'))
            for value in ('sk-secret-key','private paper','sensitive.pdf','private.example','/Users/secret'):
                self.assertNotIn(value,exported+disk)
            self.assertIn('currentPage',disk)

    def test_rotation_and_export(self):
        with tempfile.TemporaryDirectory() as temp:
            store=DiagnosticStore(Path(temp));store.root.mkdir(exist_ok=True)
            for i in range(5): store._append('service',json.dumps({'event':'sample','count':i}),45,2)
            self.assertLessEqual(len(list(Path(temp).glob('*'))),3)
            self.assertTrue(store.export()['truncated'])

    def test_heartbeat_does_not_refresh_last_progress(self):
        manager=TaskManager()
        record=TaskRecord('test','paper.pdf','openai',['dual'],{},Path('/tmp'),status='running')
        manager._tasks['test']=record
        manager._handle_progress_event('test',{'type':'parse_detail','currentPage':4,'operation':'page_start'})
        before=record.progress_detail['lastProgressAt']
        manager._handle_runtime_event('test',1,{'heartbeatAt':1234567890.,'idleSeconds':61,'stalled':True})
        self.assertEqual(record.progress_detail['lastProgressAt'],before)
        self.assertTrue(record.progress_detail['stalled'])
        manager._handle_progress_event('test',{'type':'parse_detail','operation':'page_end','completedPages':4})
        self.assertFalse(record.progress_detail['stalled'])

    def test_stale_attempt_cannot_change_detailed_progress(self):
        manager=TaskManager();record=TaskRecord('test','paper.pdf','openai',['dual'],{},Path('/tmp'),status='running',attempt=2)
        manager._tasks['test']=record
        manager._handle_runtime_event('test',1,{'cancelPhase':'killing'})
        manager._handle_progress_event('test',{'type':'parse_detail','currentPage':5},attempt=1)
        self.assertEqual(record.progress_detail,{})

    def test_bad_field_shapes_are_ignored(self):
        self.assertEqual(safe_fields({'status':{},'operation':[],'currentPage':float('nan')}),{})

    def test_http_exports_are_versioned_and_do_not_export_raw_task_fields(self):
        from unittest.mock import patch
        import server
        manager=TaskManager()
        manager._tasks['test']=TaskRecord('test','secret-paper.pdf','openai',['dual'],{'apiKey':'sk-secret'},Path('/private/user'),status='running')
        with patch.object(server,'TASK_MANAGER',manager):
            client=server.create_app().test_client()
            for url in ('/diagnostics','/tasks/test/diagnostics'):
                response=client.get(url)
                self.assertEqual(response.status_code,200)
                self.assertEqual(response.json['schemaVersion'],1)
                self.assertIn('serviceVersion',response.json['environment'])
                body=response.get_data(as_text=True)
                for secret in ('secret-paper','sk-secret','/private/user'):self.assertNotIn(secret,body)
            self.assertEqual(client.get('/tasks/missing/diagnostics').status_code,404)

    def test_slow_disk_does_not_block_recording_or_lose_memory_snapshot(self):
        import threading
        import time
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            store=DiagnosticStore(Path(temp))
            gate=threading.Event()
            with patch.object(store, '_append', side_effect=lambda *args: gate.wait(2)):
                started=time.monotonic()
                try:
                    store.record('task_state', {'taskId':'probe','currentPage':4})
                    self.assertLess(time.monotonic()-started,.1)
                    self.assertEqual(store.export()['records'][0]['currentPage'],4)
                finally:
                    gate.set()
                    store.flush(timeout=2)

    def test_duplicate_stage_update_is_not_actual_progress(self):
        manager=TaskManager()
        record=TaskRecord('test','paper.pdf','openai',['dual'],{},Path('/tmp'),status='running')
        manager._tasks['test']=record
        update={'type':'progress_update','stage':'Parse Page Layout','stage_current':2,'stage_total':11}
        manager._handle_progress_event('test',update)
        before=record.progress_detail['lastProgressAt']
        manager._handle_runtime_event('test',1,{'idleSeconds':61,'stalled':True})
        manager._handle_progress_event('test',update)
        self.assertEqual(record.progress_detail['lastProgressAt'],before)
        self.assertTrue(record.progress_detail['stalled'])

    def test_configuration_and_outcomes_survive_export_and_restart(self):
        with tempfile.TemporaryDirectory() as temp:
            manager = TaskManager(Path(temp) / 'tasks.json')
            record = TaskRecord('test', 'secret.pdf', 'openai', ['dual'], {
                'service': 'openai', 'qps': 10, 'pool_size': 50,
                'llm_api': {'model': 'org/model:latest', 'apiKey': 'sk-secret',
                            'apiUrl': 'https://private.invalid', 'extraData': {'prompt': 'private text'}},
            }, Path(temp), status='running')
            manager._tasks['test'] = record
            manager._handle_progress_event('test', {'type': 'diagnostic_configuration',
                'effectiveConfiguration': {'provider': 'openai', 'model': 'org/resolved-model',
                    'qps': 2, 'poolSize': 4, 'protocol': 'chat_completions', 'apiKey': 'sk-secret'}}, attempt=1)
            record.translation_summary = {'succeeded': 1, 'failed': 93, 'pending': 5, 'skipped': 10}
            record.failed_paragraphs = [{'page': 4, 'attempts': 2, 'errorType': 'RateLimitError',
                'statusCode': 429, 'providerCode': 'insufficient_quota', 'reason': 'private text',
                'paragraphId': 'private-hash', 'input': 'private text'}]
            record.metrics = {'requests': {'attempts': 100, 'succeeded': 2, 'failed': 98,
                'statusCodes': {'429': 93, '200': 2, 'unknown': 5, 'sk-secret': 99},
                'errorTypes': {'APITimeoutError': 5, 'RateLimitError': 93},
                'byKind': {'initialization': {'succeeded': 1}, 'translation': {'succeeded': 1}}},
                'tokens': {'total': 509, 'input': 376, 'output': 133}}
            manager._save_persistent_tasks()
            restored = TaskManager(Path(temp) / 'tasks.json')
            data = restored.export_diagnostics('test')
            task = data['tasks'][0]
            self.assertEqual(task['requestedConfiguration']['model'], 'org/model:latest')
            self.assertEqual(task['requestedConfiguration']['qps'], 10)
            self.assertEqual(task['effectiveConfiguration']['qps'], 2)
            self.assertEqual(task['effectiveConfiguration']['poolSize'], 4)
            self.assertEqual(task['translationSummary'], record.translation_summary)
            self.assertEqual(task['metrics']['requests']['attempts'], 100)
            self.assertEqual(task['metrics']['tokens']['total'], 509)
            self.assertEqual(task['metrics']['requests']['errorTypes']['APITimeoutError'], 5)
            self.assertEqual(task['failedParagraphs'][0]['providerCode'], 'insufficient_quota')
            self.assertTrue(any(row['event'] == 'task_configuration' for row in data['records']))
            for secret in ('sk-secret', 'private.invalid', 'private text', 'private-hash', 'secret.pdf'):
                self.assertNotIn(secret, json.dumps(data))
            self.assertTrue(manager.diagnostics.flush(timeout=2))
            self.assertTrue(restored.diagnostics.flush(timeout=2))

    def test_model_labels_and_nested_fields_are_filtered(self):
        for label in ('org/model-v3.2:latest', 'deepseek-chat', 'gemini-2.5-pro'):
            self.assertEqual(safe_fields({'model': label}), {'model': label})
        for label in ('https://private.invalid/model', '/Users/private/model', 'sk-secret',
                      'Bearer-secret', 'org/sk-secret', 'key?token=secret', 'a' * 201):
            self.assertEqual(safe_fields({'model': label}), {})
        self.assertEqual(safe_fields({'metrics': {'requests': {'errorTypes': {'private text': 1},
            'statusCodes': {'429': -1, '200': True, '500': 2}}}}),
            {'metrics': {'requests': {'errorTypes': {}, 'statusCodes': {'500': 2}}}})
        data = safe_fields({'failedParagraphs': [{'page': 2}] * 2001})
        self.assertEqual(len(data['failedParagraphs']), 2000)
        self.assertTrue(data['failedParagraphsTruncated'])

    def test_metric_logger_preserves_safe_model_and_timeout_class(self):
        store = DiagnosticStore()
        handler = SafeLogHandler(store.record)
        data = {'provider': 'openaicompatible', 'model': 'org/model:tag',
                'errorType': 'APITimeoutError', 'success': False, 'apiKey': 'sk-secret'}
        handler.emit(logging.LogRecord('zotero_pdf2zh_server.metrics', logging.INFO,
                     __file__, 1, 'metric=%s', (json.dumps(data),), None))
        row = store.export()['records'][0]
        self.assertEqual(row['model'], 'org/model:tag')
        self.assertEqual(row['errorType'], 'APITimeoutError')
        self.assertEqual(row['retryReason'], 'timeout')
        self.assertNotIn('apiKey', row)

    def test_repair_replaces_effective_configuration_without_changing_history(self):
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / 'input.pdf'
            source.write_bytes(b'pdf')
            record = TaskRecord('test', 'input.pdf', 'openai', ['dual'], {
                'service': 'openai', 'qps': 10, 'pool_size': 50,
                'input_path': str(source), 'output_dir': str(Path(temp) / 'output'),
            }, Path(temp), status='running')
            manager = TaskManager()
            manager._tasks['test'] = record
            manager._handle_progress_event('test', {'type': 'diagnostic_configuration',
                'effectiveConfiguration': {'qps': 10, 'poolSize': 50}}, attempt=1)
            record.status = 'incomplete'
            with patch.object(manager, '_start_worker_locked'):
                repaired = manager.repair_task('test')
            self.assertEqual(repaired['requestedConfiguration']['qps'], 2)
            self.assertNotIn('effectiveConfiguration', repaired)
            manager._handle_progress_event('test', {'type': 'diagnostic_configuration',
                'effectiveConfiguration': {'qps': 10}}, attempt=1)
            self.assertNotIn('effectiveConfiguration', record.to_dict())
            history = [r for r in manager.diagnostics.export('test')['records'] if r['event'] == 'task_configuration']
            self.assertEqual(history[0]['effectiveConfiguration']['qps'], 10)
