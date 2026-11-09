"""Deadline first, title second. Only confirmed open candidates get document analysis."""
import re
from datetime import datetime
from .screening import KST, fingerprint

COMM_TITLE = re.compile(r'통신|전파|주파수|무선|유선|위성|안테나|네트워크|네트웍|파형|다중반송파|비직교\s*전송|심볼\s*검출|간섭\s*(분석|제거)|광\s*전송|빔\s*(탐색|포밍|관리)|다중\s*안테나|채널\s*(추정|모델|예측)|(?:^|[^a-z0-9])(?:RF|RAN|MIMO|CSI|OFDM|OTFS|AFDM|FTN|5G|6G|V2X|ISAC|NTN|SDR|wireless|telecommunications?|communications?|spectrum|antenna|beamforming|channel|network)(?:$|[^a-z0-9])', re.I)
NON_CALL = re.compile(r'채용|인사발령|심사\s*결과|평가\s*결과|선정\s*결과|합격자|세미나|설명회|조선\s*AX|모델하우스|안전관리등급')
CLOSE_LABEL = r'접수\s*(?:마감(?:일시|일)?|종료(?:일시|일)?)|신청\s*(?:마감(?:일시|일)?|종료(?:일시|일)?)|제출\s*기한|제안서\s*접수\s*\(?마감\)?|마감\s*일시|마감일'
PERIOD_LABEL = r'접수\s*기간|전산\s*접수\s*기간|신청\s*기간|모집\s*기간|공고\s*기간'
FULL_DATE = re.compile(r'(20\d{2})\s*[.년/\-]\s*(\d{1,2})\s*[.월/\-]\s*(\d{1,2})(?!\d)')

def title_screen(title):
    if NON_CALL.search(title):return {'state':'excluded','reason':'제목이 연구 모집과 무관한 안내·행정·타 산업 문서입니다.'}
    # Publisher prefixes are not the subject of a research call. Keep task/tech
    # brackets such as [6G] and [위성통신], while removing explicit agency names.
    subject=re.sub(r'[\[【]([^\]】]+)[\]】]',lambda m:'' if re.search(r'연구원|진흥원|평가원|협회|과학기술정보통신부|방위사업청|산업통상',m[1]) else m[0],title)
    match=COMM_TITLE.search(subject)
    if match:return {'state':'candidate','quote':match[0],'reason':'기관명 접두어를 제외한 제목에서 통신 후보 표현을 확인했습니다. 상세 근거로 적합성을 심사합니다.'}
    return {'state':'unconfirmed','reason':'제목에서 통신 관련성을 확인하지 못해 상세·첨부 분석을 보류합니다.'}

def period_from_quote(quote, now=None):
    now=now or datetime.now(KST)
    out={'state':'unknown','quote':quote,'end_date':'','start_date':'','end_at':''}
    dates=list(FULL_DATE.finditer(quote))
    if not dates:return out
    try:
        first=datetime(*(int(x) for x in dates[0].groups()),tzinfo=KST)
        last=datetime(*(int(x) for x in dates[-1].groups()),tzinfo=KST)
        range_match=re.search(r'[~～〜]|부터',quote[dates[0].end():])
        if len(dates)==1 and range_match:
            suffix=quote[dates[0].end():][range_match.end():]
            short=re.search(r'^\s*(?:\([^)]*\)\s*)?(\d{1,2})\s*[.월/\-]\s*(\d{1,2})(?!\d)',suffix)
            if not short:return out
            month,day=map(int,short.groups());year=first.year+(month<first.month)
            last=datetime(year,month,day,tzinfo=KST)
            end_suffix=suffix[short.end():]
        else:end_suffix=quote[dates[-1].end():]
        if (len(dates)>1 or range_match) and last<first:return out
        if len(dates)>1 or range_match:out['start_date']=first.date().isoformat()
        out['end_date']=last.date().isoformat()
        clock=re.search(r'(\d{1,2})\s*(?::\s*(\d{2})|시(?:\s*(\d{1,2})\s*분)?)',end_suffix)
        if clock:
            hour=int(clock[1]);minute=int(clock[2] or clock[3] or 0)
            closing=last.replace(hour=hour,minute=minute)
            out['end_at']=closing.isoformat()
        else:closing=last.replace(hour=23,minute=59,second=59)
        if closing<now:out['state']='expired'
        elif out['start_date'] and first.date()>now.date():out['state']='upcoming'
        elif last.date()==now.date() and not clock:out['reason']='마감 당일 종료 시각 미확인'
        else:out['state']='open'
    except (ValueError,OverflowError):pass
    return out

