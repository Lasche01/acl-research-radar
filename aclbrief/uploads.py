"""Local browser uploads staged on disk; tokens never expose server paths."""
from contextlib import contextmanager
from pathlib import Path
import tempfile
import threading
import time
import uuid

CHUNK = 1024 * 1024

class UploadStore:
    def __init__(self):
        self.directory = tempfile.TemporaryDirectory(prefix='acl-browser-')
        self.items = {}
        self.lock = threading.Lock()

    def add(self, stream, size, name):
        if size <= 0: raise ValueError('빈 파일은 가져올 수 없습니다.')
        token = uuid.uuid4().hex
        path = Path(self.directory.name) / token
        try:
            with path.open('wb') as out:
                remaining = size
                while remaining:
                    chunk = stream.read(min(CHUNK, remaining))
                    if not chunk: raise ValueError('파일 전송이 중단되었습니다. 다시 가져오세요.')
                    out.write(chunk); remaining -= len(chunk)
            with self.lock:
                # Abandoned selections expire; claimed files are no longer in this map.
                for key, item in list(self.items.items()):
                    if time.monotonic() - item['created'] > 86400:
                        item['path'].unlink(missing_ok=True); self.items.pop(key)
                self.items[token] = {'path':path,'name':str(name)[:300] or 'document','created':time.monotonic()}
            return {'upload_id':token,'name':self.items[token]['name'],'bytes':size}
        except BaseException:
            path.unlink(missing_ok=True)
            raise

    @contextmanager
    def claim(self, files):
        tokens=[f['upload_id'] for f in files if isinstance(f,dict) and 'upload_id' in f]
        with self.lock:
            if len(tokens)!=len(set(tokens)) or any(t not in self.items for t in tokens):
                raise ValueError('임시 파일이 없거나 이미 처리되었습니다. 파일을 다시 선택하세요.')
            items={t:self.items.pop(t) for t in tokens}
        try:yield items
        finally:
            for item in items.values():item['path'].unlink(missing_ok=True)

    def close(self):self.directory.cleanup()
