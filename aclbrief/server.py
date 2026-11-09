import base64
import json
import mimetypes
import threading
import traceback
import urllib.parse
import uuid
import tempfile
from .uploads import UploadStore
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from .collect import extract_file, page_payload

STATIC = Path(__file__).parent / 'static'


class AppServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, port, service):
        super().__init__(('127.0.0.1', port), Handler)
        self.service = service
        self.uploads = UploadStore()
        self.jobs = {}
        self.job_lock = threading.Lock()

    def server_close(self):
        super().server_close()
        self.uploads.close()

    def background(self, fn):
        if not self.job_lock.acquire(blocking=False):
            raise ValueError('다른 작업이 진행 중입니다. 완료 후 실행하세요.')
        jid = uuid.uuid4().hex[:12]
        self.jobs[jid] = {'id': jid, 'status': 'running'}

        def work():
            try:
                self.jobs[jid] = {'id': jid, 'status': 'completed', 'result': fn()}
            except Exception as exc:
                self.jobs[jid] = {'id': jid, 'status': 'failed', 'error': str(exc)[:1000]}
            finally:
                self.job_lock.release()
        threading.Thread(target=work, daemon=True).start()
        return {'job_id': jid}


class Handler(BaseHTTPRequestHandler):
    server_version = 'ACLBriefing/0.1'

    def log_message(self, fmt, *args):
        # URLs may contain user-supplied data; keep access logs off by default.
        pass

    def guard(self, mutation=False, binary=False):
        allowed = {f'127.0.0.1:{self.server.server_port}', f'localhost:{self.server.server_port}'}
        if self.headers.get('Host', '') not in allowed:
            raise PermissionError('로컬 주소에서만 사용할 수 있습니다.')
        origin = self.headers.get('Origin')
        if origin and origin not in {f'http://{a}' for a in allowed}:
            raise PermissionError('다른 사이트의 요청을 허용하지 않습니다.')
        if mutation and self.headers.get('Content-Type', '').split(';')[0] != ('application/octet-stream' if binary else 'application/json'):
            raise ValueError('요청 데이터 형식이 올바르지 않습니다.')

    def send(self, value, status=200, ctype='application/json; charset=utf-8', filename=None):
        raw = value if isinstance(value, bytes) else (json.dumps(value, ensure_ascii=False).encode() if ctype.startswith('application/json') else str(value).encode())
        self.send_response(status)
        self.send_header('Content-Type', ctype)
        self.send_header('Content-Length', str(len(raw)))
        self.send_header('Cache-Control', 'no-store')
        self.send_header('X-Content-Type-Options', 'nosniff')
        self.send_header('Referrer-Policy', 'no-referrer')
        self.send_header('Content-Security-Policy', "default-src 'self'; style-src 'self' 'unsafe-inline'; script-src 'self'; img-src 'self' data:; connect-src 'self'; object-src 'none'; frame-ancestors 'none'; base-uri 'none'")
        if filename:
            self.send_header('Content-Disposition', f'attachment; filename="{filename}"')
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self):
        try:
            self.guard()
            url = urllib.parse.urlsplit(self.path)
            path = url.path
            if path == '/api/notice':
                return self.send(self.server.service.notice_detail(urllib.parse.parse_qs(urllib.parse.urlsplit(self.path).query).get('id',[''])[0]))
            if path == '/api/state':
                state = self.server.service.state()
                state['jobs'] = list(self.server.jobs.values())[-15:]
                return self.send(state)
            if path == '/api/versions':
                nid = urllib.parse.parse_qs(url.query).get('id', [''])[0]
                return self.send(self.server.service.store.versions(nid))
            if path == '/api/export':
                return self.send(self.server.service.store.export(), filename='acl-briefing-backup.json')
            if path == '/api/briefing':
                return self.send(self.server.service.briefing(), ctype='text/markdown; charset=utf-8', filename='acl-briefing.md')
            if path == '/api/evaluation':
                return self.send(self.server.service.evaluate())
            if path == '/api/diagnostics':
                return self.send(self.server.service.diagnostics(), filename='source-diagnostics.json')
            names = {'/': 'index.html', '/app.js': 'app.js', '/style.css': 'style.css'}
            if path not in names:
                return self.send({'error': '페이지를 찾을 수 없습니다.'}, 404)
            file = STATIC / names[path]
            ctype = {'html': 'text/html', 'js': 'text/javascript', 'css': 'text/css'}[file.suffix[1:]]
            return self.send(file.read_bytes(), ctype=ctype + '; charset=utf-8')
        except PermissionError as exc:
            self.send({'error': str(exc)}, 403)
        except ValueError as exc:
            self.send({'error': str(exc)}, 400)
        except Exception:
            traceback.print_exc()
            self.send({'error': '처리 중 오류가 발생했습니다. 실행 창을 확인하세요.'}, 500)

    def do_POST(self):
        try:
            request_url=urllib.parse.urlsplit(self.path)
            binary=request_url.path=='/api/upload'
            self.guard(mutation=True,binary=binary)
            size=int(self.headers.get('Content-Length',0))
            if size<=0:raise ValueError('요청 데이터가 비어 있습니다.')
            if binary:
                name=urllib.parse.parse_qs(request_url.query).get('name',['document'])[0]
                return self.send(self.server.uploads.add(self.rfile,size,name),201)
            with tempfile.SpooledTemporaryFile(max_size=8*1024*1024) as body:
                remaining=size
                while remaining:
                    chunk=self.rfile.read(min(1024*1024,remaining))
                    if not chunk:raise ValueError('요청 전송이 중단되었습니다.')
                    body.write(chunk);remaining-=len(chunk)
                body.seek(0);data=json.load(body)
            if not isinstance(data, dict):
                raise ValueError('요청은 JSON 객체여야 합니다.')
            svc = self.server.service
            if self.path == '/api/notices':
                return self.send(svc.ingest(data))
            if self.path == '/api/import-capture':
                files=data.get('files',[])
                if not isinstance(files,list):raise ValueError('파일 목록이 올바르지 않습니다.')
                with self.server.uploads.claim(files) as local_files:
                    return self.send(svc.import_capture(data,local_files))
            if self.path == '/api/collect-stop':
                return self.send(svc.cancel_collection())
            if self.path == '/api/collect-alternatives':
                return self.send(self.server.background(lambda:svc.collect_alternatives(data.get('source_id'))),202)
            if self.path == '/api/import-url':
                def task():
                    payload = page_payload(data.get('url', ''), svc.fetcher, title=data.get('title', ''), scope=data.get('scope', 'notice'), staged=True)
                    payload['unit'] = data.get('unit', '')
                    return svc.ingest(payload)
                return self.send(self.server.background(task), 202)
            if self.path == '/api/import-file':
                blob = base64.b64decode(data.get('base64', ''), validate=True)
                text, warnings = extract_file(blob, data.get('filename', ''))
                return self.send(svc.ingest({'title': data.get('title') or data['filename'], 'url': data.get('url', ''),
                     'text': text, 'warnings': warnings, 'scope': data.get('scope', 'rfp'), 'unit': data.get('unit', ''),
                     'source_id': 'file', 'attachments': [{'name': data['filename'], 'status': 'extracted'}]}))
            if self.path == '/api/collect':
                ids = data.get('source_ids')
                if ids is not None and (not isinstance(ids, list) or not all(isinstance(x, str) for x in ids)):
                    raise ValueError('수집 경로 목록이 올바르지 않습니다.')
                return self.send(self.server.background(lambda: svc.collect(ids)), 202)
            if self.path == '/api/reanalyze':
                return self.send(self.server.background(svc.reanalyze), 202)
            if self.path == '/api/demo':
                return self.send(svc.demo())
            if self.path == '/api/feedback':
                svc.store.feedback(data['id'], data['version'], data['verdict'], data.get('reason', ''), data.get('split', 'train'))
                return self.send({'saved': True})
            if self.path == '/api/profile':
                return self.send(svc.save_profile(data))
            if self.path == '/api/evidence':
                return self.send(svc.add_evidence(data))
            if self.path == '/api/source':
                svc.store.update_source(data['id'], data)
                return self.send({'saved': True})
            if self.path == '/api/sources/enabled':
                return self.send(svc.store.set_sources_enabled(data.get('enabled')))
            self.send({'error': 'API를 찾을 수 없습니다.'}, 404)
        except PermissionError as exc:
            self.send({'error': str(exc)}, 403)
        except (ValueError, KeyError, TypeError) as exc:
            self.send({'error': str(exc)}, 400)
        except Exception:
            traceback.print_exc()
            self.send({'error': '처리 중 오류가 발생했습니다. 실행 창을 확인하세요.'}, 500)
