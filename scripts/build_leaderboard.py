#!/usr/bin/env python3
"""
Fetches the Transaction Tracker from Google Drive and rebuilds
crosstown-lincolnway-leaderboard.html from the latest data.
"""

import os
import json
import base64
import html
from datetime import datetime
from google.oauth2 import service_account
from googleapiclient.discovery import build

SPREADSHEET_ID = os.environ["SPREADSHEET_ID"]
SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# ── Pull data from Google Sheets ─────────────────────────────────────────────

creds_json = json.loads(os.environ["GOOGLE_CREDENTIALS"])
creds = service_account.Credentials.from_service_account_info(creds_json, scopes=SCOPES)
service = build("sheets", "v4", credentials=creds)

result = service.spreadsheets().values().get(
    spreadsheetId=SPREADSHEET_ID,
    range="A:Z"
).execute()
rows = result.get("values", [])

if not rows:
    print("No data found.")
    exit(1)

headers = [h.strip().lower() for h in rows[0]]
def col(name):
    return headers.index(name) if name in headers else None

col_agent  = col("agent name") or col("agent") or 0
col_status = col("status") or col("transaction status") or None
col_volume = col("sale price") or col("volume") or col("price") or None
col_addr   = col("address") or col("property address") or None

# ── Parse transactions ────────────────────────────────────────────────────────

from collections import defaultdict

agents = defaultdict(lambda: {"closed": 0, "uc": 0, "deals": 0, "sales": []})
top_sales_all = []

for row in rows[1:]:
    if len(row) <= max(filter(None, [col_agent, col_status, col_volume])):
        continue
    agent  = row[col_agent].strip() if col_agent is not None and len(row) > col_agent else ""
    status = row[col_status].strip().lower() if col_status is not None and len(row) > col_status else ""
    try:
        volume = float(str(row[col_volume]).replace("$","").replace(",","")) if col_volume is not None and len(row) > col_volume else 0
    except:
        volume = 0
    addr   = row[col_addr].strip() if col_addr is not None and len(row) > col_addr else ""

    if not agent or volume == 0:
        continue
    if "bust" in status or "cancel" in status or "terminat" in status:
        continue  # exclude busted deals

    if "closed" in status or "close" in status:
        agents[agent]["closed"] += volume
        agents[agent]["deals"] += 1
        agents[agent]["sales"].append({"vol": volume, "status": "Closed", "addr": addr})
        top_sales_all.append({"agent": agent, "vol": volume, "status": "Closed", "addr": addr})
    elif "contract" in status or "pending" in status or "uc" in status:
        agents[agent]["uc"] += volume
        agents[agent]["deals"] += 1
        agents[agent]["sales"].append({"vol": volume, "status": "UC", "addr": addr})
        top_sales_all.append({"agent": agent, "vol": volume, "status": "UC", "addr": addr})

# Sort agents by closed volume desc
sorted_agents = sorted(agents.items(), key=lambda x: x[1]["closed"], reverse=True)
top_sales_all.sort(key=lambda x: x["vol"], reverse=True)
top3_sales = top_sales_all[:3]

total_closed = sum(v["closed"] for _, v in sorted_agents)
total_uc     = sum(v["uc"]     for _, v in sorted_agents)
total_vol    = total_closed + total_uc
total_deals  = sum(v["deals"]  for _, v in sorted_agents)
n_agents     = len(sorted_agents)
max_closed   = sorted_agents[0][1]["closed"] if sorted_agents else 1

updated = datetime.utcnow().strftime("%B %d, %Y at %I:%M %p UTC")

# ── HTML helpers ──────────────────────────────────────────────────────────────

def fmt(n):
    return f"${n:,.0f}"

COLORS = ["#FFD700","#C0C0C0","#CD7F32","#6C63FF","#00D4AA","#FF6B9D","#FF8C42","#4ECDC4"]
MEDAL  = ["🥇","🥈","🥉"]

