# GPT 설정과 실행

> [문서 안내](README.md) · [사용 가이드](USAGE.md) · GPT 설정

이 가이드는 GPT 모드를 실행하고 모델·API Key·호출 한도를 설정하는 방법을 설명합니다. 답변에 포함된 인용문과 실적 연결을 프로그램이 확인하는 과정도 다룹니다.

## 시작 전 준비

- Python 3.10 이상과 프로젝트 소스
- 사용할 수 있는 OpenAI API Key와 모델 ID
- Responses API·Structured Outputs를 지원하는 모델
- PDF를 읽는 경우 [선택 의존성](../requirements-optional.txt)

API 호출은 Python 표준 라이브러리를 사용합니다.

> [!IMPORTANT]
> 공고문·RFP와 연구 프로필을 OpenAI에 전송하며 API 요금이 발생할 수 있습니다. 외부 전송이 가능한 자료인지 확인하고 작업별 호출 한도를 설정하세요.

## 1. GPT 모드 실행

Windows는 `start_gpt_windows.bat`, 터미널은 다음 명령을 사용합니다.

```bash
python start_gpt.py serve
```

실행 창에서 모델 ID와 API Key를 입력합니다. Key는 숨김 입력하고 실행 중인 Python 프로세스의 환경변수에 보관합니다.

**확인:** 대시보드의 모드가 GPT API로 표시되는지 확인하세요. **설정됨**은 Key와 모델 값이 입력된 상태입니다. 실제 요청 결과는 공고별 검토 결과에서 확인합니다.

## 2. 첫 공고 검토

1. 기존 원문을 검토하려면 **공고 다시 분석**을 실행합니다.
2. 새 자료를 검토하려면 **공고 수집**, **공고 추가** 또는 **브라우저 자료 가져오기**를 사용합니다.
3. 검토함에서 추천·관련 후보·보류 이유를 확인합니다.
4. 상세 화면의 인용문과 연결 실적을 원문과 대조합니다.

API 요청은 수집·추가·재분석으로 검토가 시작될 때 발생합니다. 마감됐거나 기간·제목 선별을 통과하지 못한 자료는 GPT 요청 전에 보류·제외합니다.

## 환경변수로 실행

| 변수 | 설명 | 기본값 |
|---|---|---|
| `OPENAI_API_KEY` | 사용할 API Key | 실행 창에서 입력 |
| `ACL_OPENAI_MODEL` | 프로젝트에서 사용할 수 있는 모델 ID | 실행 창에서 입력 |
| `ACL_AI_MAX_CALLS` | 작업당 최대 요청 수 | 20, 설정 범위 1~200 |
| `ACL_DATA_DIR` | 데이터 저장 폴더 | 사용자 홈의 `ACLBriefingData` |

환경변수를 설정했다면 다음 CLI 명령으로 실행할 수 있습니다. `--reviewer`는 하위 명령 앞에 둡니다.

```bash
python -m aclbrief --reviewer openai serve
python -m aclbrief --reviewer openai reanalyze
python -m aclbrief --reviewer openai collect --source iris
```

`run.py`, `start_windows.bat`, `daily_run.py`는 기본 규칙 모드입니다. GPT 예약 실행에는 위 CLI 명령을 사용합니다. 예전 `ACL_AI_PROVIDER` 설정만으로는 GPT 모드가 켜지지 않습니다.

