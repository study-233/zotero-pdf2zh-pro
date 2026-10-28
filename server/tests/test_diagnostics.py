import json
import logging
import tempfile
import unittest
from pathlib import Path
from diagnostics import DiagnosticStore, SafeLogHandler, exception_frames, negotiate_schema, safe_fields
from task_manager import TaskManager,TaskRecord
from diagnostic_contract import CONTRACT

class DiagnosticTests(unittest.TestCase):
    def test_payload_cannot_override_collector_event_or_timestamp(self):
        store = DiagnosticStore()
        store.record('request', {'event': 'misleading_event', 'time': '1999-01-01T00:00:00Z'})
        row = store.export()['records'][0]
        self.assertEqual(row['event'], 'request')
        self.assertNotEqual(row['time'], '1999-01-01T00:00:00Z')

    def test_all_registered_provider_codes_and_protocols_survive_export(self):
        for code in CONTRACT['providerErrors']:
            self.assertEqual(safe_fields({'failedParagraphs': [{'providerCode': code}]}),
                             {'failedParagraphs': [{'providerCode': code}]})
        self.assertEqual(safe_fields({'protocol': 'codex_app_server',
            'metrics': {'requests': {'protocols': {'codex_app_server': 2}}}}),
            {'protocol': 'codex_app_server', 'metrics': {'requests': {'protocols': {'codex_app_server': 2}}}})

    def test_old_and_new_clients_negotiate_without_breaking_the_v1_endpoint(self):
        from unittest.mock import patch
        import server
        manager = TaskManager()
        manager._tasks['test'] = TaskRecord('test', 'secret.pdf', 'openai', ['dual'], {}, Path('/tmp'))
        with patch.object(server, 'TASK_MANAGER', manager):
            client = server.create_app().test_client()
            for endpoint in ('/diagnostics', '/tasks/test/diagnostics'):
                for suffix in ('', '?schemaVersions=1', '?schemaVersions=2,1',
                               '?schemaVersions=' + ','.join(['1'] * 8),
                               '?schemaVersions=1,' + '9' * 126):
                    response = client.get(endpoint + suffix)
                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.json['schemaVersion'], 1)
                    self.assertEqual(response.json['schemaRevision'], CONTRACT['schemaRevision'])
                self.assertEqual(client.get(endpoint + '?schemaVersions=2').status_code, 406)
                for value in ('', 'x', '1,,2', '0', '1.0', ','.join(['1'] * 9),
                              '9' * 129, '1,' + '9' * 127):
                    response = client.get(endpoint + '?schemaVersions=' + value)
                    self.assertEqual(response.status_code, 400)
                    self.assertEqual(response.json['code'], 'diagnostics_invalid_schema_versions')
            self.assertEqual(client.get('/health').json['diagnostics']['schemaVersions'], [1])

    def test_oversized_schema_versions_are_rejected_before_regex_matching(self):
        from unittest.mock import patch
        with patch('diagnostics.re.fullmatch', side_effect=AssertionError('Regex must not run')):
            for value in ('1' * 129, '1' * 1000000):
                with self.subTest(length=len(value)):
                    with self.assertRaisesRegex(ValueError, '^invalid_schema_versions$'):
                        negotiate_schema(value)

    def test_structured_and_unknown_warnings_keep_codes_or_locations_without_messages(self):
        store = DiagnosticStore()
        handler = SafeLogHandler(store.record)
        for diagnostic in ({'validationCode': 'placeholder_mismatch', 'missingPlaceholderCount': 2,
                            'prompt': 'private text'}, {}):
            record = logging.LogRecord('test', logging.WARNING, '/Users/private/source.py', 12,
                                       'sk-secret private text', (), None, 'translate')
            record.diagnostic = diagnostic
            handler.emit(record)
        rows = store.export()['records']
        self.assertEqual(rows[0]['validationCode'], 'placeholder_mismatch')
        self.assertEqual(rows[0]['missingPlaceholderCount'], 2)
        self.assertEqual(rows[1]['collectionCode'], 'unknown_warning')
        self.assertEqual((rows[1]['file'], rows[1]['function'], rows[1]['line']), ('source.py', 'translate', 12))
        for secret in ('sk-secret', 'private text', '/Users/private'):
            self.assertNotIn(secret, json.dumps(rows))

    def test_native_validation_details_survive_recovery_and_task_api(self):
        from pdf2zh_pro.translator.validation import InvalidTranslation, validate_text
        from babeldoc.format.pdf.translation_recovery import safe_error
        try:
            validate_text('Result {v1}', '结果 {v2}', 'zh-CN')
        except InvalidTranslation as error:
            fields = safe_error(error)
        else:
            self.fail('Expected a placeholder mismatch')
        self.assertEqual(fields['validationCode'], 'placeholder_mismatch')
        self.assertEqual(fields['missingPlaceholderCount'], 1)
        self.assertEqual(fields['extraPlaceholderCount'], 1)
        self.assertEqual(safe_error(InvalidTranslation('private unknown error'))['validationCode'], 'unknown_validation')
        fields['reason'] = 'unchanged_translation → stale legacy reason'
        self.assertEqual(safe_fields({'failedParagraphs': [fields]})['failedParagraphs'][0]['validationCode'],
                         'placeholder_mismatch')
        from unittest.mock import patch
        import server
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory)
            manager = TaskManager(path / 'tasks.json')
            record = TaskRecord('validation', 'private.pdf', 'openai', ['dual'], {}, path, status='incomplete')
            record.failed_paragraphs = [{'page': 1, 'attempts': 2, **fields}]
            manager._tasks['validation'] = record
            manager._save_persistent_tasks()
            restored = TaskManager(path / 'tasks.json')
            with patch.object(server, 'TASK_MANAGER', restored):
                exported = server.create_app().test_client().get('/tasks/validation/diagnostics').json
            failed = exported['tasks'][0]['failedParagraphs'][0]
            self.assertEqual(failed['validationCode'], 'placeholder_mismatch')
            self.assertEqual(failed['missingPlaceholderCount'], 1)
            self.assertNotIn('stale legacy reason', json.dumps(exported))

    def test_unknown_fields_and_values_are_counted_without_exporting_their_names(self):
        store = DiagnosticStore()
        store.record('request', {'private_field': 'private text', 'protocol': 'private protocol',
                                'metrics': {'requests': {'errorTypes': {'private type': 1}}}})
        payload = store.export()
        self.assertEqual(payload['collection']['filteredFields'], 1)
        self.assertEqual(payload['collection']['rejectedValues'], 2)
        self.assertNotIn('private', json.dumps(payload))

    def test_overflow_is_reported_without_confusing_memory_eviction_with_disk_loss(self):
        store = DiagnosticStore()
        for _ in range(1001): store.record('sample')
        collection = store.export()['collection']
        self.assertEqual(collection['memoryEvictions'], 1)
        self.assertEqual(collection['droppedRecords'], 0)
        store.record('sample', {'failedParagraphs': [{'page': 1, 'errorType': 'InvalidTranslation',
                     'validationCode': 'placeholder_mismatch', 'attempts': 2}] * 2000})
        exported = store.export()
        self.assertEqual(exported['collection']['oversizedRecords'], 1)
        self.assertTrue(exported['truncated'])

    def test_fixed_validation_codes_survive_without_free_form_reason_chains(self):
        rows = [
            {'page': 1, 'errorType': 'InvalidTranslation',
             'reason': 'placeholder_mismatch → InvalidTranslation → private text'},
            {'page': 6, 'errorType': 'InvalidTranslation',
             'reason': 'unchanged_translation → InvalidTranslation'},
            {'page': 10, 'errorType': 'InvalidTranslation',
             'validationCode': 'target_language_missing'},
            {'page': 11, 'errorType': 'InvalidTranslation',
             'reason': 'private text', 'validationCode': 'sk-secret'},
            {'page': 12, 'errorType': 'RuntimeError',
             'reason': 'placeholder_mismatch → private text'},
        ]
        safe = safe_fields({'failedParagraphs': rows})
        self.assertEqual([row.get('validationCode') for row in safe['failedParagraphs']],
                         ['placeholder_mismatch', 'unchanged_translation', 'target_language_missing', None, None])
        self.assertEqual(safe_fields(safe), safe)
        for private in ('private text', 'sk-secret', 'reason'):
            self.assertNotIn(private, json.dumps(safe))
        self.assertEqual(safe_fields({'validationCode': []}), {})

    def test_recovery_timing_is_exported_without_submission_identity(self):
        timing = {"queuedAt": "2026-01-01T00:00:00Z", "queueSeconds": 5,
                  "executionSeconds": 12, "totalSeconds": 17, "complete": False}
        public = {"status": "paused", "recoveryState": "queued", "canResume": True, "timing": timing}
        self.assertEqual(safe_fields({**public, "submission_fingerprint": "private-hash", "requestId": "private-id"}), public)

    def test_cancelled_request_count_survives_safe_export(self):
        payload = {"metrics": {"requests": {"attempts": 1, "cancelled": 1,
                   "byKind": {"translation": {"cancelled": 1}}}}}
        self.assertEqual(safe_fields(payload), payload)

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
                'paragraphId': 'private-hash', 'input': 'private text'},
                {'page': 6, 'attempts': 2, 'errorType': 'InvalidTranslation',
                 'reason': 'placeholder_mismatch → InvalidTranslation'}]
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
            self.assertEqual(task['failedParagraphs'][1]['validationCode'], 'placeholder_mismatch')
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
            self.assertNotIn('effectiveConfiguration', manager.get_task('test'))
            history = [r for r in manager.diagnostics.export('test')['records'] if r['event'] == 'task_configuration']
            self.assertEqual(history[0]['effectiveConfiguration']['qps'], 10)
