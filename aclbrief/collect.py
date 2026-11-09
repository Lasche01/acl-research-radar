"""Conservative public-web ingestion. Empty/dynamic pages never mean zero notices."""
import base64
import gzip
import http.cookiejar
import io
import tempfile
import shutil
from contextlib import contextmanager
import ipaddress
import re
import os
import ssl
import socket
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from .robots import RobotsPolicy
import zipfile
from html.parser import HTMLParser
from pathlib import PurePosixPath
from xml.etree import ElementTree
from .diagnostics import classify_error
from .notices import document_check, has_record_id

CHUNK_BYTES = 1024 * 1024


def check_expansion(size, compressed_size):
    # Structural protection, rather than rejecting normally sized research documents.
    ratio = float(os.environ.get('ACL_MAX_EXPANSION_RATIO', '1000'))
    if size > 64 * 1024 * 1024 and size > max(1, compressed_size) * ratio:
        raise ExtractionError('비정상적으로 높은 압축 해제 비율입니다. 문서를 직접 압축 해제해 입력하세요.')


@contextmanager
def document_stream(data):
    if isinstance(data, (str, os.PathLike)):
        with open(data, 'rb') as f: yield f
    elif hasattr(data, 'read'):
        data.seek(0)
        yield data
    else:
        with io.BytesIO(data) as f: yield f


def document_hash(data):
    import hashlib
    digest = hashlib.sha256()
    with document_stream(data) as f:
        while True:
            chunk = f.read(CHUNK_BYTES)
            if not chunk: break
            digest.update(chunk)
        f.seek(0)
    return digest.hexdigest()


class ExtractionError(ValueError):
    pass


class FetchError(ExtractionError):
    def __init__(self, message, code_name='unknown', stage='network'):
        super().__init__(message)
        self.code_name, self.stage = code_name, stage


def certificate_context():
    try:
        import truststore
    except ImportError:
        return ssl.create_default_context(), 'python_default'
    return truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT), 'system_truststore'


def public_url(url):
    u = urllib.parse.urlsplit(url)
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('공개 http/https 주소만 수집할 수 있습니다.')
    addresses = socket.getaddrinfo(u.hostname, u.port or (443 if u.scheme == 'https' else 80), type=socket.SOCK_STREAM)
    if not addresses or any(not ipaddress.ip_address(a[4][0]).is_global for a in addresses):
        raise ValueError('내부망·로컬 주소는 수집할 수 없습니다.')
    return url


