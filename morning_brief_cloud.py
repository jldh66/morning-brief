"""Morning Brief — cloud versie (GitHub Actions).
Zelfde analyse als de lokale run_brief.py maar:
  - geen Windows-paden
  - geen TradingView draw
  - email via Python smtplib (GMAIL_USER + GMAIL_APP_PASSWORD env vars)
"""
import warnings; warnings.filterwarnings('ignore')
from zoneinfo import ZoneInfo
import sys, json, os, re, time, smtplib
from email.message import EmailMessage
from email_template import _body_to_html

# Load .env from this dir or fall back to insider-routines sibling dir
def _load_env():
    for candidate in [
        os.path.join(os.path.dirname(__file__), '.env'),
        os.path.join(os.path.dirname(__file__), '..', 'insider-routines', '.env'),
    ]:
        path = os.path.normpath(candidate)
        if os.path.exists(path):
            with open(path) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        k, _, v = line.partition('=')
                        os.environ.setdefault(k.strip(), v.strip())
            break
_load_env()
import requests
import yfinance as yf
import pandas as pd
import numpy as np

from markov_hedge_fund_method.regime import (
    label_regimes, build_transition_matrix, signal_from_matrix, STATES
)

_CONG_CACHE = '/tmp/congress_cache.json'

# Exchange filter — NASDAQ (NMS/NGM/NCM) and NYSE (NYQ/NYS) only; excludes Arca/OTC
_NASDAQ_CODES = frozenset({'NMS', 'NGM', 'NCM'})
_NYSE_CODES   = frozenset({'NYQ', 'NYS'})

def exchange_label(code):
    if not code:
        return None
    c = str(code).upper()
    if 'NASDAQ' in c or c in _NASDAQ_CODES:
        return 'NASDAQ'
    if c in _NYSE_CODES or c == 'NYSE':
        return 'NYSE'
    return None

_NL_MONTHS = ['', 'januari', 'februari', 'maart', 'april', 'mei', 'juni',
              'juli', 'augustus', 'september', 'oktober', 'november', 'december']

# ══════════════════════════════════════════════════════════════════════════════
# TABEL BUILDERS
# ══════════════════════════════════════════════════════════════════════════════

def _cell(val, w, align):
    s = str(val)
    if align == 'r': return ' ' + s.rjust(w)  + ' '
    if align == 'c': return ' ' + s.center(w) + ' '
    return ' ' + s.ljust(w) + ' '

def table_section(title, cols, data_rows, key_reads, summary):
    n     = len(cols)
    inner = sum(w + 2 for _, w, _ in cols) + (n - 1)
    pad  = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in cols) + r
    rdiv =                  '╟' + '╫'.join('─' * (w + 2) for _, w, _ in cols) + '╢'
    hrow = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(cols, vals)) + '║'
    lines = [
        '╔' + '═' * inner + '╗',
        pad(f'  {title}'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in cols),
        hdiv('╠', '╬', '╣'),
    ]
    for i, row in enumerate(data_rows):
        lines.append(hrow(row))
        if i < len(data_rows) - 1:
            lines.append(rdiv)
    lines += [
        hdiv('╠', '╩', '╣'),
        pad('  KEY READS:'),
        *[pad(f'    {kr}') for kr in key_reads],
        '╠' + '═' * inner + '╣',
        pad(f'  SAMENVATTING:  {summary}'),
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines)

def header_box(lines_in, inner):
    top = '╔' + '═' * inner + '╗'
    bot = '╚' + '═' * inner + '╝'
    rows = ['║ ' + l.ljust(inner - 1) + '║' for l in lines_in]
    return '\n'.join([top] + rows + [bot])

# ══════════════════════════════════════════════════════════════════════════════
# MARKOV ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def bias_tag(sig):
    if sig > 0.05:  return 'LONG'
    if sig < -0.05: return 'SHORT'
    return 'NEUTRAL'

def chg(v): return f'{v:+.2f}%'

