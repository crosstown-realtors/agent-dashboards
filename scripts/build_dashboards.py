#!/usr/bin/env python3
"""
Crosstown Realtors — Agent Dashboard Builder
Reads tracker CSV (base64 from Google Drive), patches all agent HTML files with fresh data.
"""

import base64, csv, io, json, re, sys, os
from datetime import datetime, date
from collections import defaultdict

TRACKER_ID = "1i5TOe9iIAkrdMAXV8vAe-YIYxJQ8juKUUjoFcCgySF0"
TRACKER_URL = f"https://docs.google.com/spreadsheets/d/1i5TOe9iIAkrdMAXV8vAe-YIYxJQ8juKUUjoFcCgySF0/edit"
REPO_DIR = "/tmp/agent-dashboards"
TODAY = date.today().strftime("%Y-%m-%d")
TODAY_PRETTY = date.today().strftime("%B %-d, %Y")

# Goals per agent (stored as defaults in HTML, user can override in settings)
GOALS = {
    "Michelle Madden":       {"pac": 200000, "closes": 45},
    "Dan Krembuszewski":     {"pac": 150000, "closes": 20},
    "Lauren Litoborski":     {"pac": 150000, "closes": 30},
    "Mollie Kelly":          {"pac": 150000, "closes": 30},
    "Mike Kelly":            {"pac": 200000, "closes": 35},
    "Jaclyn Mitchell":       {"pac": 120000, "closes": 25},
    "Chris Lira":            {"pac": 120000, "closes": 20},
    "Angela Palma":          {"pac": 100000, "closes": 18},
}

FILES = {
    "Michelle Madden":   "michelle.html",
    "Dan Krembuszewski": "dan.html",
    "Lauren Litoborski": "lauren.html",
    "Mollie Kelly":      "mollie.html",
    "Mike Kelly":        "mike.html",
    "Jaclyn Mitchell":   "jaclyn.html",
    "Chris Lira":        "chris.html",
    "Angela Palma":      "angela.html",
}

# Initials map for override lookup
INITIALS = {"DK": "Dan Krembuszewski", "MM": "Michelle Madden", "CL": "Chris Lira", "ML": "Mollie Kelly", "AP": "Angela Palma"}

# Per-agent header metadata (initials, display role)
AGENT_META = {
    "Dan Krembuszewski":  {"initials": "DK", "role": "Broker / Owner · Krembo Group / Crosstown Realtors"},
    "Michelle Madden":    {"initials": "MM", "role": "Team Lead · Madden Group / Crosstown Realtors"},
    "Lauren Litoborski":  {"initials": "LL", "role": "Agent · Crosstown Realtors"},
    "Mollie Kelly":       {"initials": "MK", "role": "Agent · Crosstown Realtors"},
    "Mike Kelly":         {"initials": "MiK", "role": "Agent · Crosstown Realtors"},
    "Jaclyn Mitchell":    {"initials": "JM", "role": "Agent · Crosstown Realtors"},
    "Chris Lira":         {"initials": "CL", "role": "Agent · Madden Group / Crosstown Realtors"},
    "Angela Palma":       {"initials": "AP", "role": "Agent · Madden Group / Crosstown Realtors"},
}

def parse_dollar(s):
    try:
        return float(str(s).strip().replace('$','').replace(',','').replace(' ',''))
    except:
        return 0.0

def fmt_short_date(s):
    for fmt in ['%m/%d/%Y %H:%M:%S', '%m/%d/%Y %H:%M', '%m/%d/%Y']:
        try:
            return datetime.strptime(s.strip(), fmt).strftime('%b %-d'), datetime.strptime(s.strip(), fmt)
        except:
            pass
    return '', None

def parse_proj_date(s):
    for fmt in ['%m/%d/%Y', '%m/%d/%Y %H:%M:%S']:
        try:
            return datetime.strptime(s.strip(), fmt)
        except:
            pass
    return None

def days_until(s):
    dt = parse_proj_date(s)
    if dt:
        diff = (dt.date() - date.today()).days
        return diff
    return None

def badge_for_close_date(proj_date_str):
    d = days_until(proj_date_str)
    if d is None:
        return ''
    if d < 0:
        return f'<span class="badge badge-red">🚨 {abs(d)}d overdue</span>'
    elif d <= 7:
        return f'<span class="badge badge-red">🚨 {d}d</span>'
    elif d <= 21:
        dt = parse_proj_date(proj_date_str)
        return f'<span class="badge badge-orange">⏰ {dt.strftime("%b %-d") if dt else proj_date_str}</span>'
    else:
        dt = parse_proj_date(proj_date_str)
        return f'<span class="badge badge-blue">📅 {dt.strftime("%b %-d") if dt else proj_date_str}</span>'

def load_csv(b64_or_path):
    if os.path.exists(b64_or_path):
        raw = open(b64_or_path).read().strip()
    else:
        raw = b64_or_path.strip()
    # If the file looks like a raw CSV (starts with 'Timestamp'), use it directly
    if raw.startswith('Timestamp') or raw.startswith('"Timestamp"'):
        return raw
    b64 = raw.replace(' ', '')
    # Fix padding — handle the case where data chars % 4 == 1 by trying to strip 1 char
    data_chars = len(b64.rstrip('='))
    if data_chars % 4 == 1:
        # Try dropping the last non-padding char before re-padding
        b64 = b64.rstrip('=')[:-1]
    pad = (-len(b64.rstrip('=')) % 4)
    b64_fixed = b64.rstrip('=') + '=' * pad
    return base64.b64decode(b64_fixed).decode('utf-8', errors='replace')

