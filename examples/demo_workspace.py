"""Launch the real rules-mode UI with synthetic notices in a temporary DB."""
import argparse
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from aclbrief.server import AppServer
from aclbrief.service import Service


def seed(service):
    call = '본 사업은 이동통신 연구개발 신규과제의 수행기관을 공개 모집합니다.'
    goal = '연구 목표: 다중 안테나 이동통신의 빔 탐색 및 CSI 피드백 알고리즘을 연구하고 성능 평가 시뮬레이터를 개발한다.'
    eligibility = '신청 자격: 국내 대학 및 산학협력단은 본 위탁연구의 수행기관으로 직접 신청할 수 있습니다.'
    deadline = '신청 접수 마감일은 2099년 12월 31일입니다.'
    rows = [
        ('이동통신 빔 탐색·CSI 피드백 위탁연구 모집', '\n'.join([call, goal, eligibility, deadline])),
        ('이동통신 빔 탐색 위탁연구 모집 — 자격 미확인', '\n'.join([call, goal, deadline])),
        ('조선 AX AI 모델하우스 구축 신규과제 모집', '\n'.join([
            '본 사업은 조선 AX 신규과제의 수행기관을 모집합니다.',
            '조선 현장 데이터를 수집하고 산업 AI 모델하우스를 구축한다.', deadline])),
    ]
    for index, (title, text) in enumerate(rows):
        service.ingest({'title': '[합성 예제] ' + title, 'text': text, 'scope': 'rfp',
                        'url': f'https://example.org/acl-demo/{index}', 'source_id': 'demo', 'is_demo': True,
                        'warnings': ['실제 모집 공고가 아닌 화면 확인용 합성 예제입니다.']})


def main():
    parser = argparse.ArgumentParser(description='합성 공고로 실제 앱 화면 확인. API 호출·기관 수집 없음.')
    parser.add_argument('--port', type=int, default=8766)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='acl-readme-demo-') as directory:
        service = Service(directory)
        seed(service)
        server = AppServer(args.port, service)
        print(f'합성 예제 전용 · 기본 규칙 모드 · API 호출 없음\nhttp://127.0.0.1:{server.server_port}', flush=True)
        print('검토함의 예제 표시를 켜세요. 종료: Ctrl+C. 임시 DB는 종료 시 삭제됩니다.', flush=True)
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()


if __name__ == '__main__':
    main()