podium_agents = sorted_agents[:3]
top3_rows = ""
for i, (name, d) in enumerate(podium_agents):
    medal = MEDAL[i]
    color = COLORS[i]
    top3_rows += f"""
        <div class="podium-card" style="border-color:{color}">
          <div class="podium-rank" style="color:{color}">{medal} #{i+1}</div>
          <div class="podium-name">{html.escape(name)}</div>
          <div class="podium-vol" style="color:{color}">{fmt(d['closed'])}</div>
          <div class="podium-label">Closed Volume</div>
        </div>"""

agent_rows = ""
for i, (name, d) in enumerate(sorted_agents):
    rank = i + 1
    color = COLORS[min(i, len(COLORS)-1)]
    border = f"border-left: 4px solid {COLORS[min(i,2)]};" if i < 3 else ""
    pct = int(d["closed"] / max_closed * 100) if max_closed else 0
    uc_str  = f"<span class='uc-badge'>+ {fmt(d['uc'])} UC</span>" if d["uc"] > 0 else ""
    agent_rows += f"""
        <div class="agent-row" style="{border}">
          <div class="agent-rank" style="color:{color}">#{rank}</div>
          <div class="agent-info">
            <div class="agent-name">{html.escape(name)}</div>
            <div class="agent-stats">
              <span class="closed-vol">{fmt(d['closed'])} closed</span>
              {uc_str}
              <span class="deal-count">{d['deals']} deal{'s' if d['deals']!=1 else ''}</span>
            </div>
            <div class="progress-bar"><div class="progress-fill" style="width:{pct}%;background:{color}"></div></div>
          </div>
        </div>"""

sale_rows = ""
for i, s in enumerate(top3_sales):
    color = COLORS[i]
    label = s["status"]
    addr_str = f"<div class='sale-addr'>{html.escape(s['addr'])}</div>" if s["addr"] else ""
    sale_rows += f"""
        <div class="sale-card" style="border-color:{color}">
          <div class="sale-rank" style="color:{color}">{MEDAL[i]}</div>
          <div class="sale-agent">{html.escape(s['agent'])}</div>
          {addr_str}
          <div class="sale-vol" style="color:{color}">{fmt(s['vol'])}</div>
          <div class="sale-status">{label}</div>
        </div>"""

# ── Write HTML ────────────────────────────────────────────────────────────────