def parse_tracker(csv_text):
    reader = csv.DictReader(io.StringIO(csv_text))
    rows = list(reader)
    
    agents = defaultdict(lambda: {
        'closed': [], 'uc': [], 'busted': [],
        'pac_total': 0.0, 'krembo_total': 0.0,
        'override_received': [],  # {from_agent, client, addr, short_date, amt, source}
        'override_pending': [],   # UC deals with override
    })

    seen_deals = set()   # for deduplication
    for r in rows:
        def gs(key): return (r.get(key) or '').strip()
        agent = gs('Agent Name').lstrip('-').strip()   # normalize: strip leading dashes
        status = gs('Status')
        if not agent or status not in ('Closed','Under Contract','Busted'):
            continue

        bs = gs('Buy or Sell')
        client = gs('Client Name(s)')
        addr = gs('Property Address')
        source = gs('Source')
        ts = gs('Timestamp')
        proj = gs('Projected Close Date')

        # Deduplicate: skip rows that are exact duplicates of (agent, bs, client, proj, status)
        dedup_key = (agent, bs, client, proj, status)
        if dedup_key in seen_deals:
            continue
        seen_deals.add(dedup_key)
        price = parse_dollar(r.get('Price') or '')
        pac = parse_dollar(r.get('Primary Agent Commission') or '')
        krembo = parse_dollar(r.get('Krembo') or '')
        gci = parse_dollar(r.get('GCI') or '')
        override_amt = parse_dollar(r.get('Override Amt') or '')
        override_to = gs('Override To')
        short_date, dt_obj = fmt_short_date(ts)
        # Fallback: if no Timestamp on a closed deal, use Projected Close Date
        if not dt_obj and status == 'Closed' and proj:
            dt_obj = parse_proj_date(proj)
            if dt_obj:
                short_date = dt_obj.strftime('%b %-d')

        deal = dict(agent=agent, bs=bs, client=client, addr=addr, source=source,
                    ts=ts, short_date=short_date, dt=dt_obj, proj=proj,
                    price=price, pac=pac, krembo=krembo, gci=gci,
                    override_amt=override_amt, override_to=override_to)

        if 'Dan' in agent:
            # Dan's income is the Krembo column on his own deals
            deal['effective_pac'] = krembo
        else:
            deal['effective_pac'] = pac

        if status == 'Closed':
            agents[agent]['closed'].append(deal)
            if 'Dan' in agent:
                agents[agent]['krembo_total'] += krembo
            else:
                agents[agent]['pac_total'] += pac
            # Also track Krembo column on ALL closed deals (goes to Dan as broker)
            if krembo > 0 and 'Dan' not in agent:
                agents['Dan Krembuszewski']['krembo_total'] += krembo
            # Override tracking
            if override_amt > 0 and override_to and status == 'Closed':
                full_name = INITIALS.get(override_to, override_to)
                agents[full_name]['override_received'].append({
                    'from_agent': agent, 'client': client, 'addr': addr,
                    'short_date': short_date, 'dt': dt_obj, 'amt': override_amt, 'source': source
                })

        elif status == 'Under Contract':
            agents[agent]['uc'].append(deal)
            if override_amt > 0 and override_to:
                full_name = INITIALS.get(override_to, override_to)
                # Store full deal so we can render a proper pipeline card
                agents[full_name]['override_pending'].append(deal)

        elif status == 'Busted':
            agents[agent]['busted'].append(deal)

    # Dan: add explicit DK overrides (separate from Krembo column)
    dan_override_total = sum(o['amt'] for o in agents['Dan Krembuszewski']['override_received'])
    # Total Dan income = own Krembo + team Krembo (already accumulated) + explicit overrides
    # Note: override_received already includes both krembo-based and override_amt entries — don't double count
    # Actually, we tracked team krembo in krembo_total and explicit overrides separately in override_received
    # Dan's total = krembo_total (own + team) + override_received total
    agents['Dan Krembuszewski']['pac_total'] = (
        agents['Dan Krembuszewski']['krembo_total'] + dan_override_total
    )

    return agents

def fmt_money(v):
    return f"${v:,.0f}"

def fmt_money2(v):
    return f"${v:,.2f}"

def sort_closed(deals):
    def key(d):
        if d['dt']:
            return d['dt']
        # No timestamp — sort before others
        return datetime(2025, 1, 1)
    return sorted(deals, key=key, reverse=True)

def closed_row_html(deal):
    bs_badge = 'badge-blue' if deal['bs']=='Buy' else 'badge-orange'
    bs_label = deal['bs']
    pac = deal['effective_pac']
    pac_str = fmt_money(pac) if pac > 0 else '$0'
    date_str = deal['short_date'] if deal['short_date'] else '—'
    client = deal['client'].replace('&','&amp;')
    addr = deal['addr'].replace('&','&amp;')
    src = deal['source']
    name_part = f"{client} — {addr}" if addr else client
    if src:
        name_part += f" ({src})"
    return (f'<div class="closed-row">'
            f'<div class="cl-date">{date_str}</div>'
            f'<span class="badge {bs_badge}" style="font-size:10px;padding:2px 7px;">{bs_label}</span>'
            f'<div class="cl-name">{name_part}</div>'
            f'<div class="cl-pac">{pac_str}</div>'
            f'</div>')

def pipeline_card_html(deal, highlight=False):
    bs = deal['bs']
    client = deal['client'].replace('&','&amp;')
    addr = deal['addr'].replace('&','&amp;')
    proj = deal['proj']
    price = deal['price']
    pac = deal['pac'] if 'Dan' not in deal.get('agent','') else deal['krembo']
    gci = deal['gci']
    source = deal['source']
    badge = badge_for_close_date(proj)
    
    dt = parse_proj_date(proj)
    proj_fmt = dt.strftime('%b %-d') if dt else proj
    
    card_style = ' style="background:linear-gradient(135deg,#f0f7ff,#e8f2fe);border:1px solid #bfdbfe;"' if highlight else ''
    gci_chip = f'<div class="chip">GCI: <strong>{fmt_money(gci)}</strong></div>' if gci > 0 else ""
    return (f'<div class="card"{card_style}>'
            f'<div class="deal-row"><div class="deal-left">'
            f'<div class="deal-title">{client} — {bs.upper()}</div>'
            f'<div class="deal-sub">{addr}</div>'
            f'</div>{badge}</div>'
            f'<div class="deal-chips">'
            f'<div class="chip">Proj. close: <strong>{proj_fmt}</strong></div>'
            f'<div class="chip">Price: <strong>{fmt_money(price)}</strong></div>'
            f'{gci_chip}'
            f'<div class="chip">Est. PAC: <strong>{fmt_money(pac)}</strong></div>'
            f'<div class="chip">Source: <strong>{source}</strong></div>'
            f'<a class="open-link" href="{TRACKER_URL}" target="_blank">View tracker →</a>'
            f'</div></div>')

