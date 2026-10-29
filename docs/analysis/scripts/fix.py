import sys,json,pandas as pd,numpy as np,ta
S=sys.argv[1]; pd.set_option('display.width',250)
j=pd.read_pickle(S+'/j3.pkl'); rp=pd.read_pickle(S+'/replay.pkl')
rp=rp.merge(j[['id','dir_rangepos','sgn_ext','vs200','atrpct','stop_atr']],on='id')
rp=rp[rp.o!='nodata'].copy()
rp['jR']=np.where(rp.o=='win',rp.rr,np.where(rp.o=='loss',-1.0,0.0))   # journal-style: timeout=0 (scratch)
rp['pjR']=np.where(rp.po=='win',rp.rr,np.where(rp.po=='loss',-1.0,0.0))
rng=np.random.default_rng(3)
def ci(g,col):
    byd=g.groupby('date')[col].agg(['sum','count']); bs=[]
    for _ in range(2000):
        s=rng.integers(0,len(byd),len(byd)); bs.append(byd['sum'].values[s].sum()/byd['count'].values[s].sum())
    return np.percentile(bs,[2.5,97.5])
print('Journal-style R (win=+rr, loss/amb=-1, timeout=0) replay of recorded stop/target held N sessions')
print('N n win loss timeout | jR [95% CI by scan day] | placebo(flip) win loss jR | median MTM R of timeouts')
for N in [1,2,3,5,10,20]:
    g=rp[rp.N==N]; lo,hi=ci(g,'jR'); 
    print(N,len(g),(g.o=='win').sum(),(g.o=='loss').sum(),(g.o=='timeout').sum(),'|',round(g.jR.mean(),3),[round(lo,3),round(hi,3)],'|',(g.po=='win').sum(),(g.po=='loss').sum(),round(g.pjR.mean(),3),'|',round(g[g.o=='timeout'].R.median(),3))
g=rp[rp.N==5]
for col,q in [('dir_rangepos',4),('sgn_ext',4),('atrpct',3),('stop_atr',4)]:
    g=g.copy(); g['b']=pd.qcut(g[col],q,duplicates='drop')
    print('\nN=5 by',col); print(g.groupby('b',observed=True).apply(lambda x: pd.Series(dict(n=len(x),win=(x.o=='win').sum(),loss=(x.o=='loss').sum(),hit=(x.o=='win').sum()/max(1,(x.o!='timeout').sum()),jR=x.jR.mean(),placebo_jR=x.pjR.mean()))).round(3))
g=rp[(rp.N==5)&rp.vs200.notna()].copy(); g['with200']=np.where(g.bias=='long',g.vs200>0,g.vs200<0)
print('\nN=5 EMA200 alignment'); print(g.groupby('with200').apply(lambda x: pd.Series(dict(n=len(x),win=(x.o=='win').sum(),loss=(x.o=='loss').sum(),hit=(x.o=='win').sum()/max(1,(x.o!='timeout').sum()),jR=x.jR.mean(),placebo_jR=x.pjR.mean()))).round(3))
# by bias, month
g=rp[rp.N==5].copy(); g['month']=g.date.dt.strftime('%Y-%m')
print(g.groupby(['month','bias']).apply(lambda x: pd.Series(dict(n=len(x),win=(x.o=='win').sum(),loss=(x.o=='loss').sum(),jR=x.jR.mean(),placebo_jR=x.pjR.mean()))).round(3))
# alt geometry in % terms
raw=pd.read_pickle(S+'/daily.pkl'); tick=set(raw.columns.get_level_values(0))
F={}
for t in j.ticker.unique():
    if t in tick:
        f=raw[t].dropna(subset=['Close']).copy()
        if len(f)<60: continue
        f['atr']=ta.volatility.AverageTrueRange(f.High,f.Low,f.Close,window=14).average_true_range(); F[t]=f
def sim(e,s,t,long,bars,N):
    risk=abs(e-s)
    for k,(o,h,l,c) in enumerate(bars[:N]):
        if long:
            if o<=s: return (o-e)/e
            if o>=t: return (o-e)/e
            ht=h>=t; hs=l<=s
        else:
            if o>=s: return (e-o)/e
            if o<=t: return (e-o)/e
            ht=l<=t; hs=h>=s
        if hs: return -risk/e
        if ht: return abs(t-e)/e
    if len(bars)<N: return np.nan
    c=bars[N-1][3]; return (c-e)/e if long else (e-c)/e
res=[]
for k in [1.0,1.5,2.0,3.0]:
  for m in [1.5]:
    for N in [3,5,10]:
        R=[];P=[];D=[]
        for _,a in j.iterrows():
            f=F.get(a.ticker)
            if f is None or a.horizon!='swing': continue
            p=f[f.index<a.date]
            if len(p)<30: continue
            atr=p.atr.iloc[-1]; start=a.date if a.sig_hour<6.5 else a.date+pd.Timedelta(days=1)
            fb=f[f.index>=start]; bars=list(zip(fb.Open,fb.High,fb.Low,fb.Close)); long=a.bias=='long'; e=a.entry; d=k*atr
            s,t=(e-d,e+m*d) if long else (e+d,e-m*d)
            s2,t2=(e+d,e-m*d) if long else (e-d,e+m*d)
            R.append(sim(e,s,t,long,bars,N)*100); P.append(sim(e,s2,t2,not long,bars,N)*100); D.append(a.date)
        R=np.array(R,float);P=np.array(P,float);ok=~np.isnan(R)&~np.isnan(P)
        dd=pd.DataFrame({'x':R[ok],'d':np.array(D)[ok]}); byd=dd.groupby('d').x.agg(['sum','count']); bs=[]
        for _ in range(2000):
            s_=rng.integers(0,len(byd),len(byd)); bs.append(byd['sum'].values[s_].sum()/byd['count'].values[s_].sum())
        res.append(dict(stopATR=k,rr=m,N=N,n=ok.sum(),mean_pct=R[ok].mean(),ci_lo=np.percentile(bs,2.5),ci_hi=np.percentile(bs,97.5),placebo_pct=P[ok].mean()))
print('\nATR-fit geometry (stop=k*daily ATR, target=1.5*stop, exit at Nth close): mean % return per trade')
print(pd.DataFrame(res).round(3).to_string())
