import asyncio
import multiprocessing
import os
import signal
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import psutil
from diagnostics import DiagnosticStore
from task_runtime import run_translation, CleanupFailed, WorkerFailed


def success(payload,cancel,emit):
    emit('progress',{'type':'parse_detail','operation':'page_end','currentPage':1,'completedPages':1,'totalPages':1})
    emit('metrics', {'requests': {'total': 1, 'active': 0}})
    return SimpleNamespace(files={},translation_summary=None,failed_paragraphs=[])


def diagnostic_worker(payload, cancel, emit):
    emit('progress', {'type': 'diagnostic_configuration',
                     'effectiveConfiguration': {'provider': 'openai', 'model': 'org/model', 'qps': 2, 'poolSize': 4}})
    emit('metrics', {'requests': {'attempts': 100, 'succeeded': 2, 'failed': 98},
                     'tokens': {'input': 376, 'output': 133, 'total': 509}})
    return SimpleNamespace(files={}, translation_summary=None, failed_paragraphs=[])


def blocked(payload,cancel,emit):
    if os.name!='nt': signal.signal(signal.SIGTERM,signal.SIG_IGN)
    emit('progress',{'type':'parse_detail','operation':'page_start','currentPage':1})
    while True: time.sleep(.02)


def cooperative(payload,cancel,emit):
    emit('progress',{'type':'parse_detail','operation':'page_start','currentPage':1})
    while not cancel.wait(.02): pass
    raise asyncio.CancelledError


def crashing(payload,cancel,emit):
    os._exit(7)


def checkpoint_worker(payload,cancel,emit):
    import sqlite3
    db=sqlite3.connect(payload['checkpoint'])
    db.execute('CREATE TABLE progress(value TEXT)');db.execute("INSERT INTO progress VALUES ('committed')");db.commit()
    db.execute("INSERT INTO progress VALUES ('uncommitted')")
    blocked(payload,cancel,emit)


def descendant_worker(payload,cancel,emit):
    import subprocess,sys
    child=subprocess.Popen([sys.executable,'-c','import time; time.sleep(60)'])
    Path(payload['child_pid']).write_text(str(child.pid))
    try: blocked(payload,cancel,emit)
    finally:
        child.kill();child.wait(timeout=1)


def bootstrap_blocked(*args):
    while True:
        time.sleep(.1)


class RuntimeTests(unittest.TestCase):
    def execute(self,target,cancel_at_start=False,payload=None,deadlines=(.2,.4,1.2)):
        event=threading.Event(); detail=[]; progress=[]
        store=DiagnosticStore()
        def progressed(value):
            progress.append(value)
            if cancel_at_start: event.set()
        result=run_translation(payload or {},'probe',cancel_event=event,progress_callback=progressed,
            metrics_callback=lambda m:None,runtime_callback=detail.append,store=store,
            identity={'taskId':'probe','attempt':1,'serverInstanceId':'test'},target=target,deadlines=deadlines)
        return result,detail,progress,store

    def test_success_and_no_child_remains(self):
        result,_,progress,store=self.execute(success)
        self.assertEqual(result.files,{})
        self.assertTrue(progress)
        pid=next(row['pid'] for row in store.memory if row['event']=='worker_started')
        self.assertFalse(psutil.pid_exists(pid))

    def test_configuration_and_final_request_counts_arrive_before_worker_exit(self):
        _, _, progress, store = self.execute(diagnostic_worker)
        self.assertEqual(progress[0]['effectiveConfiguration']['model'], 'org/model')
        summary = [row for row in store.export()['records'] if row['event'] == 'request_summary'][-1]
        self.assertEqual(summary['requests']['attempts'], 100)
        self.assertEqual(summary['tokens']['total'], 509)
        self.assertNotIn('total', summary)

    def test_cooperative_cancellation(self):
        with self.assertRaises(asyncio.CancelledError): self.execute(cooperative,True)

    def test_uncooperative_cancellation_releases_resources_and_next_task_runs(self):
        started=time.monotonic()
        with self.assertRaises(asyncio.CancelledError): self.execute(blocked,True)
        self.assertLess(time.monotonic()-started,5)
        self.assertEqual(self.execute(success)[0].files,{})

    def test_crash_is_terminal(self):
        with self.assertRaisesRegex(RuntimeError,'WITHOUT_RESULT'): self.execute(crashing)

    def test_force_kill_preserves_only_committed_checkpoint(self):
        import sqlite3
        from contextlib import closing
        with tempfile.TemporaryDirectory() as temp:
            path=str(Path(temp)/'checkpoint.sqlite')
            with self.assertRaises(asyncio.CancelledError): self.execute(checkpoint_worker,True,{'checkpoint':path})
            with closing(sqlite3.connect(path)) as db: self.assertEqual(db.execute('select value from progress').fetchall(),[('committed',)])

    def test_unconfirmed_cleanup_fails_explicitly(self):
        with patch('task_runtime._tree_alive',return_value=True):
            with self.assertRaises(CleanupFailed): self.execute(blocked,True)

    def test_descendant_is_reaped(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'pid'
            try:
                with self.assertRaises(asyncio.CancelledError): self.execute(descendant_worker,True,{'child_pid':str(path)},deadlines=(.3,.6,2))
                pid=int(path.read_text())
                self.assertTrue(not psutil.pid_exists(pid) or psutil.Process(pid).status()==psutil.STATUS_ZOMBIE)
            finally:
                if path.exists():
                    try: psutil.Process(int(path.read_text())).kill()
                    except psutil.NoSuchProcess: pass

    def test_default_cancellation_deadline_with_real_uninterruptible_worker(self):
        started=time.monotonic()
        with self.assertRaises(asyncio.CancelledError):
            self.execute(blocked, True, deadlines=(10,12,15))
        self.assertLess(time.monotonic()-started,15)

    def test_startup_failure_is_not_user_cancellation(self):
        event=threading.Event()
        with patch('task_runtime.child_entry', bootstrap_blocked):
            with self.assertRaisesRegex(WorkerFailed,'START_TIMEOUT'):
                run_translation({},'probe',cancel_event=event,progress_callback=lambda e:None,
                    metrics_callback=lambda e:None,runtime_callback=lambda e:None,store=DiagnosticStore(),
                    identity={'taskId':'probe','attempt':1,'serverInstanceId':'test'},
                    startup_timeout=.1,deadlines=(.2,.4,1.2))
        self.assertFalse(event.is_set())

    def test_terminal_metrics_survive_worker_exit(self):
        metrics=[]
        run_translation({},'probe',cancel_event=threading.Event(),progress_callback=lambda e:None,
            metrics_callback=metrics.append,runtime_callback=lambda e:None,store=DiagnosticStore(),
            identity={'taskId':'probe','attempt':1,'serverInstanceId':'test'},target=success)
        self.assertEqual(metrics[-1]['requests']['total'],1)

if __name__=='__main__': unittest.main()
