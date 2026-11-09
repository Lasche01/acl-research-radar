"""MOCK demonstration: no live model, credentials or network calls."""
import copy
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from aclbrief.service import Service
from aclbrief.screening import GATES

research = '연구 목표는 다중 안테나 이동통신의 빔 탐색 및 CSI 피드백 알고리즘을 개발하고 성능을 평가하는 것이다.'
eligibility = '국내 대학 및 산학협력단은 본 위탁연구의 수행기관으로 직접 신청할 수 있습니다.'
call = '본 사업은 이동통신 연구개발 신규과제의 수행기관을 공개 모집합니다.'
deadline = '신청 접수 마감일은 2099년 12월 31일입니다.'
p = {'title': '[합성] 이동통신 빔 탐색 위탁연구 모집', 'scope': 'rfp',
     'text': '\n'.join([call, research, eligibility, deadline])}
results = {}
with tempfile.TemporaryDirectory() as directory, patch.dict(os.environ, {
        'OPENAI_API_KEY': 'synthetic-not-a-real-key', 'ACL_OPENAI_MODEL': 'mock-model'}), \
        patch('urllib.request.OpenerDirector.open', side_effect=AssertionError('Network forbidden')):
    service = Service(directory, reviewer_mode='openai')
    edge = next(e for e in service.matcher().profile['edges'] if e['to'] == 'beam')
    response = {k: {'verdict': 'yes', 'quote': eligibility if k == 'eligibility' else call if k == 'opportunity' else research,
                   'reason': '합성 원문 근거를 모의 응답으로 제공했습니다.'} for k in GATES}
    response.update(deadline_date='2099-12-31', deadline_quote=deadline, fit_score=87,
        summary='모의 응답: 빔 탐색 실적과 대응합니다. 실제 인력과 수행 역량은 별도 확인합니다.',
        links=[{'evidence_id': edge['from'], 'topic_id': 'beam', 'quote': research,
                'reason': '공개 그래프의 빔 탐색 실적 연결', 'gap': '실적 전문과 현재 역량 미확인'}])
    cases = {'verified_quotes_and_path': copy.deepcopy(response),
             'invented_quote': copy.deepcopy(response), 'unknown_eligibility': copy.deepcopy(response)}
    cases['invented_quote']['links'][0]['quote'] = '원문에 존재하지 않는 인용을 모델이 만들었다고 가정합니다.'
    cases['unknown_eligibility']['eligibility'].update(verdict='unknown', quote='', reason='직접 참여 조건 확인 필요')
    for index, (name, mock_response) in enumerate(cases.items()):
        with patch.object(service.reviewer, 'request', return_value=mock_response):
            n = service.ingest({**p, 'url': f'https://example.org/mock/{index}'})
        s = service.notice_detail(n['id'])['screening']
        results[name] = {'decision': s['decision'], 'validated': s.get('validated', False),
                         'related_candidate': s.get('related_candidate', False)}
    assert results['verified_quotes_and_path']['decision'] == 'recommended'
    assert results['invented_quote']['decision'] == 'review'
    assert not results['invented_quote']['validated']
    assert results['unknown_eligibility']['decision'] == 'review'
    assert results['unknown_eligibility']['related_candidate']
print(json.dumps({'mock_only': True, 'live_api_calls': 0, 'results': results}, ensure_ascii=False, indent=2))
