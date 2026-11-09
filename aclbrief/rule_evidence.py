"""Literal, document-scoped evidence for the API-free reviewer."""
import re
from .documents import document_sections
from .matcher import term_in

ACTION=r'연구|개발|설계|추정|검출|최적화|알고리즘|모델링|성능.{0,8}(평가|검증)|기술.{0,8}(확보|고도화)'
COMM=r'이동\s*통신|무선\s*통신|위성\s*통신|광\s*통신|수중\s*(?:음향\s*)?통신|통신\s*(?:파형|채널)|빔\s*(탐색|포밍|관리)|(?<![a-z0-9])(?:CSI|MIMO|OTFS|OFDM|AFDM|GNSS)(?![a-z0-9])|채널\s*(?:추정|모델|예측)|심볼\s*검출|간섭\s*제거|무선\s*전송|다중\s*안테나|주파수\s*간섭|전파\s*(환경|전달|예측)|위성\s*항법'
GOAL=r'(?:연구|개발)\s*(?:목표|내용|범위)|세부\s*(?:연구|요구)|알고리즘|설계|추정|검출|최적화|모델링|(?:모델|기법|수신기|시뮬레이터).{0,20}(?:개발|구축|분석|평가)|성능.{0,8}(?:평가|검증)'
BOUNDARY=r'신청\s*자격|지원\s*대상|참여\s*대상|접수\s*기간|사업\s*기간|연구\s*기간|제출\s*서류|연구\s*목표|연구\s*내용'
UNIVERSITY=r'대학(?:교)?(?!원생|생)|산학협력단'
APPLICATION=r'신청|참여|지원|수행|주관'
QUAL_HEADER=r'(?:신청|참여|지원)\s*(?:가능\s*)?(?:자격|대상|기관)|(?:주관|공동|위탁)\s*(?:연구개발\s*)?기관'

# Explicit relations are kept distinct from direct research-topic aliases.
# These do not add evidence nodes or change the user's enabled topic settings.
RELATED={
    'channel':r'채널\s*(?:모델링|모델|예측|환경)|전파\s*(?:예측|전달|환경)|수중\s*(?:음향\s*)?통신|channel\s*model(?:ing|ling)?',
    'detection':r'항법\s*신호.{0,20}(?:추적|획득)|GNSS.{0,20}(?:추적|획득)',
}

def alias_in(term,text):
    if term_in(term,text):return True
    # Korean layout differences are common in HWP tables. English acronyms still
    # require token boundaries and never match substrings inside another word.
    if re.search('[가-힣]',term):
        norm=lambda s:re.sub(r'[\s·ㆍ\-–]','',s).casefold()
        return norm(term) in norm(text)
    return False

def evidence_units(text):
    units=[]
    for section in document_sections(text):
        if section['role'] in ('optional','reference'):continue
        body=section['text']
        spans=list(re.finditer(r'[^\n]+',body))
        for i,m in enumerate(spans):
            # Quotes retain the original whitespace and are validated later.
            line=m[0]
            for part in re.finditer(r'.+?(?:(?<=[.!?。])\s+|$)',line):
                value=part[0].strip()
                for start in range(0,len(value),550):
                    q=value[start:start+650]
                    if len(q)>=12:units.append({'quote':q,'source':section['name'],'role':section['role']})
            # Join headings with table/list values, or a technical noun followed
            # by its action, without crossing another labelled field/document.
            if re.search(BOUNDARY,line) or re.search(COMM,line,re.I):
                for j in range(i+1,min(i+3,len(spans))):
                    if re.search(BOUNDARY,spans[j][0]):break
                    q=body[m.start():spans[j].end()].strip()
                    if 12<=len(q)<=650:units.append({'quote':q,'source':section['name'],'role':section['role']})
    unique={}
    for u in units:unique.setdefault((u['source'],u['quote']),u)
    return list(unique.values())

