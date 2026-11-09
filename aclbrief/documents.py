"""Deterministic document metadata; preserve literal source quotes."""
import re
from datetime import datetime
PRIMARY=r'RFP|제안요청|공고문|공모문|제안서\s*공모|과제.{0,5}(설명|내용|요구)|연구개발계획서\s*공모'
FORM=r'양식|서식|참여\s*인력\s*현황|개인정보.{0,8}동의|확약서|증빙|체크리스트|참고\s*자료|매뉴얼|작성\s*(요령|지침|안내)|절차\s*설명'
REFERENCE=r'법률|시행령|시행규칙|국가연구개발혁신법|연구개발비\s*사용\s*기준|사업\s*관리\s*규정'

def document_role(name):
    # Inspect the file itself before optional parent directories. An RFP inside a
    # "참고자료" folder remains required; an application manual remains optional.
    leaf=name.replace('\\','/').split('/')[-1]
    if re.search(r'RFP|제안\s*요청|과제.{0,5}(설명|내용|요구)',leaf,re.I):return 'rfp'
    if re.search(PRIMARY,leaf,re.I):return 'announcement'
    if re.search(FORM,leaf,re.I):return 'optional'
    if re.search(REFERENCE,leaf,re.I):return 'reference'
    return 'unknown'

def required_attachment(name):return document_role(name) not in ('optional','reference')

def required_record(record):
    role=document_role(record.get('name',''))
    if role in ('optional','reference'):return False
    if role in ('rfp','announcement'):return True
    return record.get('required_for_screening',True)

def document_sections(text):
    """Locate old and new attachment boundaries without changing literal quotes."""
    markers=list(re.finditer(r'(?m)^\[(?:첨부 |직접 확보 파일 |ZIP 문서 )(.+)\][ \t]*$',text))
    points=[(0,'공고 본문','body',0)]+[(m.start(),m[1],document_role(m[1]),m.end()) for m in markers]
    result=[]
    for i,(start,name,role,content_start) in enumerate(points):
        end=points[i+1][0] if i+1<len(points) else len(text)
        result.append({'name':name,'role':role,'text':text[content_start:end],'start':content_start,'end':end})
    return result

def screening_warnings(payload):
    """Do not turn a known manual/reference failure into a missing core RFP."""
    blocking=[];optional=[]
    records=payload.get('attachments',[])
    for a in records:
        if a.get('status') in ('pending','review'):
            (blocking if required_record(a) else optional).append('첨부 '+a.get('name','')+': '+a.get('error','미확보'))
    for w in payload.get('warnings',[]):
        if not any(t in str(w) for t in ('OCR 미지원','자동 판독하지','개만 처리','메뉴·다른 공고','공식 원문이 변경','복수 RFP','ZIP 미처리')) and not re.match(r'^첨부 .+[:：]',str(w)):continue
        matches=[a for a in records if a.get('name') and a['name'] in w]
        filename=w.split('ZIP 미처리: ',1)[-1].split(' / ',1)[0]
        if document_role(filename) in ('optional','reference') or matches and not any(required_record(a) for a in matches):optional.append(w)
        else:blocking.append(w)
    return list(dict.fromkeys(blocking)),list(dict.fromkeys(optional))
def deadline_evidence(text):
    from .triage import period_from_text
    period=period_from_text(text)
    return period.get('end_date',''),period.get('quote','')
