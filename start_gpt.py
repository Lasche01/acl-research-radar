"""Explicit paid API launcher. Credentials stay in the process environment."""
import getpass
import os
import sys
from aclbrief.__main__ import main


def configure():
    print('GPT API 모드: 공고·RFP와 연구 프로필을 OpenAI에 전송하며 API 요금이 발생할 수 있습니다.')
    if not os.environ.get('ACL_OPENAI_MODEL', '').strip():
        os.environ['ACL_OPENAI_MODEL'] = input('사용할 API 모델 ID (Structured Outputs 지원): ').strip()
    if not os.environ.get('OPENAI_API_KEY', '').strip():
        os.environ['OPENAI_API_KEY'] = getpass.getpass('OpenAI API Key (화면 표시·파일 저장 안 함): ').strip()
    # This launcher always selects API mode; normal run.py remains rules-only.
    sys.argv[1:1] = ['--reviewer', 'openai']


if __name__ == '__main__':
    configure()
    raise SystemExit(main())
