"""Persistent bounded discovery/detail queue. Every run revisits the newest list."""
import time
from .collect import PageParser, decode_text, list_candidates, next_pages, page_payload, ExtractionError
from .notices import document_check, has_record_id
from .screening import fingerprint
from .store import canonical_url
from .triage import candidate_metadata, metadata_payload
from .documents import required_record


def collect_incremental(config,fetcher,consume,checkpoint,save,max_pages=5,limit=30,attempted=None,refresh=True,cancel=None,progress=None,known=None,page_signatures=None):
    identity=fingerprint({'url':config['url'],'pattern':config.get('detail_pattern'),'id':config['id'],'queue_policy':'deadline-title-v3'})
    attempted = attempted if attempted is not None else set()
    stopped = lambda: cancel is not None and cancel.is_set()
    c=checkpoint if checkpoint and checkpoint.get('identity')==identity else {}
    old_identity=fingerprint({'url':config['url'],'pattern':config.get('detail_pattern'),'id':config['id'],'queue_policy':'deadline-title-v1'})
    v2_identity=fingerprint({'url':config['url'],'pattern':config.get('detail_pattern'),'id':config['id'],'queue_policy':'deadline-title-v2'})
    if checkpoint and checkpoint.get('identity') in (old_identity,v2_identity):
        # The title policy changed: old title holds must not hide new candidates.
        # Retain only verified closed items and restart discovery from the front.
        c={'terminal':{u:v for u,v in checkpoint.get('terminal',{}).items() if v.get('kind')=='expired'}}
    pending={u:t for u,t in c.get('pending',[])}
    done=c.get('done',{})
    meta=c.get('meta',{})
    terminal=c.get('terminal',{})
    known=known or {}
    counts={k:0 for k in ('expired','upcoming','title_excluded','title_unconfirmed','deadline_unknown','detailed','cached_skipped')}
    def terminal_matches(url,item):
        prior=terminal.get(url)
        if prior and prior.get('signature')==item['signature']:
            return prior.get('kind') in ('title_excluded','title_unconfirmed') or prior.get('kind')=='expired'
        saved=known.get(url)
        return bool(saved and saved.get('title')==item['title'] and saved['period']['state']=='expired' and item['period']['state']=='unknown')
    def screen_listing(url,item):
        period=item['period'];check=item['title_screen']
        if period['state'] in ('expired','upcoming'):
            counts[period['state']]+=1
            terminal[url]={'signature':item['signature'],'kind':period['state'],'period':period}
            pending.pop(url,None);meta.pop(url,None);return True
        if terminal_matches(url,item):
            counts['cached_skipped']+=1;pending.pop(url,None);meta.pop(url,None);return True
        if check['state']!='candidate':
            kind='title_excluded' if check['state']=='excluded' else 'title_unconfirmed'
            if not terminal.get(url) or terminal[url].get('signature')!=item['signature']:
                changes.append(consume(metadata_payload(url,item['title'],config['id'],period,check,'title_metadata_only')))
            counts[kind]+=1;terminal[url]={'signature':item['signature'],'kind':kind}
            pending.pop(url,None);meta.pop(url,None);return True
        return False
    # A bounded refresh cache; older links may be fetched again, never permanently ignored.
    done={u:stamp for u,stamp in done.items() if time.time()-stamp<86400}
    pages=[tuple(x) for x in c.get('pages',[])]
    visited=set(c.get('visited',[]))
    notes=[];errors=[];changes=[];attachment_errors=[];pages_read=skipped=discovered=0;list_error=''
    def persist():
        save({'identity':identity,'pending':list(pending.items()),'done':done,'pages':pages,'visited':sorted(visited),'meta':meta,'terminal':terminal})
    work=([(1,config['url'])] if refresh else [])+[p for p in pages if not refresh or p[1]!=config['url']]
    pages=[];seen_run=set();signatures=page_signatures if page_signatures is not None else {};discovery_failed=False;front_urls=[]
    while work and pages_read<max_pages and not stopped():
        number,address=work.pop(0)
        if address in seen_run:continue
        seen_run.add(address)
        try:
            data,headers,resolved=fetcher.fetch(address)
            parser=PageParser();parser.feed(decode_text(data,headers));page=parser.result()
            items,rejected=list_candidates(page,resolved,config);found=[(x['url'],x['title']) for x in items];skipped+=rejected;pages_read+=1
            if number==1:front_urls=[canonical_url(u) for u,t in found]
            if not items:
                visited.add(address);work.clear()
                list_error='detail_links_missing'
                errors.append('상세 링크를 찾지 못했습니다. 빈 목록인지 추출 실패인지 확인할 수 없어 다음 쪽 탐색을 중단합니다.')
                break
            sig=tuple(sorted(u for u,t in found))
            if sig and sig in signatures and signatures[sig]!=address:
                errors.append('목록 페이지가 동일 게시물들을 반복합니다. 페이지 매개변수 지원을 확인해야 합니다.')
                list_error='pagination_repeated';visited.add(address);work.clear();break
            signatures[sig]=address;visited.add(address)
            for item in items:
                u=canonical_url(item['url']);discovered+=1
                if screen_listing(u,item):continue
                prior=meta.get(u)
                if prior and prior['signature']!=item['signature']:done.pop(u,None)
                meta[u]=item
                if u not in done:pending[u]=item['title']
            for n,u in next_pages(page,resolved,config):
                if n>number and u not in visited and u not in seen_run and (n,u) not in work:work.append((n,u))
            # Keep oldest unfinished detail links first; newly discovered items follow.
        except Exception as exc:
            if not pages_read and not pending:raise
            discovery_failed=True;errors.append('목록: '+str(exc)[:180]);work.append((number,address));break
        finally:
            pages=list(work);persist()
            if progress:progress(pages_read,0,len(pending),len(pages))
    pages=list(work)
    # Snapshot means a failed item cannot be retried repeatedly within the same run.
    # Front-page links get half the budget; remaining capacity continues the old queue.
    front=[u for u in front_urls if u in pending and u not in dict(c.get('pending',[]))]
    ordered=list(dict.fromkeys(front[:max(1,limit//2)]+list(pending)))
    for url in [u for u in ordered if u not in attempted][:limit]:
        if stopped():break
        attempted.add(url)
        title=pending[url]
        try:
            item=meta.get(url) or candidate_metadata(title)
            if screen_listing(url,item):persist();continue
            p=page_payload(url,fetcher,title=title,source_id=config['id'],staged=True,listing=item)
            state=p.get('collection_state','')
            if state=='expired_metadata_only':
                counts['expired']+=1;terminal[url]={'signature':item['signature'],'kind':'expired','period':p['collection_triage']['period']}
                pending.pop(url,None);done.pop(url,None);persist();continue
            if state=='upcoming_metadata_only':counts['upcoming']+=1
            elif state=='deadline_unknown_metadata_only':counts['deadline_unknown']+=1
            elif state=='title_metadata_only':counts['title_unconfirmed']+=1
            else:counts['detailed']+=1
            target=p.get('collected_url',url)
            if target!=url and not has_record_id(target):raise ExtractionError('상세 주소가 목록·로그인 화면으로 이동했습니다.')
            if document_check(p)['excluded']:skipped+=1
            else:
                changes.append(consume(p))
                for a in p.get('attachments',[]):
                    if a.get('status')=='review' and required_record(a):
                        attachment_errors.append({'title':title[:100],'name':a.get('name',''),'url':a.get('url',''),'error':a.get('error','')[:500]})
                        errors.append(title[:50]+': 첨부 '+a.get('name','')[:80]+' / '+a.get('error','')[:180])
            pending.pop(url,None)
            if any(a.get('status')=='review' and required_record(a) for a in p.get('attachments',[])):
                pending[url]=title;done.pop(url,None)
            else:done[url]=time.time()
        except Exception as exc:
            errors.append(title[:50]+': '+str(exc)[:160])
            # Rotate failures to the back so one broken document does not starve others.
            pending.pop(url,None);pending[url]=title
        persist()
        if progress:progress(pages_read,len(changes),len(pending),len(pages))
    if not pages and not pending and not discovery_failed:visited=set()
    persist()
    if pages or pending:errors.append(f'다음 실행 대기: 목록 {len(pages)}개 · 상세 {len(pending)}건. 저장된 위치에서 이어갑니다.')
    if refresh and not discovered and not pending and not changes and not list_error:
        errors.append('상세 링크를 찾지 못했습니다. 신규 0건 또는 수집 완료로 판단하지 않습니다.')
    return {'status':('partial' if changes or done else 'failed') if errors else 'success',
            'discovered':discovered,'processed':len(changes),'changes':changes,'notes':notes,'list_error':list_error,
            'pages_read':pages_read,'skipped_navigation':skipped,'error':'\n'.join(errors),
            'attachment_errors':attachment_errors[:30],'coverage':'incremental_bounded','pending_details':len(pending),'pending_pages':len(pages),'limit':limit,'cancelled':stopped(),'remaining_unattempted':sum(u not in attempted for u in pending),'triage_counts':counts}
