#!/usr/bin/env python3
from __future__ import annotations
import os, re, smtplib
from email.message import EmailMessage
from html import escape as _esc

_SHELL = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head>
<meta http-equiv="Content-Type" content="text/html; charset=utf-8">
<style type="text/css">
body, table, td {{ background-color: #0d0d0d !important; }}
body {{ color: #e0e0e0 !important; }}
</style>
</head>
<body style="margin:0;padding:0;background-color:#0d0d0d;" bgcolor="#0d0d0d">
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0d0d0d" style="background-color:#0d0d0d;">
<tr><td align="center" bgcolor="#0d0d0d" style="padding:20px 8px;background-color:#0d0d0d;">
<!--[if mso]><table width="640" role="presentation" cellpadding="0" cellspacing="0" border="0"><tr><td><![endif]-->
<table width="640" cellpadding="0" cellspacing="0" border="0" bgcolor="#111111" style="border:1px solid #2a2a2a;background-color:#111111;">
<tr><td bgcolor="#111111" style="padding:24px;font-family:Courier New,monospace;background-color:#111111;">
<p style="margin:0 0 16px 0;font-size:15px;font-weight:bold;color:#4FC3F7;letter-spacing:3px;border-bottom:1px solid #2a2a2a;padding-bottom:12px;background-color:#111111;">INSIDER SIGNALS</p>
{inner}
<p style="margin:18px 0 0 0;font-size:11px;color:#444444;border-top:1px solid #1a1a1a;padding-top:10px;background-color:#111111;">Informational only. Not a trade instruction.</p>
</td></tr></table>
<!--[if mso]></td></tr></table><![endif]-->
</td></tr></table>
</body></html>"""

# ── Text paragraph styles ────────────────────────────────────────────────────
_S_NORMAL = 'margin:2px 0;font-family:Courier New,monospace;font-size:13px;color:#e0e0e0;background-color:#111111;'
_S_INDENT = 'margin:1px 0 1px 16px;font-family:Courier New,monospace;font-size:12px;color:#b0b0b0;background-color:#111111;'
_S_HEADER = 'margin:12px 0 4px 0;font-family:Courier New,monospace;font-size:12px;font-weight:bold;color:#4FC3F7;letter-spacing:1px;background-color:#111111;'
_S_DATE   = 'margin:2px 0;font-family:Courier New,monospace;font-size:12px;color:#777777;background-color:#111111;'
_S_GOLD   = 'margin:2px 0;font-family:Courier New,monospace;font-size:13px;font-weight:bold;color:#FFD700;background-color:#111111;'
_S_WARN   = 'margin:4px 0;font-family:Courier New,monospace;font-size:13px;font-weight:bold;color:#FF7043;background-color:#111111;'
_S_SETUP  = 'margin:1px 0 1px 8px;font-family:Courier New,monospace;font-size:12px;color:#cfd8dc;background-color:#111111;'

# ── HTML table styles ────────────────────────────────────────────────────────
_TD_BG  = '#111111'
_TD2_BG = '#131313'
_TABLE  = 'width:100%;border-collapse:collapse;font-family:Courier New,monospace;font-size:12px;margin:6px 0 12px 0;background-color:#111111;'
_TH     = 'padding:5px 8px;text-align:left;color:#4FC3F7;border-bottom:2px solid #2a2a2a;font-size:11px;letter-spacing:0.5px;white-space:nowrap;background-color:#111111;'
_TD     = 'padding:4px 8px;color:#e0e0e0;border-bottom:1px solid #1a1a1a;vertical-align:top;background-color:#111111;'
_TD2    = 'padding:4px 8px;color:#b8b8b8;background-color:#131313;border-bottom:1px solid #1a1a1a;vertical-align:top;'
_TD_SUB = 'padding:2px 8px 8px 8px;color:#666666;font-size:11px;border-bottom:1px solid #1e1e1e;background-color:#111111;'

# ── Coloured signal spans ────────────────────────────────────────────────────
_BULL  = '<span style="color:#4CAF50;font-weight:bold;">BULLISH</span>'
_BEAR  = '<span style="color:#EF5350;font-weight:bold;">BEARISH</span>'
_NEUT  = '<span style="color:#FFC107;font-weight:bold;">NEUTRAL</span>'
_LONG  = '<span style="color:#4CAF50;font-weight:bold;">LONG</span>'
_SHORT = '<span style="color:#EF5350;font-weight:bold;">SHORT</span>'
_OK    = '<span style="color:#4CAF50;">OK</span>'
_FAIL  = '<span style="color:#EF5350;font-weight:bold;">FAIL</span>'


def _colorize(t):
    t = re.sub(r'\bBULLISH\b', _BULL,  t)
    t = re.sub(r'\bBEARISH\b', _BEAR,  t)
    t = re.sub(r'\bNEUTRAL\b', _NEUT,  t)
    t = re.sub(r'\bLONG\b',    _LONG,  t)
    t = re.sub(r'\bSHORT\b',   _SHORT, t)
    t = re.sub(r'\bOK\b',      _OK,    t)
    t = re.sub(r'\bFAIL\b',    _FAIL,  t)
    return t


def _strip_ansi(t):
    return re.sub(r'\033\[[0-9;]*m', '', t)


# ── Box-drawing table renderer (morning_brief format: ╔ ║ ╚) ────────────────

def _split_box_row(line):
    """Return list of cell strings if the line is a ║-delimited row, else None."""
    s = line.strip()
    if not s or s[0] not in ('║', '│'):
        return None
    inner = s[1:-1] if len(s) >= 2 and s[-1] in ('║', '│') else s[1:]
    return [c.strip() for c in re.split(r'[║│]', inner)]


def _render_box_block(block_lines):
    """Convert a ╔…╚ box block to HTML tables + styled paragraphs."""
    rows = []
    for ln in block_lines:
        r = _split_box_row(ln)
        if r is not None:
            rows.append(r)
    if not rows:
        return ''

    max_cols = max(len(r) for r in rows)

    # Pure info block: every row is single-column
    if max_cols == 1:
        out = [f'<p style="{_S_HEADER}">{_colorize(_esc(rows[0][0]))}</p>']
        for r in rows[1:]:
            out.append(f'<p style="{_S_INDENT}">{_colorize(_esc(r[0]))}</p>')
        return '\n'.join(out)

    # Mixed table: parse title / column-header / data / footer sections
    title_rows, data_rows, footer_rows = [], [], []
    header_row = None
    state = 'pre'

    for row in rows:
        n = len(row)
        if state == 'pre':
            if n == 1:
                title_rows.append(row[0])
            else:
                header_row = row
                state = 'data'
        elif state == 'data':
            if n == max_cols:
                data_rows.append(row)
            elif n == 1:
                footer_rows.append(row[0])
                state = 'footer'
            else:
                data_rows.append(row + [''] * (max_cols - n))
        else:  # footer
            footer_rows.append(row[0] if n == 1 else '  '.join(row))

    out = []
    for t in title_rows:
        out.append(f'<p style="{_S_HEADER}">{_colorize(_esc(t))}</p>')

    if header_row or data_rows:
        thead = ''
        if header_row:
            thead = '<tr>' + ''.join(
                f'<th bgcolor="{_TD_BG}" style="{_TH}">{_colorize(_esc(c))}</th>' for c in header_row
            ) + '</tr>'
        tbody = ''
        for ri, row in enumerate(data_rows):
            bg = _TD2 if ri % 2 else _TD
            bg_c = _TD2_BG if ri % 2 else _TD_BG
            tbody += '<tr>' + ''.join(
                f'<td bgcolor="{bg_c}" style="{bg}">{_colorize(_esc(c))}</td>' for c in row
            ) + '</tr>'
        out.append(f'<table bgcolor="{_TD_BG}" style="{_TABLE}">{thead}{tbody}</table>')

    for f in footer_rows:
        out.append(f'<p style="{_S_INDENT}">{_colorize(_esc(f))}</p>')

    return '\n'.join(out)


# ── Plain-text section renderers (daily_refresh format) ──────────────────────

def _tbl(headers, rows):
    """Build a simple HTML table from a list of headers and row lists."""
    thead = '<tr>' + ''.join(f'<th bgcolor="{_TD_BG}" style="{_TH}">{h}</th>' for h in headers) + '</tr>'
    tbody = ''
    for ri, row in enumerate(rows):
        bg = _TD2 if ri % 2 else _TD
        bg_c = _TD2_BG if ri % 2 else _TD_BG
        tbody += '<tr>' + ''.join(f'<td bgcolor="{bg_c}" style="{bg}">{c}</td>' for c in row) + '</tr>'
    return f'<table bgcolor="{_TD_BG}" style="{_TABLE}">{thead}{tbody}</table>'


def _render_scouts(lines):
    """  name  [OK/FAIL]  signal text  →  3-column table"""
    rows = []
    for ln in lines:
        m = re.match(r'^\s+(\w+)\s+\[(OK|FAIL)\]\s*(.*)', ln)
        if not m:
            continue
        name, status, out = m.group(1), m.group(2), m.group(3).strip()
        rows.append([_esc(name), _OK if status == 'OK' else _FAIL, _colorize(_esc(out))])
    return _tbl(['SCOUT', 'STATUS', 'SIGNAL'], rows) if rows else ''


def _render_timeframes(lines):
    """   Label  ( N)  TICKERS  →  3-column table"""
    rows = []
    for ln in lines:
        m = re.match(r'^\s+(\S+)\s+\(\s*(\d+)\)\s*(.*)', ln)
        if not m:
            continue
        tf, count, tickers = m.group(1), m.group(2), m.group(3).strip()
        t_html = _esc(tickers) if tickers and tickers != '--' else '<span style="color:#444;">—</span>'
        rows.append([_esc(tf), count, t_html])
    return _tbl(['TIMEFRAME', 'COUNT', 'TICKERS'], rows) if rows else ''


def _render_setups(lines):
    """Numbered pipe-separated setup pairs  →  6-column table with meta sub-row."""
    records, cur = [], None
    for ln in lines:
        s = ln.strip()
        m = re.match(r'(\d+)\.\s+(\w+)\s+(\w+)\s+(.*)', s)
        if m:
            if cur:
                records.append(cur)
            cur = dict(num=m.group(1), ticker=m.group(2), dir=m.group(3),
                       rest=m.group(4), meta=[])
        elif cur and s:
            cur['meta'].append(s)
    if cur:
        records.append(cur)
    if not records:
        return ''

    thead = '<tr>' + ''.join(
        f'<th bgcolor="{_TD_BG}" style="{_TH}">{h}</th>'
        for h in ['#', 'TICKER', 'DIRECTION', 'ENTRY', 'STOP', 'TARGET']
    ) + '</tr>'
    tbody = ''
    for ri, r in enumerate(records):
        bg = _TD2 if ri % 2 else _TD
        bg_c = _TD2_BG if ri % 2 else _TD_BG
        entry = sl = tp = ''
        for part in r['rest'].split('|'):
            p = part.strip()
            if p.startswith('Entry'):
                entry = re.sub(r'^Entry\s+', '', p)
            elif p.startswith('SL'):
                sl = re.sub(r'^SL\s+', '', p)
            elif p.startswith('TP'):
                tp = re.sub(r'^TP\s+', '', p)
        dir_html = (_BULL if r['dir'] == 'BULLISH'
                    else _BEAR if r['dir'] == 'BEARISH'
                    else _colorize(_esc(r['dir'])))
        tbody += (
            f'<tr>'
            f'<td bgcolor="{bg_c}" style="{bg}">{_esc(r["num"])}</td>'
            f'<td bgcolor="{bg_c}" style="{bg};font-weight:bold;">{_esc(r["ticker"])}</td>'
            f'<td bgcolor="{bg_c}" style="{bg}">{dir_html}</td>'
            f'<td bgcolor="{bg_c}" style="{bg}">{_esc(entry)}</td>'
            f'<td bgcolor="{bg_c}" style="{bg}">{_esc(sl)}</td>'
            f'<td bgcolor="{bg_c}" style="{bg}">{_esc(tp)}</td>'
            f'</tr>'
        )
        if r['meta']:
            tbody += (f'<tr><td colspan="6" bgcolor="{_TD_BG}" style="{_TD_SUB}">'
                      f'{_esc("  ·  ".join(r["meta"]))}</td></tr>')
    return f'<table bgcolor="{_TD_BG}" style="{_TABLE}">{thead}{tbody}</table>'


def _render_ranking(lines):
    """  N. ★ TICKER  score=N  →  4-column table"""
    rows = []
    for ln in lines:
        m = re.match(r'^\s*(\d+)\.\s*(★)?\s*(\w+)\s+score=(\d+)', ln.strip())
        if not m:
            continue
        rank, star, ticker, score = m.group(1), m.group(2), m.group(3), m.group(4)
        star_html = '<span style="color:#FFD700;">★</span>' if star else ''
        rows.append([rank, star_html, _esc(ticker), score])
    return _tbl(['#', '', 'TICKER', 'SCORE'], rows) if rows else ''


def _render_events(lines):
    """  HH:MM  EventName  [data]  →  3-column table"""
    rows = []
    for ln in lines:
        s = ln.strip()
        m = re.match(r'(\d{1,2}:\d{2})\s{2,}(.+)', s)
        if not m:
            continue
        time_s, rest = m.group(1), m.group(2)
        m2 = re.match(r'(.+?)\s{2,}(Actual:|pending|\(pending\))(.*)', rest)
        if m2:
            rows.append([time_s, _esc(m2.group(1).strip()),
                         _esc((m2.group(2) + m2.group(3)).strip())])
        else:
            rows.append([time_s, _esc(rest), ''])
    return _tbl(['TIME', 'EVENT', 'DATA'], rows) if rows else ''


def _render_hc(lines):
    """  TICKER  BULLISH  conf=N  [scout]  reason  →  5-column table"""
    rows = []
    for ln in lines:
        m = re.match(r'(\w+)\s+(BULLISH|BEARISH|NEUTRAL)\s+conf=(\d+)\s+\[(\w+)\]\s+(.*)',
                     ln.strip())
        if not m:
            continue
        ticker, direction, conf, scout, reason = m.groups()
        dir_html = (_BULL if direction == 'BULLISH'
                    else _BEAR if direction == 'BEARISH' else _NEUT)
        rows.append([_esc(ticker), dir_html, conf, _esc(scout), _esc(reason[:60])])
    return _tbl(['TICKER', 'DIRECTION', 'CONF', 'SCOUT', 'REASON'], rows) if rows else ''


# ── Main HTML renderer ────────────────────────────────────────────────────────

def _body_to_html(subject, body):
    body = _strip_ansi(body)
    lines = body.splitlines()
    parts = []
    i = 0
    section = None   # active plain-text section type
    sec_lines = []   # lines collected for that section

    def flush():
        nonlocal section, sec_lines
        if section and sec_lines:
            fn = {
                'scouts':      _render_scouts,
                'timeframes':  _render_timeframes,
                'setups':      _render_setups,
                'ranking':     _render_ranking,
                'events':      _render_events,
                'hc':          _render_hc,
            }.get(section)
            if fn:
                html = fn(sec_lines)
                if html:
                    parts.append(html)
        section = None
        sec_lines = []

    while i < len(lines):
        raw = lines[i]
        s = raw.strip()

        # ── Box-drawing block (╔ … ╚) ──────────────────────────────────────
        if s and s[0] == '╔':
            flush()
            block = []
            while i < len(lines):
                block.append(lines[i])
                if lines[i].strip().startswith('╚'):
                    i += 1
                    break
                i += 1
            html = _render_box_block(block)
            if html:
                parts.append(html)
            continue

        # ── Separator lines ─────────────────────────────────────────────────
        ss = set(s)
        if ss == {'='} and len(s) > 6:
            flush()
            parts.append('<hr style="border:0;border-top:1px solid #2e2e2e;margin:10px 0;">')
            i += 1; continue
        if ss == {'-'} and len(s) > 6:
            flush()
            parts.append('<hr style="border:0;border-top:1px solid #1a1a1a;margin:8px 0;">')
            i += 1; continue
        if not s:
            flush()
            parts.append('<div style="height:5px;"></div>')
            i += 1; continue

        # ── Plain-text section triggers ─────────────────────────────────────
        new_sec = (
            'scouts'     if re.match(r'^SCOUTS',               s) else
            'timeframes' if re.match(r'^SIGNALS BY TIMEFRAME', s) else
            'hc'         if re.match(r'^HIGH CONVICTION',      s) else
            'events'     if 'ECONOMIC EVENTS' in s                 else
            'setups'     if 'TRADE SETUP' in s and re.match(r'^TOP', s) else
            'ranking'    if re.match(r'^CONVICTION RANKING',   s) else
            None
        )
        if new_sec:
            flush()
            section = new_sec
            parts.append(f'<p style="{_S_HEADER}">{_colorize(_esc(s))}</p>')
            i += 1; continue

        # Collect indented content lines for the active section
        if section and (raw.startswith('  ') or raw.startswith('\t')):
            sec_lines.append(raw)
            i += 1; continue

        # ── Normal line fallback ────────────────────────────────────────────
        flush()
        esc = _esc(raw)
        if s.startswith('★'):
            parts.append(f'<p style="{_S_GOLD}">{_colorize(esc)}</p>')
        elif s.startswith('MACRO HEADWIND') or s.startswith('MACRO TAILWIND'):
            parts.append(f'<p style="{_S_WARN}">{_colorize(esc)}</p>')
        elif re.match(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Time:)', s):
            parts.append(f'<p style="{_S_DATE}">{_colorize(esc)}</p>')
        elif (s.isupper() and len(s) > 3
              and not s.startswith('[') and not s.startswith('$')
              and not re.match(r'^\d', s)):
            parts.append(f'<p style="{_S_HEADER}">{_colorize(esc)}</p>')
        elif raw.startswith('  ') or raw.startswith('\t'):
            parts.append(f'<p style="{_S_INDENT}">{_colorize(esc)}</p>')
        else:
            parts.append(f'<p style="{_S_NORMAL}">{_colorize(esc)}</p>')
        i += 1

    flush()
    return _SHELL.format(inner='\n'.join(parts))


# ── Email sending ─────────────────────────────────────────────────────────────

def send_email(subject, body, to=None):
    body = _strip_ansi(body)
    user = os.environ.get('GMAIL_USER')
    pw   = os.environ.get('GMAIL_APP_PASSWORD')
    to   = to or os.environ.get('GMAIL_TO') or user
    if not user or not pw:
        raise RuntimeError('GMAIL_USER / GMAIL_APP_PASSWORD not set')
    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From']    = user
    msg['To']      = to
    msg.set_content(body)
    msg.add_alternative(_body_to_html(subject, body), subtype='html')
    with smtplib.SMTP_SSL('smtp.gmail.com', 465) as s:
        s.login(user, pw)
        s.send_message(msg)


def render_insider_signal(ticker, direction, scouts_reasons, timestamp, setup=None, calendar_events=None):
    head = f"SOPHIE CONSENSUS -- {direction} ON {ticker}"
    rule = "=" * max(len(head), 52)
    lines = [head, rule, f"Time: {timestamp}", ""]
    lines.append(f"INSIDER INTELLIGENCE ({len(scouts_reasons)} SCOUTS AGREE):")
    for scout, reason in scouts_reasons:
        lines.append(f"  {scout:<10}  {reason}")
    if setup:
        lines += ["", "TRADE SETUP:"]
        lines.append(f"  {ticker}  {direction}  Entry ${setup['entry']}  SL ${setup['sl']} (-{setup['risk_pct']}%)  TP ${setup['tp']} (+{setup['reward_pct']}%)")
    if calendar_events:
        lines += ["", "ECONOMIC EVENTS TODAY:"]
        for ev in calendar_events:
            lines.append(f"  {ev.get('time','')}  {ev.get('event','')}  Exp: {ev.get('expected','')}")
    lines += ["", "-" * 52, "Informational only. Not a trade instruction."]
    return "\n".join(lines)
