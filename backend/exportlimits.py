"""One bounded export workload across threads/processes and legacy exporters."""
import fcntl
import os
from pathlib import Path
import re
import shutil
import tempfile
import threading
import time

MAX_BYTES = int(os.environ.get('EXPORT_MAX_BYTES', str(2 * 1024**3)))
RETENTION_DAYS = int(os.environ.get('EXPORT_RETENTION_DAYS', '7'))
LOCK_PATH = os.environ.get('EXPORT_LOCK_PATH', os.path.join(tempfile.gettempdir(), f'paldash-exports-{os.getuid()}.lock'))


class ExportLock:
    def __init__(self):
        self.lock = threading.RLock()
        self.local = threading.local()

    def acquire(self, blocking=False):
        if not self.lock.acquire(blocking=blocking):
            return False
        depth = getattr(self.local, 'depth', 0)
        if depth:
            self.local.depth += 1
            return True
        fd = None
        try:
            fd = os.open(LOCK_PATH, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
            fcntl.flock(fd, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
            self.local.fd, self.local.depth = fd, 1
            return True
        except BlockingIOError:
            if fd is not None:
                os.close(fd)
            self.lock.release()
            return False
        except BaseException:
            if fd is not None:
                os.close(fd)
            self.lock.release()
            raise

    def release(self):
        self.local.depth -= 1
        if self.local.depth == 0:
            fcntl.flock(self.local.fd, fcntl.LOCK_UN)
            os.close(self.local.fd)
        self.lock.release()


RUNNING = ExportLock()


def check_storage(base: str, reserve: int = 0) -> None:
    root = Path(base)
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    if any(p.is_symlink() for p in (root, *root.parents)):
        raise OSError('Export storage must not use symlinks')
    cutoff = time.time() - RETENTION_DAYS * 86400
    # Only this legacy feature's generated paths; self/server manage their
    # account/preview retention separately. Never prune arbitrary directories.
    for entry in root.iterdir():
        if entry.is_symlink() or not re.fullmatch(r'world-[0-9a-f]{8}-to-[0-9a-f]{8}-\d{8}-\d{6}(?:-\d+)?(?:\.tar\.gz)?', entry.name):
            continue
        if entry.stat().st_mtime < cutoff:
            if entry.is_dir(): shutil.rmtree(entry)
            else: entry.unlink()
    used = 0
    for folder, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = [d for d in dirs if not Path(folder, d).is_symlink()]
        used += sum(Path(folder, f).stat().st_size for f in files if not Path(folder, f).is_symlink())
    if used + reserve > MAX_BYTES or shutil.disk_usage(root).free < reserve:
        raise OSError('Export storage quota or free-space reserve would be exceeded; remove old exports first')