def run_markov(t, s, e):
    df = yf.download(t, start=s, end=e, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close = df['Close'].dropna()
    if len(close) < 20:
        raise ValueError(f'Te weinig data voor {t}')
    labels = label_regimes(close, window=5, threshold=0.02)
    P      = build_transition_matrix(labels)
    cur    = int(labels.iloc[-1])
    sig    = float(signal_from_matrix(P, cur))
    try:
        pre = getattr(yf.Ticker(t).fast_info, 'pre_market_price', None)
    except Exception:
        pre = None
    return {
        't':      t,
        'last':   float(close.iloc[-1]),
        'pct1':   float(close.pct_change(1).iloc[-1] * 100),
        'pct5':   float(close.pct_change(5).iloc[-1] * 100),
        'state':  STATES[cur],
        'p_bull': float(P[cur, 2] * 100),
        'p_bear': float(P[cur, 0] * 100),
        'sig':    sig,
        'bias':   bias_tag(sig),
        'pre':    pre,
        'close':  close,
    }

def key_read(d, name='', extra=''):
    tag = '▲ LONG' if d['sig'] > 0.05 else ('▼ SHORT' if d['sig'] < -0.05 else '◆ NEUTRAAL')
    conflict = ''
    if d['sig'] > 0.05 and d['pct1'] < 0:
        conflict = '  ⚠ Bull regime maar rood vandaag'
    elif d['sig'] < -0.05 and d['pct1'] > 0:
        conflict = '  ⚠ Model fadet de stijging'
    elif abs(d['sig']) <= 0.05:
        conflict = '  → 5d boven +2% = flip naar Bull'
    name_part = f' ({name})' if name else ''
    return (f'{d["t"]}{name_part}  {tag}  {d["sig"]:+.3f}  |  {d["state"]}  |'
            f'  1d {chg(d["pct1"])}  5d {chg(d["pct5"])}{extra}{conflict}')

# ══════════════════════════════════════════════════════════════════════════════
# MACRO INDICES
# ══════════════════════════════════════════════════════════════════════════════

_MACRO_COLS = [
    ('INDEX',       5,  'l'),
    ('LAST',        12, 'r'),
    ('1d CHG',      7,  'r'),
    ('5d CHG',      7,  'r'),
    ('REGIME',      8,  'l'),
    ('SESSIE BIAS', 44, 'l'),
]

def fetch_macro(s_str, e_str):
    out = {}
    for sym, name in [('^NDX', 'US100'), ('^DJI', 'US30')]:
        try:
            d = run_markov(sym, s_str, e_str)
            d['name'] = name
            out[name] = d
        except Exception as ex:
            out[name] = {'name': name, 'error': str(ex)}
    return out

def macro_section(macro):
    cols  = _MACRO_COLS
    n     = len(cols)
    inner = sum(w + 2 for _, w, _ in cols) + (n - 1)
    pad  = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in cols) + r
    rdiv =                  '╟' + '╫'.join('─' * (w + 2) for _, w, _ in cols) + '╢'
    hrow = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(cols, vals)) + '║'

    ndx = macro.get('US100', {})
    dji = macro.get('US30',  {})

    rows = []
    for d, label in [(ndx, 'Tech/Groei'), (dji, 'Value/Cyclisch')]:
        if 'sig' not in d:
            rows.append([d.get('name','?'), 'ERROR', '-', '-', '-', d.get('error','')[:44]])
        else:
            sessie = f'{d["bias"]:<7}  |  {label} [{d["name"]}]'
            rows.append([d['name'], f'{d["last"]:,.2f}', chg(d['pct1']), chg(d['pct5']),
                         d['state'], sessie])

    div_reads = []
    if 'sig' in ndx and 'sig' in dji:
        diff = ndx['pct1'] - dji['pct1']
        sign = '+' if diff >= 0 else ''
        if abs(diff) > 2.0:
            div_label = '!! STERKE DIVERGENTIE'
            interp = ('Tech/Groei dominant — roteer naar groeiaandelen'
                      if diff > 0 else 'Value/Cyclisch dominant — roteer naar waardeaandelen')
        elif abs(diff) > 0.8:
            div_label = '> DIVERGENTIE'
            interp = ('US100 outperformt — lichte voorkeur voor tech'
                      if diff > 0 else 'US30 outperformt — lichte voorkeur voor waarde')
        else:
            div_label = 'GEEN DIVERGENTIE'
            interp = 'Brede marktbeweging — geen sector-rotatie signaal'

        div_reads.append(f'DIVERGENTIE  US100 vs US30: {sign}{diff:.2f}%  [{div_label}]')
        div_reads.append(f'  {interp}')

        if ndx['bias'] != dji['bias']:
            div_reads.append(
                f'  Signaalconflict: US100={ndx["bias"]} | US30={dji["bias"]} — handel met verhoogde voorzichtigheid')
        if ndx['state'] != dji['state']:
            div_reads.append(
                f'  Regime-conflict: US100={ndx["state"]} | US30={dji["state"]}')
    else:
        div_reads.append('Onvoldoende data voor divergentie analyse')

    lines = [
        '╔' + '═' * inner + '╗',
        pad('  MACRO CONTEXT  —  US100 (Nasdaq-100)  vs  US30 (Dow Jones)'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in cols),
        hdiv('╠', '╬', '╣'),
    ]
    for i, row in enumerate(rows):
        lines.append(hrow(row))
        if i < len(rows) - 1:
            lines.append(rdiv)
    lines += [
        hdiv('╠', '╩', '╣'),
        *[pad(f'  {r}') for r in div_reads],
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# VIX ANALYSE
# ══════════════════════════════════════════════════════════════════════════════

_VIX_COLS = [
    ('TICKER',       6,  'l'),
    ('VIX BETA',     8,  'r'),
    ('GEVOELIGHEID', 12, 'l'),
    ('IMPACT',       63, 'l'),
]

def fetch_vix(s_str, e_str):
    df = yf.download('^VIX', start=s_str, end=e_str, progress=False, auto_adjust=True)
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)
    close = df['Close'].dropna()
    level = float(close.iloc[-1])
    pct1  = float(close.pct_change(1).iloc[-1] * 100)
    pct5  = float(close.pct_change(5).iloc[-1] * 100)
    if level < 15:
        label, alert = 'LAAG', ''
    elif level < 20:
        label, alert = 'NORMAAL', ''
    elif level < 25:
        label, alert = 'WAARSCHUWING', '*** GEEL ALARM ***'
    else:
        label, alert = 'GEVAAR', '!!! ROOD ALARM !!!'
    return {'level': level, 'pct1': pct1, 'pct5': pct5,
            'label': label, 'alert': alert, 'close': close}

def calc_vix_beta(stock_close, vix_close):
    common = vix_close.index.intersection(stock_close.index)
    vr = vix_close.loc[common].pct_change().dropna()
    sr = stock_close.loc[common].pct_change().dropna()
    c2 = vr.index.intersection(sr.index)
    x, y = vr.loc[c2].values, sr.loc[c2].values
    cov = np.cov(x, y)
    return float(cov[0, 1] / cov[0, 0])

def sensitivity_label(beta):
    if beta < -1.5: return 'Zeer hoog'
    if beta < -0.8: return 'Hoog'
    if beta < -0.3: return 'Matig'
    if beta < 0:    return 'Laag'
    return 'Positief'

def impact_desc(beta, vix_label):
    high = vix_label in ('WAARSCHUWING', 'GEVAAR')
    if beta < -1.5: return 'Pas bias aan — sterk negatief gecorreleerd met VIX' if high else 'Kwetsbaar bij VIX spike'
    if beta < -0.8: return 'Verhoogd risico — overweeg positie te verkleinen'   if high else 'Matig gevoelig voor VIX'
    if beta < -0.3: return 'Lichte demping bij verdere VIX stijging'            if high else 'Beperkte VIX impact'
    if beta < 0:    return 'Geringe gevoeligheid — normaal handelen'
    return 'Defensief karakter — kan stijgen bij VIX piek'

