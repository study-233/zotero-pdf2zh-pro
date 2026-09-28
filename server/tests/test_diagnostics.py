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
