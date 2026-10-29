import sys,json,pandas as pd,numpy as np
S=sys.argv[1]
j=pd.read_pickle(S+'/j2.pkl'); h=json.load(open(S+'/hist.json'))
raw=pd.read_pickle(S+'/daily.pkl'); tick=set(raw.columns.get_level_values(0))
F={t:raw[t].dropna(subset=['Close']) for t in j.ticker.unique() if t in tick}
fc=h['first_closed']; 
j['res_commit']=j.id.map(lambda i: pd.Timestamp(fc[i][0]).tz_convert('America/Los_Angeles').tz_localize(None) if i in fc else pd.NaT)
j['res_date']=j.res_commit.dt.normalize()
cl=j[j.status=='closed'].copy()
cl['cal_days']=(cl.res_date-cl.date).dt.days
# trading sessions elapsed strictly between signal date and resolution date
spy=raw['SPY'].dropna(subset=['Close']).index
def sess_between(a,b): return int(((spy>a)&(spy<b)).sum())
cl['full_sessions_between']=[sess_between(a,b) for a,b in zip(cl.date,cl.res_date)]
print('calendar days to resolution:',cl.cal_days.describe().round(2).to_dict())
print(cl.groupby('outcome').cal_days.describe().round(2))
print('full sessions never inspected between signal day and resolving run date:'); print(cl.full_sessions_between.value_counts().sort_index())
# missed hits: during never-inspected full sessions (daily bars between date and res_date exclusive), did price touch stop/target?
miss=[]
for _,a in cl.iterrows():
    f=F.get(a.ticker)
    if f is None or a.full_sessions_between==0: miss.append(np.nan); continue
    fb=f[(f.index>a.date)&(f.index<a.res_date)]
    if a.sig_hour<6.5: fb=f[(f.index>=a.date)&(f.index<a.res_date)]
    long=a.bias=='long'
    if long: ht=(fb.High>=a.target).any(); hs=(fb.Low<=a.stop).any()
    else: ht=(fb.Low<=a.target).any(); hs=(fb.High>=a.stop).any()
    miss.append('both' if ht and hs else 'target' if ht else 'stop' if hs else 'none')
cl['missed']=miss
sub=cl[cl.full_sessions_between>0]
print('\ntrades with >=1 un-inspected full session:',len(sub),'of',len(cl))
print(pd.crosstab(sub.outcome,sub.missed))
sc=sub[sub.outcome=='scratch']
print('scratch trades whose stop and/or target was actually touched in un-inspected sessions: %d of %d (%.1f%%)'%((sc.missed!='none').sum(),len(sc),100*(sc.missed!='none').mean()))
# by period
cl['period']=np.where(cl.date<'2026-08-27','Jul31-Aug26 (2 scans/day)','Aug27-Oct28 (sparse)')
print(cl.groupby('period').apply(lambda g: pd.Series(dict(n=len(g),med_cal_days=g.cal_days.median(),mean_full_sess=g.full_sessions_between.mean(),pct_gap=(g.full_sessions_between>0).mean(),scratch=(g.outcome=='scratch').mean()))).round(3))
cl.to_pickle(S+'/closed.pkl')
# scans per week of calendar
print(j.groupby('period' if 'period' in j else j.date.dt.to_period('W')).size().tail(14))
