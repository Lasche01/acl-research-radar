import json
import hashlib
import threading
import uuid
from datetime import datetime, timezone, timedelta
from pathlib import Path
from .collect import Fetcher
from .matcher import Matcher, validate_profile
from .store import Store, now, canonical_url
from .screening import Reviewer, effective_screening, fingerprint
from .rules import RuleReviewer
from .incremental import collect_incremental
from .routes import FALLBACKS, route_status, collection_plan
from .captures import import_capture
from .locking import TaskLock
from .diagnostics import classify_error, status_diagnostic, summarize_sources, network_environment
from . import __version__
from .notices import document_check, relevance_key
from .documents import document_role

BASE = Path(__file__).parent
LABELS = {'direct': '직접 관련', 'candidate': '관련 후보', 'expand': '확장 후보', 'review': '검토 필요', 'low': '낮은 관련성'}

DEMO = [
    {'title': '[예제] 계층적 빔 탐색의 측정·피드백 부담 감소', 'text': '연구 목표는 다중 안테나 이동통신 시스템의 계층적 빔 탐색 과정에서 측정 횟수와 피드백 오버헤드를 줄이는 것이다. DNN 기반 빔 코드북 설계와 CSI 피드백 알고리즘을 개발하고 이동성 조건에서 탐색 지연과 빔 선택 성능을 비교한다. 성과물은 재현 가능한 시뮬레이터와 알고리즘 평가 보고서이다.', 'scope': 'rfp'},
    {'title': '[예제] AI 데이터센터용 광원 소자 제작', 'text': 'AI 데이터센터 광통신용 고밀도 병렬 광원 소자를 제작한다. 반도체 에피택셜 성장, 발광 소자 공정, 광출력 및 열 안정성 측정, 패키징 신뢰성 검증을 수행한다. 연구기관은 소자 제작 공정 및 광원 패키지의 실험 검증 역량을 갖추어야 한다. 최종 성과물은 제작한 광원 시제품과 공정 수율 보고서이다.', 'scope': 'rfp'},
    {'title': '[예제] 시변 채널에서 OTFS 심볼 검출', 'text': '고속 이동에 따른 시변 채널에서 OTFS 심볼 검출 복잡도를 낮추는 수신기를 연구한다. 채널 추정 오차를 고려하고 프레임 분할 방식과 간섭 제거 방법을 비교한다. 지연 도플러 영역 전송 모델을 구축하여 BER, 전송률, 검출 연산량을 평가한다. 성과물은 검출 알고리즘과 시뮬레이션 코드 및 성능 보고서이다.', 'scope': 'rfp'},
    {'title': '[예제] 수중 이동 채널 추정', 'text': '수중 음향 전파 특성을 반영하여 수중 통신의 시변 채널 추정 기법을 개발한다. 해양 환경의 음속 분포와 다중경로 변화를 고려하고 실제 해상 데이터를 확보하여 검증한다. 채널 추정 알고리즘뿐 아니라 수중 음향 시험환경 구축 및 해상 실측 데이터 처리 역량이 필요하다. 실험 데이터와 검증 보고서를 제출한다.', 'scope': 'rfp'},
    {'title': '[예제] 여러 세부 과제를 포함한 통합공고', 'text': '본 공고는 통신 기술, 소재, 제조 분야의 다양한 과제를 포함한다. 일부 분야는 OTFS와 관련되어 있으며, 개별 과제별 지원 규모와 참여 자격이 상이하다. 본 목록에 표시된 마감일은 통합공고의 가장 늦은 마감일이며 세부 연구개발계획과 성과물 및 신청 조건은 각 RFP를 반드시 확인해야 한다.', 'scope': 'notice'}]


