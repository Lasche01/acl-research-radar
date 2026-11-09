"""One collector/reanalysis task per data directory, including separate CLI processes."""
import os
import threading


class TaskLock:
    def __init__(self, directory):
        self.path = directory / 'task.lock'
        self.thread_lock = threading.Lock()
        self.file = None

    def acquire(self, blocking=False):
        if not self.thread_lock.acquire(blocking=blocking):
            return False
        try:
            f = open(self.path, 'a+b')
            f.seek(0, 2)
            if f.tell() == 0:
                f.write(b'0'); f.flush()
            f.seek(0)
            try:
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                f.close(); self.thread_lock.release()
                return False
            self.file = f
            return True
        except Exception:
            self.thread_lock.release()
            raise

    def release(self):
        try:
            if self.file:
                self.file.seek(0)
                if os.name == 'nt':
                    import msvcrt
                    msvcrt.locking(self.file.fileno(), msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(self.file, fcntl.LOCK_UN)
                self.file.close(); self.file = None
        finally:
            self.thread_lock.release()