def build_data_section(agent_name, d):
    """Returns the full data HTML block for one agent's dashboard."""
    g = GOALS.get(agent_name, {"pac": 150000, "closes": 20})
    pac_goal = g['pac']
    close_goal = g['closes']

    closed = sort_closed(d['closed'])
    uc = sorted(d['uc'], key=lambda x: parse_proj_date(x['proj']) or datetime(2099,1,1))
    pac_ytd = d['pac_total']
    n_closed = len(closed)
    n_uc = len(uc)
    
    override_list = sorted(d['override_received'], key=lambda x: x['dt'] or datetime(2025,1,1), reverse=True)
    override_total = sum(o['amt'] for o in override_list)
    override_pending = sum(o['override_amt'] for o in d['override_pending'])
    
    if 'Dan' in agent_name:
        own_income = d['krembo_total']
        team_krembo = own_income - sum(deal['krembo'] for deal in closed)
        own_closed_krembo = sum(deal['krembo'] for deal in closed)
        override_income = override_total
        total_ytd = d['pac_total']
        team_closes = len(override_list)
    else:
        own_income = d['pac_total']
        override_income = override_total
        total_ytd = d['pac_total'] + override_total
        team_closes = len(override_list)
    
    pct = int(total_ytd / pac_goal * 100) if pac_goal > 0 else 0
    pct_str = f"{pct}%"
    
    # Est pipeline PAC
    est_pipeline_pac = sum(deal['pac'] if 'Dan' not in agent_name else deal['krembo'] for deal in uc)
    if 'Dan' in agent_name:
        est_pipeline_pac = sum(deal['krembo'] for deal in uc)
    
    # --- ALERTS ---
    alerts = []
    # Most upcoming close
    if uc:
        next_deal = uc[0]
        d_days = days_until(next_deal['proj'])
        bs_verb = "buy" if next_deal['bs']=='Buy' else 'sell'
        pac_est = next_deal['pac'] if 'Dan' not in agent_name else next_deal['krembo']
        if d_days is not None and d_days <= 14:
            alerts.append(f'<div class="alert alert-gold">⭐ <span><strong>Closing soon ({next_deal["proj"]}):</strong> {next_deal["client"].replace("&","&amp;")} — {bs_verb} {fmt_money(next_deal["price"])} at {next_deal["addr"].replace("&","&amp;")}. Est. PAC: <strong>{fmt_money(pac_est)}</strong>.</span></div>')
    # Overdue UC
    for deal in uc:
        d_days = days_until(deal['proj'])
        if d_days is not None and d_days < -7:
            alerts.append(f'<div class="alert alert-red">🚨 <span><strong>{deal["client"].replace("&","&amp;")} ({deal["bs"]})</strong> — {deal["addr"].replace("&","&amp;")} — projected close {deal["proj"]} ({abs(d_days)}d overdue). Confirm status.</span></div>')
    # YTD summary
    recent_month_closes = [c for c in closed if c['dt'] and c['dt'].month == date.today().month and c['dt'].year == date.today().year]
    prev_month = (date.today().month - 1) or 12
    prev_month_closes = [c for c in closed if c['dt'] and c['dt'].month == prev_month and c['dt'].year == date.today().year]
    if recent_month_closes:
        month_name = date.today().strftime('%B')
        month_pac = sum(c['effective_pac'] for c in recent_month_closes)
        alerts.append(f'<div class="alert alert-green">✅ <span><strong>{len(recent_month_closes)} closing(s) this month ({month_name})</strong> — {fmt_money(month_pac)} PAC. YTD: <strong>{fmt_money(total_ytd)}</strong>{"" if override_income==0 else f" including {fmt_money(override_income)} override income"}.</span></div>')
    elif prev_month_closes:
        prev_month_name = date(date.today().year, prev_month, 1).strftime('%B')
        prev_pac = sum(c['effective_pac'] for c in prev_month_closes)
        alerts.append(f'<div class="alert alert-green">✅ <span><strong>{len(prev_month_closes)} closing(s) in {prev_month_name}</strong> — {fmt_money(prev_pac)} PAC. YTD: <strong>{fmt_money(total_ytd)}</strong>{"" if override_income==0 else f" including {fmt_money(override_income)} override income"}.</span></div>')
    else:
        alerts.append(f'<div class="alert alert-blue">ℹ️ <span>YTD: <strong>{fmt_money(total_ytd)}</strong> from {n_closed} closed deal(s){"" if override_income==0 else f" + {fmt_money(override_income)} override"}.</span></div>')
    
    if not alerts:
        alerts.append(f'<div class="alert alert-blue">ℹ️ <span>No closings this month yet. YTD: <strong>{fmt_money(total_ytd)}</strong>.</span></div>')
    
    alerts_html = '\n    '.join(alerts[:3])

    # --- STATS GRID ---
    if 'Dan' in agent_name:
        stat2_label = "Own Agent Deals"
        stat2_val = fmt_money(own_closed_krembo)
        stat2_sub = f"{n_closed} own closes"
        stat3_label = "Krembo + Override"
        stat3_val = fmt_money(override_income + (own_income - own_closed_krembo))
        stat3_sub = f"{team_closes} override deals"
    elif override_income > 0:
        stat2_label = "Own Agent Deals"
        stat2_val = fmt_money(own_income)
        stat2_sub = f"{n_closed} own closes"
        stat3_label = "Team Override"
        stat3_val = fmt_money(override_income)
        stat3_sub = f"{team_closes} team closes"
    else:
        stat2_label = "PAC — Own Deals"
        stat2_val = fmt_money(own_income)
        stat2_sub = f"{n_closed} closes"
        stat3_label = "Active Pipeline"
        stat3_val = fmt_money(est_pipeline_pac)
        stat3_sub = f"{n_uc} deal{'s' if n_uc != 1 else ''} UC"
    
    pipeline_sub = f"${est_pipeline_pac/1000:.0f}K pipeline" if est_pipeline_pac >= 1000 else fmt_money(est_pipeline_pac)
    
    stats_html = f"""  <div class="grid-4">
    <div class="stat-card">
      <div class="stat-num" style="color:var(--accent)">{fmt_money(round(total_ytd))}</div>
      <div class="stat-label">Total YTD Income</div>
      <div class="stat-sub">{"own PAC + override" if override_income > 0 else "own deals"}</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:var(--blue)">{stat2_val}</div>
      <div class="stat-label">{stat2_label}</div>
      <div class="stat-sub">{stat2_sub}</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:var(--teal)">{stat3_val}</div>
      <div class="stat-label">{stat3_label}</div>
      <div class="stat-sub">{stat3_sub}</div>
    </div>
    <div class="stat-card">
      <div class="stat-num" style="color:var(--orange)" id="pct-stat">{pct_str}</div>
      <div class="stat-label">Of {fmt_money(pac_goal)} Goal</div>
      <div class="stat-sub">{pipeline_sub}</div>
    </div>
  </div>"""

    # --- INCOME BREAKDOWN ---
    if 'Dan' in agent_name:
        income_rows = f"""      <div class="income-row">
        <span class="income-label">Own agent deals (Krembo — {n_closed} closes)</span>
        <span class="income-amt" style="color:var(--blue)">{fmt_money2(own_closed_krembo)}</span>
      </div>
      <div class="income-row">
        <span class="income-label">Team override / broker Krembo ({team_closes} team closes)</span>
        <span class="income-amt" style="color:var(--teal)">{fmt_money2(override_income + (own_income - own_closed_krembo))}</span>
      </div>"""
    elif override_income > 0:
        income_rows = f"""      <div class="income-row">
        <span class="income-label">Own agent deals (PAC — {n_closed} closes)</span>
        <span class="income-amt" style="color:var(--blue)">{fmt_money2(own_income)}</span>
      </div>
      <div class="income-row">
        <span class="income-label">Team override income ({team_closes} team closes)</span>
        <span class="income-amt" style="color:var(--teal)">{fmt_money2(override_income)}</span>
      </div>"""
    else:
        income_rows = f"""      <div class="income-row">
        <span class="income-label">Own agent deals (PAC — {n_closed} closes)</span>
        <span class="income-amt" style="color:var(--blue)">{fmt_money2(own_income)}</span>
      </div>"""

    income_html = f"""\
    <div class="section-label">💰 Income Breakdown — YTD 2026</div>
    <div class="card">
{income_rows}
      <div class="income-row" style="padding-top:10px;">
        <span style="font-weight:700;font-size:14px;">Total Income YTD</span>
        <span style="font-weight:800;font-size:16px;color:var(--green)">{fmt_money2(total_ytd)}</span>
      </div>
    </div>
"""

    # --- GOAL PROGRESS ---
    goal_html = f"""\
    <div class="section-label">📈 2026 Production — {fmt_money(pac_goal)} PAC Goal</div>
    <div class="card">
      <div class="pace-wrap">
        <div class="pace-main">
          <div class="prog-row"><span>YTD PAC: <strong>{fmt_money(round(total_ytd))}</strong></span><span id="pct-lbl" style="color:{'var(--green)' if pct >= 50 else 'var(--orange)'}">{total_ytd/pac_goal*100:.1f}% of {fmt_money(pac_goal)}</span></div>
          <div class="prog-bg"><div class="prog-fill" id="gci-bar" style="width:0%;background:linear-gradient(90deg,var(--accent),#7b78ff)"></div></div>
          <div style="margin-top:8px;font-size:12px;color:var(--sub);">On pace for: <strong id="annualized" style="color:var(--text)">—</strong> &nbsp;·&nbsp; Gap vs. pace today: <strong id="pace-gap" style="color:var(--red)">—</strong></div>
          <div class="month-grid" id="month-grid"></div>
        </div>
        <div class="pace-side">
          <div><div class="num" style="color:var(--orange)" id="mo-needed">—</div><div class="lbl">Need/mo<br>to hit goal</div></div>
          <div><div class="num" style="color:var(--blue)" id="mo-left">—</div><div class="lbl">Months<br>remaining</div></div>
        </div>
      </div>
    </div>
"""

    # --- CLOSE COUNT ---
    close_pct_str = f"{n_closed} / {close_goal} ({n_closed/close_goal*100:.1f}%)"
    pipeline_note = f"Pipeline: {n_uc} deal{'s' if n_uc != 1 else ''} pending · Est. +{fmt_money(est_pipeline_pac)} PAC when closed"
    close_html = f"""\
    <div class="section-label">🔥 Annual Progress — Close Count</div>
    <div class="card">
      <div class="streak-wrap">
        <div class="streak-count">
          <div class="streak-num" style="color:var(--accent)">{n_closed}</div>
          <div class="streak-label">Closes in 2026</div>
          <div style="font-size:11px;color:var(--sub);margin-top:4px;">Goal: {close_goal}</div>
        </div>
        <div style="flex:1;">
          <div class="prog-row"><span>Annual close goal</span><span style="color:var(--accent);font-weight:700;" id="close-label">{close_pct_str}</span></div>
          <div class="prog-bg" style="height:10px;"><div class="prog-fill" style="width:0%;background:linear-gradient(90deg,var(--accent),#7b78ff)" id="close-bar"></div></div>
          <div style="margin-top:10px;">\n            <div style="font-size:11px;font-weight:700;color:var(--sub);text-transform:uppercase;letter-spacing:.05em;margin-bottom:6px;">Monthly consistency (own closes)</div>\n            <div class="month-dots-row" id="streak-dots"></div>\n          </div>\n          <div style="margin-top:6px;font-size:12px;color:var(--sub);">{pipeline_note}</div>
        </div>
      </div>
    </div>
"""

    # --- PIPELINE ---
    if uc or d['override_pending']:
        if 'Dan' in agent_name:
            # Dan: own deals (highlighted blue) + team override deals (no highlight), sorted by proj date
            override_deals = d['override_pending']
            # Build combined list: (deal, is_own)
            combined = [(deal, True) for deal in uc] + [(deal, False) for deal in override_deals]
            combined.sort(key=lambda x: parse_proj_date(x[0]['proj']) or datetime(2099,1,1))
            # Build each card
            card_parts = []
            for deal, is_own in combined:
                if is_own:
                    card_parts.append(pipeline_card_html(deal, highlight=True))
                else:
                    # Override deal: show the override amount in the PAC chip, plus a "Team override" label
                    override_amt_val = deal['override_amt']
                    bs = deal['bs']
                    client = deal['client'].replace('&','&amp;')
                    addr = deal['addr'].replace('&','&amp;')
                    proj = deal['proj']
                    price = deal['price']
                    gci = deal['gci']
                    agent_init = deal['agent'].split()[0][0] + deal['agent'].split()[-1][0] if deal['agent'] else '?'
                    badge = badge_for_close_date(proj)
                    dt = parse_proj_date(proj)
                    proj_fmt = dt.strftime('%b %-d') if dt else proj
                    gci_chip = f'<div class="chip">GCI: <strong>{fmt_money(gci)}</strong></div>' if gci > 0 else ""
                    card_parts.append(
                        f'<div class="card" style="border-left:3px solid var(--teal);">'
                        f'<div class="deal-row"><div class="deal-left">'
                        f'<div class="deal-title">{client} — {bs.upper()}'
                        f'<span style="font-size:10px;font-weight:600;color:var(--teal);margin-left:8px;background:rgba(0,150,136,0.1);padding:2px 6px;border-radius:10px;">OVERRIDE</span>'
                        f'</div>'
                        f'<div class="deal-sub">{addr}</div>'
                        f'</div>{badge}</div>'
                        f'<div class="deal-chips">'
                        f'<div class="chip">Proj. close: <strong>{proj_fmt}</strong></div>'
                        f'<div class="chip">Price: <strong>{fmt_money(price)}</strong></div>'
                        f'{gci_chip}'
                        f'<div class="chip">Est. Override: <strong>{fmt_money(override_amt_val)}</strong></div>'
                        f'<div class="chip">Agent: <strong>{deal["agent"].split()[-1]}</strong></div>'
                        f'<a class="open-link" href="{TRACKER_URL}" target="_blank">View tracker →</a>'
                        f'</div></div>'
                    )
            # Total card covers own pipeline + pending overrides
            est_override_pending = sum(d2['override_amt'] for d2 in override_deals)
            total_pipeline_est = est_pipeline_pac + est_override_pending
            pipeline_total_card = (f'<div class="card" style="background:var(--green-bg);border:1px solid #c3e6cb;">'
                                   f'<div style="display:flex;align-items:center;justify-content:space-between;font-size:13px;">'
                                   f'<span style="color:var(--sub);">Est. pipeline income if all close:</span>'
                                   f'<span style="font-weight:800;font-size:16px;color:var(--green);">+{fmt_money(total_pipeline_est)}</span></div>'
                                   f'<div style="font-size:11px;color:var(--sub);margin-top:4px;">'
                                   f'Own PAC: {fmt_money(est_pipeline_pac)}{"  ·  Override: "+fmt_money(est_override_pending) if est_override_pending else ""}  ·  Would bring YTD to ~{fmt_money(round(total_ytd + total_pipeline_est))}</div></div>')
            pipeline_content = '\n    '.join(card_parts) + '\n    ' + pipeline_total_card
        else:
            # Other agents: spotlight highest-value
            top_uc = max(uc, key=lambda x: x['price'])
            pac_top = top_uc['pac']
            dt_top = parse_proj_date(top_uc['proj'])
            dt_top_fmt = dt_top.strftime('%b %-d') if dt_top else top_uc['proj']
            top_uc_gci_chip = f'<div class="chip">GCI: <strong>{fmt_money(top_uc["gci"])}</strong></div>' if top_uc['gci'] > 0 else ""
            spotlight = (f'<div class="spotlight"><div class="spotlight-hdr"><div>'
                         f'<div class="spotlight-title">⭐ Highest-Value Deal — Watch Closely</div>'
                         f'<div style="font-weight:600;font-size:14px;margin-top:2px;">{top_uc["client"].replace("&","&amp;")} — {top_uc["bs"].upper()}</div>'
                         f'<div style="font-size:12px;color:var(--sub);margin-top:2px;">{top_uc["addr"].replace("&","&amp;")}</div>'
                         f'</div><span class="badge badge-gold">⭐ {dt_top_fmt}</span></div>'
                         f'<div class="spotlight-chips">'
                         f'<div class="chip">Proj. close: <strong>{dt_top_fmt}</strong></div>'
                         f'<div class="chip">Price: <strong>{fmt_money(top_uc["price"])}</strong></div>'
                         f'{top_uc_gci_chip}'
                         f'<div class="chip">Est. PAC: <strong>{fmt_money(pac_top)}</strong></div>'
                         f'<div class="chip">Source: <strong>{top_uc["source"]}</strong></div>'
                         f'<a class="open-link" href="{TRACKER_URL}" target="_blank">View tracker →</a>'
                         f'</div></div>')
            other_uc = [deal for deal in uc if deal != top_uc]
            all_cards = spotlight + ('\n    ' + '\n    '.join(pipeline_card_html(deal) for deal in other_uc) if other_uc else '')
            pipeline_total_card = (f'<div class="card" style="background:var(--green-bg);border:1px solid #c3e6cb;">'
                                   f'<div style="display:flex;align-items:center;justify-content:space-between;font-size:13px;">'
                                   f'<span style="color:var(--sub);">Est. pipeline value if all close:</span>'
                                   f'<span style="font-weight:800;font-size:16px;color:var(--green);">+{fmt_money(est_pipeline_pac)} PAC</span></div>'
                                   f'<div style="font-size:11px;color:var(--sub);margin-top:4px;">'
                                   f'Would bring YTD to ~{fmt_money(round(total_ytd + est_pipeline_pac))}</div></div>')
            pipeline_content = all_cards + '\n    ' + pipeline_total_card
    else:
        pipeline_content = '<div class="card" style="color:var(--sub);text-align:center;padding:20px;">No active pipeline deals.</div>'
    
    pipeline_html = f"""\
    <div class="section-label">🏠 Active Pipeline</div>
    {pipeline_content}
"""

    # --- RECENT CLOSINGS ---
    closing_rows = '\n      '.join(closed_row_html(d) for d in closed[:12])
    closings_html = f"""\
    <div class="section-label">✅ Recent Closings — YTD 2026</div>
    <div class="card">
      {closing_rows if closing_rows else '<div style="color:var(--sub);font-size:13px;">No closings yet in 2026.</div>'}
    </div>
"""

    # --- OVERRIDE SECTION (for agents who receive them) ---
    override_html = ""
    if override_list:
        override_rows = '\n      '.join(
            f'<div class="closed-row">'
            f'<div class="cl-date">{o["short_date"]}</div>'
            f'<span class="badge badge-blue" style="font-size:10px;padding:2px 7px;">Override</span>'
            f'<div class="cl-name">{o["from_agent"].split()[0]} — {o["client"].replace("&","&amp;")} — {o["addr"].replace("&","&amp;")} ({o["source"]})</div>'
            f'<div class="cl-pac">{fmt_money(o["amt"])}</div>'
            f'</div>'
            for o in override_list
        )
        first_name = agent_name.split()[0]
        pending_note = ""
        if d['override_pending']:
            pending_note = f'<div style="font-size:11px;color:var(--sub);margin-top:4px;">Pending (UC): +{fmt_money(override_pending)} when pending deals close.</div>'
        override_html = f"""    <div class="section-label">💼 Override Income — Team Deals (YTD)</div>
    <div class="card">
      <div style="font-size:12px;color:var(--sub);margin-bottom:10px;">{first_name} receives override commissions on deals closed by other agents. This is included in {first_name}'s {fmt_money(round(total_ytd))} YTD total.</div>
      {override_rows}
      <div style="display:flex;justify-content:space-between;align-items:center;padding-top:10px;margin-top:6px;border-top:1px solid var(--border);font-size:13px;">
        <span style="color:var(--sub);">Total override income YTD:</span>
        <span style="font-weight:800;color:var(--green);">{fmt_money(override_total)}</span>
      </div>
      {pending_note}
    </div>"""

    # --- JS VARS ---
    if 'Dan' in agent_name:
        js_vars = f"const GOAL={pac_goal}, CLOSE_GOAL={close_goal};"
    else:
        js_vars = f"const PAC_YTD = {total_ytd:.2f};\nconst PAC_GOAL = {pac_goal};\nconst NUM_CLOSES = {n_closed};\nconst CLOSE_GOAL = {close_goal};"

    # --- CURRENT MONTH (always show the current calendar month) ---
    today_d = date.today()
    cur_yr, cur_mo = today_d.year, today_d.month
    cur_month_name = date(cur_yr, cur_mo, 1).strftime('%B %Y')
    all_dated_closed = [c for c in closed if c['dt']]
    month_closes_rm = [c for c in all_dated_closed if c['dt'].year == cur_yr and c['dt'].month == cur_mo]
    monthly_avg_goal = pac_goal / 12
    if 'Dan' in agent_name:
        month_own_krembo = sum(c['krembo'] for c in month_closes_rm)
        month_team_rm = [o for o in override_list if o['dt'] and o['dt'].year == cur_yr and o['dt'].month == cur_mo]
        month_team_krembo = sum(o['amt'] for o in month_team_rm)
        month_total_rm = month_own_krembo + month_team_krembo
        n_own_rm = len(month_closes_rm)
        n_team_rm = len(month_team_rm)
        krembo_label = "Total Krembo"
    else:
        month_total_rm = sum(c['effective_pac'] for c in month_closes_rm)
        n_own_rm = len(month_closes_rm)
        n_team_rm = 0
        krembo_label = "Total PAC"
    pct_rm = month_total_rm / monthly_avg_goal * 100 if monthly_avg_goal > 0 else 0
    bar_w_rm = min(100, pct_rm)
    bar_c_rm = "var(--green)" if pct_rm >= 100 else ("var(--blue)" if pct_rm >= 50 else "var(--orange)")
    if month_total_rm == 0:
        perf_label_rm = "No closes yet this month"
        bar_c_rm = "var(--border)"
    elif pct_rm >= 150:
        perf_label_rm = f"{pct_rm:.0f}% of monthly avg — great month!"
    elif pct_rm >= 100:
        perf_label_rm = f"{pct_rm:.0f}% of monthly avg ✓"
    else:
        perf_label_rm = f"{pct_rm:.0f}% of monthly avg"
    recent_month_html = (
        f"    <div class=\"section-label\">📅 This Month — {cur_month_name}</div>\n"
        f"    <div class=\"card\">\n"
        f"      <div style=\"display:grid;grid-template-columns:repeat(3,1fr);gap:10px;\">\n"
        f"        <div style=\"text-align:center;\"><div style=\"font-size:20px;font-weight:800;color:var(--accent);\">{fmt_money(round(month_total_rm))}</div>"
        f"<div style=\"font-size:10px;color:var(--sub);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px;\">{krembo_label}</div></div>\n"
        f"        <div style=\"text-align:center;\"><div style=\"font-size:20px;font-weight:800;color:var(--blue);\">{n_own_rm}</div>"
        f"<div style=\"font-size:10px;color:var(--sub);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px;\">Own Closes</div></div>\n"
        f"        <div style=\"text-align:center;\"><div style=\"font-size:20px;font-weight:800;color:var(--teal);\">{n_team_rm}</div>"
        f"<div style=\"font-size:10px;color:var(--sub);font-weight:600;text-transform:uppercase;letter-spacing:.04em;margin-top:2px;\">Team Closes</div></div>\n"
        f"      </div>\n"
        f"      <div style=\"margin-top:12px;padding-top:10px;border-top:1px solid var(--border);\">\n"
        f"        <div class=\"prog-row\"><span>vs. monthly avg goal <strong>{fmt_money(round(monthly_avg_goal))}</strong></span>"
        f"<span style=\"color:{bar_c_rm}\">{perf_label_rm}</span></div>\n"
        f"        <div class=\"prog-bg\"><div class=\"prog-fill\" style=\"width:{bar_w_rm:.0f}%;background:linear-gradient(90deg,{bar_c_rm},{bar_c_rm});\"></div></div>\n"
        f"      </div>\n"
        f"    </div>\n"
    )

    # Monthly breakdowns for Dan JS vars
    monthly_pac = {}
    monthly_closes = {}
    cur_year = date.today().year
    if 'Dan' in agent_name:
        for c in closed:
            if c['dt'] and c['dt'].year == cur_year:
                m = c['dt'].month
                monthly_pac[m] = monthly_pac.get(m, 0) + c['krembo']
                monthly_closes[m] = monthly_closes.get(m, 0) + 1

    data = {
        'alerts': alerts_html,
        'stats': stats_html,
        'income': income_html,
        'goal': goal_html,
        'close_count': close_html,
        'pipeline': pipeline_html,
        'closings': closings_html,
        'override': override_html,
        'recent_month': recent_month_html,
        'js_vars': js_vars,
        'pac_ytd': total_ytd,
        'n_closed': n_closed,
        'monthly_pac': monthly_pac,
        'monthly_closes': monthly_closes,
        'uc': uc,
    }
    return data