def vix_section(vix, wl_data):
    cols  = _VIX_COLS
    n     = len(cols)
    inner = sum(w + 2 for _, w, _ in cols) + (n - 1)
    pad  = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in cols) + r
    rdiv =                  '╟' + '╫'.join('─' * (w + 2) for _, w, _ in cols) + '╢'
    hrow = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(cols, vals)) + '║'

    ap = f'  {vix["alert"]}  |  ' if vix['alert'] else '  '
    lines = [
        '╔' + '═' * inner + '╗',
        pad(f'  VIX ANALYSE  —{ap}VIX {vix["level"]:.2f}  ({vix["label"]})'),
        pad(f'  1d: {chg(vix["pct1"])}  |  5d: {chg(vix["pct5"])}'),
        pad('  Schaal:  LAAG (<15)  |  NORMAAL (15-20)  |  WAARSCHUWING (20-25)  |  GEVAAR (>25)'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in cols),
        hdiv('╠', '╬', '╣'),
    ]
    valid = [d for d in wl_data if 'close' in d]
    for i, d in enumerate(valid):
        beta = calc_vix_beta(d['close'], vix['close'])
        lines.append(hrow([d['t'], f'{beta:+.2f}', sensitivity_label(beta),
                            impact_desc(beta, vix['label'])]))
        if i < len(valid) - 1:
            lines.append(rdiv)
    lines.append('╚' + '═' * inner + '╝')
    return '\n'.join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# SMART MONEY
# ══════════════════════════════════════════════════════════════════════════════

