"""Reuse reads within one render, never across users or automatic polls."""

from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from copy import deepcopy
from functools import wraps


class RenderReads:
    """A request-scoped facade; all writes keep the real storage safeguards."""

    def __init__(self, database):
        self._database = database
        self._reads = ContextVar("compcoach_render_reads", default=None)

    @contextmanager
    def snapshot(self):
        if self._reads.get() is not None:
            yield
            return
        token = self._reads.set({})
        try:
            yield
        finally:
            self._reads.reset(token)

    def invalidate(self):
        cache = self._reads.get()
        if cache is not None:
            cache.clear()

    def __getattr__(self, name):
        method = getattr(self._database, name)
        if not callable(method) or name.startswith("_"):
            return method
        # Revision polling must always reach storage. Authentication remains
        # uncached as well; no cached role or token survives a rotation.
        read = name.startswith(("get_", "list_")) and name != "get_meet_revision"

        @wraps(method)
        def call(*args, **kwargs):
            cache = self._reads.get()
            if read and cache is not None:
                key = (name, repr(args), repr(sorted(kwargs.items())))
                if key not in cache:
                    cache[key] = method(*args, **kwargs)
                # UI helpers occasionally annotate returned dictionaries.
                return deepcopy(cache[key])
            if not read and name not in {"get_meet_revision", "authorize", "authorize_meet"}:
                self.invalidate()
            try:
                return method(*args, **kwargs)
            finally:
                if not read and name not in {"get_meet_revision", "authorize", "authorize_meet"}:
                    self.invalidate()

        return call
