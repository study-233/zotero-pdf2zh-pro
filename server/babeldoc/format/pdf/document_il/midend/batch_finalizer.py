"""Cache completed translations after fallback workers and optional review have finished."""

import json
import logging
import threading
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ParagraphOutput:
    source: str
    output: str


class BatchFinalizer:
    def __init__(self, translator, prompt, paragraphs, sources, params, *, batch=True):
        self.translator = translator
        self.prompt = prompt
        self.paragraphs = tuple(paragraphs)
        self.sources = tuple(sources)
        self.params = dict(params)
        self.batch = batch
        self.lock = threading.RLock()
        self.outputs = {}
        self.pending = set()
        self.sealed = False
        self.committed = False
        self._attempted_commit = False

    def record(self, index, result):
        if not isinstance(result, ParagraphOutput) or result.source != self.sources[index]:
            return
        with self.lock:
            self.outputs[index] = result

    def add_future(self, index, future):
        with self.lock:
            if self.sealed:
                raise RuntimeError("batch_already_sealed")
            if isinstance(future, ParagraphOutput) or future is None:
                # Synchronous executors in callers/tests return the immutable result directly.
                self.record(index, future)
                return
            self.pending.add(index)

        def completed(done):
            try:
                if not done.cancelled():
                    self.record(index, done.result())
            except BaseException as exc:
                # Worker cancellation/failure prevents committing the whole batch.
                logger.debug("fallback cache result unavailable: error_type=%s", type(exc).__name__)
            finally:
                with self.lock:
                    self.pending.discard(index)

        future.add_done_callback(completed)

    def seal(self):
        with self.lock:
            self.sealed = True

    def finalize(self, review, check_cancelled):
        check_cancelled()
        with self.lock:
            if (not self.sealed or self.pending or self._attempted_commit
                    or len(self.outputs) != len(self.sources)):
                return False
            values = []
            for index, paragraph in enumerate(self.paragraphs):
                output = review.final_output(paragraph) if review is not None else self.outputs[index].output
                if output is None:
                    # A previously cached draft may now have a confirmed quality failure.
                    if not getattr(self.translator, "ignore_cache", False) and not self.params.get("ignore_cache", False):
                        check_cancelled()
                        try:
                            self.translator.cache.delete(self.prompt)
                        except Exception as exc:
                            check_cancelled()
                            logger.debug("rejected translation cache removal failed: error_type=%s", type(exc).__name__)
                    return False
                values.append(output)
            value = (json.dumps([{"id": index, "output": output} for index, output in enumerate(values)],
                                ensure_ascii=False) if self.batch else values[0])
            self._attempted_commit = True
        check_cancelled()
        commit = getattr(self.translator, "_commit_validated_cache", None)
        if commit is None:
            return False
        try:
            self.committed = bool(commit(self.prompt, value, rate_limit_params=self.params))
        except Exception as exc:
            check_cancelled()
            logger.warning("completed translation cache write failed: error_type=%s", type(exc).__name__)
        return self.committed
