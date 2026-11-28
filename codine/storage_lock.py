"""Cooperative project-state publication lock. Never nests a budget lock here."""
from contextlib import contextmanager
import errno
import fcntl
import os
import stat

from .input_settings import directory


@contextmanager
def mutation(project, *, exclusive=False):
    with directory(project / '.codine', create=True) as parent:
        fd = os.open('storage.lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600, dir_fd=parent)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_uid != os.getuid() or info.st_mode & 0o077:
                raise ValueError('Unsafe storage coordination lock.')
            try:fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BlockingIOError:
                raise OSError(errno.EBUSY, 'Project storage is busy; retry after the current storage operation.') from None
            yield
        finally:os.close(fd)