class Service:
    def __init__(self, directory, fetcher=None, reviewer_mode='rules'):
        if reviewer_mode not in ('rules', 'openai'):
            raise ValueError('지원하지 않는 심사 모드입니다.')
        self.store = Store(directory)
        self.store.seed(json.loads((BASE/'data/profile.json').read_text(encoding='utf-8')), json.loads((BASE/'data/sources.json').read_text(encoding='utf-8')))
        self.fetcher = fetcher or Fetcher()
        self.lock = TaskLock(self.store.directory)
        self.reviewer = Reviewer(enabled=True) if reviewer_mode == 'openai' else RuleReviewer()
        self.cancel_event = threading.Event()
        self.collection_progress = {}
        if isinstance(self.fetcher, Fetcher):self.fetcher.cancel_event = self.cancel_event

    def matcher(self):
        return Matcher(self.store.get_setting('profile'))

    def analyze(self, payload, previous=None):
        matcher = self.matcher()
        screening = self.reviewer.assess(payload, matcher.profile, previous)
        if screening.get('gates'):
            analysis=matcher.analyze(payload)
        else:
            analysis={'category':'review','priority':0,'mode':'rule_screening','mappings':[],
                'warnings':list(payload.get('warnings',[])),'quality':'metadata_only' if payload.get('collection_state') else 'pre_screened',
                'analyzed_at':now(),'profile_version':matcher.profile.get('as_of_kst',''),'summary':screening.get('reason','')}
        analysis['screening'] = screening
        if screening.get('provider')=='rules':analysis['mode']='rule_screening'
        analysis['summary'] = screening['reason']
        analysis['category'] = 'candidate' if screening['decision'] == 'recommended' else 'low' if screening['decision'] == 'excluded' else 'review'
        analysis['priority'] = screening['score'] if screening['decision'] != 'excluded' else 0
        if screening.get('validated'):
            analysis['mode'] = 'rule_screening' if screening['provider']=='rules' else 'openai_screening'
            direct={m['topic_id'] for m in screening.get('topic_matches',[]) if m['relation']=='direct'}
            analysis['mappings'] = [matcher.mapping(m['topic_id'], m['evidence_id'], m['quote'], 'direct' if not screening.get('topic_matches') or m['topic_id'] in direct else 'transfer', m['reason'], m['gap']) for m in screening['links']]
            analysis['warnings'] = list(payload.get('warnings', [])) + ['원문 인용과 그래프 경로를 검사했습니다. 실제 연구 적합성과 신청 조건은 최종 확인하세요.']
        if screening['decision'] == 'excluded':
            analysis['mappings'] = []
        return analysis

    def ingest(self, payload):
        if payload.get('scope') not in ('rfp', 'notice'):
            raise ValueError('세부 RFP 또는 공고 단위를 선택하세요.')
        if payload.get('eligibility', 'unknown') not in ('unknown', 'eligible', 'ineligible'):
            raise ValueError('신청 자격 상태가 올바르지 않습니다.')
        if payload.get('eligibility', 'unknown') != 'unknown' and not payload.get('eligibility_note', '').strip():
            raise ValueError('신청 자격 판단 근거를 입력하세요.')
        payload = {**payload, 'url': canonical_url(payload.get('url', '')),
                   'title': str(payload.get('title', '')).strip()[:400], 'text': str(payload.get('text', '')).strip()}
        existing=self.store.matching_notice(payload)
        if not payload.get('captures'):
            old=existing['payload'] if existing and existing['payload'].get('captures') else None
            if old:
                changed=hashlib.sha256(payload['text'].encode()).hexdigest()!=old.get('capture_base_text_hash')
                refresh={'text':payload['text'],'checked_at':now()} if changed else old.get('source_refresh')
                payload={**old,'collected_url':payload.get('collected_url',payload['url'])}
                if changed:
                    payload['source_refresh']=refresh
                    payload['warnings']=list(dict.fromkeys(payload.get('warnings',[])+['공식 원문이 변경되었습니다. 직접 보완한 자료와 다시 대조하세요.']))
        digest = fingerprint(payload)
        previous = existing['analysis'].get('screening') if existing and fingerprint(existing['payload'])==digest else None
        return self.store.upsert(payload, self.analyze(payload, previous))

    def import_capture(self,data,local_files=None):
        return import_capture(self,data,local_files)

    def collect_alternatives(self,sid):
        ids=FALLBACKS.get(sid,[])
        if not ids:raise ValueError('확인된 공식 재게시 경로가 없습니다. 직접 확보한 자료를 가져오세요.')
        return self.collect(ids)

    def demo(self):
        return [self.ingest({**p, 'source_id': 'demo', 'is_demo': True, 'warnings': ['실제 공고가 아닌 기능 확인용 가상 예제입니다.']}) for p in DEMO]

    def state(self):
        sources = self.store.sources()
        records = self.notice_records()
        sources=route_status(sources,records)
        collection_summary=summarize_sources([{**s,'config':{**s['config'],'enabled':s['effective_enabled']}} for s in sources])
        collection_summary['automatic_added']=sum(s['effective_enabled'] and not s['config'].get('enabled') for s in sources)
        return {'version': __version__, 'time': now(), 'collection_progress': dict(self.collection_progress), 'profile': self.store.get_setting('profile'),
                'notices': [self.light_notice(n) for n in records if not n['document']['excluded']],
                'excluded': [self.light_notice(n) for n in records if n['document']['excluded']],
                'sources': sources, 'source_summary': summarize_sources(sources), 'collection_summary':collection_summary,
                'screening_summary':self.screening_summary(records),
                'network': network_environment(self.fetcher), 'runs': self.store.runs(), 'ai': self.reviewer.status(), 'labels': LABELS}

    @staticmethod
    def light_notice(n):
        return {**n,'payload':{k:v for k,v in n['payload'].items() if k not in ('text','source_refresh')}}

    def notice_detail(self,nid):
        n=self.store.notice(nid)
        with self.store.connect() as db:
            f=db.execute('SELECT * FROM feedback WHERE notice_id=? ORDER BY id DESC LIMIT 1',(nid,)).fetchone()
            n['feedback']=dict(f) if f else None
        n['document']=document_check(n['payload']);n['screening']=effective_screening(n,self.store.get_setting('profile'), self.reviewer)
        n['screening_stage']=self.screening_stage(n)
        return n

    @staticmethod
    def screening_stage(n):
        p=n['payload']
        if p.get('collection_state')=='title_metadata_only' and not p.get('captures') and not n.get('feedback'):
            return 'title'
        if p.get('collection_state')=='deadline_unknown_metadata_only':return 'period'
        if n['screening'].get('gates'):return 'detailed'
        return 'other'

    @staticmethod
    def screening_summary(records):
        actual=[n for n in records if not n['is_demo'] and not n['document']['excluded']]
        return {'stored':len(actual),'recommended':sum(n['screening']['decision']=='recommended' for n in actual),
                'title_pending':sum(n['screening_stage']=='title' and n['screening']['decision']=='review' for n in actual),
                'period_pending':sum(n['screening_stage']=='period' and n['screening']['decision']=='review' for n in actual),
                'detailed':sum(n['screening_stage']=='detailed' for n in actual),
                'related_candidates':sum(n['screening']['decision']=='review' and bool(n['screening'].get('related_candidate')) for n in actual),
                'review':sum(n['screening_stage']!='title' and n['screening']['decision']=='review' for n in actual)}

    def notice_records(self):
        # Apply the gate to old databases too. Original text, versions and feedback stay intact.
        records = self.store.notices()
        profile = self.store.get_setting('profile')
        for n in records:
            n['document'] = document_check(n['payload'])
            n['screening'] = effective_screening(n, profile, self.reviewer)
            n['screening_stage']=self.screening_stage(n)
        return sorted(records, key=lambda n: ({'recommended': 2, 'review': 1, 'excluded': 0}[n['screening']['decision']],
                                             n['screening'].get('score', 0), relevance_key(n)), reverse=True)

    def diagnostics(self):
        sources = self.store.sources()
        records=[n for n in self.notice_records() if not n['is_demo']]
        counts={k:sum(n['screening']['decision']==k for n in records) for k in ('recommended','review','excluded')}
        unknown={k:sum(n['screening'].get('gates',{}).get(k,{}).get('verdict')=='unknown' for n in records) for k in ('opportunity','communications','lab_fit','eligibility','specific_scope')}
        review_reasons={}
        for n in records:
            if n['screening']['decision']=='review':
                reason=n['screening']['reason'];review_reasons[reason]=review_reasons.get(reason,0)+1
        # Bounded examples expose the failed gates without exporting private bodies,
        # captures, manual judgements, profile data or quoted private attachments.
        def sample(n):
            p=n['payload'];s=n['screening']
            return {'id':n['id'],'title':n['title'],'url':n['url'],'source_id':n['source_id'],
                'stage':n['screening_stage'],'decision':s['decision'],'reason':s['reason'],
                'score':s.get('score',0),'deadline_date':s.get('deadline_date',''),
                'gates':{k:{'verdict':v.get('verdict'),'reason':v.get('reason',''),'quote':v.get('quote','')[:700],
                    'source':s.get('gate_sources',{}).get(k,''),'quote_chars':len(v.get('quote',''))} for k,v in s.get('gates',{}).items()},
                'score_breakdown':s.get('score_breakdown',{}),'blocking_issues':s.get('blocking_issues',[]),
                'optional_issues':s.get('optional_issues',[]),'eligibility_checks':s.get('eligibility_checks',{}),
                'related_candidate':bool(s.get('related_candidate')),
                'matching_relations':{k:sum(m.get('relation')==k for m in s.get('topic_matches',[])) for k in ('direct','related')},
                'body_chars':len(p.get('text','')),'attachments':[{'name':a.get('name',''),'status':a.get('status',''),
                    'role':document_role(a.get('name','')),
                    'required_for_screening':a.get('required_for_screening'), 'extracted_chars':a.get('extracted_chars'),
                    'error':a.get('error','')} for a in p.get('attachments',[])],
                'warnings':p.get('warnings',[])}
        public=[n for n in records if n['source_id'] not in ('manual','demo') and not n['payload'].get('captures') and not n.get('feedback')]
        details=[n for n in public if n['screening_stage']=='detailed']
        probes=[n for n in public if n['screening_stage']=='period']
        return {'screening_summary':{**self.screening_summary(records),'counts':counts,'unknown_gates':unknown,'review_reasons':review_reasons},
                'screening_samples':{'detailed':[sample(n) for n in details[:100]],'period_pending':[sample(n) for n in probes[:30]],
                    'detailed_total':len(details),'period_pending_total':len(probes),'note':'각 상한은 진단 예시 개수입니다. 수집·분석 범위 제한이 아닙니다.'},
                'app_version': __version__, 'exported_at': now(), 'network': network_environment(self.fetcher),
                'summary': summarize_sources(sources),
                'sources': [{'id': s['id'], 'name': s['config']['name'], 'enabled': bool(s['config'].get('enabled')),
                             'url': s['config']['url'], 'status': s['status'], 'checked_at': s['checked_at'],
                             'last_success': s['last_success'], 'discovered': s['discovered'],
                             'diagnostic': s['diagnostic'], 'error': s['error'], 'alternative_routes': s.get('alternative_routes',[])} for s in route_status(sources,records)],
                'note': '프로필·전체 공고 본문·개별 사용자 판단·환경변수·인증정보는 포함하지 않습니다. 공개 자동 수집 공고의 제목·주소·항목별 짧은 원문 인용·출처 파일명·판정·점수 내역·추출량 예시를 포함합니다. 직접 입력·보완하거나 판단을 기록한 자료는 개별 예시에서 제외합니다.'}

    def save_profile(self, profile):
        validate_profile(profile)
        self.store.set_setting('profile', profile)
        return {'saved': True, 'note': '프로필을 저장했습니다. 기존 공고에 적용하려면 다시 분석을 실행하세요.'}

    def add_evidence(self, data):
        from .store import canonical_url
        if not data.get('label', '').strip() or not data.get('topics'):
            raise ValueError('실적명과 연결할 연구 주제가 필요합니다.')
        p = self.store.get_setting('profile')
        ids = {n['id'] for n in p['nodes'] if n['type'] == 'research_topic'}
        if any(t not in ids for t in data['topics']):
            raise ValueError('알 수 없는 연구 주제입니다.')
        if data.get('type') not in ('project', 'publication', 'patent'):
            raise ValueError('실적 유형을 선택하세요.')
        if data['type'] == 'project' and (not data.get('sponsor', '').strip() or not data.get('role', '').strip()):
            raise ValueError('사업은 발주기관과 연구실 역할을 기록해야 합니다.')
        eid = 'user_' + uuid.uuid4().hex[:10]
        year = int(data.get('year') or datetime.now().year)
        if not 1900 <= year <= datetime.now().year + 2:
            raise ValueError('실적 연도를 확인하세요.')
        node = {'id': eid, 'type': data['type'], 'label': data['label'][:400], 'year': year,
                'url': canonical_url(data.get('url', '')), 'abstract': data.get('abstract', '')[:16000],
                'sponsor': data.get('sponsor', '')[:200], 'role': data.get('role', '')[:300],
                'period': data.get('period', '')[:200], 'evidence_level': 'user_supplied', 'full_text_reviewed': False}
        p['nodes'].append(node)
        for tid in dict.fromkeys(data['topics']):
            p['edges'].append({'from': eid, 'to': tid, 'relation': 'topic_linked_by_user', 'human_validated': True,
                               'provenance': {'source': 'user', 'url': node['url']}})
        p['as_of_kst'] = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        self.save_profile(p)
        return {'id': eid, 'saved': True}

    def cancel_collection(self):
        running=bool(self.collection_progress.get('running'))
        if running:self.cancel_event.set()
        return {'stop_requested': running}

    def collect(self, source_ids=None):
        if not self.lock.acquire(blocking=False):raise ValueError('진행 중인 작업이 있습니다.')
        self.cancel_event.clear()
        self.collection_progress={'running':True,'pages_read':0,'processed':0}
        rid=uuid.uuid4().hex[:12];self.store.run(rid,'collect','running')
        self.reviewer.reset_budget()
        contexts=[]
        try:
            plan=collection_plan(self.store.sources(),source_ids)
            for source in plan['sources']:
                contexts.append({'source':source,'attempted':set(),'refresh':True,'aggregate':None,'problems':[],
                                 'known':None,'started':False,'finished':False,'page_signatures':{}})
            def results():return [c['result'] for c in contexts if 'result' in c]
            def publish(c):
                source=c['source'];r=c['aggregate'] or {'discovered':0,'processed':0,'pages_read':0,'skipped_navigation':0,
                    'changes':[],'notes':[],'attachment_errors':[],'pending_details':0,'pending_pages':0,'triage_counts':{}}
                if c.get('exception'):
                    d=classify_error(c['exception']);r['status']='blocked' if d['code']=='robots_disallowed' else 'failed'
                    r['error']=str(c['exception'])[:1600]
                else:
                    errors=list(dict.fromkeys(c['problems']))
                    if self.cancel_event.is_set():errors.append('사용자가 수집을 중지했습니다. 대기열을 보존합니다.')
                    if r.get('pending_details'):errors.append(f"미처리·재시도 대기 상세 {r['pending_details']}건.")
                    r['error']='\n'.join(errors)
                    r['status']='partial' if errors or not c['finished'] or r.get('pending_pages') else 'success'
                    d=status_diagnostic(r['status'],r['error'])
                    if r.get('list_error'):
                        d=status_diagnostic(r['status'],r['error'],r['list_error'])
                        r['status']='partial' if r.get('discovered') or r.get('processed') else 'failed'
                    if self.cancel_event.is_set():d.update(code='stopped',label='사용자 중지',stage='stopped',next_action='다시 실행하면 남은 작업을 이어갑니다.')
                r['coverage']='deadline_title_staged'
                d.update(automatic_for=plan['automatic'].get(source['id'],[]),directly_requested=source['id'] in plan['requested'],
                    **{k:r.get(k,0) for k in ('processed','pages_read','skipped_navigation','pending_details','pending_pages')},
                    triage_counts=r.get('triage_counts',{}),attachment_errors=r.get('attachment_errors',[]),notes=r.get('notes',[]),coverage=r['coverage'])
                self.store.source_result(source['id'],r['status'],r['discovered'],r.get('error',''),d)
                c['result']={'source_id':source['id'],**r,'diagnostic':d}
            queue=list(contexts)
            while queue and not self.cancel_event.is_set():
                c=queue.pop(0);source=c['source'];config=source['config'];c['started']=True
                aggregate=c['aggregate'] or {}
                def progress(pages,processed,pending,waiting):
                    self.collection_progress={'running':True,'source':config.get('name',source['id']),
                        'pages_read':aggregate.get('pages_read',0)+pages,'processed':aggregate.get('processed',0)+processed,
                        'pending_details':pending,'pending_pages':waiting,'triage_counts':aggregate.get('triage_counts',{})}
                try:
                    if c['known'] is None:c['known']=self.store.expired_collection_history(source['id'])
                    key='collection_queue_'+source['id']
                    batch=collect_incremental(config,self.fetcher,self.ingest,self.store.get_setting(key),
                        lambda data:self.store.set_setting(key,data),limit=30,max_pages=1,attempted=c['attempted'],
                        refresh=c['refresh'],cancel=self.cancel_event,progress=progress,known=c['known'],page_signatures=c['page_signatures'])
                    c['refresh']=False
                    c['problems'].extend(e for e in batch.get('error','').splitlines() if not e.startswith('다음 실행 대기:'))
                    if c['aggregate'] is None:c['aggregate']={**batch}
                    else:
                        for k in ('discovered','processed','pages_read','skipped_navigation'):c['aggregate'][k]+=batch.get(k,0)
                        for k in ('changes','notes','attachment_errors'):c['aggregate'][k].extend(batch.get(k,[]))
                        for k in ('pending_details','pending_pages','remaining_unattempted','cancelled'):c['aggregate'][k]=batch.get(k)
                        for k,v in batch.get('triage_counts',{}).items():c['aggregate'].setdefault('triage_counts',{})[k]=c['aggregate']['triage_counts'].get(k,0)+v
                        if batch.get('list_error'):c['aggregate']['list_error']=batch['list_error']
                    more=bool(batch.get('pending_pages') or batch.get('remaining_unattempted'))
                    stalled=batch.get('pages_read',0)==0 and batch.get('processed',0)==0 and batch.get('pending_pages')
                    if stalled:c['problems'].append('목록 탐색을 진행하지 못했습니다. 다음 실행에서 재시도합니다.')
                    c['finished']=not more or bool(stalled) or batch.get('cancelled',False)
                    if not c['finished']:queue.append(c)
                except Exception as exc:c['exception']=exc;c['finished']=True
                publish(c)
                self.store.run(rid,'collect','running',{'sources':results()})
            if self.cancel_event.is_set():
                for c in contexts:
                    if c['started']:publish(c)
            outcome='stopped' if self.cancel_event.is_set() else 'completed' if results() and all(r['status']=='success' for r in results()) else 'needs_review'
            self.store.run(rid,'collect',outcome,{'sources':results(),'note':'마감 우선 → 제목 선별 → 기간 내 후보의 상세·첨부 분석. 기관별 목록 한 쪽씩 순환하며 공식 재게시 경로를 자동 포함합니다.'})
            return {'run_id':rid,'status':outcome,'sources':results(),'requested_sources':sorted(plan['requested']),'automatic_sources':plan['automatic']}
        except Exception as exc:
            self.store.run(rid,'collect','failed',{'error':str(exc)});raise
        finally:
            self.collection_progress={**self.collection_progress,'running':False}
            self.cancel_event.clear();self.lock.release()

    def reanalyze(self):
        if not self.lock.acquire(blocking=False):
            raise ValueError('진행 중인 작업이 있습니다.')
        rid = uuid.uuid4().hex[:12]
        self.store.run(rid, 'reanalyze', 'running')
        try:
            self.reviewer.reset_budget()
            notices = self.store.notices()
            # Prioritize documents not attempted because of a budget, ahead of repeated API failures.
            notices.sort(key=lambda n: n['analysis'].get('screening', {}).get('error_code') != 'budget_exhausted')
            for notice in notices:
                self.store.set_analysis(notice['id'], self.analyze(notice['payload'], notice['analysis'].get('screening')))
            self.store.run(rid, 'reanalyze', 'completed', {'count': len(notices)})
            return {'run_id': rid, 'count': len(notices)}
        except Exception as exc:
            self.store.run(rid, 'reanalyze', 'failed', {'error': str(exc)})
            raise
        finally:
            self.lock.release()

    def evaluate(self):
        from .matcher import term_in
        rows = [n for n in self.store.notices() if not n['is_demo'] and n.get('feedback')
                and n['feedback']['split'] == 'test' and n['feedback']['notice_version'] == n['version']
                and n['feedback']['verdict'] in ('relevant', 'irrelevant')]
        results = {}
        for method in ('broad_keywords', 'graph'):
            tp = fp = fn = tn = 0
            for n in rows:
                truth = n['feedback']['verdict'] == 'relevant'
                predicted = (any(term_in(k, n['payload']['text']) for k in ['ai', '통신', '네트워크', '위성', '광통신']) if method == 'broad_keywords'
                             else effective_screening({**n, 'feedback': None}, self.store.get_setting('profile'), self.reviewer)['decision'] == 'recommended')
                tp += int(truth and predicted); fp += int(not truth and predicted)
                fn += int(truth and not predicted); tn += int(not truth and not predicted)
            results[method] = {'n': len(rows), 'tp': tp, 'fp': fp, 'fn': fn, 'tn': tn,
                               'precision': tp / (tp + fp) if tp + fp else None,
                               'recall': tp / (tp + fn) if tp + fn else None}
        return {'methods': results, 'note': '사용자가 평가용으로 지정한 최신 버전의 관련/무관 판단만 집계합니다. 예제 제외. 표본을 늘려 검증하세요.'}

    def briefing(self, include_demo=False):
        notices = [n for n in self.notice_records() if not n['document']['excluded'] and (include_demo or not n['is_demo'])]
        selected = [n for n in notices if n['screening']['decision'] == 'recommended'][:20]
        day = datetime.now(timezone(timedelta(hours=9))).date().isoformat()
        lines = [f'# ACL 연구과제 브리핑 — {day} KST', '', '근거 심사를 통과한 추천은 적합도 순 최대 20건이며, 조건 미확인 관련 후보는 별도 표시합니다. 수집 범위와 표시 수는 별개입니다. 실제 신청 전 RFP와 자격을 확인하세요.', '']
        if not selected:
            lines += ['현재 저장된 자료에서 추천 후보가 없습니다. 수집 실패·미확인 경로가 있으므로 신규 공고가 없다는 뜻은 아닙니다.', '']
        stages=self.screening_summary(notices)
        lines += [f'저장 자료 진행 단계: 제목 미선별 {stages["title_pending"]}건 / 기간 확인 대기 {stages["period_pending"]}건 / 상세 심사 기록 {stages["detailed"]}건 / 추천 {stages["recommended"]}건.',
                  '제목 미선별은 상세 검토 대기와 별도로 집계합니다. 상세 심사 기록은 현재 유효한 항목별 판정이 있는 자료 수이며, 수집 경로의 이번 실행 처리량과 다릅니다.', '']
        for n in selected:
            p, a = n['payload'], n['analysis']
            prefix = '[가상 예제] ' if n['is_demo'] else ''
            lines += [f'## {prefix}{n["title"]}', '', f'- 분류: {LABELS[a["category"]]} / 분석: {a["mode"]} / 원문 버전: {n["version"]}',
                      f'- 출처: {n["url"] or "사용자 입력"}', f'- 마감: {n["screening"].get("deadline_date") or p.get("deadline") or "미확인"}',
                      f'- 신청 자격 근거: {n["screening"].get("gates", {}).get("eligibility", {}).get("quote", "미확인")}', f'- 추천 이유: {n["screening"]["reason"]}']
            for m in a['mappings'][:3]:
                lines += [f'- 연결 실적: {m["evidence"]} ({m.get("year", "")})', f'  - 근거: {m["url"]} / {m["locator"]}',
                          f'  - RFP 원문: {m["quote"]}', f'  - 공백: {m["gap"]}']
            lines += [f'- 검토 사항: {"; ".join(a["warnings"])}', '']
        related=[n for n in notices if n['screening']['decision']=='review' and n['screening'].get('related_candidate')][:10]
        if related:
            lines += ['## 관련 후보 — 신청 조건·직접 실적 확인 필요', '', '아래 자료는 추천 확정이 아닙니다. 연구 관련성이 있어 조건 확인 대상으로 표시합니다.', '']
            for n in related:
                s=n['screening'];lines += [f'### {n["title"]}', '', f'- 연구 적합도: {s["score"]}점 / 상태: 검토 대기',
                    f'- 원문: {n["url"]}', f'- 확인할 사항: {s["reason"]}', '']
        sources = self.store.sources()
        summary = summarize_sources(sources)
        lines += ['## 수집 경로 확인 상태', '',
                  f'활성 {summary["enabled"]}개 / 비활성 {summary["disabled"]}개. 활성 경로: 접속 실패 {summary["failed"]}개, 정책 제한 {summary["blocked"]}개, 링크 추출 미지원 {summary["adapter_needed"]}개, 일부 수집 {summary["partial"]}개, 미실행 {summary["not_run"]}개.', '',
                  '| 경로 | 활성 | 상태 | 마지막 확인 | 발견 링크 | 원인·대응 |', '|---|---|---|---|---|---|']
        for s in sources:
            note = (s.get('error') or s['config'].get('note', '')).replace('|', '/').replace('\n', ' ')
            d = s['diagnostic']
            action = d.get('next_action', '').replace('|', '/').replace('\n', ' ')
            lines.append(f'| {s["config"]["name"]} | {"활성" if s["config"].get("enabled") else "비활성"} | {d["label"]} | {s.get("checked_at") or "미실행"} | {s["discovered"]} | {note} / {action} |')
        return '\n'.join(lines) + '\n'