[.env.example](../.env.example)은 설정 설명용 파일이며 `.env` 자동 로딩 기능은 없습니다. 실행 창이나 터미널 환경변수로 설정하세요. Key와 모델 권한은 [OpenAI API 플랫폼](https://platform.openai.com/api-keys)에서 관리합니다. 실제 Key는 GitHub에 올리지 마세요.

## 공고 검토 흐름

**기간·제목 선별 → 공고문·RFP 준비 → GPT 요청 → 답변 확인 → 추천·후보·보류 표시** 순서로 진행합니다.

매뉴얼·서식과 핵심 문서를 구분하고, 핵심 문서를 읽지 못하면 자료 보완을 요청합니다. 기간 내 후보의 공고문·RFP와 등록된 연구 주제·논문·특허·사업 정보, 주제별 연결을 GPT에 전송합니다.

GPT는 다음 항목을 `yes/no/unknown`, 인용문과 이유로 반환합니다.

| 항목 | 확인 내용 |
|---|---|
| 연구 모집 | 실제 연구과제 수행기관을 모집하는가 |
| 통신 연구 | 통신 기술 연구가 핵심 목표나 성과물인가 |
| 연구실 관련성 | 등록된 연구 주제·실적과 관련되는가 |
| 대학 참여 자격 | 대학이 연구 수행기관으로 직접 참여할 수 있는가 |
| 세부 연구 내용 | 하나의 세부 과제에 해당하는 연구 내용이 있는가 |

프로그램은 JSON 형식, 인용문의 원문 포함 여부, 실적과 주제의 연결, 날짜·점수를 검사합니다. 인용문은 공고문·RFP에 있어야 합니다. 다섯 항목 모두 `yes`, 미래 마감일, 적합도 70점 이상이면 추천합니다. 마감 당일에는 종료 시각을 확인하도록 보류합니다.

연구 관련성은 있으나 자격 등 조건이 불명확하면 관련 후보로 표시할 수 있습니다. 프롬프트에는 가점·보증 면제와 필수 자격, 담당부서 이름과 연구 목표를 구분하도록 작성했습니다. 답변의 의미가 맞는지는 사람이 원문과 함께 확인합니다.

이 기능은 확보한 텍스트를 검토합니다. 웹 검색·이미지 판독·누락 첨부 수집은 별도 작업입니다. GPT 점수와 규칙 모드의 고정 배점은 기준이 다릅니다. 피드백은 판단 기록과 평가 자료로 보관합니다.

## 호출 한도와 오류 처리

| 상황 | 처리 |
|---|---|
| 같은 원문·프로필·모델·검토 규칙의 완료 결과 | 저장한 결과 재사용 |
| 검토 모드·모델 변경 | 이전 추천을 보류하고 재분석 안내 |
| 작업의 호출 한도 도달 | 보류 후 다음 재분석에서 이어서 처리 |
| 401/403/429·연결 실패 | 설정 확인 안내와 함께 검토 대기 |
| 답변 거절·불완전 응답·JSON 오류 | 검토 대기 |
| 원문 인용·그래프 연결 확인 실패 | 검토 대기 |

API 오류는 자동 재시도하지 않습니다. 오류의 본문·헤더·예외 문자열도 DB 저장 대상에서 제외합니다. 요청 횟수 한도는 토큰·금액 한도와 별도로 관리해야 합니다.

공고 텍스트는 100,000자, 프로필 JSON은 60,000자까지 전송합니다. 초과하면 RFP나 프로필을 나눠 입력하도록 안내합니다. 수집·보관과 규칙 모드의 처리량은 메모리·디스크·파일 구조에 따라 달라집니다. 모델의 문맥 한도가 더 작으면 요청이 실패할 수 있습니다.

요청 주소는 `https://api.openai.com/v1/responses`이며 strict JSON schema와 `store: false`를 사용합니다. 데이터 보존 범위는 OpenAI의 서비스 정책도 함께 확인하세요.

## 테스트와 다음 단계

```bash
python examples/verify_gpt_workflow.py
python -m unittest discover -s tests -q
```

가상 공고와 모의 응답으로 인용·그래프 확인, 오류 처리, 추천·보류를 테스트합니다. 실제 API 호출과 모델별 추천 정확도·비용·속도는 아직 측정하지 않았습니다.

[테스트 범위](VALIDATION.md) · [오류 해결](TROUBLESHOOTING.md#gpt-요청-실패) · [구현](../aclbrief/screening.py) · [통합 테스트](../tests/test_gpt_mode.py)

공식 문서: [Responses API](https://platform.openai.com/docs/api-reference/responses/create) · [Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
