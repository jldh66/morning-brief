#!/usr/bin/env python3
from __future__ import annotations
import os, re, smtplib
from email.message import EmailMessage
from html import escape as _esc

_SHELL = """<!DOCTYPE html PUBLIC "-//W3C//DTD XHTML 1.0 Transitional//EN" "http://www.w3.org/TR/xhtml1/DTD/xhtml1-transitional.dtd">
<html xmlns="http://www.w3.org/1999/xhtml">
<head><meta http-equiv="Content-Type" content="text/html; charset=utf-8"></head>
<body style="margin:0;padding:0;background-color:#0d0d0d;">
<table width="100%" cellpadding="0" cellspacing="0" border="0" bgcolor="#0d0d0d">
<tr><td align="center" style="padding:20px 8px;">
<table width="640" cellpadding="0" cellspacing="0" border="0" bgcolor="#111111" style="border:1px solid #2a2a2a;">
<tr><td style="padding:24px;font-family:Courier New,monospace;">
<p style="margin:0 0 16px 0;font-size:15px;font-weight:bold;color:#4FC3F7;letter-spacing:3px;border-bottom:1px solid #2a2a2a;padding-bottom:12px;">INSIDER SIGNALS</p>
{inner}
<p style="margin:18px 0 0 0;font-size:11px;color:#444;border-top:1px solid #1a1a1a;padding-top:10px;">Informational only. Not a trade instruction.</p>
</td></tr></table>
</td></tr></table>
</body></html>"""

_S_NORMAL = 'margin:2px 0;font-family:Courier New,monospace;font-size:13px;color:#e0e0e0;'
_S_INDENT = 'margin:1px 0 1px 16px;font-family:Courier New,monospace;font-size:12px;color:#b0b0b0;'
_S_HEADER = 'margin:12px 0 4px 0;font-family:Courier New,monospace;font-size:12px;font-weight:bold;color:#4FC3F7;letter-spacing:1px;'
_S_DATE   = 'margin:2px 0;font-family:Courier New,monospace;font-size:12px;color:#777;'
_S_GOLD   = 'margin:2px 0;font-family:Courier New,monospace;font-size:13px;font-weight:bold;color:#FFD700;'
_S_WARN   = 'margin:4px 0;font-family:Courier New,monospace;font-size:13px;font-weight:bold;color:#FF7043;'
_S_SETUP  = 'margin:1px 0 1px 8px;font-family:Courier New,monospace;font-size:12px;color:#cfd8dc;'

_BULL = '<span style="color:#4CAF50;font-weight:bold;">BULLISH</span>'
_BEAR = '<span style="color:#EF5350;font-weight:bold;">BEARISH</span>'
_NEUT = '<span style="color:#FFC107;font-weight:bold;">NEUTRAL</span>'
_OK   = '<span style="color:#4CAF50;">OK</span>'
_FAIL = '<span style="color:#EF5350;font-weight:bold;">FAIL</span>'

def _colorize(t):
    t = re.sub(r'\bBULLISH\b', _BULL, t)
    t = re.sub(r'\bBEARISH\b', _BEAR, t)
    t = re.sub(r'\bNEUTRAL\b', _NEUT, t)
    t = re.sub(r'\bOK\b', _OK, t)
    t = re.sub(r'\bFAIL\b', _FAIL, t)
    return t

def _strip_ansi(t):
    return re.sub(r'\033\[[0-9;]*m', '', t)

def _body_to_html(subject, body):
    body = _strip_ansi(body)
    parts = []
    for raw in body.splitlines():
        esc = _esc(raw)
        s = esc.strip()
        ss = set(s)
        if ss == {'='} and len(s) > 6:
            parts.append('<hr style="border:0;border-top:1px solid #2e2e2e;margin:10px 0;">')
        elif ss == {'-'} and len(s) > 6:
            parts.append('<hr style="border:0;border-top:1px solid #222;margin:6px 0;">')
        elif not s:
            parts.append('<div style="height:5px;"></div>')
        elif s.startswith('&#9733;') or s.startswith('★'):
            parts.append(f'<p style="{_S_GOLD}">{_colorize(esc)}</p>')
        elif s.isupper() and len(s) > 3 and not s.startswith('[') and not s.startswith('$'):
            parts.append(f'<p style="{_S_HEADER}">{s}</p>')
        elif re.match(r'^(Mon|Tue|Wed|Thu|Fri|Sat|Sun|Time:)', s):
            parts.append(f'<p style="{_S_DATE}">{_colorize(esc)}</p>')
        elif raw.startswith('  ') or raw.startswith('\t'):
            parts.append(f'<p style="{_S_INDENT}">{_colorize(esc)}</p>')
        else:
            parts.append(f'<p style="{_S_NORMAL}">{_colorize(esc)}</p>')
    return _SHELL.format(inner='\n'.join(parts))

def send_email(subject, body):
    body = _strip_ansi(body)
    user = os.environ.get('GMAIL_USER')
    pw   = os.environ.get('GMAIL_APP_PASSWORD')
    to   = os.environ.get('GMAIL_TO') or user
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