def load_congress_data():
    if os.path.exists(_CONG_CACHE):
        if time.time() - os.path.getmtime(_CONG_CACHE) < 86400:
            try:
                with open(_CONG_CACHE, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
    data = []
    for url in [
        'https://senate-stock-watcher-data.s3-us-gov-west-1.amazonaws.com/aggregate/all_transactions.json',
        'https://house-stock-watcher-data.s3-us-gov-west-1.amazonaws.com/data/all_transactions.json',
    ]:
        try:
            r = requests.get(url, timeout=20)
            chunk = r.json()
            if isinstance(chunk, list):
                data.extend(chunk)
        except Exception:
            pass
    if data:
        try:
            with open(_CONG_CACHE, 'w', encoding='utf-8') as f:
                json.dump(data, f)
        except Exception:
            pass
    return data

def congress_for(sym, data, days=60):
    cutoff = (pd.Timestamp.now() - pd.DateOffset(days=days)).date()
    out = []
    for t in data:
        tk = (t.get('ticker') or t.get('symbol') or '').strip().upper()
        if tk != sym.upper():
            continue
        ds = t.get('transaction_date') or t.get('transactionDate') or t.get('date') or ''
        try:
            if pd.to_datetime(ds).date() < cutoff:
                continue
        except Exception:
            continue
        name = t.get('representative') or t.get('senator') or t.get('name') or '?'
        act  = (t.get('type') or t.get('transaction_type') or '').lower()
        out.append({
            'name':   name,
            'action': 'BUY' if ('purchase' in act or 'buy' in act) else 'SELL',
            'date':   str(ds)[:10],
        })
    return out

def insider_summary(sym):
    try:
        txns = yf.Ticker(sym).insider_transactions
        if txns is None or txns.empty:
            return None
        txns = txns.copy()
        if not pd.api.types.is_datetime64_any_dtype(txns.index):
            txns.index = pd.to_datetime(txns.index, errors='coerce')
        if hasattr(txns.index, 'tz') and txns.index.tz:
            txns.index = txns.index.tz_localize(None)
        cutoff = pd.Timestamp.now() - pd.DateOffset(days=90)
        recent = txns[txns.index >= cutoff]
        if recent.empty:
            recent = txns.head(10)
        best_name, best_action, best_shares, net = 'N/A', '', 0, 0
        for _, row in recent.iterrows():
            act    = str(row.get('Transaction', '') or '').lower()
            shares = abs(int(row.get('Shares', 0) or 0))
            name   = str(row.get('Insider Trading', '') or row.get('Name', ''))
            if any(w in act for w in ('buy', 'purchase', 'acqui')):
                net += shares
                if shares > best_shares:
                    best_shares, best_name, best_action = shares, name, 'BUY'
            elif any(w in act for w in ('sell', 'sale', 'dispos')):
                net -= shares
                if shares > best_shares and best_name == 'N/A':
                    best_shares, best_name, best_action = shares, name, 'SELL'
        return {
            'direction':    1 if net > 0 else (-1 if net < 0 else 0),
            'best_name':    best_name[:14],
            'best_action':  best_action,
            'best_shares':  best_shares,
        }
    except Exception:
        return None

def institutional_summary(sym):
    try:
        tk    = yf.Ticker(sym)
        inst  = tk.institutional_holders
        major = tk.major_holders
        top_name, top_pct, total_pct = 'N/A', 0.0, 0.0
        if inst is not None and not inst.empty:
            row      = inst.iloc[0]
            top_name = str(row.get('Holder', row.get('holder', 'N/A')))[:14]
            top_pct  = float(row.get('% Out', row.get('pctHeld', 0)) or 0)
            if top_pct < 1:
                top_pct *= 100
        if major is not None and not major.empty:
            for _, row in major.iterrows():
                desc = str(row.iloc[1]).lower() if len(row) > 1 else ''
                if 'institution' in desc:
                    try:
                        total_pct = float(str(row.iloc[0]).replace('%', ''))
                    except Exception:
                        pass
        return {'top_name': top_name, 'top_pct': top_pct, 'total_pct': total_pct}
    except Exception:
        return None

def calc_confidence(d, insider, cong_list):
    sig   = d.get('sig', 0)
    score = min(abs(sig) * 35, 35)
    vr    = d.get('vol_ratio', 1)
    score += 15 if vr > 3 else (10 if vr > 2 else (5 if vr > 1.5 else 0))
    pm    = d.get('pm_chg') or 0
    if (pm > 0 and sig > 0) or (pm < 0 and sig < 0):
        score += 15
    if insider:
        ins_dir = insider.get('direction', 0)
        if ins_dir != 0:
            if (ins_dir > 0 and sig > 0) or (ins_dir < 0 and sig < 0):
                score += 20
            else:
                score -= 10
    if cong_list:
        buys  = sum(1 for t in cong_list if t['action'] == 'BUY')
        sells = sum(1 for t in cong_list if t['action'] == 'SELL')
        if (buys > sells and sig > 0) or (sells > buys and sig < 0):
            score += 15
    opts = d.get('options', '')
    if ('CALL' in opts and sig > 0) or ('PUT' in opts and sig < 0):
        score += 10
    elif opts not in ('normaal', 'geen data', 'fout', ''):
        score -= 5
    return max(0, min(100, int(score)))

def calc_ease(d, vix_beta=None):
    score   = 5
    abs_sig = abs(d.get('sig', 0))
    if abs_sig > 0.65:   score += 2
    elif abs_sig > 0.45: score += 1
    elif abs_sig < 0.15: score -= 1
    state = d.get('state', '')
    if state in ('Bull', 'Bear'):  score += 1
    elif state == 'Sideways':      score -= 1
    pm  = d.get('pm_chg') or 0
    sig = d.get('sig', 0)
    if abs(pm) > 3 and ((pm > 0) == (sig > 0)):
        score += 1
    if vix_beta is not None:
        if abs(vix_beta) < 0.3:   score += 1
        elif abs(vix_beta) > 1.2: score -= 1
    opts = d.get('options', '')
    if opts not in ('normaal', 'geen data', 'fout', ''):
        score += 1 if (('CALL' in opts and sig > 0) or ('PUT' in opts and sig < 0)) else -1
    return max(1, min(10, score))

def calc_trade_levels(m):
    if m.get('bias') not in ('LONG', 'SHORT'):
        return None
    entry     = float(m.get('pm_price') or m['last'])
    close     = m['close']
    std14     = float(close.pct_change().dropna().tail(14).std())
    atr_pct   = std14 * 1.5 * 100
    if m['bias'] == 'LONG':
        sl     = entry * (1 - atr_pct / 100)
        target = entry + 2 * (entry - sl)
    else:
        sl     = entry * (1 + atr_pct / 100)
        target = entry - 2 * (sl - entry)
    risk_pct = abs(entry - sl) / entry * 100
    rr_pct   = abs(target - entry) / entry * 100
    return {
        'sym':      m['t'],
        'side':     m['bias'],
        'entry':    round(entry, 2),
        'sl':       round(sl,    2),
        'target':   round(target, 2),
        'risk_pct': round(risk_pct, 1),
        'rr_pct':   round(rr_pct,  1),
    }

def tv_setup_section(setup):
    inner = 100
    pad   = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    sym    = setup['sym']
    side   = setup['side']
    entry  = setup['entry']
    sl     = setup['sl']
    target = setup['target']
    rp     = setup['risk_pct']
    rr     = setup['rr_pct']
    sl_sign  = '-' if sl     < entry else '+'
    tgt_sign = '+' if target > entry else '-'
    lines = [
        '╔' + '═' * inner + '╗',
        pad(f'  TRADE SETUP  —  {sym}  |  {side}  |  1:2 risico/beloning'),
        pad(f'  ENTRY:   ${entry:>10,.2f}   (referentieprijs)'),
        pad(f'  STOP:    ${sl:>10,.2f}   ({sl_sign}{rp:.1f}%)  ←  1.5× dagelijkse volatiliteit'),
        pad(f'  TARGET:  ${target:>10,.2f}   ({tgt_sign}{rr:.1f}%)  →  2× risico (1:2 R:R)'),
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines)

def trade_setup_section(movers, vix, congress_data):
    TS_COLS = [
        ('TICKER',   6,  'l'),
        ('CONF%',    5,  'r'),
        ('GEMAK',    5,  'r'),
        ('SETUP',    5,  'l'),
        ('INSIDER',  18, 'l'),
        ('CONGRESS', 18, 'l'),
        ('INST',     23, 'l'),
    ]
    n     = len(TS_COLS)
    inner = sum(w + 2 for _, w, _ in TS_COLS) + (n - 1)
    pad  = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in TS_COLS) + r
    rdiv =                  '╟' + '╫'.join('─' * (w + 2) for _, w, _ in TS_COLS) + '╢'
    hrow = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(TS_COLS, vals)) + '║'

    lines = [
        '╔' + '═' * inner + '╗',
        pad('  BESTE TRADE SETUP VAN DE DAG  —  confidence + gemak + smart money'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in TS_COLS),
        hdiv('╠', '╬', '╣'),
    ]

    valid = [m for m in movers if 'sig' in m]
    rows  = []
    for m in valid:
        sym = m['t']
        ins  = insider_summary(sym)
        cong = congress_for(sym, congress_data)
        inst = institutional_summary(sym)
        vb   = calc_vix_beta(m['close'], vix['close']) if 'close' in m else None

        conf = calc_confidence(m, ins, cong)
        ease = calc_ease(m, vb)

        if ins and ins['best_name'] != 'N/A' and ins['best_action']:
            k = ins['best_shares'] // 1000
            ins_str = f'{ins["best_name"][:10]} {ins["best_action"]} {k}k'[:18]
        else:
            ins_str = 'N/A'

        if cong:
            t    = cong[0]
            last = t['name'].split()[-1][:10]
            try:
                dago = (pd.Timestamp.now() - pd.to_datetime(t['date'])).days
                dag  = f'{dago}d'
            except Exception:
                dag = ''
            cong_str = f'{last} {t["action"]} {dag}'[:18]
        else:
            cong_str = 'Geen data'

        if inst and inst['top_name'] != 'N/A':
            tot_s = f' | {inst["total_pct"]:.0f}%ins' if inst['total_pct'] else ''
            inst_str = f'{inst["top_name"][:12]} {inst["top_pct"]:.1f}%{tot_s}'[:23]
        else:
            inst_str = 'N/A'

        rows.append({'sym': sym, 'conf': conf, 'ease': ease,
                     'bias': m['bias'], 'ins': ins_str,
                     'cong': cong_str, 'inst': inst_str})

    rows.sort(key=lambda x: x['conf'], reverse=True)

    for i, r in enumerate(rows):
        lines.append(hrow([r['sym'], f'{r["conf"]}%', f'{r["ease"]}/10',
                           r['bias'][:5], r['ins'], r['cong'], r['inst']]))
        if i < len(rows) - 1:
            lines.append(rdiv)

    lines.append(hdiv('╠', '╩', '╣'))
    if rows:
        top = rows[0]
        lines.append(pad(f'  TOP PICK:  {top["sym"]}  —  {top["conf"]}% confidence'
                         f'  |  Gemak {top["ease"]}/10  |  {top["bias"]}'))
        runners = [r for r in rows[1:4] if r['conf'] >= 45]
        if runners:
            lines.append(pad('  Overige kansen:  ' +
                             '  |  '.join(f'{r["sym"]} {r["conf"]}%' for r in runners)))
    else:
        lines.append(pad('  Geen setups beschikbaar'))
    lines += [
        '╠' + '═' * inner + '╣',
        pad('  Congress/insider data is kwalitatief signaal — geen beleggingsadvies.'),
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines), rows

