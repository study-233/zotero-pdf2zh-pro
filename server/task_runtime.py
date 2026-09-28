"""Spawn-isolated translation. The supervisor never waits indefinitely on child IPC."""
from __future__ import annotations
import asyncio
import ctypes
import json
import logging
import multiprocessing as mp
import os
import queue
import signal
import threading
import time
from pathlib import Path
import psutil
from diagnostics import SafeLogHandler, thread_frames, exception_frames

class CleanupFailed(RuntimeError):
    pass

class WorkerFailed(RuntimeError):
    pass

class WindowsJob:
    """All descendants inherit a kill-on-close job; no name/PID based bulk killing."""
    def __init__(self, pid):
        from ctypes import wintypes as w
        class BASIC(ctypes.Structure):
            _fields_=[('PerProcessUserTimeLimit',ctypes.c_int64),('PerJobUserTimeLimit',ctypes.c_int64),('LimitFlags',w.DWORD),('MinimumWorkingSetSize',ctypes.c_size_t),('MaximumWorkingSetSize',ctypes.c_size_t),('ActiveProcessLimit',w.DWORD),('Affinity',ctypes.c_size_t),('PriorityClass',w.DWORD),('SchedulingClass',w.DWORD)]
        class IO(ctypes.Structure):
            _fields_=[(n,ctypes.c_uint64) for n in ('ReadOperationCount','WriteOperationCount','OtherOperationCount','ReadTransferCount','WriteTransferCount','OtherTransferCount')]
        class EXTENDED(ctypes.Structure):
            _fields_=[('BasicLimitInformation',BASIC),('IoInfo',IO),('ProcessMemoryLimit',ctypes.c_size_t),('JobMemoryLimit',ctypes.c_size_t),('PeakProcessMemoryUsed',ctypes.c_size_t),('PeakJobMemoryUsed',ctypes.c_size_t)]
        self.k=ctypes.WinDLL('kernel32',use_last_error=True)
        self.k.CreateJobObjectW.argtypes=[ctypes.c_void_p,w.LPCWSTR]; self.k.CreateJobObjectW.restype=w.HANDLE
        self.k.OpenProcess.argtypes=[w.DWORD,w.BOOL,w.DWORD]; self.k.OpenProcess.restype=w.HANDLE
        self.k.SetInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD]
        self.k.AssignProcessToJobObject.argtypes=[w.HANDLE,w.HANDLE]
        self.k.TerminateJobObject.argtypes=[w.HANDLE,w.UINT]
        self.k.CloseHandle.argtypes=[w.HANDLE]
        self.k.QueryInformationJobObject.argtypes=[w.HANDLE,ctypes.c_int,ctypes.c_void_p,w.DWORD,ctypes.c_void_p]
        self.handle=self.k.CreateJobObjectW(None,None)
        if not self.handle: raise ctypes.WinError(ctypes.get_last_error())
        proc=None
        try:
            limits=EXTENDED(); limits.BasicLimitInformation.LimitFlags=0x2000
            if not self.k.SetInformationJobObject(self.handle,9,ctypes.byref(limits),ctypes.sizeof(limits)): raise ctypes.WinError(ctypes.get_last_error())
            proc=self.k.OpenProcess(0x0100|0x0001|0x1000,False,pid)
            if not proc or not self.k.AssignProcessToJobObject(self.handle,proc): raise ctypes.WinError(ctypes.get_last_error())
        except BaseException:
            self.close(); raise
        finally:
            if proc: self.k.CloseHandle(proc)
    def terminate(self):
        if not self.k.TerminateJobObject(self.handle,1): raise ctypes.WinError(ctypes.get_last_error())
    def alive(self):
        class ACCOUNTING(ctypes.Structure):
            _fields_=[('times',ctypes.c_int64*4),('faults',ctypes.c_uint32),('total',ctypes.c_uint32),('active',ctypes.c_uint32),('terminated',ctypes.c_uint32)]
        info=ACCOUNTING()
        if not self.k.QueryInformationJobObject(self.handle,1,ctypes.byref(info),ctypes.sizeof(info),None): return True
        return bool(info.active)
    def close(self):
        if self.handle: self.k.CloseHandle(self.handle); self.handle=None