class RedirectGuard(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        public_url(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _owned_spool:
    def __init__(self):
        self.file = tempfile.SpooledTemporaryFile(max_size=8*1024*1024)
    def __enter__(self): return self
    def __exit__(self, *args):
        if self.file is not None: self.file.close()
    def __getattr__(self, key): return getattr(self.file, key)
    def detach(self):
        f, self.file = self.file, None
        return f


class Fetcher:
    def __init__(self, delay=0.8, timeout=None, attempts=2, respect_robots=None):
        self.respect_robots=(os.environ.get('ACL_RESPECT_ROBOTS','0')=='1') if respect_robots is None else bool(respect_robots)
        self.user_agent=os.environ.get('ACL_USER_AGENT','Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/139.0.0.0 Safari/537.36')
        if any(c in self.user_agent for c in ('\r','\n')):raise ValueError('User-Agent에 줄바꿈을 넣을 수 없습니다.')
        self.cancel_event = threading.Event()
        self.delay = delay
        self.last = {}
        self.robots = {}
        self.robots_checked = {}
        self.lock = threading.Lock()
        self.timeout = timeout or float(os.environ.get('ACL_FETCH_TIMEOUT', '35'))
        self.attempts = max(1, min(3, attempts))
        context, self.certificate_backend = certificate_context()
        self.opener = urllib.request.build_opener(RedirectGuard(), urllib.request.HTTPSHandler(context=context),
                                                  urllib.request.HTTPCookieProcessor(http.cookiejar.CookieJar()))

    def fetch(self, url, robots=True, _document=False):
        if self.cancel_event.is_set(): raise InterruptedError('수집을 중지했습니다.')
        public_url(url)
        u = urllib.parse.urlsplit(url)
        root = f'{u.scheme}://{u.netloc}'
        if robots and self.respect_robots:
            if root not in self.robots or time.monotonic()-self.robots_checked.get(root, time.monotonic())>3600:
                parser = None
                try:
                    data, _, _ = self.fetch(root + '/robots.txt', robots=False)
                    if len(data)>512000:raise ValueError('robots.txt 크기 제한 초과')
                    parser = RobotsPolicy(data.decode('utf-8', errors='replace'))
                except urllib.error.HTTPError as exc:
                    if exc.code in (404, 410):
                        parser = RobotsPolicy('')
                    else:
                        info = classify_error(exc)
                        raise FetchError(f'robots.txt 확인 실패({exc.code}). 수집 보류: {exc}', info['code'], 'robots') from exc
                except Exception as exc:
                    info = classify_error(exc)
                    raise FetchError('robots.txt를 확인하지 못해 수집을 보류했습니다: ' + str(exc)[:300], info['code'], 'robots') from exc
                self.robots[root] = parser
                self.robots_checked[root] = time.monotonic()
            if not self.robots[root].can_fetch(self.user_agent, url):
                decision = self.robots[root].decision(self.user_agent, url) if isinstance(self.robots[root], RobotsPolicy) else {}
                exc = FetchError('robots.txt가 이 경로의 자동 수집을 허용하지 않습니다.', 'robots_disallowed', 'robots')
                exc.policy = {**decision, 'robots_url': root+'/robots.txt', 'target_url': url}
                raise exc
        with self.lock:
            wait = self.delay - (time.monotonic() - self.last.get(root, 0))
            if wait > 0:
                time.sleep(wait)
            self.last[root] = time.monotonic()
        request = urllib.request.Request(url, headers={'User-Agent': self.user_agent, 'Accept-Encoding': 'identity'})
        return self._open_with_retry(request, read_body=True, to_file=_document)

    def download(self, url):
        """Return a seekable temporary file; caller closes it after extraction."""
        return self.fetch(url, _document=True)

    def _open_with_retry(self, request, read_body=False, to_file=False):
        for attempt in range(self.attempts):
            try:
                response = self.opener.open(request, timeout=self.timeout)
                if not read_body:
                    return response
                with response, tempfile.SpooledTemporaryFile(max_size=8*1024*1024) if not to_file else _owned_spool() as buffer:
                    stream = gzip.GzipFile(fileobj=response) if response.headers.get('Content-Encoding') == 'gzip' else response
                    while True:
                        if self.cancel_event.is_set(): raise InterruptedError('수집을 중지했습니다.')
                        chunk = stream.read(CHUNK_BYTES)
                        if not chunk: break
                        buffer.write(chunk)
                    received=buffer.tell()
                    expected=response.headers.get('Content-Length','')
                    if response.headers.get('Content-Encoding','identity')=='identity' and str(expected).isdigit() and received!=int(expected):
                        raise ExtractionError('파일 전송이 완료되지 않았습니다. 일부만 받은 문서는 다시 수집해야 합니다.')
                    buffer.seek(0)
                    headers, resolved = dict(response.headers), response.geturl()
                    if to_file:
                        return buffer.detach(), headers, resolved
                    return buffer.read(), headers, resolved
            except Exception as exc:
                info = classify_error(exc)
                if not info['retryable'] or attempt + 1 == self.attempts:
                    raise
                time.sleep(min(1.5 * (attempt + 1), 3))


class PageParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.skip = 0
        self.title_depth = 0
        self.title = []
        self.text = []
        self.links = []
        self.current_link = None
        self.has_images = False
        self.rows = []
        self.current_row = None
        self.table_row = None
        self.current_cell = None
        self.table_headers = []
        self.metadata = []
        self.definition = None
        self.definition_label = ''

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'noscript'):
            self.skip += 1
        if tag == 'table':self.table_headers=[]
        if not self.skip and tag in ('dt','dd'):self.definition={'tag':tag,'text':''}
        if not self.skip and tag=='tr':self.table_row={'cells':[],'links':[],'text':[]}
        if not self.skip and tag in ('td','th') and self.table_row is not None:
            self.current_cell={'text':'','label':attrs.get('data-label') or attrs.get('data-title') or '', 'header':tag=='th'}
        if tag == 'img' and self.current_cell is not None:self.current_cell['text']+=' '+attrs.get('alt','')
        if tag == 'title':
            self.title_depth += 1
        if tag == 'img':
            self.has_images = True
        if not self.skip:
            if tag in ('p', 'div', 'br', 'li', 'tr', 'h1', 'h2', 'h3', 'section', 'article', 'td','th','dt','dd'):
                self.text.append('\n')
            if tag == 'a':
                self.current_link = {'href': attrs.get('href', ''), 'onclick': attrs.get('onclick', ''), 'text': ''}
                if self.table_row is not None:self.table_row['links'].append(self.current_link)
            if tag == 'tr' and attrs.get('onclick'):
                self.current_row = {'href': '', 'onclick': attrs['onclick'], 'text': '', 'row_title': ''}
            if self.current_row and tag == 'td' and attrs.get('title'):
                self.current_row['row_title'] = attrs['title']

    def handle_endtag(self, tag):
        if tag in ('dt','dd') and self.definition is not None:
            value=self.definition['text'].strip()
            if tag=='dt':self.definition_label=value
            elif self.definition_label:self.metadata.append({'label':self.definition_label,'text':value})
            self.definition=None
        if tag=='dl':self.definition_label=''
        if tag in ('td','th') and self.current_cell is not None and self.table_row is not None:
            self.table_row['cells'].append(self.current_cell);self.current_cell=None
        if tag=='tr' and self.table_row is not None:
            cells=self.table_row['cells']
            for i,cell in enumerate(cells[:-1]):
                if cell['header'] and not cells[i+1]['header']:
                    self.metadata.append({'label':cell['text'].strip(),'text':cells[i+1]['text'].strip()})
            if cells and all(c['header'] for c in cells):self.table_headers=[c['text'].strip() for c in cells]
            else:
                fields=[{'label':c['label'] or (self.table_headers[i] if i<len(self.table_headers) else ''),'text':c['text'].strip()} for i,c in enumerate(cells)]
                listing={'fields':fields,'text':' '.join(self.table_row['text']).strip()}
                for link in self.table_row['links']:link['listing']=listing
                if self.current_row is not None:self.current_row['listing']=listing
            self.table_row=None
        if tag in ('script', 'style', 'nav', 'footer', 'header', 'noscript'):
            self.skip = max(0, self.skip - 1)
        if tag == 'title':
            self.title_depth = max(0, self.title_depth - 1)
        if tag == 'a' and self.current_link:
            self.links.append(self.current_link)
            self.current_link = None
        if tag == 'tr' and self.current_row:
            self.current_row['text'] = self.current_row['row_title'] or self.current_row['text']
            self.rows.append(self.current_row)
            self.current_row = None

    def handle_data(self, data):
        if self.title_depth:
            self.title.append(data)
        if not self.skip and not self.title_depth:
            if self.definition is not None:self.definition['text']+=data
            if self.current_cell is not None:self.current_cell['text']+=data
            if self.table_row is not None:self.table_row['text'].append(data)
            self.text.append(data)
            if self.current_link:
                self.current_link['text'] += data
            if self.current_row:
                self.current_row['text'] += data

    def result(self):
        text = '\n'.join(re.sub(r'\s+', ' ', x).strip() for x in ''.join(self.text).splitlines() if x.strip())
        return {'text': text, 'title': ' '.join(self.title).strip(), 'links': self.links + self.rows, 'has_images': self.has_images,'metadata':self.metadata}


def decode_text(data, headers=None):
    headers = {k.lower(): v for k, v in (headers or {}).items()}
    hint = re.search(r'charset\s*=\s*["\']?([\w-]+)', headers.get('content-type', ''), re.I)
    meta = re.search(br'charset\s*=\s*["\']?([\w-]+)', data[:3000], re.I)
    encodings = [hint.group(1) if hint else '', meta.group(1).decode() if meta else '', 'utf-8-sig', 'cp949', 'utf-16']
    for enc in dict.fromkeys(encodings):
        if not enc: continue
        try: return data.decode(enc)
        except (UnicodeDecodeError, LookupError): pass
    return data.decode('utf-8', errors='replace')


def extract_file(data, filename, headers=None, _depth=0):
    with document_stream(data) as stream:
        return _extract_stream(stream, filename, headers, _depth)


def _extract_stream(stream, filename, headers=None, _depth=0):
    head = stream.read(1000); stream.seek(0)
    name = filename.lower().split('?')[0]
    warnings = []
    if re.search(br'<(?:!doctype|html|body)\b',head[:500],re.I) and not name.endswith(('.html','.htm')):
        raise ExtractionError('첨부 주소가 문서 대신 HTML 안내·오류 화면을 반환했습니다. 브라우저에서 받은 원본 파일로 보완하세요.')
    if name.endswith('.doc'):
        raise ExtractionError('구형 Word DOC는 지원하지 않습니다. DOCX 또는 PDF로 저장해 입력하세요.')
    if head.startswith(b'%PDF') or name.endswith('.pdf'):
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise ExtractionError('PDF 추출에는 pypdf가 필요합니다. pip install pypdf 후 다시 시도하세요.') from exc
        reader = PdfReader(stream)
        if reader.is_encrypted:
            raise ExtractionError('암호화 PDF는 해제 후 다시 입력하세요.')
        parts = []
        for i, page in enumerate(reader.pages):
            content = page.extract_text() or ''
            if len(content.strip()) < 20:
                warnings.append(f'{filename} {i+1}쪽: 이미지/빈 페이지 가능. OCR 미지원으로 수동 확인 필요')
            parts.append(f'[파일 {filename} | 페이지 {i+1}]\n{content}')
        return '\n\n'.join(parts), warnings
    if name.endswith(('.hwpx', '.docx')):
        parts = []
        with zipfile.ZipFile(stream) as z:
            infos = z.infolist()
            files = sorted(n for n in z.namelist() if re.search(r'Contents/section\d+\.xml$|word/document\.xml$', n, re.I))
            for path in files:
                info = z.getinfo(path)
                check_expansion(info.file_size, info.compress_size)
                raw = z.read(path)
                if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():
                    raise ExtractionError('지원하지 않는 XML 선언입니다.')
                root = ElementTree.fromstring(raw)
                paragraphs = [el for el in root.iter() if el.tag.split('}')[-1] in ('p',)]
                texts = [''.join(el.itertext()).strip() for el in paragraphs]
                parts.append(f'[파일 {filename} | {path}]\n' + '\n'.join(texts))
            if not files:
                raise ExtractionError('문서 본문을 찾을 수 없습니다.')
        return '\n\n'.join(parts), ['문서의 이미지·수식·도표는 자동 분석하지 않았습니다.']
    if name.endswith('.zip'):
        from .documents import required_attachment
        if _depth >= 16:raise ExtractionError('ZIP 중첩 구조가 너무 깊습니다. 직접 압축 해제해 입력하세요.')
        parts=[];issues=[];required=0;rfps=set()
        with zipfile.ZipFile(stream) as archive:
            infos=[i for i in archive.infolist() if not i.is_dir()]
            for info in infos:
                if not required_attachment(info.filename):continue
                required+=1
                check_expansion(info.file_size, info.compress_size)
                if re.search(r'RFP|제안요청',info.filename,re.I):rfps.add(re.sub(r'\.[^.]+$','',info.filename))
                if not re.search(r'\.(?:hwpx?|docx|pdf|txt|md|html?|zip)$',info.filename,re.I):
                    issues.append('ZIP 미처리: '+info.filename);continue
                try:
                    with archive.open(info) as src, tempfile.SpooledTemporaryFile(max_size=8*1024*1024) as child:
                        shutil.copyfileobj(src, child, CHUNK_BYTES); child.seek(0)
                        content,w=extract_file(child,info.filename,_depth=_depth+1)
                    parts.append('[ZIP 문서 '+info.filename+']\n'+content);issues.extend(w)
                except Exception as exc:issues.append('ZIP 미처리: '+info.filename+' / '+str(exc)[:150])
        if not required:return '', ['선택 매뉴얼·양식·참고 법령만 있는 ZIP으로 핵심 심사에서 제외했습니다.']
        if not parts:raise ExtractionError('ZIP에서 읽을 수 있는 필수 문서를 찾지 못했습니다. 개별 RFP를 입력하세요.')
        if len(rfps)>1:issues.append('복수 RFP가 ZIP에 포함되어 있습니다. 과제별 파일로 분리하세요.')
        return '\n\n'.join(parts),issues
    if name.endswith('.hwp') or head.startswith(b'\xd0\xcf\x11\xe0'):
        from .hwp import extract_hwp
        try:
            return extract_hwp(stream.read())
        except (ValueError, KeyError, IndexError, struct.error) as exc:
            raise ExtractionError('HWP 바이너리 본문 추출 실패: ' + str(exc)) from exc
    if name.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp')):
        raise ExtractionError('이미지 OCR은 아직 지원하지 않습니다. 추출한 본문을 붙여 넣으세요.')
    data = stream.read()
    text = decode_text(data, headers)
    if '<html' in text[:1000].lower() or name.endswith(('.html', '.htm')):
        p = PageParser(); p.feed(text); body_parser=BodyParser();body_parser.feed(text);body=body_parser.result()
        issues=[]
        if (body_parser.has_images if body else p.result()['has_images']):issues.append('페이지 이미지의 상세 내용은 자동 판독하지 않았습니다.')
        return body or p.result()['text'], issues
    if b'\x00' in data[:1000] and not data.startswith((b'\xff\xfe', b'\xfe\xff')):
        raise ExtractionError('텍스트로 읽을 수 없는 파일입니다.')
    return text, warnings


ATTACHMENT = re.compile(r'filedown|download|atchfile|file_down|/getFile\?|\.pdf(?:\?|$)|\.hwpx?(?:\?|$)|\.docx(?:\?|$)', re.I)


class BodyParser(HTMLParser):
    """Prefer the board's article body over headers, related posts and menus."""
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.stack, self.captures = [], []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        tokens = (attrs.get('class', '') + ' ' + attrs.get('id', '')).split()
        strong = {'view-cont', 'view_con', 'view_content', 'board-view-content', 'bbs_view_cont',
                  'td_view', 'hwp_editor_board_content', 'board-content', 'bbs-content', 'cont_align'}
        capture = {'parts': [], 'images': False, 'score': 2 if strong.intersection(tokens) else 1} if (
            strong.intersection(tokens) or tag in ('main', 'article') or attrs.get('id') in ('contents', 'content')) else None
        if capture:
            self.captures.append(capture)
        if tag=='img' and not any(t in ('nav','footer','header') for t,_ in self.stack):
            for _, c in self.stack:
                if c:c['images']=True
        for _, c in self.stack:
            if c and tag in ('p','br','div','tr','li','td','th','dt','dd','h1','h2','h3','h4','section'):
                c['parts'].append('\n')
        if tag not in ('area','base','br','col','embed','hr','img','input','link','meta','param','source','track','wbr'):
            self.stack.append((tag, capture))

    def handle_endtag(self, tag):
        if tag in ('p','div','tr','li','td','th','dt','dd','h1','h2','h3','h4','section'):
            for _,c in self.stack:
                if c:c['parts'].append('\n')
        for i in range(len(self.stack)-1, -1, -1):
            if self.stack[i][0] == tag:
                del self.stack[i:]
                break

    def handle_data(self, data):
        if any(t in ('script','style','nav','footer','header','noscript') for t, _ in self.stack):
            return
        for _, c in self.stack:
            if c:
                c['parts'].append(data)

    def result(self):
        choices = [(c['score'], '\n'.join(re.sub(r'\s+', ' ', x).strip() for x in ''.join(c['parts']).splitlines() if x.strip()), c) for c in self.captures]
        choices = [(score, text, capture) for score, text, capture in choices if len(text) >= 80]
        selected=max(choices,key=lambda x:(x[0],len(x[1]))) if choices else None
        self.has_images=bool(selected and selected[2]['images'])
        return selected[1] if selected else ''


def attachment_address(link, resolved, source_id):
    action = link.get('onclick', '') or link['href']
    if source_id == 'iris':
        m = re.search(r"f_bsnsAncm_downloadAtchFile\('([^']+)'\s*,\s*'([^']+)'", action)
        if m:
            return query_url(urllib.parse.urljoin(resolved, '/comm/file/fileDownload.do'), atchDocId=m[1], atchFileId=m[2])
    if source_id == 'dtims':
        m = re.search(r"download\('([A-Za-z0-9]+)'\s*,\s*'(\d+)'\)", action)
        if m:
            return query_url(urllib.parse.urljoin(resolved, '/vps/vpsFileDownload.do'), attcIden=m[1], attcOrdr=m[2], tabsGubn='VPS_CTRND_STMG', wiselog='VPS_CTRND_STMG|'+m[1])
    if not link['href'].lower().startswith(('javascript:', '#')):
        return urllib.parse.urljoin(resolved, link['href'])
    return None


def page_payload(url, fetcher, title='', source_id='manual', scope='notice', attachment_limit=None, staged=False, listing=None):
    data, headers, resolved = fetcher.fetch(url)
    ctype = next((v for k, v in headers.items() if k.lower() == 'content-type'), '')
    if data.startswith(b'%PDF') or any(x in ctype for x in ['application/pdf', 'application/haansoft', 'application/vnd.']):
        name = PurePosixPath(urllib.parse.urlsplit(resolved).path).name or 'attachment.pdf'
        if staged:
            from .triage import title_screen, metadata_payload
            period=(listing or {}).get('period',{'state':'unknown'})
            check=title_screen(title)
            if period['state']!='open' or check['state']!='candidate':
                return metadata_payload(url,title or name,source_id,period,check,'deadline_unknown_metadata_only')
        text, warnings = extract_file(data, name, headers)
        return {'title': title or name, 'url': url, 'text': text, 'source_id': source_id, 'scope': scope,
                'warnings': warnings, 'attachments': []}
    raw = decode_text(data, headers)
    parser = PageParser(); parser.feed(raw); page = parser.result()
    if any(s in page['text'].lower() for s in ['access denied', 'captcha', '비정상적인 접근', '자동입력 방지문자']):
        raise ExtractionError('접근 제한 또는 자동화 방지 화면입니다. 수동 확인 필요')
    if len(page['text']) < 80:
        raise ExtractionError('본문이 짧거나 동적으로 표시됩니다. 본문을 붙여 넣어 입력하세요.')
    body_parser = BodyParser(); body_parser.feed(raw)
    body = body_parser.result()
    # Announcement metadata often sits above the board article. Keep exact visible
    # labels/values, rather than falling back to unrelated whole-page menu text.
    metadata=[f for f in page['metadata'] if re.fullmatch(
        r'\s*(?:접수\s*(?:기간|마감(?:일시|일)?|종료(?:일시|일)?)|신청\s*(?:기간|마감(?:일시|일)?)|모집\s*기간|공고\s*기간|제출\s*기한|마감\s*일시|마감일|신청\s*자격|지원\s*대상|참여\s*대상)\s*[:：]?\s*',f['label'])]
    if body and metadata:
        quotes=list(dict.fromkeys(f['label']+'\n'+f['text'] for f in metadata))
        body+='\n[공고 페이지의 항목별 정보]\n'+'\n'.join(q for q in quotes if q not in body)
    warnings = ['세부 RFP·신청 자격은 첨부 원문에서 확인하세요.'] if body else ['HTML 본문은 메뉴·다른 공고 문구를 포함할 수 있습니다. 세부 RFP를 확인하세요.']
    triage=None
    if staged:
        from .triage import period_from_text, title_screen, metadata_payload
        period=period_from_text(body or page['text'])
        if period['state']=='unknown' and not period.get('end_date') and (listing or {}).get('period',{}).get('state')=='open':
            period=listing['period']
        check=title_screen(title or page['title'])
        state=('expired_metadata_only' if period['state']=='expired' else 'upcoming_metadata_only' if period['state']=='upcoming'
               else 'deadline_unknown_metadata_only' if period['state']!='open' else 'title_metadata_only' if check['state']!='candidate' else '')
        triage={'period':period,'title':check,'reason':''}
        if state:
            p=metadata_payload(url,title or page['title'],source_id,period,check,state)
            p.update(text=body or page['text'],collected_url=resolved)
            return p
        if period.get('basis','').startswith('listing') and period.get('quote'):
            body=(body or page['text'])+'\n[목록에서 확인한 접수정보]\n'+period['quote']
    candidates = []
    unresolved = []
    for link in page['links']:
        if ATTACHMENT.search(link['href']) or re.search(r'\.(?:hwp[x]?|pdf|docx|zip)\b', link['text'], re.I):
            full = attachment_address(link, resolved, source_id)
            if not full:
                unresolved.append({'name': link['text'].strip()[:300], 'status': 'review', 'error': '파일 클릭 함수를 해석하지 못했습니다. 원문에서 첨부파일을 확인하세요.'})
                continue
            if full.startswith(('http://', 'https://')) and full not in [a[0] for a in candidates]:
                name = re.sub(r'\s+', ' ', link['text']).strip()
                match = re.search(r'^(.+?\.(?:hwpx?|pdf|docx|zip|xlsx?))\b', name, re.I)
                candidates.append((full, match[1] if match else name))
    candidates.sort(key=lambda a: (not bool(re.search(r'RFP|제안요청|공고문', a[1], re.I)), bool(re.search(r'양식|서식|증빙|매뉴얼', a[1]))))
    from .documents import deadline_evidence
    from .screening import KST
    from datetime import datetime
    deadline,deadline_quote=deadline_evidence(body or page['text'])
    expired=bool(deadline and deadline<datetime.now(KST).date().isoformat())
    if expired:
        return {'title':title or page['title'],'url':url,'text':body or page['text'],'scope':scope,'source_id':source_id,'warnings':[],
                'attachments':[],'collected_url':resolved,'collection_state':'expired_metadata_only','deadline_evidence':{'date':deadline,'quote':deadline_quote}}
    attachments = unresolved
    texts = [body or page['text']]
    for address, name in candidates[:attachment_limit]:
        name = name or PurePosixPath(urllib.parse.urlsplit(address).path).name
        from .documents import required_attachment, document_role
        record = {'url': address, 'name': name, 'status': 'pending','required_for_screening':required_attachment(name),'role':document_role(name)}
        if not record['required_for_screening']:
            record['status']='optional_not_fetched';attachments.append(record);continue
        try:
            downloader = getattr(fetcher, 'download', fetcher.fetch)
            file_data, file_headers, _ = downloader(address)
            disposition = next((v for k, v in file_headers.items() if k.lower() == 'content-disposition'), '')
            found = re.search(r"filename\*?=(?:UTF-8''|\")?([^\";]+)", disposition, re.I)
            if found:
                name = urllib.parse.unquote(found.group(1)); record['name'] = name
            try:
                if required_attachment(name):
                    content, issues = extract_file(file_data, name, file_headers)
                    texts.append('[첨부 '+name+']\n'+content); warnings.extend(issues)
                    record.update(status='extracted', sha256=document_hash(file_data),extracted_chars=len(content.strip()),warnings=issues)
                else:record.update(status='optional_not_analyzed')
            finally:
                if hasattr(file_data, 'close'): file_data.close()
        except Exception as exc:
            record.update(status='review', error=str(exc)[:300])
            warnings.append(f'첨부 {name}: ' + str(exc)[:160])
        from .documents import required_attachment
        record['required_for_screening'] = required_attachment(record['name'])
        record['role']=document_role(record['name'])
        attachments.append(record)
    if attachment_limit is not None and len(candidates) > attachment_limit:
        warnings.append(f'첨부파일 {len(candidates)}개 중 {attachment_limit}개만 처리했습니다.')
    if (body_parser.has_images if body else page['has_images']):
        warnings.append('페이지 이미지의 상세 내용은 자동 판독하지 않았습니다.')
    return {'title': title or page['title'] or '제목 확인 필요', 'url': url, 'text': '\n\n'.join(texts),
            'scope': scope, 'source_id': source_id, 'warnings': warnings, 'attachments': attachments,
            'eligibility': 'unknown', 'collected_url': resolved, **({'collection_triage':triage} if triage else {})}


def query_url(url, **values):
    u = urllib.parse.urlsplit(url)
    q = dict(urllib.parse.parse_qsl(u.query, keep_blank_values=True))
    q.update({k: str(v) for k, v in values.items()})
    return urllib.parse.urlunsplit((u.scheme, u.netloc, u.path, urllib.parse.urlencode(q), ''))


def detail_url(link, resolved, config):
    """Decode only observed, allowlisted click handlers; never execute page JavaScript."""
    sid = config['id']
    action = link.get('onclick', '') or link['href']
    if sid == 'iris':
        m = re.search(r"f_bsnsAncmBtinSituListForm_view\('([0-9]+)'\s*,\s*'(ancm\w+)'\)", action)
        if m:
            return query_url(urllib.parse.urljoin(resolved, '/contents/retrieveBsnsAncmView.do'), ancmId=m[1], ancmPrg=m[2])
    if sid == 'koita':
        m = re.search(r"page_move\(\s*'commBoardGovRnDView'\s*,\s*\{\s*no\s*:\s*(\d+)\s*\}", action)
        if m:
            return query_url(urllib.parse.urljoin(resolved, 'commBoardGovRnDView.do'), no=m[1])
    if sid in ('dapa', 'future', 'rapid'):
        m = re.search(r"fn_selectDoc\('([0-9]+)'\)", action)
        if m:
            return query_url(urllib.parse.urljoin(resolved, 'selectDoc.do'), **{**dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(resolved).query)), 'docSeq':m[1]})
    if sid == 'dtims':
        m = re.search(r"fncCtPrjNotiInfo\('([A-Za-z0-9]+)'\)", action)
        if m:
            return query_url(urllib.parse.urljoin(resolved, '/vps/OINF_CtPrjNotiInfo.do'), ctRndIden=m[1], wiselog=m[1])
    href = link['href'].strip()
    if not href or href.lower().startswith(('javascript:', '#')):
        return None
    address = urllib.parse.urljoin(resolved, href)
    path = urllib.parse.urlsplit(address).path
    known = sid == 'nipa' and re.fullmatch(r'/home/2-2/\d+/?', path)
    if (known or re.search(config.get('detail_pattern', r'view|detail'), href, re.I)) and has_record_id(address):
        return address
    return None


