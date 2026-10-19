"""Reuse translation cache connections within a task, closing on the owner thread."""

import threading
from contextlib import ExitStack, contextmanager


_state = threading.local()


@contextmanager
def cache_connection_scope():
    """Keep this thread's connections until the outermost task scope exits."""
    if getattr(_state, "scope", None) is not None:
        yield
        return
    with ExitStack() as cleanup:
        _state.scope = (set(), cleanup)
        try:
            yield
        finally:
            del _state.scope


@contextmanager
def cache_connection(database):
    scope = getattr(_state, "scope", None)
    if scope is None:
        # Standalone calls have no task owner to release their connection later.
        with database.connection_context():
            yield
    else:
        databases, cleanup = scope
        if database not in databases:
            databases.add(database)
            cleanup.callback(database.close)
        yield
