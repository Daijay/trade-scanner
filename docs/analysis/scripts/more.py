import sys,json,pandas as pd,numpy as np,ta
from scipy import stats
S=sys.argv[1]
pd.set_option('display.width',250); pd.set_option('display.max_columns',30)
j=pd.read_pickle(S+'/j2.pkl'); cl=pd.read_pickle(S+'/closed.pkl'); rp=pd.read_pickle(S+'/replay.pkl')
raw=pd.read_pickle(S+'/daily.pkl'); tick=set(raw.columns.get_level_values(0))
F={t:raw[t].dropna(subset=['Close']) for t in j.ticker.unique() if t in tick}
spy=raw['SPY'].dropna(subset=['Close']); vix=raw['^VIX'].dropna(subset=['Close']);qqq=raw['QQQ'].dropna(subset=['Close'])
def wilson(k,n,z=1.96):
    if n==0: return (np.nan,np.nan)
    p=k/n; d=1+z*z/n; c=p+z*z/(2*n); a=z*np.sqrt(p*(1-p)/n+z*z/(4*n*n)); return ((c-a)/d,(c+a)/d)
c=j[j.status=='closed']; w=(c.outcome=='win').sum(); l=c.outcome.isin(['loss','ambiguous']).sum()
print('overall hit Wilson CI',w,w+l,wilson(w,w+l))
for nm,mask in [('alerted conv>=6',c.conviction>=6),('conv==6',c.conviction==6)]:
    s=c[mask]; ww=(s.outcome=='win').sum(); ll=s.outcome.isin(['loss','ambiguous']).sum(); print(nm,ww,ww+ll,round(ww/(ww+ll),3),wilson(ww,ww+ll))
# fisher tests on win vs loss (excl scratch)
def fisher(a,b,name):
    wa=(a.outcome=='win').sum(); la=a.outcome.isin(['loss','ambiguous']).sum(); wb=(b.outcome=='win').sum(); lb=b.outcome.isin(['loss','ambiguous']).sum()
    print(name,f'{wa}/{wa+la}={wa/(wa+la):.3f} vs {wb}/{wb+lb}={wb/(wb+lb):.3f}  fisher p={stats.fisher_exact([[wa,la],[wb,lb]])[1]:.4f}')
fisher(c[c.scan=='premarket'],c[c.scan=='preclose'],'premarket vs preclose')
fisher(c[c.conviction>=6],c[c.conviction<6],'conv>=6 vs <6')
fisher(c[c.bias=='long'],c[c.bias=='short'],'long vs short')
fisher(c[c.conviction<=3],c[c.conviction.between(4,8)],'conv<=3 vs 4-8')
# scratch rate vs scan period
print('scratch share by period:',cl.groupby('period').apply(lambda g:(g.outcome=='scratch').mean()).round(3).to_dict())
# duplicates / repeats
j=j.sort_values('ts'); j['tb']=j.ticker+'|'+j.bias
j['prev_same']=j.groupby('tb').date.shift(1); j['gap_days']=(j.date-j.prev_same).dt.days
print('trades with same ticker+direction within previous 5 calendar days:',(j.gap_days<=5).sum(),'of',len(j),round((j.gap_days<=5).mean(),3))
print('same ticker same day (premarket+preclose):',j.duplicated(['ticker','date'],keep=False).sum())
print('opposite direction flips same ticker within 5d:', (j.groupby('ticker').bias.shift(1).notna() & (j.groupby('ticker').bias.shift(1)!=j.bias) & (j.groupby('ticker').date.diff().dt.days<=5)).sum())
cj=j[j.status=='closed']
first=cj[~(cj.gap_days<=5)]; rep=cj[cj.gap_days<=5]
for nm,s in [('first-in-5d',first),('repeat-in-5d',rep)]:
    ww=(s.outcome=='win').sum(); ll=s.outcome.isin(['loss','ambiguous']).sum(); print(nm,len(s),ww,ll,round(ww/(ww+ll),3))
