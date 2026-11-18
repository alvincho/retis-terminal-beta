"""An exclusive lock on a lock file for the length of a `with` block, the same on macOS, Linux and Windows.

Another process (or another open of the same file) that asks for the lock waits until it is released.
Leaving the block releases it; so does the process ending.
"""
import contextlib
import os
import time


@contextlib.contextmanager
def exclusive(path):
    with open(path, 'a+b') as handle:
        if os.name == 'nt':
            import msvcrt
            # Windows locks byte ranges: everyone locks the first byte, which may lie past the end of an empty file.
            handle.seek(0)
            while True:
                try:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    time.sleep(0.05)
            try:
                yield handle
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                yield handle
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def try_exclusive(fd):
    """Take an exclusive lock on the open file `fd` without waiting, the same on macOS, Linux and Windows.

    Raises BlockingIOError while another process (or another open of the file) holds it. The lock lasts until
    unlock(fd), or until the file is closed (the process ending included): for locks held for a lifetime."""
    if os.name == 'nt':
        import msvcrt
        # Windows locks byte ranges: every holder locks the first byte, which may lie past the end of an empty file.
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        except OSError as error:
            raise BlockingIOError(*error.args) from error
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)


def unlock(fd):
    """Release a lock try_exclusive took on `fd`."""
    if os.name == 'nt':
        import msvcrt
        os.lseek(fd, 0, os.SEEK_SET)
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    else:
        import fcntl
        fcntl.flock(fd, fcntl.LOCK_UN)
