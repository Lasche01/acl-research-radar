"""Reproducible, document-scoped screening without inference APIs or local AI."""
import re
from .matcher import Matcher
from .screening import Reviewer, GATES, POLICY, fingerprint, precheck
from .store import now
from .documents import deadline_evidence, document_sections, screening_warnings
from .triage import title_screen, period_from_text
from .rule_evidence import evidence_units, technical_units, eligibility_evidence, alias_in, RELATED


INDUSTRY=r'조선|모델하우스|스마트공장|제조\s*AI|안전관리|광원\s*소자|반도체\s*공정|에피택셜|패키징'
CALL=r'(?:연구개발|연구\s*과제|위탁\s*연구|공동\s*연구|연구\s*용역|RFP).{0,120}(?:모집|공모|입찰)|신규\s*(?:지원\s*)?(?:대상\s*)?과제.{0,80}(?:모집|공고|공모)|(?:지원\s*대상|수행\s*기관).{0,70}(?:공모|모집)'

class RuleReviewer(Reviewer):
    def __init__(self):self.model='rules-v6'
    def reset_budget(self):pass
    def status(self):
        return {'configured':False,'provider':'rules','model':self.model,'calls':0,'call_limit':0,
                'note':'API·로컬 AI 없이 문서 역할·원문 근거·주제 관계를 확인합니다.'}

    def assess(self,payload,profile,previous=None):
        base={'policy':POLICY,'profile_hash':fingerprint(profile),'input_hash':fingerprint(payload),
              'provider':'rules','model':self.model,'checked_at':now(),'decision':'review',
              'reason':'','gates':{},'links':[],'score':0,'deadline_date':''}
        blocked=precheck(payload)
        if blocked:return {**base,'decision':'excluded','reason':blocked}
        text=payload.get('text','');period=period_from_text(text)
        base.update(deadline_date=period.get('end_date',''),deadline_quote=period.get('quote',''))
        if period['state']=='expired':return {**base,'decision':'excluded','reason':'신청 마감일 경과'}
        if period['state']=='upcoming':return {**base,'reason':'접수 시작 전입니다.'}
        if payload.get('collection_state')=='deadline_unknown_metadata_only':
            return {**base,'reason':'접수기간 미확인: 기간 내 공고인지 확인하기 전 첨부 분석을 보류합니다.' if period['state']!='open' else '접수기간은 확인됐지만 이전 수집에서 첨부를 읽지 않았습니다. 공고 수집을 다시 실행하세요.'}
        tc=title_screen(payload.get('title',''))
        if tc['state']!='candidate':return {**base,'decision':'excluded' if tc['state']=='excluded' else 'review','reason':tc['reason']}

        units=evidence_units(text)
        result={k:{'verdict':'unknown','quote':'','reason':'판정에 필요한 명시적 원문 근거를 찾지 못했습니다.'} for k in GATES}
        def find(pattern):return next((u for u in units if re.search(pattern,u['quote'],re.I|re.S)),None)
        def setgate(k,u,reason,verdict='yes'):
            if u:result[k]={'verdict':verdict,'quote':u['quote'],'reason':reason}
        setgate('opportunity',find(CALL),'연구 수행기관 모집·신규지원 대상과제 공고 표현을 확인했습니다.')
        core=technical_units(units,bool(re.search(INDUSTRY,payload.get('title',''))))
        if core:setgate('communications',core[0],'연구 목표·행위와 통신 기술이 같은 문맥에 있습니다.')
        elif re.search(INDUSTRY,payload.get('title','')):
            setgate('communications',find(INDUSTRY),'산업 응용·소자·구축 요구가 중심이며 핵심 통신 연구 요구를 확인하지 못했습니다.','no')

        matcher=Matcher(profile);links=[];topic_matches=[]
        for tid,topic in matcher.topics.items():
            if topic.get('enabled') is False or not matcher.paths.get(tid):continue
            direct=next((u for u in core if any(alias_in(a,u['quote']) for a in matcher.aliases(tid))),None)
            related=next((u for u in core if tid in RELATED and re.search(RELATED[tid],u['quote'],re.I)),None) if not direct else None
            u=direct or related
            if not u:continue
            relation='direct' if direct else 'related'
            def overlap(eid):
                e=matcher.evidence[eid];description=e.get('label','')+' '+e.get('abstract','')
                return [a for a in matcher.aliases(tid) if alias_in(a,u['quote']) and alias_in(a,description)]
            eid=max(matcher.paths[tid],key=lambda x:(len(overlap(x)),matcher.evidence[x].get('year',0)))
            shared=overlap(eid)
            topic_matches.append({'topic_id':tid,'evidence_id':eid,'relation':relation,'source':u['source'],'quote':u['quote'],'evidence_terms':shared[:5]})
            links.append({'evidence_id':eid,'topic_id':tid,'quote':u['quote'],
                'reason':('직접 주제 대응: ' if direct else '인접 기술 관계: ')+topic['label']+'에 연결된 실적과 원문 연구 요구를 대조했습니다.'+(' 실적 제목·제공 초록의 공통 표현: '+', '.join(shared[:5]) if shared else ' 실적의 기존 주제 연결 경로를 사용했습니다.'),
                'gap':'실적의 전문·실제 인력·장비 역량은 별도 확인해야 합니다.' if direct else '모델링·예측 또는 신호 처리의 인접 관계입니다. 직접 수행 실적이 입증된 것은 아니며 실적 원문 대조가 필요합니다.'})
        pairs=sorted(zip(topic_matches,links),key=lambda pair:pair[0]['relation']!='direct')
        topic_matches=[a for a,b in pairs];links=[b for a,b in pairs]
        direct_matches=[m for m in topic_matches if m['relation']=='direct']
        if direct_matches:
            m=direct_matches[0];result['lab_fit']={'verdict':'yes','quote':m['quote'],'reason':'활성 연구 주제와 실제 그래프에 연결된 실적 경로를 확인했습니다. 원문 전문과 수행 역량은 별도 검토 대상입니다.'}
        elif topic_matches:
            result['lab_fit']['reason']='인접 기술 관계는 확인했지만 직접 연구 주제 대응은 미확인입니다. 실적 원문을 확인해야 합니다.'
        result['eligibility'],checks=eligibility_evidence(units)
        mixed=find(r'과제별.{0,30}(상이|다르)|세부\s*과제별|여러\s*세부\s*과제|통합공고')
        rfps={s['name'] for s in document_sections(text) if s['role']=='rfp'}
        if core and not mixed and len(rfps)<=1:setgate('specific_scope',core[0],'하나의 문서 문맥에서 구체적인 연구 기술·행위를 확인했습니다.')
        else:result['specific_scope']['reason']='세부 과제별 조건 또는 복수 RFP를 구분해야 합니다.' if mixed or len(rfps)>1 else '세부 연구 요구를 확인하지 못했습니다.'

        # Relevance is independent of application eligibility. Missing eligibility
        # keeps the item in review without erasing its technical priority.
        breakdown={'technical_core':30 if core else 0,
                   'topic_alignment':min(40,24+8*(len(direct_matches)-1)) if direct_matches else 14 if topic_matches else 0,
                   'specific_research':20 if result['specific_scope']['verdict']=='yes' else 0,
                   'research_call':10 if result['opportunity']['verdict']=='yes' else 0}
        deadline,dq=deadline_evidence(text)
        result.update(deadline_date=deadline,deadline_quote=dq,fit_score=sum(breakdown.values()),
            summary='모집·통신 연구·연구실 주제 대응·대학 자격·마감의 원문 근거를 확인했습니다.',links=links[:5])
        try:screen=self.validate(result,payload,profile,base)
        except (ValueError,KeyError,TypeError):return {**base,'reason':'원문 인용·날짜·실적 경로를 검증하지 못했습니다. 문서를 보완하세요.'}
        blocking,optional=screening_warnings(payload)
        if blocking and screen['decision']!='excluded':
            screen.update(decision='review',reason=screen['reason']+'; 핵심 자료 미확보·미판독: '+'; '.join(blocking[:3]))
        sources={k:next((u['source'] for u in units if u['quote']==g['quote']),'') for k,g in screen['gates'].items()}
        return {**screen,'score_breakdown':breakdown,'topic_matches':topic_matches,'gate_sources':sources,
                'eligibility_checks':{k:v[:10] for k,v in checks.items()},'blocking_issues':blocking,'optional_issues':optional,
                'related_candidate':bool(core and topic_matches and screen['decision']=='review')}