def list_links(page, resolved, config):
    links, seen, rejected = [], set(), 0
    for link in page['links']:
        title = re.sub(r'\s+', ' ', link['text']).strip()
        title = re.sub(r'\s*새 글\s*$', '', title)
        if document_check({'title': title})['excluded']:
            rejected += 1
            continue
        address = detail_url(link, resolved, config)
        if (not address or len(title) < 5 or address in seen
                or urllib.parse.urlsplit(address).hostname != urllib.parse.urlsplit(resolved).hostname):
            continue
        seen.add(address)
        links.append((address, title))
    return links, rejected


def list_candidates(page,resolved,config):
    from .triage import candidate_metadata
    links,rejected=list_links(page,resolved,config)
    contexts={}
    for link in page['links']:
        address=detail_url(link,resolved,config)
        if address and link.get('listing'):contexts[address]=link['listing']
    return [{'url':u,**candidate_metadata(title,contexts.get(u))} for u,title in links],rejected


def next_pages(page, resolved, config):
    found = []
    current = urllib.parse.urlsplit(resolved)
    for link in page['links']:
        href, action = link['href'].strip(), link.get('onclick', '')
        address = urllib.parse.urljoin(resolved, href)
        target = urllib.parse.urlsplit(address)
        q = dict(urllib.parse.parse_qsl(target.query))
        page_values = [int(v) for k, v in q.items() if k.lower() in ('pageindex', 'currentpageno', 'curpage', 'page', 'pageno', 'pagenum', 'movepage') and v.isdigit()]
        if target.hostname == current.hostname and target.path == current.path and page_values:
            found.append((page_values[0], address))
        elif re.fullmatch(r'\d+', link['text'].strip()):
            number = int(link['text'].strip())
            key = {'iris':'pageIndex', 'dapa':'currentPageNo', 'koita':'pageIndex', 'dtims':'pageIndex'}.get(config['id'])
            if key and ('(' in action or href.startswith('javascript:')):
                found.append((number, query_url(resolved, **{key:number})))
    return sorted(set(found))


