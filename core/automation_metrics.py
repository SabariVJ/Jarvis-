"""Nested execution timings. No contact names, content or credentials in spans."""
from contextlib import contextmanager
import contextvars
from functools import wraps
import time

_parent = contextvars.ContextVar('automation_metric_parent', default=None)


@contextmanager
def measure(phase, context=None, **attributes):
    from core.automation_runtime import get_runtime
    rt = get_runtime()
    context = context or getattr(rt._local, 'context', None) or rt.context()
    parent = _parent.get()
    token = _parent.set(phase)
    started = time.perf_counter()
    try:
        yield attributes
    finally:
        _parent.reset(token)
        rt.trace('performance_span', context, phase=phase, parent=parent,
                 seconds=round(time.perf_counter() - started, 6), **attributes)


def timed(phase):
    def decorate(fn):
        @wraps(fn)
        def wrapped(*args, **kwargs):
            with measure(phase):
                return fn(*args, **kwargs)
        return wrapped
    return decorate


async def timed_stream(stream, context=None, runtime=None):
    """Time model iteration waits, excluding time spent speaking between yields."""
    elapsed = 0
    iterator = stream.__aiter__()
    try:
        while True:
            started = time.perf_counter()
            try:
                value = await anext(iterator)
            except StopAsyncIteration:
                break
            finally:
                elapsed += time.perf_counter() - started
            yield value
    finally:
        if runtime:
            runtime.trace('performance_span', context, phase='model_planning', parent=None,
                          seconds=round(elapsed, 6), excludes='speech synthesis and tool execution')