def child_entry(payload, identity, cancel, gate, stack_request, telemetry, control, parent_pid, parent_created, target=None):
    """Minimal bootstrap before importing translation libraries or opening task files."""
    if os.name != 'nt': os.setsid()
    control.send(('ready',identity,None))
    if not gate.wait(15): return
    stopped=threading.Event()
    dropped=0
    progress_sequence=0
    final_metrics=None
    progress_lock=threading.Lock()
    outgoing=queue.Queue(maxsize=128)
    def write_updates():
        try:
            while True: telemetry.send(outgoing.get())
        except (EOFError, OSError): return
    threading.Thread(target=write_updates,daemon=True,name='task-events').start()
    control_lock=threading.Lock()
    def send_control(kind,value):
        with control_lock: control.send((kind,identity,value))
    def emit(kind, value):
        nonlocal dropped, progress_sequence, final_metrics
        if kind == "metrics":
            final_metrics = value
        if kind == "progress":
            with progress_lock:
                progress_sequence += 1
                value = {**value, "progressSequence": progress_sequence}
        if kind == 'progress' and (value.get('type') in {'progress_start','progress_end','translation_summary'} or value.get('operation') in {'page_start','page_end'}):
            send_control('progress',value)
            return
        try: outgoing.put_nowait((kind,identity,value))
        except queue.Full: dropped+=1
    def record(event,data=None,*,frames=None):
        from diagnostics import safe_fields
        row={'event':event, 'data':safe_fields(data or {})}
        if frames is not None: row['frames']=frames
        emit('log',row)
    def watch():
        while not stopped.wait(1):
            try:
                owner=psutil.Process(parent_pid)
                if owner.create_time()!=parent_created: raise psutil.NoSuchProcess(parent_pid)
            except psutil.NoSuchProcess:
                if os.name!='nt': os.killpg(os.getpgrp(),signal.SIGKILL)
                os._exit(1)
            except psutil.Error: pass
            emit('heartbeat', {'droppedLogRecords':dropped})
            if stack_request.is_set():
                stack_request.clear(); emit('stack',thread_frames())
    threading.Thread(target=watch,daemon=True,name='task-watchdog').start()
    logging.basicConfig(level=logging.INFO,handlers=[SafeLogHandler(record)],force=True)
    try:
        if target is not None:  # Dependency injection for offline process tests only; never request-controlled.
            result=target(payload,cancel,emit)
        else:
            import truststore
            truststore.inject_into_ssl()
            from pdf2zh_next_service import translate_pdf_with_callbacks
            result=asyncio.run(translate_pdf_with_callbacks(payload,identity['taskId'],cancel_event=cancel,
                progress_callback=lambda e:emit('progress',e),metrics_callback=lambda m:emit('metrics',m)))
        if final_metrics is not None:
            send_control('metrics', final_metrics)
        send_control('result',result)
    except asyncio.CancelledError:
        send_control('cancelled',None)
    except BaseException as exc:
        # Error message may contain document text or credentials. Send code + frames only.
        send_control('error',{'errorType':type(exc).__name__, 'frames':exception_frames(exc)})
    finally:
        stopped.set()
        telemetry.close()
        control.close()


def recover_leases(directory):
    """Only recover matching process identity and its own session, never arbitrary PIDs."""
    blocked=False
    for path in Path(directory).glob('lease-*.json'):
        try:
            lease=json.loads(path.read_text()); pid=int(lease['pid'])
            process=psutil.Process(pid)
            if process.create_time()!=lease['created']: path.unlink(); continue
            try:
                parent=psutil.Process(lease['parentPid'])
                if parent.create_time()==lease['parentCreated']:
                    blocked=True; continue  # A second server must not kill another live server's task.
            except psutil.NoSuchProcess: pass
            if not any('multiprocessing.spawn' in arg for arg in process.cmdline()): blocked=True; continue
            if os.name=='nt':
                # Kill-on-close job should already have removed the process. Do not guess ownership.
                blocked=True; continue
            if os.getpgid(pid)!=pid: blocked=True; continue
            os.killpg(pid,signal.SIGKILL)
            try: process.wait(timeout=2)
            except psutil.TimeoutExpired: blocked=True; continue
            path.unlink()
        except psutil.NoSuchProcess:
            # A vanished group leader is insufficient proof that all descendants exited.
            if os.name != 'nt':
                try:
                    os.killpg(pid, 0)
                    blocked = True
                    continue
                except ProcessLookupError:
                    pass
                except PermissionError:
                    blocked = True
                    continue
            path.unlink(missing_ok=True)
        except (OSError,ValueError,KeyError,psutil.Error): blocked=True
    return blocked