def collect_source(config, fetcher, consume, limit=None, max_pages=None):
    if limit is not None:limit=max(1,int(limit))
    if max_pages is not None:max_pages=max(1,int(max_pages))
    queue, visited, seen = [(1, config['url'])], set(), set()
    links, errors, notes, changes, skipped = [], [], [], [], 0
    pages_read, pagination_left = 0, False
    while queue and (max_pages is None or pages_read < max_pages) and (limit is None or len(links) < limit):
        number, address = queue.pop(0)
        if address in visited:
            continue
        visited.add(address)
        try:
            data, headers, resolved = fetcher.fetch(address)
            parser = PageParser(); parser.feed(decode_text(data, headers)); page = parser.result()
            pages_read += 1
            found, rejected = list_links(page, resolved, config)
            skipped += rejected
            fresh = [(u, t) for u, t in found if u not in seen]
            # Some list endpoints ignore GET pagination: don't repeat the first page.
            if pages_read > 1 and not fresh:
                notes.append('다음 목록에서 새로운 게시물 번호가 확인되지 않아 페이지 탐색을 중단했습니다.')
                pagination_left = True
                break
            links.extend(fresh); seen.update(u for u, _ in fresh)
            for n, u in next_pages(page, resolved, config):
                if n > number and u not in visited and (n, u) not in queue:
                    queue.append((n, u))
            queue.sort()
        except Exception as exc:
            if not pages_read:
                raise
            errors.append('목록 다음 페이지: ' + str(exc)[:180])
            pagination_left = True
            break
    pagination_left = pagination_left or bool(queue) or (limit is not None and len(links) > limit)
    if not links:
        return {'status': 'review', 'discovered': 0, 'processed': 0, 'skipped_navigation': skipped,
                'pages_read': pages_read, 'coverage': 'unknown', 'notes': notes,
                'error': '상세 링크를 찾지 못했습니다. 메뉴·소개 링크는 제외했습니다. 동적 목록·안내 페이지 여부를 확인하세요. 신규 0건으로 판단하지 않습니다.', 'changes': []}
    for url, title in links[:limit]:
        try:
            payload = page_payload(url, fetcher, title=title, source_id=config['id'])
            if document_check(payload)['excluded']:
                skipped += 1
                continue
            # A redirect to a list/login is not a successfully collected detail.
            target = payload.get('collected_url', url)
            if target != url and not has_record_id(target):
                raise ExtractionError('상세 주소가 게시물 번호 없는 목록·안내 화면으로 이동했습니다.')
            changes.append(consume(payload))
            if payload['warnings']:
                notes.append(f'{title[:50]}: 본문·이미지·첨부의 검토 안내는 공고 상세 참조')
            for a in payload.get('attachments', []):
                from .documents import required_record
                if a.get('status') == 'review' and required_record(a):
                    errors.append(f'{title[:50]}: 첨부 {a.get("name", "")} 미확보: {a.get("error", "")[:150]}')
        except Exception as exc:
            errors.append(f'{title[:50]}: {str(exc)[:160]}')
    if pagination_left:
        errors.append(f'목록 {pages_read}쪽 확인, 발견 {len(links)}건 중 {len(changes)}건 저장. 이후 페이지는 미확인입니다.')
    status = ('failed' if not changes else 'partial') if errors else ('success' if changes else 'review')
    return {'status': status, 'discovered': len(links), 'processed': len(changes),
            'skipped_navigation': skipped, 'pages_read': pages_read,
            'error': '\n'.join(errors), 'notes': notes, 'changes': changes,
            'coverage': 'bounded_pages' if pages_read > 1 else 'first_page_only', 'limit': limit}
