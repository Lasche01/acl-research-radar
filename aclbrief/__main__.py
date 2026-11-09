import argparse
import json
import os
import sys
import threading
import webbrowser
from pathlib import Path
from .service import Service
from .server import AppServer
from . import __version__


def main():
    parser = argparse.ArgumentParser(description='ACL 연구실 맞춤 공고 브리핑 프로그램')
    parser.add_argument('--data-dir', default=os.environ.get('ACL_DATA_DIR', str(Path.home()/'ACLBriefingData')))
    parser.add_argument('--reviewer', choices=('rules', 'openai'), default='rules',
                        help='기본 rules; openai는 유료 GPT API 심사를 명시적으로 활성화')
    sub = parser.add_subparsers(dest='command')
    serve = sub.add_parser('serve', help='로컬 대시보드 실행')
    serve.add_argument('--port', type=int, default=8765)
    serve.add_argument('--no-browser', action='store_true')
    collect = sub.add_parser('collect', help='활성 수집 경로를 한 번 실행')
    collect.add_argument('--source', action='append', help='특정 수집 경로 ID; 반복 지정 가능')
    brief = sub.add_parser('brief', help='Markdown 브리핑 저장')
    brief.add_argument('--output', default='briefing.md')
    backup = sub.add_parser('backup', help='SQLite 전체 백업')
    backup.add_argument('--output', required=True)
    diagnose = sub.add_parser('diagnose', help='본문·개인 판단을 제외한 수집 진단 내보내기')
    diagnose.add_argument('--output', default='source-diagnostics.json')
    imp = sub.add_parser('import', help='단일 공고 JSON 입력')
    imp.add_argument('file')
    sub.add_parser('demo', help='가상 예제 5개 추가')
    sub.add_parser('reanalyze', help='현재 프로필과 모델로 기존 공고 재분석')
    args = parser.parse_args()
    if args.reviewer == 'openai' and not (os.environ.get('OPENAI_API_KEY', '').strip() and os.environ.get('ACL_OPENAI_MODEL', '').strip()):
        parser.exit(2, 'GPT 모드는 OPENAI_API_KEY와 ACL_OPENAI_MODEL 설정이 필요합니다. docs/GPT_API.md를 확인하세요.\n')
    service = Service(args.data_dir, reviewer_mode=args.reviewer)
    if args.reviewer == 'openai':
        print('GPT API 모드: 공고·RFP와 연구 프로필을 OpenAI에 전송합니다. API 요금이 발생할 수 있습니다.', flush=True)
    if args.command in ('serve', None):
        port = getattr(args, 'port', 8765)
        try:
            server = AppServer(port, service)
        except OSError as exc:
            parser.exit(2, f'실행 포트를 열 수 없습니다: {exc}\n다른 포트: python -m aclbrief serve --port 8766\n')
        url = f'http://127.0.0.1:{server.server_port}'
        print(f'ACL Briefing v{__version__}\n대시보드: {url}\n데이터: {service.store.directory}\n종료: Ctrl+C', flush=True)
        if not getattr(args, 'no_browser', False):
            threading.Timer(0.5, lambda: webbrowser.open(url)).start()
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            print('\n종료합니다. 진행 중 수집은 재실행할 수 있습니다.')
        finally:
            server.server_close()
    elif args.command == 'collect':
        result = service.collect(args.source)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result['status'] == 'completed' else 2
    elif args.command == 'brief':
        Path(args.output).write_text(service.briefing(), encoding='utf-8')
        print(Path(args.output).resolve())
    elif args.command == 'backup':
        service.store.backup(args.output)
        print(Path(args.output).resolve())
    elif args.command == 'diagnose':
        Path(args.output).write_text(json.dumps(service.diagnostics(), ensure_ascii=False, indent=2), encoding='utf-8')
        print(Path(args.output).resolve())
    elif args.command == 'import':
        print(json.dumps(service.ingest(json.loads(Path(args.file).read_text(encoding='utf-8'))), ensure_ascii=False))
    elif args.command == 'demo':
        print(json.dumps(service.demo(), ensure_ascii=False))
    elif args.command == 'reanalyze':
        print(json.dumps(service.reanalyze(), ensure_ascii=False))
    return 0


if __name__ == '__main__':
    sys.exit(main())