SITE_HEADER_CSS = """
  /* ── Site-wide Command Center Header ── */
  .site-header {
    background: linear-gradient(135deg, #080e1f 0%, #14103a 50%, #1e1b4b 100%);
    border-bottom: 1px solid rgba(99,102,241,0.4);
    position: relative;
    overflow: hidden;
  }
  .site-header::before {
    content: '';
    position: absolute;
    inset: 0;
    background: radial-gradient(ellipse 60% 80% at 15% 50%, rgba(88,86,214,0.18) 0%, transparent 70%),
                radial-gradient(ellipse 40% 60% at 85% 50%, rgba(99,102,241,0.12) 0%, transparent 70%);
    pointer-events: none;
  }
  .site-header-inner {
    max-width: 860px; margin: 0 auto; padding: 10px 20px;
    display: flex; align-items: center; justify-content: space-between; gap: 12px;
    position: relative; z-index: 1;
  }
  .site-header-brand { display: flex; align-items: center; gap: 11px; flex-shrink: 0; }
  .site-header-emblem {
    width: 36px; height: 36px;
    background: linear-gradient(135deg, #4f46e5, #7c3aed);
    border-radius: 9px; display: flex; align-items: center; justify-content: center;
    font-size: 18px; flex-shrink: 0; box-shadow: 0 0 12px rgba(99,102,241,0.5);
  }
  .site-header-org { font-size: 13px; font-weight: 800; color: #fff; letter-spacing: 0.03em; line-height: 1.2; }
  .site-header-cmd { font-size: 9px; font-weight: 700; color: #a5b4fc; letter-spacing: 0.14em; text-transform: uppercase; margin-top: 1px; }
  .site-header-divider { width: 1px; height: 32px; background: rgba(165,180,252,0.2); flex-shrink: 0; }
  .site-header-agent { display: flex; align-items: center; gap: 10px; flex: 1; min-width: 0; }
  .site-header-avatar {
    width: 34px; height: 34px; border-radius: 50%;
    background: rgba(255,255,255,0.15);
    border: 1.5px solid rgba(255,255,255,0.25);
    display: flex; align-items: center; justify-content: center;
    font-size: 13px; font-weight: 800; color: white; flex-shrink: 0;
  }
  .site-header-agent-name { font-size: 14px; font-weight: 700; color: #fff; line-height: 1.2; }
  .site-header-agent-role { font-size: 10px; color: rgba(255,255,255,0.55); margin-top: 1px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
  .site-header-right { display: flex; align-items: center; gap: 10px; flex-shrink: 0; }
  .site-header-clock { text-align: right; }
  .site-header-clock .tb-date { color: rgba(255,255,255,0.9); font-size: 12px; font-weight: 600; }
  .site-header-clock .tb-time { color: rgba(255,255,255,0.5); font-size: 11px; }
  .site-header .gear-btn {
    background: rgba(255,255,255,0.1); border: none; border-radius: 8px;
    width: 34px; height: 34px; cursor: pointer;
    display: flex; align-items: center; justify-content: center; transition: background .15s;
  }
  .site-header .gear-btn:hover { background: rgba(255,255,255,0.2); }
  .live-dot { width: 6px; height: 6px; background: #34d399; border-radius: 50%; animation: livepulse 2s ease-in-out infinite; display: inline-block; }
  @keyframes livepulse { 0%,100% { opacity: 1; transform: scale(1); } 50% { opacity: .45; transform: scale(.8); } }
"""