def technical_units(units,industrial=False):
    selected=[]
    for u in units:
        s=u['quote']
        if not re.search(COMM,s,re.I) or not re.search(ACTION,s):continue
        if re.search(r'단순\s*(활용|이용)|기관\s*소개|연구실\s*소개|지난\s*연구|전년도\s*실적|통신망을?\s*(활용|이용)|연구.{0,25}(제외|아님|않|없)|개발.{0,25}(제외|아님|않|없)',s):continue
        if re.search(r'배경',s) and not re.search(r'(?:연구|개발)\s*(?:목표|내용)|필수\s*(?:연구|기술)',s):continue
        if re.search(r'모집|공고|자격|접수',s) and not re.search(r'(?:연구|개발)\s*(?:목표|내용|범위)|사업\s*목적|세부\s*(?:연구|요구)|(?:개발|설계|추정|검출|검증|평가|구현|연구)(?:한다|하고|하여|하는|할\s*것)',s):continue
        if re.search(QUAL_HEADER,s) and not re.search(r'(?:연구|개발)\s*(?:목표|내용)',s):continue
        if industrial and not re.search(r'핵심\s*(연구|기술|목표)|필수\s*(연구|기술)|연구\s*목표|세부\s*연구',s):continue
        selected.append(u)
    return selected

def eligibility_evidence(units):
    positive=[];negative=[];conditions=[]
    for u in units:
        s=u['quote']
        # Benefits, fees and guarantees are not application restrictions.
        restriction=bool(re.search(r'(?:기업|중소기업|사업자)\s*만\s*(?:신청|지원|참여)(?:\s*가능)?|(?:대학(?:교)?|산학협력단).{0,15}(?:신청|참여|지원)\s*불가|대학.{0,8}제외',s))
        if re.search('(?:'+QUAL_HEADER+r').{0,100}(?:기업|중소기업|사업자).{0,15}(?:한정|에\s*한함|만\s*가능)',s,re.S):
            if not re.search(r'보증|보험|면제|감면|우대|가점|기술료|부담금|세액',s):restriction=True
        if restriction:negative.append(u)
        conditional=bool(re.search(r'(?:기업.{0,30}(?:주관|참여|컨소시엄).{0,15}(?:필수|의무)|컨소시엄.{0,20}(?:필수|의무)|기업.{0,15}주관|주관.{0,25}기업.{0,10}(?:한정|필수))',s,re.S))
        if conditional:conditions.append(u)
        if not re.search(UNIVERSITY,s):continue
        if not re.search('(?:'+QUAL_HEADER+r').{0,140}(?:'+UNIVERSITY+r')|(?:'+UNIVERSITY+r').{0,100}(?:직접\s*신청|신청할\s*수|지원\s*가능|참여\s*가능|신청\s*가능)',s,re.S):continue
        if re.search(r'향후|재위탁|참여\s*불가|신청.{0,5}(?:없|않|불가)|대학.{0,10}제외|설명회|소속|졸업|재학|특정\s*대학|[가-힣]+대학교\s*(?:만|에\s*한정)',s):continue
        if not restriction and not conditional:positive.append(u)
    # The same sentence may be emitted both alone and with its field heading.
    if positive and negative:
        verdict='unknown';reason='대학 참여 허용과 제한 근거가 함께 있습니다. 기관 역할·적용 과제·조건을 확인해야 합니다.';choice=negative[0]
    elif conditions:
        verdict='unknown';reason='기업 주관·필수 컨소시엄 등 조건이 있습니다. 대학의 참여 역할과 조건 충족 여부를 확인해야 합니다.';choice=conditions[0]
    elif negative:
        verdict='no';reason='신청 대상 또는 대학 참여에 대한 명시적 제한 문장을 확인했습니다.';choice=negative[0]
    elif positive:
        verdict='yes';reason='대학·산학협력단의 신청·연구 수행 자격을 명시한 문장을 확인했습니다.';choice=positive[0]
    else:
        verdict='unknown';reason='대학의 신청 역할을 확인할 명시적 근거가 없습니다. 법령 참조만으로 참여 가능을 추정하지 않습니다.';choice=None
    return {'verdict':verdict,'reason':reason,'quote':choice['quote'] if choice else ''}, {'positive':positive,'negative':negative,'conditions':conditions}
