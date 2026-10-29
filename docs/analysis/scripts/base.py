import json,sys,pandas as pd,numpy as np
S=sys.argv[1]
j=pd.DataFrame(json.load(open('journal.json')))
j['ts']=pd.to_datetime(j.timestamp,utc=True).dt.tz_convert('America/Los_Angeles')
j['date']=j.ts.dt.normalize().dt.tz_localize(None)
j['dow']=j.ts.dt.day_name()
sec=json.load(open(S+'/sectors.json')); j['sector']=j.ticker.map(sec).fillna('Unclassified')
j['rres']=pd.to_datetime(j.resolved_at,utc=True,errors='coerce')
def blk(g):
    c=g[g.status=='closed']
    w=(c.outcome=='win').sum(); l=(c.outcome=='loss').sum(); a=(c.outcome=='ambiguous').sum(); s=(c.outcome=='scratch').sum()
    n=len(c)
    hr=w/(w+l+a) if (w+l+a) else np.nan
    adj=(w+.5*s)/n if n else np.nan
    r=np.where(c.outcome=='win',c.rr,np.where(c.outcome.isin(['loss','ambiguous']),-1.0,0.0))
    return pd.Series(dict(total=len(g),open=(g.status=='open').sum(),closed=n,win=w,loss=l,amb=a,scratch=s,hit=hr,adj=adj,scr_rate=s/n if n else np.nan,avgR=r.mean() if n else np.nan,sumR=r.sum()))
if __name__=='__main__':
    pd.set_option('display.width',250); pd.set_option('display.max_rows',500)
    print(blk(j))
    for name,mask in [('conv>=6 (actually alerted)',j.conviction>=6),('conv<6',j.conviction<6),('>=2026-08-14 baseline',j.date>='2026-08-14'),('<2026-08-14',j.date<'2026-08-14'),('conv>=6 & >=08-14',(j.conviction>=6)&(j.date>='2026-08-14')),('conv>=6 &<08-14',(j.conviction>=6)&(j.date<'2026-08-14')),('Aug7-13 dup period',(j.date>='2026-08-07')&(j.date<='2026-08-13')),('conv>=1',j.conviction>=1),('rr>=1.5',j.rr>=1.5)]:
        print(name); print(blk(j[mask]).round(3).to_dict())
    for k in ['horizon','bias','scan','conviction','dow','sector']:
        print('\n==',k); print(j.groupby(k).apply(blk).round(3).to_string())
    j['month']=j.date.dt.strftime('%Y-%m'); print(j.groupby('month').apply(blk).round(3).to_string())
    print(j[j.conviction>=6].groupby('conviction').apply(blk).round(3).to_string())
