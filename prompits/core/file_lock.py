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