def period_from_text(text, now=None):
    from .documents import document_sections
    for section in document_sections(text):
        if section['role'] in ('optional','reference'):continue
        period=_period_from_plain_text(section['text'],now)
        if period['end_date']:return period
    return {'state':'unknown','quote':'','end_date':'','start_date':'','end_at':''}

def _period_from_plain_text(text, now=None):
    lines=[s.strip() for s in text.splitlines() if s.strip()]
    for label in (CLOSE_LABEL,PERIOD_LABEL):
        for i,line in enumerate(lines):
            if not re.search(label,line):continue
            quote=line
            if not FULL_DATE.search(line) or re.search(r'[~～〜]\s*$',line):
                for following in lines[i+1:i+3]:
                    if re.search(r'사업\s*기간|연구\s*기간|등록일|게시일|작성일|지원\s*규모|신청\s*자격',following):break
                    quote+='\n'+following
                    if FULL_DATE.search(following) and not re.search(r'[~～〜]\s*$',following):break
            result=period_from_quote(quote,now)
            if result['end_date']:return result
    return {'state':'unknown','quote':'','end_date':'','start_date':'','end_at':''}

def listing_period(listing, now=None):
    fields=listing.get('fields',[])
    def with_start(period):
        for f in fields:
            if re.search(r'(?:접수|신청)\s*(?:시작일|개시일)',f.get('label','')):
                dates=list(FULL_DATE.finditer(f['text']))
                if dates:
                    try:
                        start=datetime(*(int(x) for x in dates[0].groups()),tzinfo=KST)
                        period['start_date']=start.date().isoformat()
                        if period['state']=='open' and start.date()>(now or datetime.now(KST)).date():period['state']='upcoming'
                    except ValueError:pass
        return period
    # Never mistake publication date for application closing date.
    for label in (CLOSE_LABEL,PERIOD_LABEL):
        for f in fields:
            if re.search(label,f.get('label','')):
                period=period_from_quote(f['label']+'\n'+f['text'],now)
                if period['end_date']:return with_start({**period,'basis':'listing'})
    period=period_from_text(listing.get('text',''),now)
    if period['end_date']:return with_start({**period,'basis':'listing'})
    for f in fields:
        if re.search(r'상태|진행\s*현황',f.get('label','')):
            value=re.sub(r'\s+','',f.get('text',''))
            if value in ('접수마감','신청마감','모집마감','접수종료','모집종료','마감','종료'):
                return {**period,'state':'expired','quote':f['label']+'\n'+f['text'],'basis':'listing_status'}
            if value in ('접수중','신청접수중','모집중','신청가능'):
                return {**period,'state':'open','quote':f['label']+'\n'+f['text'],'basis':'listing_status'}
    return period

def candidate_metadata(title,listing=None):
    listing=listing or {};period=listing_period(listing)
    return {'title':title,'period':period,'title_screen':title_screen(title),
            'signature':fingerprint({'title':title,'period':{k:v for k,v in period.items() if k not in ('state','reason')}})}

def metadata_payload(url,title,source_id,period,title_check,state):
    reason={'expired_metadata_only':'접수 마감 경과로 상세·첨부 분석을 생략했습니다.',
            'upcoming_metadata_only':'접수 시작 전이므로 상세·첨부 분석을 보류합니다.',
            'deadline_unknown_metadata_only':'접수기간을 확인하지 못해 첨부 분석을 보류합니다.',
            'title_metadata_only':title_check['reason']}.get(state,'선별 자료')
    return {'url':url,'title':title,'source_id':source_id,'scope':'notice',
            'text':'목록 제목: '+title+'\n'+period.get('quote','')+'\n'+reason,
            'attachments':[],'warnings':[reason], 'collection_state':state,
            'collection_triage':{'period':period,'title':title_check,'reason':reason}}
