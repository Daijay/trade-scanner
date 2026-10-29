import sys,json,pandas as pd,numpy as np,ta
S=sys.argv[1]
exec(open(S+'/base.py').read().split("if __name__")[0])
raw=pd.read_pickle(S+'/daily.pkl')
tick=set(raw.columns.get_level_values(0))
def frame(t):
    if t not in tick: return None
    f=raw[t].dropna(subset=['Close']); 
    return f if len(f)>30 else None
j['sig_hour']=j.ts.dt.hour+j.ts.dt.minute/60
rows=[]
cache={}
for _,a in j.iterrows():
    t=a.ticker
    if t not in cache:
        f=frame(t)
        if f is not None:
            f=f.copy(); f['atr']=ta.volatility.AverageTrueRange(f.High,f.Low,f.Close,window=14).average_true_range()
        cache[t]=f
    f=cache[t]
    r=dict(id=a.id)
    if f is None: rows.append(r); continue
    D=a.date
    idx=f.index
    prev=idx[idx<D]; cur=idx[idx==D]
    if len(prev)==0: rows.append(r); continue
    pc=f.loc[prev[-1],'Close']; cc=f.loc[cur[0],'Close'] if len(cur) else np.nan
    r['pc']=pc; r['cc']=cc; r['d_prev']=abs(a.entry/pc-1); r['d_cur']=abs(a.entry/cc-1) if cc==cc else np.nan
    r['atr_prev']=f.loc[prev[-1],'atr']; r['atr_cur']=f.loc[cur[0],'atr'] if len(cur) else np.nan
    rows.append(r)
m=pd.DataFrame(rows); j=j.merge(m,on='id',how='left')
j['entry_ref']=np.where(j.d_cur<j.d_prev,'today_close','prev_close')
j['match_tol']=np.minimum(j.d_prev.fillna(9),j.d_cur.fillna(9))
print('data coverage',j.pc.notna().mean())
print('entry within 0.5% of a daily close:',(j.match_tol<0.005).mean())
print(j.groupby('scan').apply(lambda g: pd.Series(dict(n=len(g),prev_close=(g.d_prev<0.005).mean(),today_close=(g.d_cur<0.005).mean()))))
j['sig_bucket']=pd.cut(j.sig_hour,[0,7.5,11,12.99,24],labels=['<7:30','7:30-11','11-13','>=13 (after close)'])
print(j.groupby('sig_bucket').apply(lambda g: pd.Series(dict(n=len(g),prev_close=(g.d_prev<0.005).mean(),today_close=(g.d_cur<0.005).mean()))))
j.to_pickle(S+'/j1.pkl')