def run_translation(payload, task_id, *, cancel_event, progress_callback, metrics_callback,
                    runtime_callback, store, identity, lease_dir=None, target=None,
                    deadlines=(10,12,15), snapshot_events=None, startup_timeout=15):
    context=mp.get_context('spawn')
    cancel=context.Event(); gate=context.Event(); stack=context.Event()
    telemetry,telemetry_send=context.Pipe(duplex=False)
    receive,send=context.Pipe(duplex=False)
    owner=psutil.Process()
    process=context.Process(target=child_entry,args=(payload,identity,cancel,gate,stack,telemetry_send,send,owner.pid,owner.create_time(),target),name='pdf2zh-translation')
    controls=queue.Queue(maxsize=4); updates=queue.Queue(maxsize=256)
    stop_readers=threading.Event()
    def read_control():
        try:
            while not stop_readers.is_set():
                item = receive.recv()
                while not stop_readers.is_set():
                    try:
                        controls.put(item, timeout=.1)
                        break
                    except queue.Full:
                        continue
        except (EOFError,OSError,ValueError): pass
    # Iterative queue reader, so long quiet tasks cannot exhaust the call stack.
    def pump_updates():
        while not stop_readers.is_set():
            try: item=telemetry.recv()
            except (OSError,EOFError,ValueError): return
            try: updates.put_nowait(item)
            except queue.Full: pass
    job=None; ready=False; terminal=None; cancellation_started=None; phase=None
    latest_metrics={}
    terminal_metrics=None
    started=time.monotonic(); last_progress=started; last_sample=0; last_snapshot=0; snapshots=0
    lease=None; cleanup_ok=False; child_ps=None
    startup_failed=False; last_sequence=0; last_stage=None; stage_started=started
    last_stage_progress=None
    def progress(value):
        nonlocal last_sequence, last_progress, last_stage, stage_started, last_stage_progress
        sequence = value.get("progressSequence", last_sequence + 1)
        if sequence <= last_sequence:
            return
        last_sequence = sequence
        stage = value.get("stage")
        if stage and stage != last_stage:
            last_stage = stage
            stage_started = time.monotonic()
        signature = (stage, value.get("stage_current"), value.get("stage_total"))
        if value.get("type") != "progress_update" or signature != last_stage_progress:
            last_progress = time.monotonic()
        if value.get("type") in {"progress_start", "progress_update", "progress_end"}:
            last_stage_progress = signature
        progress_callback(value)
    try:
        process.start(); send.close(); telemetry_send.close()
        child_ps=psutil.Process(process.pid)
        if lease_dir:
            Path(lease_dir).mkdir(parents=True,exist_ok=True)
            lease=Path(lease_dir)/f"lease-{task_id}-{identity['attempt']}.json"
            lease.write_text(json.dumps({'pid':process.pid,'created':child_ps.create_time(),'parentPid':owner.pid,'parentCreated':owner.create_time(),**identity}))
        if os.name=='nt': job=WindowsJob(process.pid)
        threading.Thread(target=read_control,daemon=True).start()
        threading.Thread(target=pump_updates,daemon=True).start()
        store.record('worker_started',{**identity,'pid':process.pid})
        child_ps.cpu_percent(); owner.cpu_percent()
        while True:
            now=time.monotonic()
            if snapshot_events and snapshot_events[0].is_set():
                snapshot_events[0].clear(); stack.set()
            if cancel_event.is_set() and cancellation_started is None:
                cancellation_started=getattr(cancel_event, 'requested_at', now); cancel.set(); stack.set(); phase='requested'
                runtime_callback({'cancelPhase':phase}); store.record('cancel_requested',identity)
            # Never block supervisor on partially transmitted child messages.
            try:
                while True:
                    kind,token,value=controls.get_nowait()
                    if token!=identity: continue
                    if kind=='ready': ready=True; gate.set(); stack.set()
                    elif kind=='progress':
                        progress(value)
                    elif kind=='metrics':
                        terminal_metrics=value; latest_metrics=value; metrics_callback(value)
                    else: terminal=(kind,value)
            except queue.Empty: pass
            for _ in range(256):
                try: kind,token,value=updates.get_nowait()
                except queue.Empty: break
                if token!=identity: continue
                if kind=='progress':
                    progress(value)
                elif kind=='metrics':
                    latest_metrics=value; metrics_callback(value)
                elif kind=='heartbeat': runtime_callback({'heartbeatAt':time.time(),**value})
                elif kind=='stack':
                    store.record('stack',identity,frames=value)
                    if snapshot_events: snapshot_events[1].set()
                elif kind=='log': store.record(value['event'],{**value['data'],**identity},frames=value.get('frames'))
            idle=now-last_progress
            if now-last_sample>=5:
                last_sample=now
                detail={'idleSeconds':round(idle,1),'stalled':idle>=60,'stageElapsedSeconds':round(now-stage_started,1)}
                try: detail.update(cpuPercent=child_ps.cpu_percent(),rssBytes=child_ps.memory_info().rss,parentCpuPercent=owner.cpu_percent(),parentRssBytes=owner.memory_info().rss)
                except psutil.Error: pass
                runtime_callback(detail); store.record('resource_sample',{**identity,**detail})
                request=latest_metrics.get('requests',{}); tokens=latest_metrics.get('tokens',{})
                store.record('request_summary',{**identity, **request, **tokens})
            if idle>=60 and snapshots<3 and (not last_snapshot or now-last_snapshot>=300):
                stack.set(); last_snapshot=now; snapshots+=1; store.record('stall_snapshot',{**identity,'idleSeconds':idle})
            if cancellation_started is not None:
                elapsed=now-cancellation_started
                desired='killing' if elapsed>=deadlines[1] else 'terminating' if elapsed>=deadlines[0] else 'requested'
                if desired!=phase:
                    phase=desired; runtime_callback({'cancelPhase':phase}); store.record('cancel_escalation',{**identity,'cancelPhase':phase})
                    _terminate(process,job,ready,kill=phase=='killing')
                if elapsed>=deadlines[2]:
                    if _tree_alive(process,job,ready): raise CleanupFailed('CANCEL_CLEANUP_FAILED')
                    cleanup_ok=True; break
            if not process.is_alive():
                # A worker exiting cannot leave helper processes owning files.
                if _tree_alive(process,job,ready):
                    if cancellation_started is None:
                        cancellation_started=now; phase='terminating'; _terminate(process,job,ready,kill=False)
                else:
                    cleanup_ok=True
                    # Give the independent terminal reader a bounded chance to deliver EOF/result.
                    delivery_deadline=time.monotonic()+.5
                    while terminal is None and time.monotonic()<delivery_deadline:
                        try:
                            kind,token,value=controls.get(timeout=.05)
                            if token != identity: continue
                            if kind=='progress': progress(value)
                            elif kind=='metrics': terminal_metrics=value; metrics_callback(value)
                            elif kind!='ready': terminal=(kind,value)
                        except queue.Empty: pass
                    break
            if not ready and now-started>startup_timeout and cancellation_started is None:
                startup_failed=True
                cancellation_started=now
                cancel.set()
                phase='terminating'
                _terminate(process,job,ready,kill=False)
            time.sleep(.025)
        process.join(timeout=.1)
        if terminal_metrics is not None:
            metrics_callback(terminal_metrics)
        store.record('worker_exited',{**identity,'exitCode':process.exitcode or 0,'elapsedSeconds':time.monotonic()-started})
        if cancel_event.is_set():
            runtime_callback({'cancelPhase':'cancelled','cancelReason':'forced' if phase in {'terminating','killing'} else 'user'})
            raise asyncio.CancelledError
        if startup_failed: raise WorkerFailed('WORKER_START_TIMEOUT')
        if not terminal: raise WorkerFailed('WORKER_EXITED_WITHOUT_RESULT')
        kind,value=terminal
        if kind=='result': return value
        if kind=='cancelled': raise asyncio.CancelledError
        store.record('worker_error',identity,frames=value.get('frames',[]))
        raise WorkerFailed('TRANSLATION_WORKER_ERROR: '+value.get('errorType','Error'))
    finally:
        stop_readers.set()
        if process.pid and not cleanup_ok:
            _terminate(process,job,ready,kill=True)
            process.join(timeout=max(0, min(.2, (cancellation_started + deadlines[2] - time.monotonic()) if cancellation_started else .2)))
            cleanup_ok=not _tree_alive(process,job,ready)
        if job: job.close()
        if lease and cleanup_ok: lease.unlink(missing_ok=True)
        receive.close(); send.close(); telemetry.close(); telemetry_send.close()
        if process.pid and not cleanup_ok: raise CleanupFailed('CANCEL_CLEANUP_FAILED')


def _terminate(process,job,ready,*,kill):
    try:
        if job: job.terminate()
        elif ready and os.name!='nt': os.killpg(process.pid,signal.SIGKILL if kill else signal.SIGTERM)
        elif process.is_alive(): process.kill() if kill else process.terminate()
    except (ProcessLookupError,psutil.NoSuchProcess): pass
    except OSError: pass  # Supervisor verifies exit and reports failure at the deadline.


def _tree_alive(process,job,ready):
    if job: return job.alive()
    if process.is_alive(): return True
    if ready and os.name!='nt':
        try:
            os.killpg(process.pid,0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        # Reparented zombies have exited and cannot hold task resources. Some
        # container init processes defer reaping them indefinitely.
        for member in psutil.process_iter(['pid', 'status']):
            try:
                if os.getpgid(member.pid) == process.pid and member.status() != psutil.STATUS_ZOMBIE:
                    return True
            except (ProcessLookupError, psutil.NoSuchProcess):
                continue
            except (PermissionError, psutil.AccessDenied):
                return True
        return False
    return False