html_out = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Crosstown-Lincolnway Sales Leaderboard</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;600;700;800;900&display=swap" rel="stylesheet">
<style>
  :root{{--bg:#0f0f1a;--card:#1a1a2e;--card2:#16213e;--text:#e0e0ff;--muted:#888aaa;--gold:#FFD700;--silver:#C0C0C0;--bronze:#CD7F32}}
  *{{box-sizing:border-box;margin:0;padding:0}}
  body{{background:var(--bg);color:var(--text);font-family:'Inter',sans-serif;min-height:100vh}}
  .header{{background:linear-gradient(135deg,#1a1a3e 0%,#2d1b69 50%,#11998e 100%);padding:40px 20px;text-align:center}}
  .header h1{{font-size:clamp(1.8rem,4vw,3rem);font-weight:900;letter-spacing:-1px;background:linear-gradient(90deg,#fff,#a78bfa,#34d399);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .header p{{color:#aab;margin-top:8px;font-size:.95rem}}
  .updated{{color:#666;font-size:.8rem;margin-top:6px}}
  .container{{max-width:900px;margin:0 auto;padding:24px 16px}}
  .banner{{background:linear-gradient(135deg,#1a1a3e,#2d1b69);border-radius:16px;padding:28px;margin-bottom:24px;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:16px;text-align:center}}
  .banner-item .val{{font-size:1.6rem;font-weight:800;background:linear-gradient(90deg,#a78bfa,#34d399);-webkit-background-clip:text;-webkit-text-fill-color:transparent}}
  .banner-item .lbl{{color:var(--muted);font-size:.8rem;margin-top:4px}}
  .section-title{{font-size:1.1rem;font-weight:700;color:#a78bfa;text-transform:uppercase;letter-spacing:2px;margin:28px 0 14px}}
  .podium{{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));gap:14px;margin-bottom:24px}}
  .podium-card{{background:var(--card);border:2px solid;border-radius:14px;padding:20px;text-align:center}}
  .podium-rank{{font-size:1.4rem;font-weight:800;margin-bottom:6px}}
  .podium-name{{font-weight:700;font-size:1rem;margin-bottom:8px}}
  .podium-vol{{font-size:1.5rem;font-weight:800}}
  .podium-label{{color:var(--muted);font-size:.75rem;margin-top:4px}}
  .agent-row{{background:var(--card);border-radius:12px;padding:16px;margin-bottom:10px;display:flex;align-items:center;gap:16px}}
  .agent-rank{{font-size:1.3rem;font-weight:800;min-width:36px;text-align:center}}
  .agent-info{{flex:1}}
  .agent-name{{font-weight:700;font-size:1rem;margin-bottom:4px}}
  .agent-stats{{display:flex;flex-wrap:wrap;gap:10px;font-size:.82rem;margin-bottom:8px}}
  .closed-vol{{color:#34d399;font-weight:600}}
  .uc-badge{{background:#2d1b69;color:#a78bfa;padding:2px 8px;border-radius:20px;font-weight:600}}
  .deal-count{{color:var(--muted)}}
  .new-badge{{background:#1e3a2f;color:#34d399;padding:2px 8px;border-radius:20px;font-size:.75rem;font-weight:600}}
  .progress-bar{{background:#1a1a2e;border-radius:4px;height:6px;overflow:hidden}}
  .progress-fill{{height:100%;border-radius:4px;transition:width .5s ease}}
  .sales-grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:14px}}
  .sale-card{{background:var(--card);border:2px solid;border-radius:14px;padding:18px;text-align:center}}
  .sale-rank{{font-size:1.6rem;margin-bottom:6px}}
  .sale-agent{{font-weight:700;margin-bottom:4px}}
  .sale-addr{{color:var(--muted);font-size:.8rem;margin-bottom:8px}}
  .sale-vol{{font-size:1.4rem;font-weight:800;margin-bottom:4px}}
  .sale-status{{font-size:.78rem;color:var(--muted);background:#1a1a2e;display:inline-block;padding:2px 10px;border-radius:20px}}
  footer{{text-align:center;color:var(--muted);font-size:.78rem;padding:30px 16px}}
</style>
</head>
<body>
<div class="header">
  <h1>🏆 Crosstown-Lincolnway</h1>
  <p>Sales Leaderboard · {datetime.utcnow().year}</p>
  <div class="updated">Last updated: {updated}</div>
</div>
<div class="container">
  <div class="banner">
    <div class="banner-item"><div class="val">{fmt(total_vol)}</div><div class="lbl">Total Volume</div></div>
    <div class="banner-item"><div class="val">{fmt(total_closed)}</div><div class="lbl">Closed Volume</div></div>
    <div class="banner-item"><div class="val">{fmt(total_uc)}</div><div class="lbl">Under Contract</div></div>
    <div class="banner-item"><div class="val">{total_deals}</div><div class="lbl">Total Deals</div></div>
    <div class="banner-item"><div class="val">{n_agents}</div><div class="lbl">Agents</div></div>
  </div>

  <div class="section-title">🥇 Top 3 Closed Volume</div>
  <div class="podium">{podium_rows}</div>

  <div class="section-title">📊 Full Leaderboard</div>
  {agent_rows}

  <div class="section-title">💎 Top 3 Individual Sales</div>
  <div class="sales-grid">{sale_rows}</div>
</div>
<footer>Crosstown-Lincolnway · Auto-updated daily from Transaction Tracker · {datetime.utcnow().year}</footer>
</body>
</html>
"""

# Fix the podium_rows reference
html_out = html_out.replace("{podium_rows}", top3_rows)

with open("crosstown-lincolnway-leaderboard.html", "w") as f:
    f.write(html_out)

print(f"Built leaderboard: {n_agents} agents, {fmt(total_vol)} total volume")
