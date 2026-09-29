"""One reentrant maintenance lease across threads and backend processes.

Save edits, restores, settings writes and dashboard start/restart commands share
this lease. External supervisors still need to be stopped by the operator; a
filesystem lock cannot prevent another program from starting the game.
"""
from contextlib import contextmanager
from functools import wraps
import fcntl
import os
import threading
import tempfile

_lock = threading.RLock()
_local = threading.local()
LOCK_PATH = os.environ.get("MAINTENANCE_LOCK_PATH", os.path.join(tempfile.gettempdir(), f"paldash-maintenance-{os.getuid()}.lock"))


@contextmanager
def lease():
    with _lock:
        if getattr(_local, 'depth', 0):
            _local.depth += 1
            try:
                yield
            finally:
                _local.depth -= 1
            return
        fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            _local.depth = 1
            yield
        finally:
            _local.depth = 0
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)


def serialized(fn):
    @wraps(fn)
    def call(*args, **kwargs):
        with lease():
            return fn(*args, **kwargs)
    return call


@contextmanager
def stopped_writes():
    previous = getattr(_local, 'check_commit', None)
    from safety import assert_writable
    _local.check_commit = assert_writable
    try:
        yield
    finally:
        _local.check_commit = previous


def check_commit():
    check = getattr(_local, 'check_commit', None)
    if check is not None:
        check()
