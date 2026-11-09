"""Separate document identity from relevance. Keep excluded originals for audit."""
import re
from urllib.parse import parse_qsl, urlsplit

MENU_TITLES = {
    '전체', '전체선택', '전체선택됨', '선택됨', '선택해제', '인사채용', '회의세미나',
    '사업설명회', '사업공고', '과제공고', '공지사항', '공고현황', '사업소개',
    'dtims소개', '로그인', '회원가입', '사이트맵', '목록', '검색', '이전', '다음',
    '자료실', '주요업무', '기관소개', '이용안내',
}


def document_check(payload):
    title = str(payload.get('title', '')).strip()
    compact = re.sub(r'[\W_]+', '', title).casefold()
    reason = ''
    if compact in MENU_TITLES:
        reason = '게시물 제목이 아닌 메뉴·분류·화면 조작 문구입니다.'
    elif re.fullmatch(r'(?:[A-Za-z가-힣]{2,20})(?:시스템|기관|서비스)소개', compact):
        reason = '기관·시스템 소개 페이지입니다.'
    return {'kind': 'navigation' if reason else 'notice_candidate', 'excluded': bool(reason),
            'reason': reason, 'rule_version': 1}


def has_record_id(url):
    u = urlsplit(url)
    # Menu/category IDs identify a list, not an individual document.
    keys = {'id', 'no', 'seq', 'docseq', 'boardseq', 'nttid', 'nttseq', 'ancmid',
            'annoid', 'annoseq', 'postno', 'postid', 'notiid', 'notino', 'bidx',
            'ntisid', 'ctrndiden', 'articleid', 'linkid', 'titleid', 'idx'}
    if any(k.casefold() in keys and re.fullmatch(r'[\w.-]+', v or '') for k, v in parse_qsl(u.query)):
        return True
    return bool(re.search(r'/(?:view|detail|read|post)/[\w-]+/?$|/home/\d+-\d+/\d+/?$', u.path, re.I))


def relevance_key(notice):
    """Same order for UI and briefing. Scores are ordering rules, not probabilities."""
    a = notice['analysis']
    rejected = notice['payload'].get('eligibility') == 'ineligible' or bool(
        notice.get('feedback') and notice['feedback']['notice_version'] == notice['version']
        and notice['feedback']['verdict'] == 'irrelevant')
    tier = {'direct': 5, 'candidate': 4, 'expand': 3, 'review': 2, 'low': 1}.get(a['category'], 0)
    score = a.get('priority', 0)
    if a['category'] == 'review' and a.get('mappings'):
        score = 10 + min(len({m['topic_id'] for m in a['mappings']}), 5) * 9
    return (not rejected, tier, score, notice.get('first_seen', ''), notice['id'])
