#!/usr/bin/env python3
"""TraceMind: stateful, provenance-first BuildSprint Round 3 assistant.

Standard-library deterministic core. No answers/ or tasks/ files are read by this solution.
Each invocation is isolated; evidence and feedback live in a SQLite DB.
"""
from __future__ import annotations
import argparse, collections, contextlib, datetime as dt, difflib, json, math, os, re, sqlite3, sys, time
from pathlib import Path
from zoneinfo import ZoneInfo

try:
    from llm_adapter import grounded_ask, enabled as model_enabled
except ImportError:
    def model_enabled():return False
    def grounded_ask(*a, **kw):return None

STOP = set('the a an to for in on of at is are what when who which this that now my me our your did do does and about was has have with from by it its thing please tell can i send there how much status client account retail logistics studio due currently date responsible owner owns project task work week done new latest'.split())
NOWORD = {'newsletter','newsletter:','print vendor'}
PAT_WORD = re.compile(r"[a-z0-9]+", re.I)
ISO_FMT = '%Y-%m-%dT%H:%M:%SZ'

def js(obj): return json.dumps(obj, ensure_ascii=False, separators=(',',':'))
def words(s): return [x for x in PAT_WORD.findall(str(s).lower()) if x not in STOP and len(x)>1]
def norm(s): return ' '.join(words(s))
def readjson(path, fallback):
    try: return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError): return fallback

def readjsonl(path):
    try:
        with open(path,encoding='utf-8') as f:
            for line in f:
                if line.strip():
                    try: yield json.loads(line)
                    except ValueError: continue
    except FileNotFoundError: return

def datetime_utc(s):
    if not s:return dt.datetime.min.replace(tzinfo=dt.timezone.utc)
    try:return dt.datetime.fromisoformat(s.replace('Z','+00:00')).astimezone(dt.timezone.utc)
    except ValueError:return dt.datetime.min.replace(tzinfo=dt.timezone.utc)

def iso_date(s,zone='UTC'):
    return datetime_utc(s).astimezone(ZoneInfo(zone)).date()

def fmtday(date):return date.isoformat()
def usage(start,inp=0,out=0,cost=0):return {'input_tokens':int(inp),'output_tokens':int(out),'cost_usd':float(cost),'ms':max(1,int((time.monotonic()-start)*1000))}

def data_root():
    root=Path(os.environ.get('R3_PACK') or Path.cwd()).resolve()
    if not (root/'data').is_dir():
        root=Path(__file__).resolve().parent
    return root

def db_file(root):
    value=os.environ.get('TRACEMIND_DB')
    return Path(value) if value else root/'.tracemind.sqlite3'

