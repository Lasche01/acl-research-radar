"""Robots path decisions with longest-rule priority and explicit diagnostic evidence."""
import re
from urllib.parse import urlsplit, quote
UNRESERVED=set('abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-._~')
def normalize(path):
    path=quote(path,safe="/%!$&'()*+,;=:@?~-._")
    return re.sub(r'%([0-9a-fA-F]{2})',lambda m: chr(int(m[1],16)) if chr(int(m[1],16)) in UNRESERVED else '%'+m[1].upper(),path)
class RobotsPolicy:
    def __init__(self,text):
        if re.search(r'<(?:!doctype|html|body)\b',text,re.I):raise ValueError('robots.txt 대신 HTML 화면이 반환되었습니다.')
        self.groups=[];agents=[];rules=[];started=False
        for line in text.lstrip('\ufeff').splitlines():
            line=line.split('#',1)[0].strip()
            if ':' not in line:continue
            field,value=line.split(':',1);field=field.strip().lower();value=value.strip()
            if field=='user-agent':
                if started:
                    self.groups.append((agents,rules));agents=[];rules=[];started=False
                if value:agents.append(value.lower())
            elif field in ('allow','disallow') and agents:
                started=True
                if value and value.startswith('/'):rules.append((field,value))
        if agents:self.groups.append((agents,rules))
    def decision(self,agent,url):
        agent=agent.lower();specific=[(max((len(a) for a in agents if a!='*' and a in agent),default=0),rules) for agents,rules in self.groups]
        best=max((n for n,_ in specific),default=0)
        groups=[r for n,r in specific if n==best] if best else [r for a,r in self.groups if '*' in a]
        u=urlsplit(url);path=normalize((u.path or '/')+('?' + u.query if u.query else ''))
        matched=[]
        for rules in groups:
            for field,raw in rules:
                pattern=normalize(raw);end=pattern.endswith('$');pattern=pattern[:-1] if end else pattern
                regex='^'+'.*'.join(re.escape(p) for p in pattern.split('*'))+('$' if end else '')
                if re.search(regex,path):
                    # Count encoded octets, not percent-triplet display characters.
                    specificity=len(re.sub(r'%[0-9A-F]{2}','X',pattern.replace('*','')).encode())
                    matched.append((specificity,field=='allow',field,raw))
        if not matched:return {'allowed':True,'matched_rule':'','user_agent':agent,'reason':'일치하는 제한 규칙 없음'}
        _,allowed,field,raw=max(matched)
        return {'allowed':allowed,'matched_rule':field.title()+': '+raw,'user_agent':agent,'reason':'가장 구체적인 일치 규칙 적용'}
    def can_fetch(self,agent,url):return self.decision(agent,url)['allowed']
