"""Fetch the external inputs the analysis scripts read. Run from the repo root:
    python docs/analysis/scripts/00_fetch_inputs.py <scratch_dir>
Writes into <scratch_dir>: runs.json (gh run list), sectors.json (S&P 500 GICS sectors from
Wikipedia), daily.pkl (yfinance unadjusted daily bars for every journal ticker plus SPY, QQQ,
^VIX), hist.json (first git revision of journal.json in which each alert id appears closed).
Then run, in order, with the same <scratch_dir> as argv[1] and the repo root as cwd:
    base.py, daily_an.py, replay.py, moves.py, audit.py, more.py, v21.py, fix.py
(base.py is imported by the others; daily_an.py writes j1.pkl, moves.py j2.pkl, audit.py
closed.pkl, v21.py j3.pkl.) Inputs were fetched on 2026-10-28; yfinance values may differ slightly
if refetched. This consolidated fetch script was written after the fact from the commands
that were actually run and was not re-executed end to end."""
import io, json, subprocess, sys
import pandas as pd, requests, yfinance as yf
S = sys.argv[1]
j = pd.DataFrame(json.load(open('journal.json', encoding='utf-8')))
with open(S + '/runs.json', 'w') as f:
    subprocess.run(['gh', 'run', 'list', '-R', 'Daijay/trade-scanner', '-L', '1000', '--json',
                    'databaseId,createdAt,updatedAt,event,conclusion,displayTitle,headSha'], stdout=f, check=True)
r = requests.get("https://en.wikipedia.org/wiki/List_of_S%26P_500_companies", headers={"User-Agent": "Mozilla/5.0"}, timeout=20)
t = pd.read_html(io.StringIO(r.text))[0]
json.dump(dict(zip(t.Symbol.str.replace('.', '-'), t['GICS Sector'])), open(S + '/sectors.json', 'w'))
tk = sorted(j.ticker.unique())
yf.download(tk + ['SPY', 'QQQ', '^VIX'], start='2025-06-01', end='2026-10-30', interval='1d',
            auto_adjust=False, group_by='ticker', progress=False, threads=True).to_pickle(S + '/daily.pkl')
revs = subprocess.check_output(['git', 'log', '--format=%H|%cI', '--reverse', '--', 'journal.json'], text=True).strip().splitlines()
first_seen, first_closed, snap = {}, {}, []
for rv in revs:
    h, d = rv.split('|')
    try: data = json.loads(subprocess.check_output(['git', 'show', f'{h}:journal.json'], stderr=subprocess.DEVNULL))
    except Exception: continue
    for a in data:
        first_seen.setdefault(a['id'], d)
        if a['status'] == 'closed' and a['id'] not in first_closed: first_closed[a['id']] = (d, a['outcome'], a['bars_open'])
    snap.append((d, len(data)))
json.dump({'first_seen': first_seen, 'first_closed': first_closed, 'snap': snap}, open(S + '/hist.json', 'w'))
