import hashlib
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit, parse_qsl, urlencode
from .diagnostics import status_diagnostic


def now():
    return datetime.now(timezone.utc).isoformat(timespec='seconds')


def dumps(obj):
    return json.dumps(obj, ensure_ascii=False, sort_keys=True)


def canonical_url(url):
    if not url:
        return ''
    u = urlsplit(url.strip())
    if u.scheme not in ('http', 'https') or not u.hostname or u.username or u.password:
        raise ValueError('http 또는 https 주소만 입력할 수 있습니다.')
    pairs = [(k, v) for k, v in parse_qsl(u.query, keep_blank_values=True)
             if not k.lower().startswith('utm_') and k.lower() not in ('fbclid', 'gclid')]
    return urlunsplit((u.scheme.lower(), u.netloc.lower(), u.path or '/', urlencode(pairs), ''))


class Store:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self.path = self.directory / 'briefing.sqlite3'
        with self.connect() as db:
            db.executescript('''
              PRAGMA journal_mode=WAL;
              CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT NOT NULL);
              CREATE TABLE IF NOT EXISTS notices(
                id TEXT PRIMARY KEY, url TEXT, title TEXT, source_id TEXT,
                first_seen TEXT, last_seen TEXT, version INTEGER, content_hash TEXT,
                payload TEXT, analysis TEXT, is_demo INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS versions(
                id INTEGER PRIMARY KEY, notice_id TEXT, version INTEGER,
                content_hash TEXT, payload TEXT, created_at TEXT,
                UNIQUE(notice_id, version));
              CREATE TABLE IF NOT EXISTS feedback(
                id INTEGER PRIMARY KEY, notice_id TEXT, notice_version INTEGER,
                verdict TEXT, reason TEXT, split TEXT, created_at TEXT);
              CREATE TABLE IF NOT EXISTS sources(
                id TEXT PRIMARY KEY, config TEXT, status TEXT DEFAULT 'unchecked',
                checked_at TEXT, last_success TEXT, error TEXT, discovered INTEGER DEFAULT 0);
              CREATE TABLE IF NOT EXISTS runs(
                id TEXT PRIMARY KEY, kind TEXT, status TEXT, started_at TEXT,
                finished_at TEXT, result TEXT);
            ''')
            fields = {r['name'] for r in db.execute('PRAGMA table_info(sources)')}
            if 'diagnostic' not in fields:
                db.execute("ALTER TABLE sources ADD COLUMN diagnostic TEXT DEFAULT '{}' ")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def get_setting(self, key, default=None):
        with self.connect() as db:
            row = db.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return json.loads(row['value']) if row else default

    def set_setting(self, key, value):
        with self.connect() as db:
            db.execute('INSERT INTO settings VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value', (key, dumps(value)))

    def seed(self, profile, sources):
        if self.get_setting('profile') is None:
            self.set_setting('profile', profile)
        with self.connect() as db:
            for source in sources:
                db.execute('INSERT OR IGNORE INTO sources(id,config) VALUES(?,?)', (source['id'], dumps(source)))
                row = db.execute('SELECT * FROM sources WHERE id=?', (source['id'],)).fetchone()
                config = json.loads(row['config'])
                if config.get('note', '').startswith('정적 HTML 수집 후보 경로.'):
                    config['note'] = source.get('note', config['note'])
                if config.get('note','').replace('최대 3쪽·30건.','목록 최대 5쪽·상세 30건, 대기 작업 이어서 처리.') == source.get('note'):
                    config['note']=source['note']
                for old_note in ('목록 최대 5쪽·상세 30건, 대기 작업 이어서 처리.','최대 3쪽·30건.'):
                    if config.get('note','').replace(old_note,'총건수 제한 없이 연속 탐색, 중지 후 대기 작업 이어서 처리.') == source.get('note'):
                        config['note']=source['note']
                if source['id'] == 'dratri' and config.get('url') == 'https://www.add.re.kr/dratri':
                    config['url'] = source['url']
                    config['note'] = source.get('note', '')
                db.execute('UPDATE sources SET config=? WHERE id=?', (dumps(config), source['id']))
                # Correct the known old false-success case without changing the recorded check time.
                from .notices import document_check
                diagnostic = json.loads(row['diagnostic'] or '{}')
                notes = diagnostic.get('notes', [])
                if row['status'] == 'success' and notes and all(
                        document_check({'title': str(note).split(':', 1)[0]})['excluded'] for note in notes):
                    message = '이전 수집 결과가 메뉴·소개 링크여서 완료 상태를 취소했습니다. 업데이트된 수집기로 다시 확인하세요.'
                    db.execute('UPDATE sources SET status=?,last_success=NULL,error=?,diagnostic=? WHERE id=?',
                               ('review', message, dumps(status_diagnostic('review', '상세 링크를 찾지 못했습니다. ' + message)), source['id']))

    def sources(self):
        with self.connect() as db:
            rows = []
            for r in db.execute('SELECT * FROM sources ORDER BY rowid'):
                d = {**dict(r), 'config': json.loads(r['config'])}
                d['diagnostic'] = json.loads(r['diagnostic'] or '{}') or status_diagnostic(r['status'], r['error'] or '')
                if d['diagnostic'].get('code') == 'robots_disallowed':
                    d['status'] = 'blocked'
                rows.append(d)
            return rows

    def update_source(self, sid, config):
        if sid != config.get('id'):
            raise ValueError('수집 경로 ID가 일치하지 않습니다.')
        canonical_url(config['url'])
        with self.connect() as db:
            if not db.execute('SELECT 1 FROM sources WHERE id=?', (sid,)).fetchone():
                raise ValueError('알 수 없는 수집 경로입니다.')
            db.execute('UPDATE sources SET config=? WHERE id=?', (dumps(config), sid))

    def set_sources_enabled(self, enabled):
        if type(enabled) is not bool:
            raise ValueError('전체 선택 값은 true 또는 false여야 합니다.')
        with self.connect() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute('SELECT id,config FROM sources').fetchall()
            for row in rows:
                config = json.loads(row['config'])
                config['enabled'] = enabled
                db.execute('UPDATE sources SET config=? WHERE id=?', (dumps(config), row['id']))
        return {'updated': len(rows), 'enabled': enabled}

    def source_result(self, sid, status, count=0, error='', diagnostic=None):
        stamp = now()
        with self.connect() as db:
            processed = (diagnostic or {}).get('processed', 0)
            db.execute('UPDATE sources SET status=?,checked_at=?,error=?,discovered=?,diagnostic=?,last_success=CASE WHEN ? THEN ? ELSE last_success END WHERE id=?',
                       (status, stamp, error[:1600], count, dumps(diagnostic or status_diagnostic(status, error)), status == 'success' or status == 'partial' and processed > 0, stamp, sid))

    def upsert(self, payload, analysis):
        p = dict(payload)
        p['url'] = canonical_url(p.get('url', ''))
        p['title'] = str(p.get('title', '')).strip()[:400]
        p['text'] = str(p.get('text', '')).strip()
        if not p['title'] or len(p['text']) < 10:
            raise ValueError('제목과 10자 이상의 원문이 필요합니다.')
        if p.get('deadline'):
            datetime.fromisoformat(p['deadline'])
        unit = p.get('unit', '')[:200]
        nid = self.payload_id(p)
        stable = {k: p.get(k) for k in ('title', 'text', 'deadline', 'attachments', 'unit', 'eligibility')}
        digest = hashlib.sha256(dumps(stable).encode()).hexdigest()
        stamp = now()
        with self.connect() as db:
            old = db.execute('SELECT * FROM notices WHERE id=?', (nid,)).fetchone()
            changed = old is None or old['content_hash'] != digest
            version = (old['version'] + 1 if changed else old['version']) if old else 1
            first = old['first_seen'] if old else stamp
            db.execute('''INSERT INTO notices VALUES(?,?,?,?,?,?,?,?,?,?,?)
              ON CONFLICT(id) DO UPDATE SET title=excluded.title,source_id=excluded.source_id,
              last_seen=excluded.last_seen,version=excluded.version,content_hash=excluded.content_hash,
              payload=excluded.payload,analysis=excluded.analysis''',
                       (nid, p['url'], p['title'], p.get('source_id', 'manual'), first, stamp,
                        version, digest, dumps(p), dumps(analysis), int(bool(p.get('is_demo')))))
            if changed:
                db.execute('INSERT INTO versions(notice_id,version,content_hash,payload,created_at) VALUES(?,?,?,?,?)',
                           (nid, version, digest, dumps(p), stamp))
        return {'id': nid, 'version': version, 'change': 'new' if old is None else 'changed' if changed else 'unchanged'}

    @staticmethod
    def payload_id(p):
        url=canonical_url(p.get('url',''));unit=p.get('unit','')[:200]
        identity=(url+'|'+unit) if url else dumps([str(p.get('title','')).strip()[:400],str(p.get('text','')).strip(),unit])
        if p.get('is_demo'):identity='demo|'+identity
        return hashlib.sha256(identity.encode()).hexdigest()[:20]

    def matching_notice(self,payload):
        with self.connect() as db:
            row=db.execute('SELECT * FROM notices WHERE id=?',(self.payload_id(payload),)).fetchone()
            return self._notice(row) if row else None

    def expired_collection_history(self,source_id):
        from .triage import period_from_text
        history={}
        with self.connect() as db:
            for row in db.execute('SELECT url,title,payload FROM notices WHERE source_id=? AND is_demo=0',(source_id,)):
                period=period_from_text(json.loads(row['payload']).get('text',''))
                if period['state']=='expired':history[row['url']]={'title':row['title'],'period':period}
        return history

    def notices(self):
        with self.connect() as db:
            records = [self._notice(r) for r in db.execute('SELECT * FROM notices ORDER BY last_seen DESC')]
            for r in records:
                f = db.execute('SELECT * FROM feedback WHERE notice_id=? ORDER BY id DESC LIMIT 1', (r['id'],)).fetchone()
                r['feedback'] = dict(f) if f else None
        return records

    @staticmethod
    def _notice(row):
        r = dict(row)
        r['payload'], r['analysis'] = json.loads(r['payload']), json.loads(r['analysis'])
        return r

    def notice(self, nid):
        with self.connect() as db:
            r = db.execute('SELECT * FROM notices WHERE id=?', (nid,)).fetchone()
            if r is None:
                raise ValueError('공고를 찾을 수 없습니다.')
            return self._notice(r)

    def set_analysis(self, nid, analysis):
        with self.connect() as db:
            db.execute('UPDATE notices SET analysis=? WHERE id=?', (dumps(analysis), nid))

    def versions(self, nid):
        with self.connect() as db:
            return [{**dict(r), 'payload': json.loads(r['payload'])} for r in db.execute('SELECT * FROM versions WHERE notice_id=? ORDER BY version DESC', (nid,))]

    def feedback(self, nid, version, verdict, reason, split='train'):
        if verdict not in ('relevant', 'irrelevant', 'expand', 'uncertain') or split not in ('train', 'test'):
            raise ValueError('올바른 판단과 평가 구분을 선택하세요.')
        if not reason.strip():
            raise ValueError('판단 이유를 입력하세요.')
        notice = self.notice(nid)
        if version != notice['version']:
            raise ValueError('공고가 변경되었습니다. 새 버전에서 다시 판단하세요.')
        with self.connect() as db:
            db.execute('INSERT INTO feedback(notice_id,notice_version,verdict,reason,split,created_at) VALUES(?,?,?,?,?,?)',
                       (nid, version, verdict, reason[:2000], split, now()))

    def run(self, rid, kind, status, result=None):
        with self.connect() as db:
            db.execute('INSERT INTO runs VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET status=excluded.status,finished_at=excluded.finished_at,result=excluded.result',
                       (rid, kind, status, now(), now() if status != 'running' else None, dumps(result or {})))

    def runs(self):
        with self.connect() as db:
            return [{**dict(r), 'result': json.loads(r['result'])} for r in db.execute('SELECT * FROM runs ORDER BY started_at DESC LIMIT 30')]

    def export(self):
        with self.connect() as db:
            feedback = [dict(r) for r in db.execute('SELECT * FROM feedback')]
            versions = [{**dict(r), 'payload': json.loads(r['payload'])} for r in db.execute('SELECT * FROM versions')]
        return {'schema_version': 1, 'exported_at': now(), 'profile': self.get_setting('profile'),
                'sources': self.sources(), 'notices': self.notices(), 'feedback': feedback,
                'versions': versions, 'runs': self.runs()}

    def backup(self, destination):
        with self.connect() as source, sqlite3.connect(destination) as target:
            source.backup(target)