# ══════════════════════════════════════════════════════════════════════════════
# PRE-MARKT DYNAMISCHE MOVERS
# ══════════════════════════════════════════════════════════════════════════════

_PM_COLS = [
    ('TICKER',  6,  'l'),
    ('PRE%',    7,  'r'),
    ('PRE$',    8,  'r'),
    ('VOL',     5,  'r'),
    ('REGIME',  8,  'l'),
    ('SIGNAL',  7,  'r'),
    ('BIAS',    7,  'l'),
    ('OPTIONS', 29, 'l'),
]

def get_unusual_options(sym):
    try:
        tk   = yf.Ticker(sym)
        exps = tk.options
        if not exps:
            return 'geen data'
        chain = tk.option_chain(exps[0])
        flags = []
        for side, df in [('CALL', chain.calls), ('PUT', chain.puts)]:
            if df.empty:
                continue
            df = df.copy()
            df['voi'] = df['volume'] / (df['openInterest'].replace(0, 1))
            top = df.nlargest(1, 'volume')
            if top.empty:
                continue
            r = top.iloc[0]
            vol = int(r.get('volume', 0) or 0)
            oi  = int(r.get('openInterest', 0) or 0)
            voi = float(r['voi'])
            strike = r.get('strike', 0)
            if vol > 200 and voi > 1.5:
                flags.append(f'{side} {strike:.0f}  {vol}v/{oi}oi  ({voi:.1f}x)')
        return '  |  '.join(flags) if flags else 'normaal'
    except Exception:
        return 'geen data'

def fetch_premarket_movers(s_str, e_str, limit=10):
    now  = time.time()
    hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}

    all_quotes = {}
    for scr in ['most_actives_change_up_premarket', 'most_actives', 'day_gainers', 'day_losers']:
        try:
            r = requests.get(
                f'https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved'
                f'?formatted=false&scrIds={scr}&count=30&region=US&lang=en-US',
                headers=hdrs, timeout=10)
            for q in r.json()['finance']['result'][0]['quotes']:
                sym = q.get('symbol', '')
                if sym and sym not in all_quotes:
                    all_quotes[sym] = q
        except Exception:
            pass

    candidates = []
    for sym, q in all_quotes.items():
        pm_chg   = q.get('preMarketChangePercent') or q.get('regularMarketChangePercent', 0)
        pm_price = q.get('preMarketPrice')
        vol = q.get('regularMarketVolume', 0)
        avg = max(q.get('averageDailyVolume3Month', 1), 1)
        vr  = vol / avg
        candidates.append({
            'sym':        sym,
            'short_name': q.get('shortName', sym)[:20],
            'pm_chg':     float(pm_chg or 0),
            'pm_price':   float(pm_price) if pm_price else None,
            'vol_ratio':  vr,
            'score':      abs(float(pm_chg or 0)) * vr,
            '_news_items': [],
            'exchange':   q.get('exchange', ''),
        })

    candidates.sort(key=lambda x: x['score'], reverse=True)

    for c in candidates[:20]:
        try:
            items = yf.Ticker(c['sym']).news or []
            c['_news_items'] = items
            if any((now - n.get('providerPublishTime', 0)) < 86400 for n in items):
                c['score'] *= 1.3
        except Exception:
            pass

    candidates.sort(key=lambda x: x['score'], reverse=True)

    results = []
    for c in candidates[:limit]:
        sym = c['sym']
        try:
            md = run_markov(sym, s_str, e_str)
            pm_price = c['pm_price'] or md.get('pre')
            pm_chg   = c['pm_chg']
            if pm_price is None and md.get('pre'):
                pm_price = md['pre']
                pm_chg   = (pm_price / md['last'] - 1) * 100
            md['pm_price']   = pm_price
            md['pm_chg']     = pm_chg
            md['vol_ratio']  = c['vol_ratio']
            md['short_name'] = c['short_name']
            raw = c.get('_news_items', [])
            md['news']    = [(n.get('title','')[:70], n.get('publisher','')[:14])
                             for n in raw[:1]]
            md['options']  = get_unusual_options(sym)
            md['exchange'] = c.get('exchange', '')
            results.append(md)
        except Exception as ex:
            c['error']   = str(ex)[:50]
            c['options'] = 'fout'
            c['news']    = []
            results.append(c)
    return results

