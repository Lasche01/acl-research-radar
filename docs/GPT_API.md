# GPT 모드 사용 방법

GPT 모드는 공고문과 RFP를 연구실 프로필과 비교해, 관련성과 신청 조건을 검토합니다. GPT가 답변에 인용한 문장은 프로그램이 원문과 대조하고, 선택한 연구 주제와 실적이 등록된 그래프에서 연결되는지 확인합니다.

## 실행

Python 3.10 이상이 필요합니다. API 호출에는 Python 표준 라이브러리를 사용합니다. PDF를 읽으려면 [선택 의존성](../requirements-optional.txt)을 설치하세요.

Windows에서는 `start_gpt_windows.bat`를 실행합니다. 터미널에서는 다음 명령을 사용합니다.

```bash
python start_gpt.py serve
```

실행 창에서 사용할 모델 ID와 API Key를 입력합니다. Key는 숨김 입력하고 실행 중인 Python 프로세스의 환경변수에 보관합니다. 화면이 열리면 **공고 다시 분석**을 눌러 저장된 공고를 검토하세요. API 요청은 수집·추가·재분석으로 검토가 시작될 때 발생합니다.

GPT 모드는 공고문·RFP와 연구 프로필을 OpenAI에 전송하며 API 요금이 발생할 수 있습니다. 외부 전송이 가능한 자료인지 먼저 확인하세요.

## 환경변수 설정

| 변수 | 설명 |
|---|---|
| `OPENAI_API_KEY` | 사용할 API Key |
| `ACL_OPENAI_MODEL` | 해당 프로젝트에서 사용할 수 있는 Responses API·Structured Outputs 지원 모델 ID |
| `ACL_AI_MAX_CALLS` | 작업당 최대 요청 수. 기본 20회, 설정 범위 1~200회 |
| `ACL_DATA_DIR` | 데이터 저장 폴더. 테스트용 폴더를 따로 지정할 때 사용 |

환경변수를 설정했다면 다음과 같이 실행할 수 있습니다. `--reviewer` 옵션은 하위 명령 앞에 둡니다.

```bash
python -m aclbrief --reviewer openai serve
python -m aclbrief --reviewer openai reanalyze
python -m aclbrief --reviewer openai collect --source iris
```

기본 실행기인 `run.py`, `start_windows.bat`, `daily_run.py`는 규칙 모드로 동작합니다. GPT를 예약 실행하려면 위 CLI 명령을 예약 작업에 등록하세요. 예전 `ACL_AI_PROVIDER` 설정만으로는 GPT 모드가 켜지지 않습니다.

[.env.example](../.env.example)은 변수 설명용 파일입니다. 프로그램은 `.env`를 자동으로 읽지 않으므로 실행 창에서 입력하거나 터미널 환경변수로 설정하세요. Key와 모델 권한은 [OpenAI API 플랫폼](https://platform.openai.com/api-keys)에서 관리합니다. 실제 Key는 GitHub에 올리지 마세요.

## 공고를 검토하는 과정

1. 접수기간을 확인하고 제목에서 통신 후보를 고릅니다. 마감됐거나 기간·제목을 확인하지 못한 공고는 이 단계에서 보류·제외합니다.
2. 공고문·RFP와 매뉴얼·서식을 구분합니다. 핵심 문서를 읽지 못하면 자료 보완을 요청합니다.
3. 기간 내 후보의 공고문·RFP와 연구 프로필을 GPT에 보냅니다. 프로필에는 등록된 연구 주제, 논문·특허·사업 정보와 주제별 연결이 포함됩니다.
4. GPT는 연구 모집 여부, 통신 연구가 핵심인지, 연구실 실적과 관련되는지, 대학이 직접 참여할 수 있는지, 세부 연구 내용이 있는지를 `yes/no/unknown`과 인용문으로 반환합니다.
5. 프로그램은 JSON 형식, 인용문의 원문 포함 여부, 실적과 주제의 연결, 날짜와 점수를 검사합니다. 인용문은 공고문·RFP에 있어야 합니다.
6. 다섯 조건이 모두 `yes`이고 마감일이 미래이며 적합도가 70점 이상이면 추천합니다. 마감 당일에는 접수 종료 시각을 확인하도록 검토 대기로 남깁니다.
7. 연구 관련성은 있지만 자격 등 조건이 불명확한 공고는 관련 후보로 보여줍니다. 사람이 원문을 확인하고 판단을 기록합니다.

프롬프트에는 기업 가점·보증 면제와 필수 자격, 담당부서 이름과 실제 연구 목표를 구분하도록 작성했습니다. 최종 답변의 의미가 맞는지는 원문과 함께 검토해야 합니다.

이 기능은 확보한 텍스트를 대상으로 합니다. 웹 검색, 이미지 판독, 누락 첨부 수집은 각각 별도 작업입니다. GPT 점수는 관련성에 대한 모델의 판단값이며, 규칙 모드의 고정 배점과 기준이 다릅니다. 기록한 피드백은 판단 기록과 평가 자료로 보관합니다.

## 호출 한도와 오류 처리

| 상황 | 처리 |
|---|---|
| 같은 원문·프로필·모델·검토 규칙의 완료 결과가 있음 | 저장한 결과 재사용 |
| 검토 모드·모델 변경 | 이전 추천을 보류하고 재분석 안내 |
| 작업의 호출 한도 도달 | 남은 공고를 보류하고 다음 재분석에서 이어서 처리 |
| 401/403/429 또는 연결 실패 | 설정 확인 안내와 함께 검토 대기 |
| 답변 거절·불완전 응답·JSON 오류 | 검토 대기 |
| 원문 인용·그래프 연결 확인 실패 | 검토 대기 |

API 오류는 자동으로 재시도하지 않습니다. HTTP 오류의 본문·헤더와 예외 문자열도 DB 저장 대상에서 제외합니다. 요청 횟수 한도는 별도의 토큰·금액 한도와 함께 관리하세요.

공고 텍스트는 100,000자, 프로필 JSON은 60,000자까지 전송합니다. 초과하면 세부 RFP나 프로필을 나눠 입력하도록 안내합니다. 이 한도는 API 전송에 적용하며, 수집·보관과 규칙 모드의 문서 처리량은 메모리·디스크·파일 구조에 따라 달라집니다. 모델의 문맥 한도가 더 작으면 API 요청이 실패할 수 있습니다.

화면의 **설정됨**은 Key와 모델 값이 입력된 상태를 뜻합니다. 실제 요청 결과는 각 공고의 검토 결과에서 확인하세요.

요청 주소는 `https://api.openai.com/v1/responses`로 고정하고 strict JSON schema와 `store: false`를 사용합니다. 데이터 보존 범위는 OpenAI의 서비스 정책도 함께 확인해야 합니다.

## 테스트

가상 공고와 미리 만든 GPT 응답으로 처리 과정을 실행할 수 있습니다.

```bash
python examples/verify_gpt_workflow.py
python -m unittest discover -s tests -q
```

이 테스트는 네트워크 요청을 차단하고 인용·그래프 확인, 오류 처리, 추천·보류 결과를 검사합니다. 실제 API 호출과 모델별 추천 정확도·비용·속도는 아직 측정하지 않았습니다. [전체 테스트 기록](VALIDATION.md)

구현: [GPT 호출과 답변 확인](../aclbrief/screening.py), [서비스](../aclbrief/service.py), [실행기](../start_gpt.py), [통합 테스트](../tests/test_gpt_mode.py), [응답 형식 테스트](../tests/test_screening.py).

공식 문서: [Responses API](https://platform.openai.com/docs/api-reference/responses/create), [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs).
