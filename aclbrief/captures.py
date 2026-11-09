"""Import user-saved public pages/files without making any network requests."""
import base64
import hashlib
from .collect import extract_file, document_hash
from .documents import required_attachment, document_role
from .store import now

def import_capture(service,data,local_files=None):
    local_files=local_files or {}
    target=service.store.notice(data['notice_id']) if data.get('notice_id') else None
    old=target['payload'] if target else {}
    text=str(data.get('text','')).strip()
    files=data.get('files',[])
    if not isinstance(files,list) or not all(isinstance(f,dict) for f in files):raise ValueError('파일 목록이 올바르지 않습니다.')
    if not text and not files:raise ValueError('복사한 본문 또는 직접 받은 파일을 추가하세요.')
    pieces=[];warnings=[];attachments=[]
    if text:pieces.append(text)
    elif target:pieces.append(old.get('text',''))
    for item in files:
        if 'upload_id' in item:
            if item['upload_id'] not in local_files:raise ValueError('임시 파일을 다시 전송하세요.')
            local=local_files[item['upload_id']];name=local['name'];blob=local['path']
        else:
            name=str(item.get('name',''))[:300]
            blob=base64.b64decode(item.get('base64',''),validate=True)
        record={'name':name,'status':'extracted','required_for_screening':required_attachment(name),'role':document_role(name),'sha256':document_hash(blob),'acquisition':'browser_export'}
        try:
            content,issues=extract_file(blob,name)
            pieces.append('[직접 확보 파일 '+name+']\n'+content);warnings.extend(issues)
            record.update(extracted_chars=len(content.strip()),warnings=issues)
        except Exception as exc:
            record.update(status='review',error=str(exc)[:400])
        attachments.append(record)
    # Keep unresolved original attachments unless the user supplies matching files.
    names={a['name'].replace('+',' ') for a in attachments}
    for a in old.get('attachments',[]):
        if data.get('replace_missing') and a.get('status') in ('review','pending'):
            continue
        if a.get('name','').replace('+',' ') not in names:
            a={**a,'required_for_screening':required_attachment(a.get('name',''))};attachments.append(a)
    if not text:
        prior=list(old.get('warnings',[]))
        if data.get('replace_missing'):prior=[w for w in prior if not any(x in w for x in ('첨부 ', '개만 처리','자동 판독하지','메뉴·다른 공고'))]
        warnings=prior+warnings
    provenance=list(old.get('captures',[]))+[{'captured_at':now(),'source_url':data.get('url') or old.get('url',''),
                                            'files':[a['name'] for a in attachments if a.get('acquisition')=='browser_export'],'body_supplied':bool(text)}]
    p={**old,'title':data.get('title') or old.get('title') or '직접 확보 공고','url':old.get('url') or data.get('url',''),
       'scope':data.get('scope') or old.get('scope','rfp'),'unit':data.get('unit') if data.get('unit') is not None else old.get('unit',''),
       'text':'\n\n'.join(pieces),'warnings':list(dict.fromkeys(warnings)),'attachments':attachments,'captures':provenance,
       'source_id':old.get('source_id','browser_import'),
       'capture_base_text_hash':old.get('capture_base_text_hash') or hashlib.sha256(old.get('text','').encode()).hexdigest()}
    if text:p.pop('source_refresh',None)
    # A manual supplement must be judged from the newly supplied evidence rather
    # than being permanently held by an earlier metadata-only collection stage.
    p.pop('collection_state',None);p.pop('collection_triage',None)
    if target and (p['scope']!=old.get('scope') or p['unit']!=old.get('unit','')):raise ValueError('기존 공고의 분석 단위는 변경할 수 없습니다. 세부 RFP는 새 공고로 추가하세요.')
    return service.ingest(p)