def premarket_section(movers):
    cols  = _PM_COLS
    n     = len(cols)
    inner = sum(w + 2 for _, w, _ in cols) + (n - 1)
    pad  = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in cols) + r
    rdiv =                  '╟' + '╫'.join('─' * (w + 2) for _, w, _ in cols) + '╢'
    hrow = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(cols, vals)) + '║'

    lines = [
        '╔' + '═' * inner + '╗',
        pad('  PRE-MARKT DYNAMISCHE MOVERS  ( score = |pre%| x volume-ratio )'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in cols),
        hdiv('╠', '╬', '╣'),
    ]

    for i, m in enumerate(movers):
        if 'sig' not in m:
            row = [m.get('sym','?'), 'ERROR', '-', '-', '-', '-', '-',
                   m.get('error','fout')[:29]]
        else:
            pm_chg_str   = chg(m['pm_chg']) if m.get('pm_chg') is not None else chg(m['pct1'])
            pm_price_str = (f'${m["pm_price"]:,.2f}' if m.get('pm_price')
                            else f'${m["last"]:,.2f}')
            opts = (m.get('options') or 'normaal')[:29]
            row = [
                m['t'],
                pm_chg_str,
                pm_price_str,
                f'{m["vol_ratio"]:.1f}x',
                m['state'],
                f'{m["sig"]:+.3f}',
                m['bias'],
                opts,
            ]
        lines.append(hrow(row))
        if i < len(movers) - 1:
            lines.append(rdiv)

    lines.append(hdiv('╠', '╩', '╣'))
    lines.append(pad('  NIEUWS:'))
    had_news = False
    for m in movers:
        for title, pub in m.get('news', []):
            ticker = m.get('t', m.get('sym', '?'))
            line   = f'    {ticker:<6}  {title[:70]}  [{pub}]'
            lines.append(pad(line[:inner - 1]))
            had_news = True
    if not had_news:
        lines.append(pad('    Geen recente nieuwskoppen beschikbaar'))

    valid  = [m for m in movers if 'sig' in m]
    longs  = sum(1 for m in valid if m['sig'] > 0.05)
    shorts = sum(1 for m in valid if m['sig'] < -0.05)
    neuts  = len(valid) - longs - shorts
    lines += [
        '╠' + '═' * inner + '╣',
        pad(f'  SAMENVATTING:  {longs} Longs  |  {neuts} Neutraal  |  {shorts} Shorts'
            f'  |  {len(valid)} movers'),
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines)

# ══════════════════════════════════════════════════════════════════════════════
# TOP-5
# ══════════════════════════════════════════════════════════════════════════════

_BASE = [
    ('TICKER',  6, 'l'),
    ('LAST',    9, 'r'),
    ('1d CHG',  7, 'r'),
    ('5d CHG',  7, 'r'),
    ('REGIME',  8, 'l'),
    ('P(BULL)', 6, 'r'),
    ('P(BEAR)', 6, 'r'),
    ('SIGNAL',  7, 'r'),
    ('BIAS',    7, 'l'),
]
T5_COLS = _BASE + [('VOL', 8, 'r')]

def t5_row(d, vol_ratio):
    return [d['t'], f'${d["last"]:,.2f}', chg(d['pct1']), chg(d['pct5']),
            d['state'], f'{d["p_bull"]:.1f}%', f'{d["p_bear"]:.1f}%',
            f'{d["sig"]:+.3f}', d['bias'], vol_ratio]

# ══════════════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════════════

# ══════════════════════════════════════════════════════════════════════════════
# INSIDER SIGNALEN WATCHLIST — TRADE SETUPS (NASDAQ / NYSE)
# Kolommen (10): TICKER=6 BEURS=8 BIAS=5 SIGNAL=7 CONF%=5
#                ENTRY$=10 SL$=10 TARGET$=10 RISK%=5 R:R=5
# inner = (6+2)+(8+2)+(5+2)+(7+2)+(5+2)+(10+2)+(10+2)+(10+2)+(5+2)+(5+2) + 9 = 100 ✓
# ══════════════════════════════════════════════════════════════════════════════

_IS_COLS = [
    ('TICKER',  6,  'l'),
    ('BEURS',   8,  'l'),
    ('BIAS',    5,  'l'),
    ('SIGNAL',  7,  'r'),
    ('CONF%',   5,  'r'),
    ('ENTRY $', 10, 'r'),
    ('SL $',    10, 'r'),
    ('TARGET$', 10, 'r'),
    ('RISK %',  5,  'r'),
    ('R:R',     5,  'r'),
]

