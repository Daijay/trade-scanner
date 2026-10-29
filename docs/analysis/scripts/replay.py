import sys,json,pandas as pd,numpy as np
S=sys.argv[1]
j=pd.read_pickle(S+'/j1.pkl')
raw=pd.read_pickle(S+'/daily.pkl'); tick=set(raw.columns.get_level_values(0))
F={t:raw[t].dropna(subset=['Close']) for t in j.ticker.unique() if t in tick}
spy=raw['SPY'].dropna(subset=['Close'])
def sim(entry,stop,target,long,bars,N):
    """returns (outcome, R, sessions_used). outcome win/loss/timeout. ambiguous->loss."""
    risk=abs(entry-stop)
    sgn=1 if long else -1
    for k,(o,h,l,c) in enumerate(bars[:N]):
        if long:
            if o<=stop: return 'loss',(o-entry)/risk,k+1
            if o>=target: return 'win',(o-entry)/risk,k+1
            ht=h>=target; hs=l<=stop
        else:
            if o>=stop: return 'loss',(entry-o)/risk,k+1
            if o<=target: return 'win',(entry-o)/risk,k+1
            ht=l<=target; hs=h>=stop
        if ht and hs: return 'loss',-1.0,k+1
        if hs: return 'loss',-1.0,k+1
        if ht: return 'win',abs(target-entry)/risk,k+1
    if len(bars)<N: return 'nodata',np.nan,len(bars)
    c=bars[N-1][3]; return 'timeout',sgn*(c-entry)/risk,N
Ns=[1,2,3,5,10,20]
out=[]
for _,a in j.iterrows():
    f=F.get(a.ticker)
    if f is None: continue
    long=a.bias=='long'
    if not ((long and a.stop<a.entry<a.target) or ((not long) and a.target<a.entry<a.stop)): continue
    D=a.date
    start=D if a.sig_hour<6.5 else D+pd.Timedelta(days=1)
    fb=f[f.index>=start]
    bars=list(zip(fb.Open,fb.High,fb.Low,fb.Close))
    risk=abs(a.entry-a.stop)
    # placebo: opposite direction, same distances mirrored
    pe=a.entry; pstop=a.entry+(a.entry-a.stop); ptgt=a.entry-(a.target-a.entry)
    for N in Ns:
        o,R,k=sim(a.entry,a.stop,a.target,long,bars,N)
        po,pR,pk=sim(pe,pstop,ptgt,not long,bars,N)
        out.append(dict(id=a.id,N=N,o=o,R=R,po=po,pR=pR,date=a.date,scan=a.scan,bias=a.bias,conv=a.conviction,rr=a.rr))
r=pd.DataFrame(out); r.to_pickle(S+'/replay.pkl')
rng=np.random.default_rng(1)
def summ(g,col_o,col_R):
    g=g[g[col_o]!='nodata']
    n=len(g); w=(g[col_o]=='win').sum(); l=(g[col_o]=='loss').sum(); t=(g[col_o]=='timeout').sum()
    return n,w,l,t,w/(w+l) if w+l else np.nan,g[col_R].mean()
print('N  n   win loss tmo  hit   meanR(mtm) | placebo: win loss tmo hit meanR | breakeven hit')
for N in Ns:
    g=r[r.N==N]
    n,w,l,t,h,mR=summ(g,'o','R')
    gg=g[g.o!='nodata']; 
    _,pw,pl,pt,ph,pmR=summ(g,'po','pR')
    # cluster bootstrap on meanR by scan date
    dates=gg.date.unique(); 
    bs=[]
    for _ in range(2000):
        s=rng.choice(dates,len(dates)); 
        gb=pd.concat([gg[gg.date==d] for d in s]) if False else None
    byd=gg.groupby('date').R.agg(['sum','count'])
    for _ in range(2000):
        s=rng.integers(0,len(byd),len(byd)); bs.append(byd['sum'].values[s].sum()/byd['count'].values[s].sum())
    lo,hi=np.percentile(bs,[2.5,97.5])
    print(N,n,w,l,t,round(h,3),round(mR,3),f'[{lo:.3f},{hi:.3f}]','|',pw,pl,pt,round(ph,3),round(pmR,3),'|',round(1/(1+gg.rr.mean()),3))
