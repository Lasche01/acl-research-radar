"""Graph evidence paths and lexical baseline; GPT screening lives in screening.py."""
import re
from .store import now
from .notices import document_check

ALIASES = {
    'waveform': ['waveform', '파형 설계', '파형설계', 'fbmc', 'gfdm', '다중반송파', '필터뱅크', 'filter bank'],
    'ftn': ['faster than nyquist', 'faster-than-nyquist', 'ftn', '비직교 전송', '시간 압축 전송'],
    'otfs': ['otfs', 'delay doppler', 'delay-doppler', '지연 도플러', '지연-도플러'],
    'beam': ['빔포밍', 'beamforming', '빔 탐색', '빔탐색', '빔 관리', 'beam management', 'beam selection', 'beam sweeping', '빔 코드북', '빔코드북'],
    'csi': ['csi', 'channel state information', '채널 상태 정보', '채널상태정보', '채널 피드백', '채널 정보 피드백'],
    'mimo': ['mimo', '다중 안테나', '다중안테나', '사용자 스케줄링', 'user scheduling', 'cell-free', 'cell free'],
    'channel': ['채널 추정', '채널추정', 'channel estimation', '도플러 추정', 'doppler estimation', '시변 채널', '시변채널', '고속 이동', '고속이동'],
    'detection': ['심볼 검출', '심볼검출', 'symbol detection', '등화', 'equalization', '간섭 제거', '간섭제거', 'interference cancellation', 'isi 보상'],
    'lowpower': ['후방산란', 'backscatter', 'swipt', '무선 전력 전송', '저전력 무선', '저전력 통신'],
    'storage': ['홀로그래픽', 'holographic', '자기기록', 'magnetic recording', '저장장치 채널', '블루레이', 'blu-ray']}


def term_in(term, text):
    if re.fullmatch(r'[a-z0-9]+', term):
        return bool(re.search(r'(?<![a-z0-9])' + re.escape(term) + r'(?![a-z0-9])', text.lower()))
    return term.lower() in text.lower()


def clauses(text):
    lines = re.split(r'\n+|(?<=[.!?。])\s+', text)
    return [s.strip() for line in lines for s in [line[i:i + 650] for i in range(0, len(line), 650)] if len(s.strip()) >= 8]


def validate_profile(profile):
    nodes = profile.get('nodes', [])
    if not isinstance(nodes, list) or not 1 <= len(nodes) <= 3000:
        raise ValueError('프로필에는 1~3000개의 노드가 필요합니다.')
    ids = [n.get('id') for n in nodes]
    if any(not isinstance(i, str) or not i for i in ids) or len(set(ids)) != len(ids):
        raise ValueError('노드 ID는 중복 없는 문자열이어야 합니다.')
    if any(not isinstance(n.get('label'), str) for n in nodes):
        raise ValueError('모든 노드에는 label이 필요합니다.')
    for e in profile.get('edges', []):
        if e.get('from') not in ids or e.get('to') not in ids:
            raise ValueError('존재하지 않는 노드로 연결된 관계가 있습니다.')
    return profile


