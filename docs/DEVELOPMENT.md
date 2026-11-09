# 개발 가이드

> [문서 안내](README.md) · 개발 가이드

이 문서는 코드 구조, 공고 처리 흐름, 수정 후 확인할 테스트를 설명합니다. 문제 정의와 AI 활용 과정은 [개발 사례](CASE_STUDY.md#개발-과정과-ai-활용), 실행 절차는 [사용 가이드](USAGE.md)를 참고하세요.

## 개발 환경 준비

- Python 3.10 이상
- PDF 테스트용 pypdf
- JavaScript 문법·UI 테스트용 Node.js. GitHub Actions는 Node.js 22 사용

프로젝트 폴더에서 의존성을 설치합니다.

```bash
python -m pip install -r requirements-optional.txt
```

인증서 지원을 추가할 때는 `requirements-network.txt`를 사용합니다. 규칙 모드와 GPT 호출의 나머지 코드는 Python 표준 라이브러리로 실행합니다.

## 구성 요소

| 구성 요소 | 파일 | 역할 |
|---|---|---|
| 명령과 모드 선택 | [__main__.py](../aclbrief/__main__.py), [start_gpt.py](../start_gpt.py) | CLI 처리와 규칙·GPT 모드 선택 |
| 수집 | [collect.py](../aclbrief/collect.py), [incremental.py](../aclbrief/incremental.py) | HTTP 요청, 목록·첨부 추출, 대기열과 재개 |
| 선별 | [triage.py](../aclbrief/triage.py) | 접수기간·제목 확인 |
| 문서 구분 | [documents.py](../aclbrief/documents.py), [rule_evidence.py](../aclbrief/rule_evidence.py) | 문서 역할과 판정 문장 추출 |
| 규칙 검토 | [rules.py](../aclbrief/rules.py) | 연구 관련성·자격·점수 계산 |
| GPT 검토 | [screening.py](../aclbrief/screening.py) | API 요청, 응답 형식·인용·실적 연결 확인 |
| 연구 그래프 | [matcher.py](../aclbrief/matcher.py) | 주제·실적 경로와 화면에 표시할 연결 구성 |
| 서비스·저장 | [service.py](../aclbrief/service.py), [store.py](../aclbrief/store.py) | 작업 조정, 원문·버전·피드백·진단 보관 |
| 웹 화면 | [server.py](../aclbrief/server.py), [static](../aclbrief/static) | 로컬 HTTP API와 대시보드 |

## 공고 처리 흐름

1. CLI가 검토 모드를 선택하고 `Service`를 생성합니다.
2. 수집기는 기관별 목록을 읽고 기간·제목을 선별합니다.
3. 기간 내 후보의 본문·첨부를 추출하거나 수동 보완 자료를 받습니다.
4. 서비스가 선택한 검토기로 공고와 현재 연구 프로필을 확인합니다.
5. 판정에 사용한 문장과 그래프 연결을 확인하고 표시용 결과를 구성합니다.
6. 원문과 분석 결과를 저장합니다. 원문이 바뀌면 이전 버전을 보존합니다.
7. 화면·브리핑을 만들 때 현재 프로필·검토 방식·마감일·피드백을 다시 확인합니다.

수집 대기열과 검토 결과의 캐시는 각각 관리합니다. 수집 완료와 추천 통과는 별도 상태입니다. 검토 모드가 달라지면 이전 추천을 새 모드의 결과로 재사용하지 않습니다.

## 데이터와 초기 설정

- `aclbrief/data/profile.json`: 공개 목록 기반 초기 연구 주제·실적·연결
- `aclbrief/data/sources.json`: 기관별 초기 수집 경로
- `ACL_DATA_DIR`: 실행 데이터 폴더. 기본값은 사용자 홈의 `ACLBriefingData`
- `briefing.sqlite3`: 설정·공고·버전·피드백·기관 상태·실행 기록

프로필은 저장된 값이 없을 때 초기화합니다. 기관 경로는 누락 항목을 추가하고 일부 구버전 설정을 보정합니다. 기존 실행 환경의 프로필과 경로는 DB에 저장된 설정을 기준으로 확인하세요. 사용자 DB와 내려받은 원문은 공개 소스에 포함하지 않습니다.

## 규칙 모드의 점수

점수는 검토 우선순위를 정하는 값입니다. 마감일과 자격은 별도로 통과해야 추천됩니다.

| 항목 | 배점 |
|---|---:|
| 구체적인 통신 연구 목표와 수행 내용 | 30 |
| 공고와 연구실 주제의 직접 관련성 | 1개 주제 24, 2개 32, 3개 이상 40 |
| 직접 관련성 없이 인접 기술만 연결 | 위 항목 대신 14, 추가 검토 필요 |
| 하나의 세부 과제 내용 확인 | 20 |
| 연구과제 모집 확인 | 10 |

동일 주제의 여러 실적 중에는 공고와 제목·제공 초록의 공통 기술 표현이 많은 실적을 우선합니다. 비교 범위는 등록된 프로필입니다. 인력·장비와 실적 전문은 별도로 확인해야 합니다.

## 문서와 신청 조건 처리

- 대학의 주관·공동·위탁 역할을 확인할 수 있는 원문 문장을 사용합니다.
- 법령 조문만 있는 자격, 허용·제한 문장의 충돌, 필수 기업 주관·컨소시엄 조건은 적용 범위를 추가로 확인합니다.
- 기업의 가점·보증보험 면제는 해당 부가 조건의 범위에서 검토합니다.
- 공고문·RFP를 읽지 못하면 검토를 보류하고, 매뉴얼·서식·참고 법령의 오류는 따로 기록합니다.

## 수정 후 확인

### 1. 관련 소스와 테스트 확인

수집 변경은 기간·제목별 실제 요청 여부와 대기열 재개를, 판정 변경은 인용문·자격·그래프 연결과 보류 처리를 확인합니다. [테스트 항목](VALIDATION.md#테스트-범위)에서 관련 범위를 찾을 수 있습니다.

### 2. 자동 검사 실행

```bash
python -m unittest discover -s tests -q
node --check aclbrief/static/app.js
node tests/test_ui_filters.js
python examples/verify_evidence.py
python examples/verify_gpt_workflow.py
```

현재 v0.7.1의 Python 테스트는 252개입니다. API 테스트는 모의 응답을 사용합니다. [실행 로그](validation-log.txt)

### 3. 실행 결과 확인

규칙 모드의 화면과 가상 공고는 다음 명령으로 확인합니다.

```bash
python examples/demo_workspace.py
```

실제 API를 테스트할 때는 [GPT 설정](GPT_API.md)의 전송 범위와 호출 한도를 먼저 확인하세요. GitHub Actions는 push·PR에서 자동 검사를 실행합니다. 결과는 [Actions](https://github.com/Lasche01/acl-research-radar/actions)에서 확인합니다.

## 패키지 빌드

wheel 패키지가 필요하면 다음 명령을 사용합니다.

```bash
python -m pip wheel . --no-deps -w dist
```

기본 실행은 소스와 Python을 사용합니다. 생성한 패키지·캐시·실행 DB는 Git 추적 대상에서 제외합니다.

다음 문서: [테스트 기록](VALIDATION.md) · [개발 사례](CASE_STUDY.md) · [공개 전 점검](PUBLICATION_CHECK.md)
