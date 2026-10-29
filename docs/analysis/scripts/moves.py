import sys,json,pandas as pd,numpy as np
S=sys.argv[1]
j=pd.read_pickle(S+'/j1.pkl')
raw=pd.read_pickle(S+'/daily.pkl'); tick=set(raw.columns.get_level_values(0))
F={t:raw[t].dropna(subset=['Close']) for t in j.ticker.unique() if t in tick}
spy=raw['SPY'].dropna(subset=['Close']); vix=raw['^VIX'].dropna(subset=['Close'])
pd.set_option('display.width',250)
L=(j.bias=='long')
j['risk']=(j.entry-j.stop).abs(); j['risk_pct']=j.risk/j.entry*100
j['atr_pct_prev']=j.atr_prev/j.entry*100
j['stop_atr']=j.risk/j.atr_prev; j['tgt_atr']=(j.target-j.entry).abs()/j.atr_prev
ok=j.stop_atr.notna()&(j.geo_ok if 'geo_ok' in j else True)
g=j[ok&(j.horizon=='swing')]
print('stop in daily ATR (swing, n=%d):'%len(g)); print(g.stop_atr.describe(percentiles=[.1,.25,.5,.75,.9]).round(2).to_dict())
print('target in ATR:',g.tgt_atr.describe(percentiles=[.1,.25,.5,.75,.9]).round(2).to_dict())
print('share stop within 1.5-2.5 ATR: %.3f ; <1.5: %.3f ; >2.5: %.3f'%(((g.stop_atr>=1.5)&(g.stop_atr<=2.5)).mean(),(g.stop_atr<1.5).mean(),(g.stop_atr>2.5).mean()))
print('risk pct:',g.risk_pct.describe().round(2).to_dict()); print('ATR% :',g.atr_pct_prev.describe().round(2).to_dict())
# hit rate by stop/ATR bucket
g=g.copy(); g['sb']=pd.cut(g.stop_atr,[0,1,1.5,2.5,4,100],labels=['<1','1-1.5','1.5-2.5','2.5-4','>4'])
exec(open(S+'/base.py').read().split("if __name__")[0].split("def blk")[1].join(["def blk",""])) if False else None
def blk2(x):
    c=x[x.status=='closed']; w=(c.outcome=='win').sum(); l=c.outcome.isin(['loss','ambiguous']).sum(); s=(c.outcome=='scratch').sum()
    return pd.Series(dict(n=len(x),closed=len(c),win=w,loss=l,scr=s,hit=w/(w+l) if w+l else np.nan,scr_rate=s/len(c) if len(c) else np.nan))
print(g.groupby('sb').apply(blk2).round(3))
# forward signed returns
rows=[]
for _,a in j.iterrows():
    f=F.get(a.ticker)
    if f is None: continue
    D=a.date; 
    start=D if a.sig_hour<6.5 else D+pd.Timedelta(days=1)
    fb=f[f.index>=start]
    sg=1 if a.bias=='long' else -1
    r=dict(id=a.id)
    for N in [1,2,3,5,10]:
        if len(fb)>=N:
            c=fb.Close.iloc[N-1]; r[f'ret{N}']=sg*(c/a.entry-1)*100; r[f'retR{N}']=sg*(c-a.entry)/a.risk if a.risk>0 else np.nan
            ss=spy[spy.index>=start]
            if len(ss)>=N:
                # spy return from prev close to Nth close
                p=spy[spy.index<start].Close.iloc[-1]; r[f'spy{N}']=(ss.Close.iloc[N-1]/p-1)*100
    if len(fb)>=1:
        o,h,l,c=fb.iloc[0][['Open','High','Low','Close']]
        # day-1 range in units of stop distance
        r['rng1_R']=(h-l)/a.risk if a.risk>0 else np.nan
        r['mfe1_R']=((h-a.entry) if sg==1 else (a.entry-l))/a.risk
        r['mae1_R']=((a.entry-l) if sg==1 else (h-a.entry))/a.risk
        r['gap_R']=sg*(o-a.entry)/a.risk
        r['day1_oc_R']=sg*(c-a.entry)/a.risk
    rows.append(r)
m=pd.DataFrame(rows); j=j.merge(m,on='id',how='left'); j.to_pickle(S+'/j2.pkl')
rng=np.random.default_rng(2)
def cl(x,dates):
    d=pd.DataFrame({'x':x,'d':dates}).dropna(); byd=d.groupby('d').x.agg(['sum','count'])
    bs=[]
    for _ in range(2000):
        s=rng.integers(0,len(byd),len(byd)); bs.append(byd['sum'].values[s].sum()/byd['count'].values[s].sum())
    return d.x.mean(),np.percentile(bs,2.5),np.percentile(bs,97.5),len(d),d.d.nunique()
print('\nsigned fwd return % (positive = in the signal direction), clustered by scan day')
for N in [1,2,3,5,10]:
    for bias in ['all','long','short']:
        s=j if bias=='all' else j[j.bias==bias]
        m_,lo,hi,n,nd=cl(s[f'ret{N}'],s.date)
        pos=(s[f'ret{N}']>0).mean()
        print(N,bias,'mean %.3f%% CI[%.3f,%.3f] n=%d days=%d  pct_pos=%.3f'%(m_,lo,hi,n,nd,pos), ' SPY mean N-day %.3f'%s[f'spy{N}'].mean())
print('\nin R units (signed move / stop distance):')
for N in [1,3,5,10]:
    m_,lo,hi,n,nd=cl(j[f'retR{N}'],j.date); print(N,'meanR %.3f CI[%.3f,%.3f] n=%d'%(m_,lo,hi,n), 'median %.3f'%j[f'retR{N}'].median())
print('\nDay-1 range / stop dist (swing):',j[j.horizon=='swing'].rng1_R.describe(percentiles=[.1,.25,.5,.75,.9]).round(2).to_dict())
print('MFE1 in R',j.mfe1_R.describe().round(2).to_dict()); print('MAE1 in R',j.mae1_R.describe().round(2).to_dict())
print('gap_R',j.gap_R.describe().round(2).to_dict()); print('pct trades with day1 MFE<0.5R & MAE<0.5R (barely moved):',((j.mfe1_R<0.5)&(j.mae1_R<0.5)).mean().round(3))
print('MFE1 >= 1R:',(j.mfe1_R>=1).mean().round(3),' MAE1>=1R:',(j.mae1_R>=1).mean().round(3), ' MFE1>=target R:',(j.mfe1_R>=j.rr).mean().round(3))