def insider_trade_setups_section(all_movers, congress_data, vix):
    """Trade setups voor alle NASDAQ/NYSE movers met LONG/SHORT bias, min 2:1 R:R."""
    cols  = _IS_COLS
    n     = len(cols)
    inner = sum(w + 2 for _, w, _ in cols) + (n - 1)
    pad  = lambda t: '║ ' + t.ljust(inner - 1) + '║'
    hdiv = lambda l, m, r: l + m.join('═' * (w + 2) for _, w, _ in cols) + r
    rdiv =                  '╟' + '╫'.join('─' * (w + 2) for _, w, _ in cols) + '╢'
    hrow = lambda vals: '║' + '║'.join(_cell(v, w, a) for (_, w, a), v in zip(cols, vals)) + '║'

    # Deduplicate by ticker, keep first occurrence
    seen = set()
    candidates = []
    for m in all_movers:
        sym = m.get('t') or m.get('sym', '')
        if not sym or sym in seen:
            continue
        seen.add(sym)
        # NASDAQ / NYSE filter
        exch = exchange_label(m.get('exchange', ''))
        if not exch:
            continue
        # Actionable bias only
        if m.get('bias') not in ('LONG', 'SHORT'):
            continue
        if 'sig' not in m or 'close' not in m:
            continue
        candidates.append((m, exch))

    rows = []
    for m, exch in candidates:
        sym  = m['t']
        ins  = insider_summary(sym)
        cong = congress_for(sym, congress_data)
        vb   = calc_vix_beta(m['close'], vix['close'])
        conf = calc_confidence(m, ins, cong)
        lvl  = calc_trade_levels(m)
        if not lvl:
            continue

        entry  = lvl['entry']
        sl     = lvl['sl']
        target = lvl['target']
        risk   = lvl['risk_pct']
        rr     = round(abs(target - entry) / max(abs(entry - sl), 0.0001), 1)
        if rr < 2.0:
            continue  # skip if somehow below 2:1

        # Smart-money label for risk line
        smart = []
        if ins and ins['best_action']:
            smart.append(f'INS:{ins["best_action"]}')
        if cong:
            smart.append(f'CONG:{cong[0]["action"]}')
        smart_str = ' '.join(smart) if smart else '—'

        rows.append({
            'sym':    sym,
            'exch':   exch,
            'bias':   m['bias'],
            'sig':    m['sig'],
            'conf':   conf,
            'entry':  entry,
            'sl':     sl,
            'target': target,
            'risk':   risk,
            'rr':     rr,
            'smart':  smart_str,
        })

    rows.sort(key=lambda x: x['conf'], reverse=True)

    lines = [
        '╔' + '═' * inner + '╗',
        pad('  INSIDER SIGNALEN — TRADE SETUPS  (NASDAQ / NYSE)  |  min. 2:1 R:R  |  1.5× ATR stop'),
        hdiv('╠', '╦', '╣'),
        hrow(name.center(w) for name, w, _ in cols),
        hdiv('╠', '╬', '╣'),
    ]

    if not rows:
        lines += [
            pad('  Geen kwalificerende NASDAQ/NYSE setups vandaag (min. 2:1 R:R)'),
            '╚' + '═' * inner + '╝',
        ]
        return '\n'.join(lines)

    for i, r in enumerate(rows):
        sl_sign  = '-' if r['sl']     < r['entry'] else '+'
        tgt_sign = '+' if r['target'] > r['entry'] else '-'
        lines.append(hrow([
            r['sym'],
            r['exch'],
            r['bias'],
            f'{r["sig"]:+.3f}',
            f'{r["conf"]}%',
            f'${r["entry"]:,.2f}',
            f'${r["sl"]:,.2f}',
            f'${r["target"]:,.2f}',
            f'{sl_sign}{r["risk"]:.1f}%',
            f'{r["rr"]:.1f}:1',
        ]))
        if i < len(rows) - 1:
            lines.append(rdiv)

    lines.append(hdiv('╠', '╩', '╣'))

    # Key reads per row
    lines.append(pad('  SETUPS:'))
    for r in rows:
        sl_sign  = '-' if r['sl']     < r['entry'] else '+'
        tgt_sign = '+' if r['target'] > r['entry'] else '-'
        kr = (f'    {r["sym"]:<6}  {r["bias"]:<5}  {r["sig"]:+.3f}  '
              f'entry ${r["entry"]:,.2f}  SL ${r["sl"]:,.2f} ({sl_sign}{r["risk"]:.1f}%)  '
              f'target ${r["target"]:,.2f}  R:R {r["rr"]:.1f}:1  |  {r["smart"]}')
        lines.append(pad(kr[:inner - 1]))

    top = rows[0]
    lines += [
        '╠' + '═' * inner + '╣',
        pad(f'  TOP SETUP:  {top["sym"]}  [{top["exch"]}]  —  {top["conf"]}% confidence'
            f'  |  {top["bias"]}  |  entry ${top["entry"]:,.2f}'
            f'  →  target ${top["target"]:,.2f}  (R:R {top["rr"]:.1f}:1)'),
        pad('  Entry = pre-markt prijs indien beschikbaar, anders slotkoers gisteren.'),
        pad('  Stop = 1.5× 14-daagse ATR. Target = 2× risico (gegarandeerd ≥ 2:1 R:R).'),
        pad('  Geen beleggingsadvies. Gebruik als startpunt voor eigen analyse.'),
        '╚' + '═' * inner + '╝',
    ]
    return '\n'.join(lines)


