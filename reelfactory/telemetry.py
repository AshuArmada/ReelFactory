"""Optional per-request tracing, independent of Flask and provider payloads."""
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from time import monotonic

sink = ContextVar('reelfactory_trace', default=None)


def event(label, **details):
    callback = sink.get()
    if callback:
        # Observability must never turn a successful render into a failure.
        try:
            callback(label, details)
        except Exception:
            pass


@contextmanager
def stage(label):
    started = monotonic()
    event(label, state='started')
    try:
        yield
    except Exception as exc:
        event(label, state='failed', seconds=round(monotonic() - started, 2), error=type(exc).__name__)
        raise
    else:
        event(label, state='completed', seconds=round(monotonic() - started, 2))


def traced(label):
    def decorate(function):
        @wraps(function)
        def call(*args, **kwargs):
            with stage(label):
                return function(*args, **kwargs)
        return call
    return decorate
