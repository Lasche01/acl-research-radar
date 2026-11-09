"""Keep network failures, policy restrictions and parser gaps separate."""
import importlib.util
import platform
import ssl

REASONS = {
    'certificate': ('인증서 검증 실패', 'setup_network_windows.bat로 truststore를 설치하고 재시작하세요. 계속 실패하면 서버 인증서 체인 또는 기관 네트워크의 인증서를 확인하세요.'),
    'timeout': ('응답 시간 초과', '일시적인 지연일 수 있습니다. 제한된 재시도 후에도 실패하면 해당 경로를 나중에 다시 확인하세요.'),
    'dns': ('주소 해석 실패', '해당 기관 주소를 일반 브라우저에서 열 수 있는지 확인하고 네트워크·DNS 설정을 점검하세요.'),
    'connection_reset': ('연결 강제 종료', '원격 서버나 중간 네트워크가 연결을 끊었습니다. 제한된 재시도 후에도 실패하면 해당 경로를 나중에 다시 확인하세요.'),
    'robots_disallowed': ('자동 수집 제한', '이 경로는 자동 수집을 중단합니다. 공식 API·허용된 다른 출처 또는 직접 확보한 RFP 파일로 보완하세요.'),
    'robots_unavailable': ('수집 정책 확인 불가', 'robots.txt를 확인하지 못했습니다. 수집 허용 여부가 확인될 때까지 보류합니다.'),
    'detail_links_missing': ('상세 링크 추출 실패', '목록 접속은 되었으나 현재 HTML 수집기가 상세 링크를 찾지 못했습니다. 기관별 어댑터 보강이 필요합니다.'),
    'pagination_repeated': ('같은 목록 반복', '다음 쪽 요청이 같은 공고를 반환해 탐색을 중단했습니다. 해당 기관의 페이지 이동 방식 보강이 필요합니다.'),
    'http_server': ('서버 응답 오류', '기관 서버 또는 중간 네트워크의 5xx 응답입니다. 나중에 다시 확인하세요.'),
    'http_access': ('접근 거절', '기관의 응답 코드와 일반 브라우저 접근 여부를 확인하세요. 인증·차단 화면을 우회하지 않습니다.'),
    'coverage_limited': ('목록 일부 수집', '수집 자체는 진행됐습니다. 처리 한도 또는 상세 수집 오류 때문에 전체 확인을 확정하지 않습니다.'),
    'details_failed': ('상세 본문 수집 실패', '목록 링크는 찾았지만 상세 자료를 저장하지 못했습니다. 개별 오류를 확인하세요.'),
    'unknown': ('미분류 오류', '오류 문구와 실행 환경이 포함된 수집 진단 파일을 확인하세요.'),
    'ok': ('설정 범위 내 수집 완료', '본문·첨부·신청 자격의 검토 사항은 공고 상세에서 별도로 확인하세요.')}


def classify_error(error, stage='network'):
    message = str(error)
    lower = message.lower()
    code = getattr(error, 'code_name', None)
    if not code or code == 'unknown':
        if 'certificate_verify_failed' in lower or 'certificate verify failed' in lower:
            code = 'certificate'
        elif ('timed out' in lower or 'timeout' in lower
              or '10060' in lower and ('winerror' in lower or 'errno' in lower)):
            code = 'timeout'
        elif any(s in lower for s in ('getaddrinfo', 'name resolution', 'name or service not known', 'nodename nor servname')):
            code = 'dns'
        elif any(s in lower for s in ('10054', 'connection reset', 'remote end closed connection', 'connection aborted')):
            code = 'connection_reset'
        elif 'robots.txt가 이 경로' in message:
            code = 'robots_disallowed'
        elif '상세 링크를 찾지 못' in message or '상세 링크를 확인하지 못' in message:
            code = 'detail_links_missing'
        elif any(s in lower for s in ('http error 500', 'http error 502', 'http error 503', 'http error 504')):
            code = 'http_server'
        elif any(s in lower for s in ('http error 401', 'http error 403', 'http error 429')):
            code = 'http_access'
        elif 'robots.txt' in lower:
            code = 'robots_unavailable'
        else:
            code = 'unknown'
    label, action = REASONS.get(code, REASONS['unknown'])
    return {'code': code, 'label': label, 'stage': getattr(error, 'stage', 'robots' if 'robots.txt' in lower else 'list' if code == 'detail_links_missing' else stage),
            'message': message[:3000], 'next_action': action, 'policy': getattr(error,'policy',{}),
            'retryable': code in ('timeout', 'dns', 'http_server', 'connection_reset')}


def status_diagnostic(status, error='', code=None):
    if code in REASONS:
        label,action=REASONS[code]
        return {'code':code,'label':label,'next_action':action,'stage':'list','message':error,'retryable':False}
    if status == 'success':
        return {'code': 'ok', 'label': REASONS['ok'][0], 'next_action': REASONS['ok'][1], 'stage': 'complete'}
    if status == 'partial':
        return {'code': 'coverage_limited', 'label': REASONS['coverage_limited'][0], 'next_action': REASONS['coverage_limited'][1], 'stage': 'details', 'message': error}
    if status == 'unchecked':
        return {'code': 'unchecked', 'label': '미실행', 'next_action': '활성 경로를 수집하면 확인 상태가 기록됩니다.', 'stage': 'not_started'}
    return classify_error(error)


def summarize_sources(sources):
    active = [s for s in sources if s['config'].get('enabled')]
    codes = [s.get('diagnostic', {}).get('code') for s in active]
    return {'total': len(sources), 'enabled': len(active), 'disabled': len(sources) - len(active),
            'not_run': sum(s['status'] == 'unchecked' for s in active),
            'failed': sum(s['status'] == 'failed' and s.get('diagnostic', {}).get('code') not in ('robots_disallowed', 'detail_links_missing') for s in active),
            'blocked': codes.count('robots_disallowed'), 'adapter_needed': codes.count('detail_links_missing'),
            'partial': sum(s['status'] == 'partial' for s in active),
            'success': sum(s['status'] == 'success' for s in active)}


def network_environment(fetcher):
    return {'python': platform.python_version(), 'platform': platform.system(), 'openssl': ssl.OPENSSL_VERSION,
            'truststore_installed': importlib.util.find_spec('truststore') is not None,
            'certificate_backend': getattr(fetcher, 'certificate_backend', 'test_or_custom'),
            'timeout_seconds': getattr(fetcher, 'timeout', None), 'attempts': getattr(fetcher, 'attempts', None),
            'certificate_verification': True, 'respect_robots':getattr(fetcher,'respect_robots',None),
            'user_agent':getattr(fetcher,'user_agent','custom'), 'request_mode':'direct_http' if not getattr(fetcher,'respect_robots',False) else 'robots_checked'}
