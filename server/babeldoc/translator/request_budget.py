"""A paragraph's request allowance survives batch-to-single fallback."""

import threading


class TranslationBudgetExhausted(Exception):
    pass


class ParagraphRequestBudget:
    def __init__(self):
        self.lock = threading.Lock()
        self.attempts = {}
        self.waits = {}

    def request(self, paragraphs):
        return RequestBudget(self, tuple(dict.fromkeys(id(p) for p in paragraphs)))


class RequestBudget:
    def __init__(self, owner, keys):
        self.owner, self.keys = owner, keys
        self.attempts = 0

    def available(self, delay=0):
        with self.owner.lock:
            return self.attempts < 2 and all(
                self.owner.attempts.get(key, 0) < 3
                and self.owner.waits.get(key, 0) + delay <= 30
                for key in self.keys
            )

    def reserve(self):
        with self.owner.lock:
            if self.attempts >= 2 or any(self.owner.attempts.get(key, 0) >= 3 for key in self.keys):
                raise TranslationBudgetExhausted()
            self.attempts += 1
            for key in self.keys:
                self.owner.attempts[key] = self.owner.attempts.get(key, 0) + 1

    def reserve_wait(self, seconds):
        with self.owner.lock:
            if any(self.owner.waits.get(key, 0) + seconds > 30 for key in self.keys):
                raise TranslationBudgetExhausted()
            for key in self.keys:
                self.owner.waits[key] = self.owner.waits.get(key, 0) + seconds


def submit_translation(executor, fn, *args, collector=None, fallback=False, **kwargs):
    """Track queued work separately from HTTP requests and retry backoff."""
    if collector is None:
        return executor.submit(fn, *args, **kwargs)
    collector.activity_changed("queued", 1)
    if fallback:
        collector.activity_changed("fallbackPending", 1)
    lock = threading.Lock()
    started = False
    finished = False

    def finish():
        nonlocal finished
        with lock:
            if finished:
                return
            finished = True
            if not started:
                collector.activity_changed("queued", -1)
            if fallback:
                collector.activity_changed("fallbackPending", -1)

    def run(*args, **kwargs):
        nonlocal started
        with lock:
            started = True
            collector.activity_changed("queued", -1)
        try:
            return fn(*args, **kwargs)
        finally:
            finish()

    try:
        future = executor.submit(run, *args, **kwargs)
        if hasattr(future, "add_done_callback"):
            future.add_done_callback(lambda f: finish() if f.cancelled() else None)
        return future
    except BaseException:
        finish()
        raise
