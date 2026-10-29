import sys,json,pandas as pd,numpy as np,ta
S=sys.argv[1]
pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
j=pd.read_pickle(S+'/j2.pkl'); rp=pd.read_pickle(S+'/replay.pkl')
raw=pd.read_pickle(S+'/daily.pkl'); tick=set(raw.columns.get_level_values(0))
F={}
for t in j.ticker.unique():
    if t in tick:
        f=raw[t].dropna(subset=['Close']).copy()
        if len(f)<60: continue
        f['atr']=ta.volatility.AverageTrueRange(f.High,f.Low,f.Close,window=14).average_true_range()
        f['ema21']=ta.trend.EMAIndicator(f.Close,21).ema_indicator(); f['ema200']=ta.trend.EMAIndicator(f.Close,200).ema_indicator()
        f['rsi']=ta.momentum.RSIIndicator(f.Close,14).rsi()
        f['hi20']=f.High.rolling(20).max(); f['lo20']=f.Low.rolling(20).min(); f['ret20']=f.Close.pct_change(20)*100
        f['tr']=np.maximum(f.High-f.Low,np.maximum((f.High-f.Close.shift()).abs(),(f.Low-f.Close.shift()).abs()))
        F[t]=f
feat=[]
for _,a in j.iterrows():
    f=F.get(a.ticker)
    if f is None: continue
    p=f[f.index<a.date]
    if len(p)<30: continue
    q=p.iloc[-1]; r=dict(id=a.id)
    span=q.hi20-q.lo20; r['rangepos']=(a.entry-q.lo20)/span if span>0 else np.nan
    r['ext_ema21_atr']=(a.entry-q.ema21)/q.atr; r['rsi']=q.rsi; r['ret20']=q.ret20
    r['ema200']=q.ema200; r['vs200']=(a.entry/q.ema200-1)*100 if q.ema200==q.ema200 else np.nan
    r['atrpct']=q.atr/a.entry*100
    # realized TR next 5 sessions vs atr
    start=a.date if a.sig_hour<6.5 else a.date+pd.Timedelta(days=1)
    fb=f[f.index>=start].head(5)
    r['real_tr_over_atr']=fb.tr.mean()/q.atr if len(fb)==5 else np.nan
    feat.append(r)
ft=pd.DataFrame(feat); j=j.merge(ft,on='id',how='left')
sg=np.where(j.bias=='long',1,-1)
j['sgn_ext']=sg*j.ext_ema21_atr; j['sgn_ret20']=sg*j.ret20
j['dir_rangepos']=np.where(j.bias=='long',j.rangepos,1-j.rangepos)  # 1 = at the edge in trade direction
print('coverage',j.rangepos.notna().mean())
print('long: median rangepos %.2f, ext vs EMA21 %.2f ATR, RSI %.1f, ret20 %.1f%%'%tuple(j[j.bias=='long'][['rangepos','ext_ema21_atr','rsi','ret20']].median()))
print('short: median rangepos %.2f, ext vs EMA21 %.2f ATR, RSI %.1f, ret20 %.1f%%'%tuple(j[j.bias=='short'][['rangepos','ext_ema21_atr','rsi','ret20']].median()))
print('realized next-5-session mean TR / signal ATR:',j.real_tr_over_atr.describe().round(2).to_dict())
r5=rp[rp.N==5].merge(j[['id','dir_rangepos','sgn_ext','vs200','atrpct','stop_atr','tgt_atr','ticker','conviction']],on='id')
r5=r5[r5.o!='nodata']
def tab(col,bins,labels=None):
    r5['b']=pd.qcut(r5[col],bins,duplicates='drop') if isinstance(bins,int) else pd.cut(r5[col],bins)
    t=r5.groupby('b',observed=True).apply(lambda g: pd.Series(dict(n=len(g),win=(g.o=='win').sum(),loss=(g.o=='loss').sum(),hit=(g.o=='win').sum()/max(1,((g.o=='win')|(g.o=='loss')).sum()),meanR=g.R.mean(),placeboR=g.pR.mean())))
    print('\nby',col); print(t.round(3))