print('unique tickers',j.ticker.nunique(),'unique ticker-direction',j.tb.nunique(), 'unique ticker-direction-week',(j.tb+'|'+j.date.dt.strftime('%G-%V')).nunique())
h=json.load(open(S+'/hist.json')); gone=set(h['first_seen'])-set(j.id); print('ids that existed in an earlier revision but not now (dedupe):',len(gone))
# regime
def trend(s,a,b):
    x=s[(s.index>=a)&(s.index<=b)].Close; return x.iloc[0],x.iloc[-1],(x.iloc[-1]/x.iloc[0]-1)*100
print('SPY Jul31-Oct28',trend(spy,'2026-07-31','2026-10-28'),'QQQ',trend(qqq,'2026-07-31','2026-10-28'))
print('VIX stats',vix[(vix.index>='2026-07-31')].Close.describe().round(1).to_dict())
for a,b in [('2026-07-31','2026-08-14'),('2026-08-14','2026-08-31'),('2026-09-01','2026-09-30'),('2026-10-01','2026-10-28')]:
    print(a,b,'SPY %.1f -> %.1f (%.1f%%)'%trend(spy,a,b), 'VIX mean %.1f'%vix[(vix.index>=a)&(vix.index<=b)].Close.mean())
# per-month long vs short hit
cj=cj.copy(); cj['month']=cj.date.dt.strftime('%Y-%m')
print(cj.groupby(['month','bias']).apply(lambda g: pd.Series(dict(n=len(g),win=(g.outcome=='win').sum(),loss=g.outcome.isin(['loss','ambiguous']).sum(),scr=(g.outcome=='scratch').sum()))))
# mean replay N=5 R by month and bias
r5=rp[rp.N==5].merge(j[['id','month' if 'month' in j else 'date']],on='id',suffixes=('','_j'))
r5=rp[rp.N==5].copy(); r5['month']=r5.date.dt.strftime('%Y-%m')
print(r5[r5.o!='nodata'].groupby(['month','bias']).apply(lambda g: pd.Series(dict(n=len(g),R=g.R.mean(),placeboR=g.pR.mean()))).round(3))
# correlation daily: per-date mean signed 1-day ret vs spy 1-day
# equity curve in R (journal convention) by resolution date
cl2=cl.copy(); cl2['R']=np.where(cl2.outcome=='win',cl2.rr,np.where(cl2.outcome.isin(['loss','ambiguous']),-1.0,0.0))
eq=cl2.sort_values('res_commit'); eq['cum']=eq.R.cumsum(); dd=(eq.cum-eq.cum.cummax()).min()
print('cumR total %.1f, max drawdown %.1f R, trades %d'%(eq.cum.iloc[-1],dd,len(eq)))
byres=eq.groupby(eq.res_commit.dt.strftime('%Y-%m')).R.agg(['sum','count','mean']).round(3); print(byres)
a6=eq[eq.conviction>=6]; a6=a6.assign(cum=a6.R.cumsum()); print('alerted(conv>=6) cumR %.1f maxDD %.1f n=%d'%(a6.cum.iloc[-1],(a6.cum-a6.cum.cummax()).min(),len(a6)))
print('avg R per closed trade %.3f ; R std %.3f'%(eq.R.mean(),eq.R.std()))
print('R of winners mean',eq[eq.R>0].R.mean().round(2), ' win rr dist',eq[eq.outcome=="win"].rr.describe().round(2).to_dict())
print('breakeven hit given mean win rr: %.3f'%(1/(1+eq[eq.outcome=="win"].rr.mean())))
print('\nBEST wins by rr:'); print(eq[eq.outcome=='win'].sort_values('rr',ascending=False)[['id','bias','conviction','rr','entry','target']].head(5).to_string())
# worst by replay N=5 R
w5=rp[rp.N==5].merge(j[['id','ticker','bias','entry','stop','target']],on='id').sort_values('R').head(6)[['id','R','o','bias','entry','stop']]; print('WORST by 5-session replay R:'); print(w5.to_string())
b5=rp[(rp.N==5)&(rp.o=='win')].sort_values('R',ascending=False).head(5)[['id','R','bias']]; print(b5.to_string())
