"""Official republication routes, never represented as complete original-site coverage."""
import re
FALLBACKS={'iitp':['iris','koita'],'nrf':['iris','koita'],'ntis':['iris','koita'],'etri':['koita'],
           'future':['dapa'],'rapid':['dapa'],'krit':['dtims']}
PUBLISHERS={'iitp':r'정보통신기획평가원|IITP','nrf':r'한국연구재단|NRF','etri':r'한국전자통신연구원|ETRI'}
def publishers(payload):
    title=payload.get('title','');text=payload.get('text','')
    return [sid for sid,pattern in PUBLISHERS.items() if re.search(r'^\[('+pattern+r')\]',title,re.I) or re.search(r'(?:전문기관|공고기관)\s*[:：]?\s*('+pattern+r')(?=\s|$)',text,re.I)]
def collection_plan(sources, source_ids=None):
    """Selected institutions bring their known official providers, regardless of provider checkbox."""
    byid={s['id']:s for s in sources}
    requested=set(source_ids) if source_ids is not None else {s['id'] for s in sources if s['config'].get('enabled')}
    if requested-set(byid):raise ValueError('알 수 없는 수집 경로입니다.')
    automatic={}
    for sid in sorted(requested):
        for provider in FALLBACKS.get(sid,[]):
            if provider in byid:automatic.setdefault(provider,[]).append(sid)
    selected=requested|set(automatic)
    # Fetch available republications before original endpoints that may fail or take time.
    ordered=sorted((s for s in sources if s['id'] in selected),key=lambda s:s['id'] in FALLBACKS)
    return {'requested':requested,'automatic':automatic,'sources':ordered}

def route_status(sources,records):
    byid={s['id']:s for s in sources}
    plan=collection_plan(sources)
    for source in sources:
        parents=plan['automatic'].get(source['id'],[])
        source['automatic_for']=parents
        source['automatic_for_names']=[byid[s]['config']['name'] for s in parents]
        source['effective_enabled']=bool(source['config'].get('enabled')) or bool(parents)
        providers=[]
        for sid in FALLBACKS.get(source['id'],[]):
            if sid not in byid:continue
            s=byid[sid]
            matches=sum(n['source_id']==sid and source['id'] in publishers(n['payload']) for n in records)
            providers.append({'id':sid,'name':s['config']['name'],'url':s['config']['url'],'enabled':bool(s['config'].get('enabled')),
                              'status':s['status'],'checked_at':s['checked_at'],'publisher_matches':matches})
        source['alternative_routes']=providers
    return sources