tab('dir_rangepos',4); tab('sgn_ext',4); tab('atrpct',3)
# EMA200 trend filter: long only if entry>ema200, short only if entry<ema200
r5['with200']=np.where(r5.bias=='long',r5.vs200>0,r5.vs200<0)
print('\nEMA200 filter (5-session replay, real stops/targets):')
print(r5[r5.vs200.notna()].groupby('with200').apply(lambda g: pd.Series(dict(n=len(g),win=(g.o=='win').sum(),loss=(g.o=='loss').sum(),hit=(g.o=='win').sum()/max(1,((g.o=='win')|(g.o=='loss')).sum()),meanR=g.R.mean(),placeboR=g.pR.mean()))).round(3))
# journal outcomes by with200
jj=j.copy(); jj['with200']=np.where(jj.bias=='long',jj.vs200>0,jj.vs200<0); cj=jj[(jj.status=='closed')&jj.vs200.notna()]
print(cj.groupby('with200').apply(lambda g: pd.Series(dict(n=len(g),win=(g.outcome=='win').sum(),loss=g.outcome.isin(['loss','ambiguous']).sum(),scr=(g.outcome=='scratch').sum()))))
print('share of trades already with-200:',jj[jj.vs200.notna()].with200.mean().round(3), 'long share above 200:', (jj[(jj.bias=='long')&jj.vs200.notna()].vs200>0).mean().round(3),'short share below:',(jj[(jj.bias=='short')&jj.vs200.notna()].vs200<0).mean().round(3))
# ATR-based alternative geometry replay: stop=k*ATR, target=m*stop, hold N, actual direction + placebo
def sim(entry,stop,target,long,bars,N):
    risk=abs(entry-stop); sgn=1 if long else -1
    for k,(o,h,l,c) in enumerate(bars[:N]):
        if long:
            if o<=stop: return -abs(o-entry)/risk if False else (o-entry)/risk
            if o>=target: return (o-entry)/risk
            ht=h>=target; hs=l<=stop
        else:
            if o>=stop: return (entry-o)/risk
            if o<=target: return (entry-o)/risk
            ht=l<=target; hs=h>=stop
        if hs: return -1.0
        if ht: return abs(target-entry)/risk
    if len(bars)<N: return np.nan
    return sgn*(bars[N-1][3]-entry)/risk
res=[]
for k in [1.0,1.5,2.0,3.0]:
  for m in [1.5,2.0]:
    for N in [3,5,10]:
        R=[];P=[]
        for _,a in j.iterrows():
            f=F.get(a.ticker)
            if f is None or a.horizon!='swing': continue
            p=f[f.index<a.date]
            if len(p)<30: continue
            atr=p.atr.iloc[-1]; start=a.date if a.sig_hour<6.5 else a.date+pd.Timedelta(days=1)
            fb=f[f.index>=start]; bars=list(zip(fb.Open,fb.High,fb.Low,fb.Close))
            long=a.bias=='long'; e=a.entry; d=k*atr
            if long: s,t=e-d,e+m*d
            else: s,t=e+d,e-m*d
            R.append(sim(e,s,t,long,bars,N))
            # placebo opposite
            if long: s2,t2=e+d,e-m*d
            else: s2,t2=e-d,e+m*d
            P.append(sim(e,s2,t2,not long,bars,N))
        R=np.array(R,float);P=np.array(P,float); ok=~np.isnan(R)
        res.append(dict(stopATR=k,rr=m,N=N,n=ok.sum(),meanR=R[ok].mean(),placebo=P[ok].mean(),win=(R[ok]>=m*0.999).mean(),loss=(R[ok]<=-0.999).mean()))
print('\nAlternative geometry replay (signal direction, same entries): stop=k*dailyATR, target=m*stop, hold N sessions then exit at close')
print(pd.DataFrame(res).round(3).to_string())
# time stop on journal geometry: N-session mark-to-market R (ignoring stop/target) already: retR
# mid-cap proxy: ATR% terciles hit
jj['atr_t']=pd.qcut(jj.atrpct,3,labels=['low ATR%','mid','high ATR%'])
print(jj[jj.status=='closed'].groupby('atr_t',observed=True).apply(lambda g: pd.Series(dict(n=len(g),win=(g.outcome=='win').sum(),loss=g.outcome.isin(['loss','ambiguous']).sum(),scr=(g.outcome=='scratch').sum(),atrpct=g.atrpct.median()))).round(2))
jj.to_pickle(S+'/j3.pkl')