class Matcher:
    def __init__(self, profile):
        self.profile = validate_profile(profile)
        self.nodes = {n['id']: n for n in profile['nodes']}
        self.topics = {i: n for i, n in self.nodes.items() if n['type'] == 'research_topic'}
        self.evidence = {i: n for i, n in self.nodes.items() if n['type'] != 'research_topic'}
        self.source_urls = {s['id']: s['url'] for s in profile.get('sources', [])}
        self.paths = {}
        for edge in profile['edges']:
            if edge['from'] in self.evidence and edge['to'] in self.topics:
                self.paths.setdefault(edge['to'], []).append(edge['from'])

    def aliases(self, tid):
        return self.topics[tid].get('aliases', ALIASES.get(tid, [self.topics[tid]['label']]))

    def analyze(self, payload):
        document = document_check(payload)
        if document['excluded']:
            return {'category': 'low', 'priority': 0, 'mode': 'graph_rules', 'mappings': [],
                    'warnings': [document['reason']], 'quality': 'not_notice', 'analyzed_at': now(),
                    'profile_version': self.profile.get('as_of_kst', ''), 'summary': document['reason']}
        text = payload.get('text', '')
        chunks = clauses(text)
        mappings, warnings = [], list(payload.get('warnings', []))
        mode = 'graph_rules'
        incomplete_analysis = (any('OCR 미지원' in str(w) for w in warnings)
                               or any(a.get('status') == 'review' for a in payload.get('attachments', [])))
        for tid in self.topics:
            if self.topics[tid].get('enabled', True) is False:
                continue
            hits = [(c, a) for c in chunks for a in self.aliases(tid) if term_in(a, c)]
            if hits and self.paths.get(tid):
                quote, alias = hits[0]
                for eid in sorted(self.paths[tid], key=lambda i: self.evidence[i].get('year', 0), reverse=True)[:2]:
                    mappings.append(self.mapping(tid, eid, quote, 'candidate', f'원문의 “{alias}”와 연결된 연구 주제입니다. 필수 연구 요구인지 검토하세요.', '목록 제목 기반이며 현재 수행 역량은 미확인입니다.'))
        mappings = list({(m['topic_id'], m['evidence_id'], m['quote']): m for m in mappings}.values())
        quality = 'full_text' if len(text.strip()) >= 120 else 'insufficient'
        if quality == 'insufficient':
            warnings.append('원문이 짧아 세부 요구사항 확인이 필요합니다.')
        outside = any(term_in(t, text) for t in ['수중', 'underwater', '광원', '반도체 공정', '소자 제작', '소자제작'])
        storage_only = mappings and all(m['topic_id'] in ('storage', 'detection') for m in mappings) and any(term_in(t, text) for t in self.aliases('storage')) if 'storage' in self.topics else False
        scope = payload.get('scope', 'notice')
        if incomplete_analysis:
            category = 'review'
        elif not mappings:
            category = 'low' if quality == 'full_text' else 'review'
        elif quality == 'insufficient' or scope != 'rfp':
            category = 'review'
        else:
            category = 'expand' if outside or storage_only else 'candidate'
        if scope != 'rfp':
            warnings.append('공고 단위 자료입니다. 세부 RFP별로 분리해 다시 분석하세요.')
        if outside and mappings:
            warnings.append('적용 환경·소자 역량의 공백을 확인하세요. 주제 연결만으로 직접 수행을 확정하지 않습니다.')
        if payload.get('eligibility', 'unknown') == 'unknown':
            warnings.append('신청 자격은 미확인입니다. 법령 참조 조항과 첨부 공고문을 확인하세요.')
        # An index for ordering, never a calibrated probability. Duplicate patents/papers do not add weight.
        count = len({m['topic_id'] for m in mappings})
        priority = min(90, 25 + count * 12) if mappings else 5
        if category == 'review': priority = min(65, 10 + min(count, 5) * 9) if mappings else 5
        if category == 'expand': priority = min(priority, 50)
        return {'category': category, 'priority': priority, 'mode': mode, 'mappings': mappings,
                'warnings': list(dict.fromkeys(warnings)), 'quality': quality, 'analyzed_at': now(),
                'profile_version': self.profile.get('as_of_kst', ''),
                'summary': f'{count}개 연구 주제에서 {len(mappings)}개 근거 연결을 찾았습니다.' if mappings else '현재 프로필에서 구체적인 근거 연결을 찾지 못했습니다. 관련 없음이 확정된 것은 아닙니다.'}

    def mapping(self, tid, eid, quote, strength, reason, gap):
        e = self.evidence[eid]
        return {'topic_id': tid, 'topic': self.topics[tid]['label'], 'evidence_id': eid,
                'evidence': e['label'], 'year': e.get('year'), 'url': e.get('url', self.source_urls.get(e.get('source'), '')),
                'locator': e.get('locator', ''), 'evidence_level': e.get('evidence_level', 'user_supplied'),
                'quote': quote, 'strength': strength, 'reason': reason, 'gap': gap}