GEAR_SVG = """\
        <svg viewBox="0 0 20 20" fill="none" xmlns="http://www.w3.org/2000/svg" width="18" height="18">
          <line x1="3" y1="5" x2="17" y2="5" stroke="white" stroke-width="1.6" stroke-linecap="round"/>
          <line x1="3" y1="10" x2="17" y2="10" stroke="white" stroke-width="1.6" stroke-linecap="round"/>
          <line x1="3" y1="15" x2="17" y2="15" stroke="white" stroke-width="1.6" stroke-linecap="round"/>
          <circle cx="7" cy="5" r="2" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="1.6"/>
          <circle cx="13" cy="10" r="2" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="1.6"/>
          <circle cx="8" cy="15" r="2" fill="rgba(0,0,0,0.4)" stroke="white" stroke-width="1.6"/>
        </svg>"""

def make_site_header_html(agent_name):
    """Generate the unified Command Center header HTML for a specific agent."""
    meta = AGENT_META.get(agent_name, {"initials": agent_name[:2].upper(), "role": "Agent · Crosstown Realtors"})
    initials = meta["initials"]
    role = meta["role"]
    return f"""<div class="site-header">
  <div class="site-header-inner">
    <div class="site-header-brand">
      <div class="site-header-emblem">🏠</div>
      <div>
        <div class="site-header-org">Crosstown Realtors</div>
        <div class="site-header-cmd">Agent Command Center</div>
      </div>
    </div>
    <div class="site-header-divider"></div>
    <div class="site-header-agent">
      <div class="site-header-avatar">{initials}</div>
      <div style="min-width:0;">
        <div class="site-header-agent-name">{agent_name}</div>
        <div class="site-header-agent-role">{role}</div>
      </div>
    </div>
    <div class="site-header-right">
      <div class="site-header-clock">
        <div class="tb-date" id="js-date"></div>
        <div class="tb-time" id="js-time"></div>
      </div>
      <button class="gear-btn" onclick="openSettings()" title="Customize dashboard">
{GEAR_SVG}
      </button>
    </div>
  </div>
</div>
"""