def send_email(subject, body):
    body = re.sub(r'\033\[[0-9;]*m', '', body)
    user     = os.environ['GMAIL_USER']
    password = os.environ['GMAIL_APP_PASSWORD']
    to       = os.environ.get('GMAIL_TO') or user
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From']    = user
    msg['To']      = to
    msg.set_content(body)
    msg.add_alternative(_body_to_html(subject, body), subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as smtp:
        smtp.login(user, password)
        smtp.send_message(msg)

# ══════════════════════════════════════════════════════════════════════════════
# MAIN
# ══════════════════════════════════════════════════════════════════════════════

end   = pd.Timestamp.now(ZoneInfo('Europe/Brussels')).normalize()
start = end - pd.DateOffset(years=2)
s_str, e_str = start.strftime('%Y-%m-%d'), end.strftime('%Y-%m-%d')
today = f'{end.day} {_NL_MONTHS[end.month]} {end.year}'

# 1. VIX
vix = fetch_vix(s_str, e_str)

# 2. Macro indices
macro = fetch_macro(s_str, e_str)

# 3. Congress data
congress_data = load_congress_data()

# 4. Pre-markt dynamische movers
pm_movers = fetch_premarket_movers(s_str, e_str, limit=10)
valid_pm  = [m for m in pm_movers if 'close' in m]

# 5. Top-5 momentum
t5_data, t5_vols, t5_names = [], [], []
try:
    hdrs = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36'}
    r = requests.get(
        'https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved'
        '?formatted=false&scrIds=most_actives&count=50&region=US&lang=en-US',
        headers=hdrs, timeout=10)
    quotes = r.json()['finance']['result'][0]['quotes']
    for q in quotes:
        vol = q.get('regularMarketVolume', 0)
        avg = max(q.get('averageDailyVolume3Month', 1), 1)
        q['_vr'] = vol / avg
        q['_ms'] = abs(q.get('regularMarketChangePercent', 0)) * q['_vr']
    for q in sorted(quotes, key=lambda x: x['_ms'], reverse=True)[:5]:
        sym = q['symbol']
        try:
            md = run_markov(sym, s_str, e_str)
            md['exchange'] = q.get('exchange', '')
            t5_data.append(md)
            t5_vols.append(f'{q["_vr"]:.1f}x')
            t5_names.append(q.get('shortName', sym)[:28])
        except Exception as ex:
            t5_data.append({'t': sym, 'error': str(ex), 'exchange': q.get('exchange', '')})
            t5_vols.append('?')
            t5_names.append(q.get('shortName', sym)[:28])
except Exception as ex:
    t5_data.append({'t': 'FOUT', 'error': str(ex)})
    t5_vols.append('?')
    t5_names.append('')

t5_rows   = [t5_row(d, v) for d, v in zip(t5_data, t5_vols) if 'sig' in d]
valid_t5  = [(d, v, nm) for d, v, nm in zip(t5_data, t5_vols, t5_names) if 'sig' in d]
sorted_t5 = sorted(valid_t5, key=lambda x: abs(x[0]['sig']), reverse=True)
t5_reads  = [key_read(d, name=nm, extra=f'  |  {v} vol') for d, v, nm in sorted_t5]
longs_t5  = sum(1 for d, _, _ in valid_t5 if d['sig'] > 0.05)
shorts_t5 = sum(1 for d, _, _ in valid_t5 if d['sig'] < -0.05)
neut_t5   = len(valid_t5) - longs_t5 - shorts_t5
t5_summary = f'{longs_t5} Longs  |  {neut_t5} Neutraal  |  {shorts_t5} Shorts'

# ══════════════════════════════════════════════════════════════════════════════
# ASSEMBLEER BRIEF
# ══════════════════════════════════════════════════════════════════════════════
INNER = 100

alert_hdr = f'  {vix["alert"]}  |  ' if vix['alert'] else '  '
hdr = header_box([
    f'  MORNING BRIEF  —  {today}',
    f'  Markov Regime Model  |  2jr lookback  |  5-daags venster  |  2% drempel',
    f'{alert_hdr}VIX {vix["level"]:.2f}  ({vix["label"]})',
], INNER)

macro_blk       = macro_section(macro)
vix_blk         = vix_section(vix, valid_pm)
pm_blk          = premarket_section(pm_movers)
ts_blk, ts_rows = trade_setup_section(pm_movers, vix, congress_data)

tv_setup_blk = ''
if ts_rows:
    top_sym = ts_rows[0]['sym']
    top_m   = next((m for m in valid_pm if m.get('t') == top_sym), None)
    if top_m:
        tv_setup = calc_trade_levels(top_m)
        if tv_setup:
            tv_setup_blk = tv_setup_section(tv_setup)

# 6. Insider trade setups — NASDAQ/NYSE only, ≥ 2:1 R:R
all_movers_dedup = pm_movers + [d for d in t5_data if d.get('t') not in
                                {m.get('t') for m in pm_movers}]
is_blk = insider_trade_setups_section(all_movers_dedup, congress_data, vix)

t5_block = table_section(
    'TOP 5 MOMENTUM AANDELEN VANDAAG  ( |chg%| × volume vs 3-maands gemiddelde )',
    T5_COLS, t5_rows, t5_reads, t5_summary)
ftr = header_box([
    '  Framework: markov-hedge-fund-method  (Roan @RohOnChain)',
    '  Historische backtests — geen voorspelling van toekomstig rendement.',
], INNER)

sections = [hdr, macro_blk, vix_blk, pm_blk, ts_blk]
if tv_setup_blk:
    sections.append(tv_setup_blk)
sections += [is_blk, t5_block, ftr]
brief = '\n\n'.join(sections)
sys.stdout.buffer.write((brief + '\n').encode('utf-8', errors='replace'))

# ══════════════════════════════════════════════════════════════════════════════
# EMAIL
# ══════════════════════════════════════════════════════════════════════════════
alert_tag = f'  [{vix["alert"]}]' if vix['alert'] else ''
ndx_d = macro.get('US100', {})
dji_d = macro.get('US30',  {})
div_str = ''
if 'pct1' in ndx_d and 'pct1' in dji_d:
    diff = ndx_d['pct1'] - dji_d['pct1']
    div_str = f'  |  Div: {diff:+.1f}%'
top_syms = ' '.join(m.get('t', m.get('sym','?')) for m in pm_movers[:5] if 'sig' in m)
subject  = (f'Morning Brief — {today}{alert_tag}'
            f'  |  VIX {vix["level"]:.1f}{div_str}'
            f'  |  {top_syms}  ...')

try:
    send_email(subject, brief)
    sys.stdout.buffer.write('\n[OK] Verstuurd naar jldh66@gmail.com\n'.encode('utf-8'))
except Exception as e:
    sys.stdout.buffer.write(f'\n[FOUT] Email fout: {e}\n'.encode('utf-8'))
    sys.exit(1)