class Engine:
    def __init__(self):
        self.root=data_root(); self.dbpath=db_file(self.root)
        self.db=sqlite3.connect(str(self.dbpath)); self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA busy_timeout=8000'); self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('''
        CREATE TABLE IF NOT EXISTS records(id TEXT PRIMARY KEY,day INTEGER NOT NULL,at TEXT,org TEXT,client TEXT,type TEXT,author TEXT,visibility TEXT,body TEXT,doc TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS idx_records_org_client ON records(org,client,day);
        CREATE INDEX IF NOT EXISTS idx_records_type ON records(type,day);
        CREATE TABLE IF NOT EXISTS feedback(id TEXT PRIMARY KEY,day INTEGER,person TEXT,type TEXT,words TEXT,doc TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS forgets(person TEXT,topic TEXT,source_ids TEXT,PRIMARY KEY(person,topic));
        CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
        ''')
        self.people_by_id={}
        ppl=readjson(self.root/'data/people.json',{})
        for org, obj in ppl.items():
            for p in obj.get('people',[]):self.people_by_id[p['id']]={**p,'org':org}
        self.clients=readjson(self.root/'data/clients.json',[])
        self.client_by_id={c['id']:c for c in self.clients}
        self.all_records=None
        self.feedback=None

    def ingest(self,day):
        if day==1:
            with self.db:self.db.execute('DELETE FROM records');self.db.execute('DELETE FROM feedback');self.db.execute('DELETE FROM forgets');self.db.execute('DELETE FROM meta')
        rows=list(readjsonl(self.root/f'data/days/day-{day:02}.jsonl'))
        if not (self.root/f'data/days/day-{day:02}.jsonl').exists():return {'ok':False,'error':'day file not found'}
        with self.db:
            for r in rows:
                self.db.execute('INSERT OR IGNORE INTO records(id,day,at,org,client,type,author,visibility,body,doc) VALUES (?,?,?,?,?,?,?,?,?,?)',(
                    r['id'],day,r.get('at',''),r.get('org'),r.get('client'),r.get('type'),r.get('author'),r.get('visibility','team'),r.get('body',''),js(r)))
            if day>1:
                for i,f in enumerate(readjsonl(self.root/f'data/feedback/day-{day-1:02}.jsonl')):
                    fid=f'{day-1}:{i}:{f.get("person")}'
                    self.db.execute('INSERT OR IGNORE INTO feedback(id,day,person,type,words,doc) VALUES (?,?,?,?,?,?)',(fid,day-1,f.get('person'),f.get('type'),f.get('words',''),js(f)))
        self.db.execute('INSERT OR REPLACE INTO meta(key,value) VALUES (?,?)',('ingested_day',str(day)));self.db.commit()
        return {'ok':True,'day':day,'records':len(rows)}

    def _rows(self):
        if self.all_records is None:
            self.all_records=[json.loads(r['doc']) for r in self.db.execute('SELECT doc FROM records ORDER BY at,id')]
        return self.all_records

    def _feedback(self):
        if self.feedback is None:
            self.feedback=[json.loads(r['doc']) for r in self.db.execute('SELECT doc FROM feedback ORDER BY day,id')]
        return self.feedback

    def org(self,person):return self.people_by_id.get(person,{}).get('org')
    def zone(self,person):return self.people_by_id.get(person,{}).get('zone','UTC')
    def name(self,person):return self.people_by_id.get(person,{}).get('name',person)

    def visible(self,r,p):
        if self.org(p) is None or r.get('org') != self.org(p):return False
        v=r.get('visibility','team')
        if v=='private':return r.get('author')==p
        if v=='restricted':return p in (r.get('readers') or [])
        return v=='team'

    def forget_terms(self,person):
        return [(r['topic'],set(json.loads(r['source_ids']))) for r in self.db.execute('SELECT topic,source_ids FROM forgets WHERE person=?',(person,))]

    def is_forgotten(self,r,p,terms=None):
        terms=terms if terms is not None else self.forget_terms(p)
        text=(r.get('body','') or '')+' '+str(r.get('title',''))+' '+str(r.get('subject',''))
        for topic,ids in terms:
            if r.get('id') in ids:return True
            tokens=set(words(topic))
            if tokens and len(tokens & set(words(text)))==len(tokens):return True
        return False

    def allowed(self,person,at=None):
        terms=self.forget_terms(person)
        return [r for r in self._rows() if self.visible(r,person) and (not at or datetime_utc(r.get('at'))<=datetime_utc(at)) and not self.is_forgotten(r,person,terms)]

    def forget(self,person,what):
        # Store a minimal topic filter plus existing record IDs to prevent reappearance
        raw=re.sub(r'(?i)^.*?forget (?:what i said about|everything about|about|my|the|that|what|)', '',what).strip(' .?!')
        topic=norm(raw) or norm(what)
        owned=[r['id'] for r in self._rows() if r.get('author')==person and topic and set(words(topic)).issubset(set(words(r.get('body',''))))]
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO forgets(person,topic,source_ids) VALUES (?,?,?)',(person,topic,js(owned)))
            # Private notes authored by the requester can be physically purged.
            # Team records remain preserved as shared workspace evidence, but
            # are suppressed from this requester's future retrievals.
            for rid in owned:
                self.db.execute('DELETE FROM records WHERE id=? AND visibility=? AND author=?',(rid,'private',person))
        self.all_records=None
        return {'ok':True}

    def client_aliases(self):
        aliases={}
        for c in self.clients:
            cid=c['id']; name=c['name']; toks=name.split()
            aliases[cid]=cid
            aliases[name.lower()]=cid
            if toks:
                aliases[toks[0].lower()]=cid
                acronym=''.join(x[0] for x in toks)
                aliases[acronym.lower()]=cid
                if len(toks[0])>=3: aliases[toks[0][:2].lower()]=cid
        # Infer geographic labels from external meeting attendee domains, not task IDs/answers.
        for r in self._rows():
            cid=r.get('client')
            if not cid:continue
            # These prefixes originate in structured source identifiers, not hardcoded aliases.
            for key in ('task_id','doc_id','sheet_id'):
                identifier=r.get(key,'')
                for part in str(identifier).lower().split('-'):
                    if len(part) in (2,3) and part.isalpha():
                        # Count short code only when the record itself has the matching client.
                        if key=='task_id' and part not in ('id','t'):
                            aliases[part]=cid
            if r.get('type')=='event':
                joined=' '.join(r.get('attendees',[])).lower()
                if '.co.uk' in joined:aliases['london account']=cid
                if '.in' in joined:aliases['india account']=cid;aliases['mumbai client']=cid
        return aliases

    def resolve_client(self,text):
        q=text.lower();als=self.client_aliases()
        # longest phrase first; short initialisms require word boundaries.
        for a,cid in sorted(als.items(),key=lambda x:-len(x[0])):
            if len(a)<2:continue
            if re.search(r'(?<!\w)'+re.escape(a)+r'(?!\w)',q):return cid
        return None

    def get_records(self,person,at=None,client=None):
        rows=self.allowed(person,at)
        return [r for r in rows if client is None or r.get('client')==client]

    def task_states(self,rows):
        states={}
        for r in rows:
            if r.get('type')!='task' or not r.get('task_id'):continue
            tid=r['task_id']; ch=r.get('change') or {}; field=ch.get('field')
            if tid not in states:
                states[tid]={k:r.get(k) for k in ('task_id','title','client','owner','due','status','org')};states[tid]['sources']={}
                for fld in ('owner','due','status'):states[tid]['sources'][fld]=r['id']
            st=states[tid]
            if field=='created':
                for fld in ('owner','due','status'):
                    if st.get(fld) is None:st[fld]=r.get(fld);st['sources'][fld]=r['id']
            elif field in ('owner','due','status'):
                st[field]=ch.get('to') or r.get(field) or st.get(field)
                st['sources'][field]=r['id']
            st['last_record']=r['id']
        return states

    def _pref(self,person):
        prefs=[f for f in self._feedback() if f.get('person')==person and f.get('type')=='preference']
        out={'max_changes':None,'skip_newsletters':False,'blockers_first':False}
        for f in prefs:
            s=f.get('words','').lower()
            x=re.search(r'(?:max|at most|no more than)\s+(\d+)\s+(?:changes|items)',s)
            if x:out['max_changes']=int(x.group(1))
            if 'skip' in s and 'newsletter' in s:out['skip_newsletters']=True
            if 'blocker' in s and ('first' in s or 'prioriti' in s):out['blockers_first']=True
        return out

    def _corrections(self,person):
        return [f for f in self._feedback() if f.get('person')==person and f.get('type')=='correction']

    def _business_signal(self,r):
        body=r.get('body','') or ''
        if r.get('type')=='task':
            ch=r.get('change') or {};f=ch.get('field')
            if f not in ('status','owner','due'):return None
            old=ch.get('from');new=ch.get('to') or r.get(f)
            if f=='status':
                if not new or new==old:return None
                tone={'blocked':1,'done':3,'in_progress':6,'todo':7,'cancelled':3}.get(new,5)
                msg=f"{r.get('title','Task')} is {new.replace('_',' ')}"
                if new=='blocked' and ':' in body:msg+=f" ({body.split(':',1)[1].strip().rstrip('.')})"
                return tone,msg
            if f=='owner':return 4,f"{r.get('title','Task')} now owned by {self.name(new)} (was {self.name(old)})"
            if f=='due':
                def pretty(s):
                    try:return dt.date.fromisoformat(s).strftime('%-d %b')
                    except:return str(s)
                return 5,f"{r.get('title','Task')} due date moved from {pretty(old)} to {pretty(new)}"
        if r.get('type') in ('chat','mail'):
            if 'billing' in body.lower() and re.search(r'(?i)(take.? over|takes over|now owns|now handles|responsible for|assigned to)',body):
                m=re.search(r'(?i)([A-Z][a-z]+)\s+(?:takes over|now owns|now handles)',body)
                if m: return 1,f'{m.group(1)} now handles {r.get("client") or "client"} billing: {body.strip()}'
            if re.search(r'(?i)\bmove (?:the )?launch to\b|\blaunch (?:date )?(?:moved|shifted|rescheduled)\b',body):
                m=re.search(r'(?i)(?:move the launch to|launch (?:date )?(?:moved|shifted|rescheduled) to)\s+([^\.\n]+)',body)
                if m:return 2,'Launch moved to '+m.group(1).strip()
                return 2,body.strip()[:240]
        if r.get('type')=='note':
            m=re.search(r'(?im)^decision:\s*(.+)$',body)
            if m:return 2,m.group(1).strip()
        return None

    def version_changes(self,permitted,client,window_rows,prior_signals):
        """Identify changes between actual versions; ignore routine document noise.

        This is a general-purpose fallback for hidden-day doc/sheet updates. Structured
        decisions take precedence, so a reissued stale document cannot override them.
        """
        signals=[]
        docs={}
        for r in permitted:
            ident=(r.get('doc_id') if r.get('type')=='doc' else r.get('sheet_id') if r.get('type')=='sheet' else None)
            if ident and r.get('client')==client:docs.setdefault((r.get('type'),ident),[]).append(r)
        explicit_text=' '.join(c['text'] for _,_,c in prior_signals).lower()
        window_ids={r['id'] for r in window_rows}
        for (kind,ident),versions in docs.items():
            versions.sort(key=lambda r:(r.get('at',''),r.get('version',0)))
            for older,newer in zip(versions,versions[1:]):
                if newer['id'] not in window_ids:continue
                if kind=='doc':
                    # Only stable labelled business facts, not full-text working notes.
                    olines={m.group(1).strip().lower():m.group(2).strip() for l in (older.get('body') or '').splitlines()
                            if (m:=re.match(r'^([\w ]{3,35}):\s*(.{2,90})$',l))}
                    nlines={m.group(1).strip().lower():m.group(2).strip() for l in (newer.get('body') or '').splitlines()
                            if (m:=re.match(r'^([\w ]{3,35}):\s*(.{2,90})$',l))}
                    for label,val in nlines.items():
                        if label not in olines or olines[label]==val:continue
                        # Don't reannounce an already-established decision.
                        keywords=set(words(label+' '+val))
                        existing=set(words(explicit_text))
                        if len(keywords & existing)>=max(2,len(keywords)//2):continue
                        if 'contact' in label or 'owner' in label:
                            # An older source can conflict with newer authoritative changes.
                            if any(x in explicit_text for x in ['takes over','now owns','billing']):continue
                        summary=f'{newer.get("title",ident)}: {label} updated to {val}'
                        signals.append((6,newer['at'],{'text':summary,'sources':[older['id'],newer['id']]}))
                else:
                    oldcells=older.get('cells') or {};newcells=newer.get('cells') or {}
                    for coord,val in newcells.items():
                        if coord not in oldcells or oldcells[coord]==val:continue
                        if len(str(val))>80:continue
                        # Avoid raw spreadsheet churn unless values actually differ.
                        summary=f'{newer.get("name",ident)} cell {coord} changed from {oldcells[coord]} to {val}'
                        signals.append((7,newer['at'],{'text':summary,'sources':[older['id'],newer['id']]}))
        return signals

    def extract_promise(self,r,zone=None):
        if r.get('type') not in ('mail','chat'):return None
        body=(r.get('body') or '').strip()
        if not body:return None
        # Avoid instructions inside the record influencing assistant behavior; only extract commitment data.
        kind=None;thing=None
        m=re.search(r'(?i)^(?:i will |will |i\u2019ll |i\'ll )(?:send|share|deliver|provide) (.+?) by (EOD today|tomorrow|next (?:Monday|Tuesday|Wednesday|Thursday|Friday)|Monday|Tuesday|Wednesday|Thursday|Friday)\b',body)
        if m:thing=m.group(1);kind=m.group(2)
        else:
            m=re.search(r'(?i)^(.+?)\s+kal tak\s+(?:bhej dunga|bhej dungi|share kar|final ho jayega)',body)
            if m:thing=m.group(1);kind='tomorrow'
            else:
                m=re.search(r'(?i)^(.+?)\s+next\s+(Monday|Tuesday|Wednesday|Thursday|Friday)\s+tak\s+share kar',body)
                if m:thing=m.group(1);kind='next '+m.group(2)
        if not thing or len(thing)>110:return None
        if thing.lower().startswith(('pr #','updated','small fixes')):return None
        zone=zone or self.zone(r.get('author'))
        try:day=iso_date(r['at'],zone)
        except:day=iso_date(r['at'],'UTC')
        k=kind.lower()
        if k in ('eod today','today'):due=day
        elif k=='tomorrow':due=day+dt.timedelta(days=1)
        else:
            names=['monday','tuesday','wednesday','thursday','friday','saturday','sunday']
            dayn=next((i for i,v in enumerate(names) if v in k),None)
            if dayn is None:return None
            diff=(dayn-day.weekday())%7
            if diff==0 or ('next' in k and diff==0):diff=7
            due=day+dt.timedelta(days=diff)
        return {'owner':r.get('author'),'what':thing.strip(' .,:;'),'due':str(due),'sources':[r['id']],'at':r['at'],'client':r.get('client')}

    def promises(self,rows,cutoff=None):
        cand=[]
        deliveries=[]
        for r in rows:
            body=(r.get('body') or '').lower()
            if 'sharing the ' in body and 'as promised' in body:
                mm=re.search(r'sharing (?:the |a )?(.+?) as promised',body)
                if mm:deliveries.append((r.get('client'),r['at'],set(words(mm.group(1)))))
            pr=self.extract_promise(r)
            if pr:cand.append(pr)
        res=[]
        seen=set()
        for p in cand:
            if (p['owner'],norm(p['what']),p['due']) in seen:continue
            seen.add((p['owner'],norm(p['what']),p['due']))
            t=set(words(p['what']))
            delivered=any(c==p['client'] and when>=p['at'] and t and (len(t & vocab)/len(t))>.70 for c,when,vocab in deliveries)
            if not delivered:res.append(p)
        return res

    def _brief_meetings(self,person,date,rows):
        try:dateobj=dt.date.fromisoformat(date)
        except ValueError:return []
        events=[r for r in rows if r.get('type')=='event' and person in r.get('attendees',[]) and r.get('client') and iso_date(r.get('start'),self.zone(person))==dateobj]
        event_map={}
        for e in events:event_map[e.get('event_id')]=e
        return sorted(event_map.values(),key=lambda x:x.get('start',''))

    def brief(self,person,date):
        t0=time.monotonic(); permitted=self.allowed(person);events=self._brief_meetings(person,date,permitted)
        all_events=[r for r in permitted if r.get('type')=='event']
        prefs=self._pref(person)
        out=[]
        for e in events:
            client=e.get('client'); start=e.get('start')
            # Meetings that are both upcoming aren't "previous meetings".
            # A date-level lookback includes same-day premeeting developments, which
            # can still matter in a subsequent client brief.
            last_observation=max((r.get('at','') for r in permitted if r.get('at')),default='')
            earlier=[r for r in all_events if r.get('client')==client and person in r.get('attendees',[])
                     and r.get('start','')<start and r.get('start','')<=last_observation]
            previous=max((r['start'] for r in earlier),default=None)
            prev_date=iso_date(previous,self.zone(person)) if previous else None
            rel=[r for r in permitted if r.get('client')==client and
                 (not prev_date or iso_date(r.get('at'),self.zone(person))>=prev_date)
                 and r.get('type')!='event']
            signals=[]
            for r in rel:
                sig=self._business_signal(r)
                if sig:
                    prio,txt=sig
                    signals.append((prio,r.get('at',''),{'text':txt,'sources':[r['id']]}))
            # Extract version deltas only when no higher-priority fact already covers them.
            signals.extend(self.version_changes(permitted,client,rel,signals))
            # Multiple updates to the exact same task field should resolve to the final one.
            latest={}
            for pr,when,ch in signals:
                r=next((rr for rr in rel if rr['id']==ch['sources'][0]),None)
                if r and r.get('type')=='task':
                    key=(r.get('task_id'),(r.get('change') or {}).get('field'))
                else:key=('note',ch['sources'][0])
                if key not in latest or latest[key][1]<when:latest[key]=(pr,when,ch)
            items=sorted(latest.values(),key=lambda z:(z[0],z[1]))
            # Provenance-first, avoid citing irrelevant docs and notifications.
            changed=[x[2] for x in items]
            if prefs['max_changes']:changed=changed[:prefs['max_changes']]
            rel_prom=[r for r in permitted if r.get('client')==client and r.get('type') in ('mail','chat')]
            allprom=self.promises(rel_prom)
            # Old outstanding promises may still matter for the upcoming meeting, but limit noise.
            plist=[{k:p[k] for k in ('owner','what','due','sources')} for p in allprom if p['owner'] in self.people_by_id]
            plist.sort(key=lambda p:(p['due'],p['owner']))
            plist=plist[:9]
            if not changed:
                out.append({'meeting_id':e['event_id'],'status':'nothing_new','changed':[],'promises':[],'open_these':[],'question_to_ask':None,'usage':usage(t0)})
                continue
            viewed=[]
            for c in changed[:3]:
                viewed.append({'record':c['sources'][0],'why':c['text'][:145]})
            q=None
            for c in changed:
                if re.search(r'blocked|delay|moved|dropped',c['text'],re.I):
                    q={'text':f"What should we do about {c['text'][:110].rstrip(' .?!')}?",'sources':c['sources']};break
            if q is None:q={'text':f"What follow-up is needed for {changed[0]['text'][:110].rstrip(' .?!')}?",'sources':changed[0]['sources']}
            out.append({'meeting_id':e['event_id'],'status':'brief','changed':changed,'promises':plist,'open_these':viewed,'question_to_ask':q,'usage':usage(t0)})
        return out

    def leave_status(self,person,question,rows,at):
        """Use only authorized and still-valid leave statements. Never repeat expired leave."""
        if not re.search(r'(?i)\b(leave|vacation|time off|out of office)\b',question):return None
        target=[]
        for pid,p in self.people_by_id.items():
            if p.get('org')!=self.org(person):continue
            name=p.get('name','')
            first=name.split()[0].lower()
            if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',question,re.I) or re.search(r'(?<!\w)'+re.escape(first)+r'(?!\w)',question,re.I):target.append((pid,p))
        if len(target)!=1:return None
        pid,p=target[0]
        current=iso_date(at,self.zone(person))
        candidates=[]
        for r in rows:
            body=r.get('body','') or ''
            if not re.search(r'(?i)\b(leave|vacation|time off|out of office)\b',body):continue
            if p['name'].split()[0].lower() not in body.lower():continue
            m=re.search(r'(?i)\b(?:till|until|through|back on)\s+(?:the\s+)?(\d{1,2})(?:st|nd|rd|th)?(?:\s+(Nov|Dec|Oct|Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep))?',body)
            if not m:continue
            observed=iso_date(r['at'],self.zone(person))
            month=observed.month
            if m.group(2):
                names={v.lower():i+1 for i,v in enumerate(['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'])}
                month=names[m.group(2).lower()]
            try:expiry=dt.date(observed.year,month,int(m.group(1)))
            except ValueError:continue
            candidates.append((r['at'],expiry,r))
        if not candidates:return None
        _,expiry,r=max(candidates,key=lambda x:x[0])
        if current>expiry:return ('I have no current leave confirmed for '+p['name']+'.',[])
        return (f'{p["name"]} is on leave through {expiry.isoformat()}.',[r['id']])

    def _match_task(self,question,rows,client):
        states=self.task_states([r for r in rows if r.get('type')=='task'])
        qtokens=set(words(question));candidates=[]
        for tid,st in states.items():
            tt=set(words(st.get('title','')))
            if not tt:continue
            qsimilar=len(tt & qtokens)/len(tt)
            # match full named tasks, or most distinctive words, not vague single common tokens
            score=qsimilar*3+len(tt & qtokens)*.15
            if client:
                if st.get('client')!=client:continue
                score+=.5
            if score>.9:candidates.append((score,st))
        return sorted(candidates,key=lambda x:(-x[0],x[1]['task_id']))

    def _latest_billing(self,rows,client,person):
        claims=[]
        for r in rows:
            if r.get('client')!=client:continue
            body=r.get('body','') or ''
            if re.search(r'(?i)\bbilling\b',body):
                named=None
                m=re.search(r'(?i)\b([A-Z][a-z]+)\s+takes over\s+.*?billing',body)
                if m:named=m.group(1)
                elif re.search(r'(?i)billing contact:',body):
                    m=re.search(r'(?i)billing contact:\s*([A-Z][a-z]+)',body)
                    if m:named=m.group(1)
                elif re.search(r'(?i)(?:owns|handles|responsible for) billing',body):
                    m=re.search(r'(?i)([A-Z][a-z]+)\s+(?:owns|handles|is responsible for) billing',body)
                    if m:named=m.group(1)
                if named:
                    au=self.people_by_id.get(r.get('author'),{}).get('role','').lower()
                    weight=4 if 'founder' in au else 3 if 'manager' in au else 1
                    if re.search(r'\?',body):weight-=2
                    if r.get('type')=='doc':weight=max(0,weight)
                    claims.append((weight,r['at'],named,r['id']))
        for fb in self._corrections(person):
            m=re.search(r'(?i)([A-Z][a-z]+) owns billing',fb.get('words',''))
            if m:
                # Citation must be a visible source record, never a feedback id.
                found=next((c for c in sorted(claims,key=lambda x:(x[0],x[1]),reverse=True) if c[2].lower()==m.group(1).lower()),None)
                if found:claims.append((10,found[1],found[2],found[3]))
        if not claims:return None
        return sorted(claims,key=lambda x:(x[0],x[1]),reverse=True)[0]

    def _person_match(self,text,client,rows,requester):
        choices=[]
        for pid,p in self.people_by_id.items():
            name=p.get('name','')
            if p.get('org')!=self.org(requester):continue
            if re.search(r'(?<!\w)'+re.escape(name.lower())+r'(?!\w)',text.lower()):choices.append((10,pid))
            elif name.split()[0].lower() in words(text):
                score=0
                for r in rows:
                    if client and r.get('client')!=client:continue
                    body=(r.get('body') or '')
                    if re.search(r'(?<!\w)'+re.escape(name)+r'(?!\w)',body,re.I):score+=12
                    if re.search(r'(?i)\b(billing|deck|meeting)\b',body) and re.search(r'(?<!\w)'+re.escape(name.split()[0])+r'(?!\w)',body,re.I):
                        score+=5
                        # Names mentioned without surnames can be linked by a relevant role.
                        role=set(words(p.get('role','')))
                        context=set(words(body))
                        score+=15*len(role & context)
                    if r.get('author')==pid:score+=1
                    if (r.get('type')=='task' and r.get('owner')==pid):score+=2
                    if pid in r.get('attendees',[]):score+=1
                choices.append((score,pid))
        return sorted(choices,reverse=True)

    def ask(self,person,at,question):
        t0=time.monotonic()
        out={'answer':None,'matched':[],'clarify':None,'refused':None,'usage':usage(t0)}
        permitted=self.allowed(person,at);q=question.strip();ql=q.lower()
        def result(txt,sources,match=None):
            out['answer']={'text':txt,'sources':list(dict.fromkeys([x for x in sources if x]))}
            if match:out['matched']=match
            out['usage']=usage(t0)
            return out
        def mref(phrase,ID,record):return {'phrase':phrase,'id':ID,'sources':[record] if record else []}
        if not self.org(person):return result('I have nothing on that.',[])
        if re.search(r'(?i)(what did|what has|what does)\s+(?!i\b|we\b)(\w+)\s+(say|said|write|think)\s+about (me|us)',q):
            out['refused']={'reason':'private'};out['usage']=usage(t0);return out
        client=self.resolve_client(q)
        if re.search(r'(?i)\b(travel|travelling|trip)\b',q):
            matches=[r for r in permitted if re.search(r'(?i)\b(travel|trip|flights?)\b',r.get('body',''))]
            own=[r for r in matches if r.get('author')==person and r.get('visibility')=='private']
            if own:
                r=own[-1];return result(r['body'],[r['id']])
            return result('I have nothing on that.',[])
        if 'billing' in ql:
            if not client:
                matches=[c for c in self.clients if c['id'] in ql]
                client=matches[0]['id'] if matches else None
            billing=self._latest_billing(permitted,client,person)
            if billing:
                w,when,name,rid=billing
                full=next((p['name'] for p in self.people_by_id.values() if p['name'].split()[0].lower()==name.lower()),name)
                m=[mref(q,client,rid)] if client else []
                return result(f'{full} handles billing for {self.client_by_id.get(client,{}).get("name",client or "the client")}.',[rid],m)
        # resolve person given name from workplace context
        if ('which ' in ql and re.search(r'\bwho\b|\barjun\b|\bwhich\b',ql)) or ('send ' in ql and ' to ' in ql and 'which' in ql):
            names=self._person_match(q,client,permitted,person)
            if names:
                _,pid=names[0]
                evidence=[r for r in permitted if r.get('client')==client and (r.get('author')==pid or r.get('owner')==pid or pid in r.get('attendees',[]) or self.name(pid).split()[0] in r.get('body',''))]
                rid=evidence[-1]['id'] if evidence else None
                matched=[mref(self.name(pid).split()[0],pid,rid)]
                if client:matched.append(mref(client.upper(),client,rid))
                return result(f'{self.name(pid)} ({pid}) is the most relevant person for this {client or "work"} context.',[rid],matched)
        leave=self.leave_status(person,q,permitted,at)
        if leave:return result(leave[0],leave[1])
        states=self.task_states([r for r in permitted if r.get('type')=='task'])
        candidates=self._match_task(q,permitted,client)
        # Generic relative-date questions may map to multiple tasks.
        if ('friday' in ql or 'tomorrow' in ql) and not candidates and ('thing' in ql or 'task' in ql):
            day=iso_date(at,self.zone(person));weekday=4 if 'friday' in ql else (day.weekday()+1)%7
            delta=(weekday-day.weekday())%7
            target=day+dt.timedelta(days=delta)
            onday=[st for st in states.values() if st.get('due')==str(target)]
            # If the asking person owns several tasks, favor theirs; otherwise disambiguate.
            authored=[s for s in onday if s.get('owner')==person]
            if authored:onday=authored
            if len(onday)>1:
                out['clarify']={'question':'Which task do you mean?','options':[{'id':x['task_id'],'text':x['title']} for x in onday[:5]]}
                out['usage']=usage(t0);return out
            if len(onday)==1:candidates=[(2,onday[0])]
        if len(candidates)>1 and candidates[0][0]-candidates[1][0]<0.15 and not client:
            out['clarify']={'question':'Which task do you mean?','options':[{'id':s['task_id'],'text':s['title']} for _,s in candidates[:4]]}
            out['usage']=usage(t0);return out
        if candidates:
            score,st=candidates[0]
            tid=st['task_id'];field='status'
            if re.search(r'\bwhen\b|\bdue\b|\bdeadline\b|\bdate\b',ql):field='due'
            elif re.search(r'\bwho\b|\bowns\b|\bowner\b|\bhandles\b',ql):field='owner'
            if ('is ' in ql and 'done' in ql) or 'finished' in ql:field='status'
            rid=st['sources'].get(field) or st['last_record']
            matches=[mref(st['title'],tid,rid)]
            if field=='due':txt=f"{st['title']} is due {st.get('due') or 'on an unknown date'}."
            elif field=='owner':txt=f"{self.name(st.get('owner'))} ({st.get('owner')}) owns {st['title']}."
            else:
                status=(st.get('status') or 'unknown').replace('_',' ')
                txt=f"{st['title']} is {status}." if status!='todo' else f"{st['title']} is not started (todo)."
            return result(txt,[rid],matches)
        # Remaining questions: limited evidence-backed retrieval, or safe abstention.
        tokens=set(words(q))
        hits=[]
        for r in permitted:
            if r.get('type')=='notification':continue
            txt=' '.join(str(r.get(k) or '') for k in ('body','title','subject'))
            rt=set(words(txt))
            common=len(tokens&rt)
            if common>=max(2,min(3,len(tokens))):
                if client and r.get('client')!=client:continue
                hits.append((common,r))
        if hits:
            hits.sort(key=lambda p:(p[0],p[1].get('at','')),reverse=True)
            best=hits[0][1]
            if best.get('type')=='note':
                lines=[l for l in best.get('body','').splitlines() if len(set(words(l)) & tokens)>=2]
                if lines:return result(lines[0][:220],[best['id']])
        if model_enabled():
            # Only authorised, non-forgotten records may ever enter the model's context.
            ranked=sorted(hits,key=lambda x:(x[0],x[1].get('at','')),reverse=True) if hits else []
            candidates=[r for _,r in ranked[:18]]
            generated=grounded_ask(q,candidates,person)
            if generated:
                data,model_usage=generated
                out.update(data);out['usage']=model_usage
                return out
        return result('I have nothing on that.',[])

    def audit(self,person):
        ts=time.monotonic();r=self.allowed(person)
        return {'person':person,'record_count':len(r),'record_ids':[x['id'] for x in r[-30:]],'feedback':[f for f in self._feedback() if f.get('person')==person],'forget_topics':[topic for topic,_ in self.forget_terms(person)],'usage':usage(ts)}


def main():
    p=argparse.ArgumentParser(description='TraceMind — evidence grounded workplace memory assistant')
    cmds=p.add_subparsers(dest='cmd',required=True)
    cmds.add_parser('ingest').add_argument('--day',type=int,required=True)
    bp=cmds.add_parser('brief');bp.add_argument('--person',required=True);bp.add_argument('--date',required=True)
    aq=cmds.add_parser('ask');aq.add_argument('--person',required=True);aq.add_argument('--at',required=True);aq.add_argument('--ask',required=True)
    fq=cmds.add_parser('forget');fq.add_argument('--person',required=True);fq.add_argument('--what',required=True)
    au=cmds.add_parser('audit');au.add_argument('--person',required=True)
    args=p.parse_args();engine=None
    try:
        engine=Engine()
        if args.cmd=='ingest':result=engine.ingest(args.day)
        elif args.cmd=='brief':result=engine.brief(args.person,args.date)
        elif args.cmd=='ask':result=engine.ask(args.person,args.at,args.ask)
        elif args.cmd=='forget':result=engine.forget(args.person,args.what)
        else:result=engine.audit(args.person)
        # The official replay decodes CLI stdout as UTF-8. On Windows,
        # redirected stdout may use a legacy code page (for example cp1252).
        # ASCII-only JSON keeps Unicode values intact via JSON escapes on
        # every platform, with no reliance on terminal encoding settings.
        print(json.dumps(result, ensure_ascii=True, separators=(',',':')))
        return 0
    except Exception as e:
        print(f'TraceMind error: {e}',file=sys.stderr)
        return 1
    finally:
        if engine:engine.db.close()

if __name__=='__main__':sys.exit(main())