def patch_html(html_path, agent_name, data):
    with open(html_path) as f:
        html = f.read()

    # ── Unified Command Center header injection / upgrade ──
    site_header_html = make_site_header_html(agent_name)
    if 'site-header' not in html:
        # Fresh file: inject CSS + header before <body> and topbar
        html = html.replace('</style>', SITE_HEADER_CSS + '\n</style>', 1)
        # If old topbar present, place header before it; otherwise place after <body>
        if '<div class="topbar">' in html:
            html = html.replace('<div class="topbar">', site_header_html + '\n<div class="topbar">', 1)
        else:
            html = html.replace('<body>', '<body>\n' + site_header_html, 1)
    elif 'site-header-agent' not in html:
        # Old-style header (brand+leaderboard only) — upgrade to unified header
        # Replace CSS block
        html = re.sub(
            r'/\* ── Site-wide Command Center Header ──.*?@keyframes livepulse \{[^}]*\}',
            SITE_HEADER_CSS.strip(),
            html, count=1, flags=re.DOTALL
        )
        # Replace old site-header div with new unified one
        html = re.sub(
            r'<div class="site-header">.*?</div>\s*\n(?=\s*<div class="topbar">|<div class="settings)',
            site_header_html + '\n',
            html, count=1, flags=re.DOTALL
        )
        # Remove old topbar div entirely
        html = re.sub(r'\n<div class="topbar">.*?(?=\n<div class="settings)', '', html, count=1, flags=re.DOTALL)

    # Update cache-bust comment
    html = re.sub(r'<!-- cache-bust:.*?-->', f'<!-- cache-bust: {TODAY} -->', html)

    def replace_marked(html, marker, new_content):
        """Replace content between AG comment markers — never touches pre-contract section."""
        s = f'<!-- AG:{marker}-start -->'
        e = f'<!-- AG:{marker}-end -->'
        if s not in html or e not in html:
            return html
        return re.sub(
            re.escape(s) + r'(.*?)' + re.escape(e),
            s + '\n' + new_content + '\n  ' + e,
            html, count=1, flags=re.DOTALL
        )

    # Alerts block
    html = replace_marked(html, 'alerts',
        f'  <div style="margin-bottom:18px;">\n    {data["alerts"]}\n  </div>')

    # Stats grid
    html = replace_marked(html, 'stats', data['stats'])

    # Data sections (label + card content, no outer section wrapper)
    html = replace_marked(html, 'income',      data['income'])
    html = replace_marked(html, 'goal',        data['goal'])
    html = replace_marked(html, 'recentmonth', data.get('recent_month', ''))
    html = replace_marked(html, 'closecount',  data['close_count'])
    html = replace_marked(html, 'pipeline',    data['pipeline'])
    html = replace_marked(html, 'closings',    data['closings'])
    if data.get('override'):
        html = replace_marked(html, 'override', data['override'])

    # Update JS variables
    if 'Dan' in agent_name:
        html = re.sub(r'const GOAL=\d+, CLOSE_GOAL=\d+;', data['js_vars'], html)
        html = re.sub(r'const YTD=[\d.]+, OWN_CLOSES=\d+;',
                      f'const YTD={data["pac_ytd"]:.2f}, OWN_CLOSES={data["n_closed"]};', html)
    else:
        html = re.sub(r'const PAC_YTD = [\d.]+;', f'const PAC_YTD = {data["pac_ytd"]:.2f};', html)
        html = re.sub(r'const NUM_CLOSES = \d+;', f'const NUM_CLOSES = {data["n_closed"]};', html)

    # Extra JS vars for Dan (lowercase ytd, monthlyPAC, closesPerMonth)
    if 'Dan' in agent_name:
        html = re.sub(r'const ytd=[\d.]+;', f'const ytd={data["pac_ytd"]:.2f};', html)
        monthly_pac_js = '{' + ','.join(f'{m}:{v:.2f}' for m,v in sorted(data['monthly_pac'].items())) + '}'
        html = re.sub(r'const monthlyPAC=\{[^}]+\};', f'const monthlyPAC={monthly_pac_js};', html)
        monthly_closes_js = '{' + ','.join(f'{m}:{c}' for m,c in sorted(data['monthly_closes'].items())) + '}'
        html = re.sub(r'const closesPerMonth=\{[^}]+\};', f'const closesPerMonth={monthly_closes_js};', html)
        # Inject pipeline data for countdown banner and urgency detection
        pipeline_dk = []
        for deal in sorted(data['uc'], key=lambda x: parse_proj_date(x['proj']) or datetime(2099,1,1)):
            dt = parse_proj_date(deal['proj'])
            pipeline_dk.append({
                'client': deal['client'],
                'addr': deal['addr'],
                'proj': deal['proj'],
                'dt_iso': dt.strftime('%Y-%m-%d') if dt else None,
                'krembo': round(deal['krembo'], 2),
                'price': deal['price']
            })
        pipeline_dk_js = json.dumps(pipeline_dk)
        html = re.sub(r'const PIPELINE_DK=\[.*?\];', f'const PIPELINE_DK={pipeline_dk_js};', html, flags=re.DOTALL)

    with open(html_path, 'w') as f:
        f.write(html)
    print(f"  ✓ Updated {os.path.basename(html_path)}")

def main():
    b64_path = sys.argv[1] if len(sys.argv) > 1 else '/sessions/inspiring-sleepy-wozniak/mnt/outputs/tracker_b64.txt'
    csv_text = load_csv(b64_path)
    agents = parse_tracker(csv_text)
    
    print(f"Dashboard builder running — {TODAY_PRETTY}")
    print(f"Parsed {sum(len(agents[a]['closed']) for a in agents)} closed + "
          f"{sum(len(agents[a]['uc']) for a in agents)} UC deals\n")
    
    for agent_name, html_file in FILES.items():
        html_path = os.path.join(REPO_DIR, html_file)
        if not os.path.exists(html_path):
            print(f"  ⚠ Skipping {html_file} (not found)")
            continue
        d = agents[agent_name]
        data = build_data_section(agent_name, d)
        patch_html(html_path, agent_name, data)
    
    print(f"\nDone! All dashboards updated from tracker data.")

if __name__ == '__main__':
    main()
