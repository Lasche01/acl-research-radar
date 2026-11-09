"""Run synthetic review cases without HTTP requests or a user database."""
import copy
import json
import sys
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from aclbrief.rules import RuleReviewer

profile=json.loads((ROOT/'aclbrief/data/profile.json').read_text(encoding='utf-8'))
call='본 사업은 이동통신 연구개발 신규과제의 수행기관을 공개 모집합니다.'
goal='연구 목표: 다중 안테나 이동통신의 빔 탐색 및 CSI 피드백 알고리즘을 연구하고 성능 평가 시뮬레이터를 개발한다.'
qual='신청 자격: 국내 대학 및 산학협력단은 본 위탁연구의 수행기관으로 직접 신청할 수 있습니다.'
deadline='신청 접수 마감일은 2099년 12월 31일입니다.'
cases={
    'synthetic_control':'\n'.join([call,goal,qual,deadline]),
    'same_goal_with_announcement_word':'\n'.join([call,'본 공고의 '+goal,qual,deadline]),
    'missing_eligibility':'\n'.join([call,goal,deadline]),
    'enterprise_guarantee_exemption':'\n'.join([call,goal,qual,'중소기업에 한정하여 보증보험 제출을 면제한다.',deadline]),
    'known_limit_bonus_wording':'\n'.join([call,goal,qual,'녹색인증을 받은 기업이 주관연구개발기관으로 신청한 경우(1점)',deadline]),
}
results={}
with patch('urllib.request.OpenerDirector.open',side_effect=AssertionError('Network must not be used')):
    for name,text in cases.items():
        s=RuleReviewer().assess({'title':'[합성 예제] 이동통신 빔 탐색 위탁연구 모집','scope':'rfp','text':text},copy.deepcopy(profile))
        results[name]={'decision':s['decision'],'research_score':s['score'],'eligibility':s['gates']['eligibility']['verdict'],
                       'related_candidate':s.get('related_candidate',False)}
assert results['synthetic_control']['decision']=='recommended'
assert results['same_goal_with_announcement_word']==results['synthetic_control']
assert results['missing_eligibility']['decision']=='review'
assert results['missing_eligibility']['research_score']==results['synthetic_control']['research_score']
assert results['enterprise_guarantee_exemption']['decision']=='recommended'
# Report the known v0.6.0 limitation without claiming that it has been fixed.
print(json.dumps({'synthetic_examples_only':True,'results':results},ensure_ascii=False,indent=2))
