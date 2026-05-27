#!/usr/bin/env python3
"""Sports Log web server."""

import html
import json
import os
from datetime import datetime
from http.server import BaseHTTPRequestHandler
from socketserver import TCPServer

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "dashboard.json")
PORT = int(os.environ.get("PORT", "18081"))
HOST = os.environ.get("HOST", "127.0.0.1")
BASE_PATH = os.environ.get("BASE_PATH", "").rstrip("/")


def esc(value):
    return html.escape("" if value is None else str(value), quote=True)


def fmt(value, suffix=""):
    if value is None or value == "":
        return "—"
    return "%s%s" % (value, suffix)


def load_data():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def latest(data):
    rows = data.get("daily", [])
    return rows[-1] if rows else {}


def stat_card(label, value, sub=""):
    return (
        '<section class="stat">'
        '<span>%s</span><strong>%s</strong><small>%s</small>'
        "</section>"
    ) % (esc(label), esc(value), esc(sub))


def bars(rows, key, label, unit="", scale=None):
    values = [r.get(key) or 0 for r in rows]
    max_value = scale or max(values or [1]) or 1
    items = []
    for row, value in zip(rows, values):
        h = max(4, min(100, value / max_value * 100))
        items.append(
            '<div class="bar" title="%s %s%s"><i style="height:%.1f%%"></i><b>%s</b></div>'
            % (esc(row.get("date", "")[5:]), esc(value), esc(unit), h, esc(row.get("date", "")[8:]))
        )
    return '<div class="chart"><h3>%s</h3><div class="bars">%s</div></div>' % (esc(label), "".join(items))


def line_points(rows, key):
    vals = [r.get(key) for r in rows if isinstance(r.get(key), (int, float))]
    if not vals:
        return ""
    low, high = min(vals), max(vals)
    span = high - low or 1
    pts = []
    for idx, row in enumerate(rows):
        value = row.get(key)
        if not isinstance(value, (int, float)):
            continue
        x = 10 + idx * (380 / max(1, len(rows) - 1))
        y = 110 - ((value - low) / span) * 90
        pts.append("%.1f,%.1f" % (x, y))
    return " ".join(pts)


def trend_svg(rows, key, title, color):
    pts = line_points(rows, key)
    return (
        '<div class="chart"><h3>%s</h3>'
        '<svg viewBox="0 0 400 130" role="img">'
        '<line x1="10" y1="110" x2="390" y2="110" />'
        '<polyline points="%s" style="stroke:%s" />'
        '</svg></div>'
    ) % (esc(title), esc(pts), esc(color))


def summary_table(title, rows):
    trs = []
    for row in rows:
        trs.append(
            "<tr><td>%s</td><td>%s</td><td>%s km</td><td>%s min</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                esc(row.get("key")),
                esc(row.get("activities")),
                esc(row.get("distance_km")),
                esc(row.get("exercise_min")),
                esc(row.get("avg_sleep_score")),
                esc(row.get("load_state")),
                esc(row.get("note")),
            )
        )
    return (
        '<section class="panel wide"><h2>%s</h2><div class="table-wrap">'
        '<table><thead><tr><th>周期</th><th>运动</th><th>距离</th><th>活动分钟</th>'
        '<th>睡眠分</th><th>负荷</th><th>解读</th></tr></thead><tbody>%s</tbody></table>'
        "</div></section>"
    ) % (esc(title), "".join(trs))


def activity_table(rows):
    trs = []
    for act in rows[:18]:
        trs.append(
            "<tr><td>%s</td><td>%s</td><td>%s</td><td>%s km</td><td>%s</td><td>%s</td><td>%s</td></tr>"
            % (
                esc(act.get("date")),
                esc(act.get("sport")),
                esc(act.get("location")),
                esc(act.get("distance_km")),
                esc(act.get("pace")),
                esc(act.get("avg_hr")),
                esc(act.get("label_id")),
            )
        )
    return (
        '<section class="panel wide"><h2>最近运动记录</h2><div class="table-wrap">'
        '<table><thead><tr><th>日期</th><th>类型</th><th>地点/课程</th><th>距离</th>'
        '<th>配速</th><th>均心</th><th>LabelId</th></tr></thead><tbody>%s</tbody></table>'
        "</div></section>"
    ) % "".join(trs)


def workout_panel(rows):
    items = []
    for row in rows[:8]:
        items.append(
            '<li><strong>%s</strong><span>%s · %s steps · %s min</span></li>'
            % (
                esc(row.get("name")),
                esc(row.get("sport_name")),
                esc(row.get("exercise_count")),
                esc(round((row.get("estimated_time_seconds") or 0) / 60, 1)),
            )
        )
    if not items:
        items.append("<li><span>暂无结构化训练</span></li>")
    return '<section class="panel"><h2>训练库</h2><ul class="list">%s</ul></section>' % "".join(items)


def cache_panel(data):
    cache = data.get("coros_cache", {})
    auth = data.get("meta", {}).get("coros_auth", {})
    rows = []
    for key, label in [
        ("daily_records", "Daily"),
        ("sleep_records", "Sleep"),
        ("activities", "Activities"),
    ]:
        item = cache.get(key, {})
        rows.append(
            '<li><strong>%s</strong><span>%s 条 · %s → %s</span></li>'
            % (esc(label), esc(item.get("count", 0)), esc(item.get("from") or "—"), esc(item.get("to") or "—"))
        )
    rows.append(
        '<li><strong>Auth</strong><span>%s · mobile %s · token %.1fh</span></li>'
        % (
            esc(auth.get("region") or "—"),
            esc("ok" if auth.get("mobile_authenticated") else "missing"),
            float(auth.get("expires_in_hours") or 0),
        )
    )
    return '<section class="panel"><h2>COROS MCP</h2><ul class="list">%s</ul></section>' % "".join(rows)


def build_home_legacy():
    data = load_data()
    payload = {
        "meta": data.get("meta", {}),
        "daily": data.get("daily", []),
        "activities": data.get("activities", []),
        "summaries": data.get("summaries", {}),
        "recovery": data.get("recovery", {}),
        "fitness": data.get("fitness", {}),
        "schedule": data.get("schedule", []),
        "workouts": data.get("workouts", []),
    }
    json_blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_doc = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sports Log</title>
<style>
:root{color-scheme:light;--ink:#101615;--muted:#5d6b65;--faint:#899790;--paper:#f8f9f5;--panel:#fffefa;--panel2:#f6f8f4;--line:#dfe7df;--line2:#edf2eb;--green:#127a5a;--green2:#7cbea0;--coral:#cf6047;--gold:#aa7722;--charcoal:#1a211f;--shadow:0 18px 54px rgba(24,36,31,.08);--font:Geist,Satoshi,"Cabinet Grotesk",Outfit,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--mono:"JetBrains Mono","SFMono-Regular",ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:linear-gradient(180deg,#fbfcf8 0,#f1f5ee 48%,#f7f4ee 100%);color:var(--ink);font-family:var(--font);letter-spacing:0;font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
button{font:inherit}.shell{max-width:1320px;margin:0 auto;padding:18px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.brand{display:flex;align-items:center;gap:10px}.mark{width:34px;height:34px;border-radius:50%;background:conic-gradient(from 210deg,var(--green),#d2ded4,var(--gold),var(--coral),var(--green));box-shadow:inset 0 0 0 7px #fff9}.brand h1{font-size:18px;line-height:1.1;margin:0;font-weight:780;letter-spacing:-.02em}.brand span{font-size:11px;color:var(--faint);font-family:var(--mono)}.seg,.tabs{display:flex;background:rgba(255,254,250,.92);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px;box-shadow:0 8px 28px rgba(20,28,24,.05)}.seg button,.tabs button{border:0;background:transparent;border-radius:7px;padding:6px 9px;color:var(--muted);cursor:pointer;font-size:12px;font-weight:680;min-width:42px;transition:transform .18s cubic-bezier(.16,1,.3,1),background .18s}.seg button:active,.tabs button:active{transform:scale(.97)}.seg button.active,.tabs button.active{background:var(--ink);color:white}
.hero{display:grid;grid-template-columns:minmax(0,1.36fr) minmax(316px,.64fr);gap:12px;margin-bottom:12px}.stage{position:relative;min-height:330px;border:1px solid var(--line);border-radius:10px;background:linear-gradient(135deg,#fffef8 0,#e9f6f0 50%,#f2eee1 100%);overflow:hidden;padding:20px;box-shadow:var(--shadow)}.stage:after{content:"";position:absolute;inset:auto 0 0 0;height:38%;background:linear-gradient(180deg,transparent,rgba(255,255,255,.54))}.stage canvas{position:absolute;inset:0;width:100%;height:100%}.hero-copy{position:relative;z-index:1;display:flex;flex-direction:column;height:100%;justify-content:space-between}.kicker{display:flex;gap:7px;flex-wrap:wrap}.chip{display:inline-flex;align-items:center;border:1px solid rgba(255,255,255,.74);background:rgba(255,255,255,.66);backdrop-filter:blur(8px);border-radius:999px;padding:6px 9px;font-size:11px;font-weight:650;color:#40504a}.headline{margin-top:58px}.headline strong{display:block;font-size:clamp(46px,7vw,76px);font-weight:800;letter-spacing:-.055em;line-height:.88}.headline span{display:block;color:var(--muted);margin-top:10px;font-size:13px;font-weight:560}.side{display:grid;gap:12px}.ring-card,.panel,.metric{border:1px solid var(--line);border-radius:10px;background:rgba(255,254,250,.9);box-shadow:var(--shadow)}.ring-card{padding:18px;display:grid;grid-template-columns:132px 1fr;gap:14px;align-items:center}.ring{position:relative;width:128px;height:128px}.ring svg{width:128px;height:128px;transform:rotate(-90deg)}.ring circle{fill:none;stroke-width:12;stroke-linecap:round}.ring .bg{stroke:#e7ece4}.ring .fg{stroke:var(--green);stroke-dasharray:0 999}.ring b{position:absolute;inset:0;display:grid;place-items:center;font-size:30px;font-weight:780;letter-spacing:-.035em}.ring-card h2{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--faint);font-weight:740}.ring-card .big{font-size:25px;line-height:1.08;font-weight:780;letter-spacing:-.03em}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}.metric{padding:14px;min-height:116px;position:relative;overflow:hidden;animation:rise .52s cubic-bezier(.16,1,.3,1) both;animation-delay:calc(var(--i,0)*55ms)}.metric:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent,var(--green))}.metric span,.panel h2 small{display:block;color:var(--faint);font-size:11px;font-weight:650}.metric b{display:block;font-size:26px;line-height:1.04;font-weight:790;letter-spacing:-.04em;margin:8px 0 6px;font-family:var(--mono)}.metric em{font-style:normal;color:var(--delta,var(--muted));font-size:11px;font-weight:740}.spark{width:100%;height:30px;margin-top:5px}.decision{display:grid;grid-template-columns:1.2fr .8fr;gap:12px;margin-bottom:12px}.coach{display:grid;grid-template-columns:minmax(220px,.8fr) 1.2fr;gap:12px;align-items:stretch}.coach-score{border-right:1px solid var(--line);padding-right:14px}.coach-score strong{display:block;font-size:56px;line-height:.88;letter-spacing:-.06em;font-family:var(--mono)}.coach-score span{color:var(--muted);font-size:12px;font-weight:650}.coach-list{display:grid;gap:8px}.coach-item{border-top:1px solid var(--line2);padding-top:8px}.coach-item b{display:block;font-size:13px}.coach-item p{margin:2px 0 0;color:var(--muted);font-size:12px}.focus-grid{display:grid;grid-template-columns:repeat(4,1fr);gap:8px;margin-top:10px}.focus-grid div{border:1px solid var(--line2);background:var(--panel2);border-radius:9px;padding:10px}.focus-grid span{display:block;color:var(--faint);font-size:11px}.focus-grid b{display:block;font-size:16px;margin-top:3px;font-family:var(--mono)}.grid{display:grid;grid-template-columns:1.24fr .76fr;gap:12px}.panel{padding:16px;min-width:0}.panel h2{display:flex;align-items:flex-end;justify-content:space-between;gap:6px;margin:0;font-size:14px;font-weight:780;letter-spacing:-.01em}.panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px}.legend{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--faint);font-size:11px;font-weight:650}.legend i{display:inline-block;width:16px;height:4px;border-radius:999px;margin-right:5px;vertical-align:2px}.chart-note{display:flex;gap:8px;flex-wrap:wrap;margin:-2px 0 10px}.pill{border:1px solid var(--line2);background:var(--panel2);border-radius:999px;padding:5px 8px;color:var(--muted);font-size:11px;font-weight:690}.canvas-wrap{height:318px;position:relative}.canvas-wrap.tall{height:374px}.canvas-wrap.short{height:246px}.canvas-wrap canvas{width:100%;height:100%;display:block}.chart-tip{position:absolute;pointer-events:none;z-index:4;min-width:158px;border:1px solid rgba(16,22,21,.12);background:rgba(17,24,22,.94);color:white;border-radius:9px;padding:8px 9px;font-size:11px;line-height:1.45;box-shadow:0 16px 38px rgba(0,0,0,.20);transform:translate(-50%,-112%);opacity:0;transition:opacity .12s ease}.chart-tip b{display:block;font-size:12px;margin-bottom:3px}.chart-tip span{display:flex;justify-content:space-between;gap:18px;color:#d8e0dc}.chart-tip em{font-style:normal;color:#aab8b2}.chart-tip strong{font-weight:760;color:white}.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.tile{border:1px solid var(--line2);border-radius:9px;background:var(--panel2);padding:11px;min-width:0}.tile b{display:block;font-size:18px;line-height:1.1;font-weight:760;letter-spacing:-.025em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile span,.summary span,.pred span{display:block;color:var(--faint);font-size:11px;font-weight:650}.heat{display:grid;grid-template-columns:repeat(19,1fr);gap:4px}.cell{aspect-ratio:1;border-radius:4px;background:#e7ece4;position:relative}.cell[data-lvl="1"]{background:#cce7d5}.cell[data-lvl="2"]{background:#85c99e}.cell[data-lvl="3"]{background:#2e8f68}.cell[data-lvl="4"]{background:#0f6047}.activity-list{display:grid;gap:8px;max-height:478px;overflow:auto;padding-right:2px;content-visibility:auto}.activity{display:grid;grid-template-columns:50px 1fr auto;gap:10px;align-items:center;border:1px solid var(--line2);background:#fffdfa;border-radius:9px;padding:9px;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),border-color .18s,background .18s}.activity:hover{border-color:#bdcac1;background:#fbfffb;transform:translateY(-1px)}.activity:active{transform:scale(.99)}.badge{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;background:#e8f5ef;color:#126046;font-size:14px;font-weight:780;font-family:var(--mono)}.activity h3{margin:0;font-size:13px;line-height:1.25;font-weight:720;letter-spacing:-.01em}.activity p{margin:3px 0 0;color:var(--faint);font-size:11px}.activity .num{text-align:right;font-size:12px;font-weight:780;font-family:var(--mono)}.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.filters button{border:1px solid var(--line);background:#fffdfa;border-radius:999px;padding:5px 9px;color:var(--muted);cursor:pointer;font-size:11px;font-weight:660}.filters button.active{background:var(--ink);color:white;border-color:var(--ink)}
.wide{grid-column:1/-1}.summary-row{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.summary{border:1px solid var(--line2);border-radius:9px;background:#fffdfa;padding:12px;min-height:104px}.summary b{display:block;font-size:21px;line-height:1.1;font-weight:780;letter-spacing:-.03em;margin:5px 0}.progress{height:7px;background:#e8eee6;border-radius:999px;overflow:hidden;margin-top:10px}.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--gold));width:0}.heat-summary{display:grid;grid-template-columns:repeat(2,1fr);gap:8px;margin-top:14px}.heat-summary div{border:1px solid var(--line2);background:var(--panel2);border-radius:9px;padding:12px}.heat-summary span{display:block;color:var(--faint);font-size:11px;font-weight:650}.heat-summary b{display:block;margin-top:4px;font-size:18px;font-family:var(--mono)}.preds{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.pred{border:1px solid var(--line2);border-radius:9px;padding:11px;background:var(--panel2)}.pred b{display:block;font-size:19px;font-weight:780;letter-spacing:-.025em}.muted{color:var(--muted);font-size:12px}.detail-drawer{position:fixed;right:18px;bottom:18px;width:min(420px,calc(100% - 36px));background:#101615;color:white;border-radius:10px;padding:18px;box-shadow:0 24px 80px #0007;transform:translateY(130%);transition:.25s ease;z-index:5}.detail-drawer.show{transform:translateY(0)}.detail-drawer button{position:absolute;right:12px;top:10px;border:0;background:#ffffff18;color:white;border-radius:50%;width:30px;height:30px;cursor:pointer}.detail-drawer h2{margin:0 36px 12px 0;font-size:18px;letter-spacing:-.02em}.detail-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.detail-stats div{background:#ffffff12;border-radius:8px;padding:10px}.detail-stats span{display:block;color:#b9c6c0;font-size:11px}.detail-stats b{font-size:15px}.empty{color:var(--muted);padding:18px;font-size:12px}
@keyframes rise{from{opacity:0;transform:translateY(10px)}to{opacity:1;transform:none}}@media (prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}
@media (max-width:980px){.hero,.grid,.decision,.coach{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.summary-row,.preds{grid-template-columns:repeat(2,1fr)}.coach-score{border-right:0;border-bottom:1px solid var(--line);padding-right:0;padding-bottom:12px}}
@media (max-width:560px){.shell{padding:12px}.top{align-items:flex-start;gap:10px}.seg,.tabs{overflow:auto;max-width:100%}.seg button,.tabs button{min-width:38px;padding:6px 8px}.hero{gap:10px}.stage{min-height:286px}.headline{margin-top:42px}.ring-card{grid-template-columns:1fr}.metrics,.summary-row,.preds,.focus-grid{grid-template-columns:1fr}.activity{grid-template-columns:44px 1fr}.activity .num{grid-column:2;text-align:left}.heat{grid-template-columns:repeat(10,1fr)}.panel-head{display:block}.legend,.tabs{margin-top:7px}.canvas-wrap{height:280px}}
</style>
</head>
<body>
<main class="shell">
<div class="top">
  <div class="brand"><div class="mark"></div><div><h1>Sports Log</h1><span id="updated">--</span></div></div>
  <div class="seg" aria-label="range"><button data-range="1">1D</button><button data-range="7">7D</button><button data-range="30" class="active">30D</button><button data-range="60">60D</button><button data-range="all">ALL</button></div>
</div>
<section class="hero">
  <div class="stage">
    <canvas id="heroCanvas"></canvas>
    <div class="hero-copy">
      <div class="kicker"><span class="chip" id="windowChip">--</span><span class="chip" id="loadChip">--</span><span class="chip" id="raceChip">--</span></div>
      <div class="headline"><strong id="heroDistance">--</strong><span id="heroSub">--</span></div>
    </div>
  </div>
  <aside class="side">
    <section class="ring-card">
      <div class="ring"><svg viewBox="0 0 160 160"><circle class="bg" cx="80" cy="80" r="62"></circle><circle class="fg" id="recoveryRing" cx="80" cy="80" r="62"></circle></svg><b id="recoveryText">--</b></div>
      <div><h2>Recovery</h2><div class="big" id="recoveryLevel">--</div><span class="muted" id="recoveryTime">--</span></div>
    </section>
    <section class="ring-card">
      <div class="ring"><svg viewBox="0 0 160 160"><circle class="bg" cx="80" cy="80" r="62"></circle><circle class="fg" id="vo2Ring" cx="80" cy="80" r="62"></circle></svg><b id="vo2Text">--</b></div>
      <div><h2>Running</h2><div class="big" id="thresholdText">--</div><span class="muted">VO2max / Threshold</span></div>
    </section>
  </aside>
</section>
<section class="metrics" id="metrics"></section>
<section class="decision">
  <section class="panel coach">
    <div class="coach-score"><span>TRAINING DECISION</span><strong id="coachScore">--</strong><span id="coachLabel">--</span></div>
    <div><div class="panel-head"><h2>Coach Brief</h2><div class="legend"><span><i style="background:#127a5a"></i>ready</span><span><i style="background:#cf6047"></i>watch</span></div></div><div class="coach-list" id="coachBrief"></div></div>
  </section>
  <section class="panel">
    <div class="panel-head"><h2>Day Focus <small id="focusDate">latest</small></h2><div class="tabs" id="focusTabs"><button data-focus="latest" class="active">Latest</button><button data-focus="hard">Hardest</button><button data-focus="best">Best HRV</button></div></div>
    <div class="focus-grid" id="focusGrid"></div>
  </section>
</section>
<section class="grid">
  <section class="panel">
    <div class="panel-head"><h2>Training Load <small id="rangeLabel"></small></h2><div class="legend"><span><i style="background:linear-gradient(90deg,#127a5a,#7cbea0)"></i>km</span><span><i style="background:#cf6047"></i>load</span><span><i style="background:#899790"></i>7d avg</span></div></div>
    <div class="chart-note" id="loadInsight"></div><div class="canvas-wrap tall"><canvas id="distanceChart"></canvas></div>
  </section>
  <section class="panel"><h2>Recent Activities <small id="activityCount"></small></h2><div class="filters" id="filters"></div><div class="activity-list" id="activityList"></div></section>
  <section class="panel">
    <div class="panel-head"><h2>Recovery Signals</h2><div class="legend"><span><i style="background:#127a5a"></i>HRV</span><span><i style="background:#cf6047"></i>RHR</span><span><i style="background:#aa7722"></i>load</span></div></div>
    <div class="chart-note" id="healthInsight"></div><div class="canvas-wrap"><canvas id="healthChart"></canvas></div>
  </section>
  <section class="panel"><h2>Sleep Architecture</h2><div class="chart-note" id="sleepInsight"></div><div class="canvas-wrap"><canvas id="sleepChart"></canvas></div></section>
  <section class="panel"><h2>Run Heat</h2><div class="heat" id="heatmap"></div><div class="heat-summary" id="heatSummary"></div></section>
  <section class="panel"><h2>Weekly Progress</h2><div class="canvas-wrap short"><canvas id="weekChart"></canvas></div></section>
  <section class="panel wide"><h2>Weeks</h2><div class="summary-row" id="weeklyCards"></div></section>
  <section class="panel"><h2>Predictions</h2><div class="preds" id="preds"></div></section>
  <section class="panel"><h2>Next Up</h2><div class="trio" id="nextUp"></div></section>
</section>
</main>
<aside class="detail-drawer" id="drawer"><button id="closeDrawer">×</button><h2 id="drawerTitle">Activity</h2><div class="detail-stats" id="drawerStats"></div></aside>
<script type="application/json" id="payload">__DATA__</script>
<script>
const DATA = JSON.parse(document.getElementById('payload').textContent);
let range = 30;
let sportFilter = 'All';
let focusMode = 'latest';
const $ = (q) => document.querySelector(q);
const fmt = (v, suffix='') => (v === null || v === undefined || v === '') ? '--' : `${v}${suffix}`;
const sum = (arr, fn) => arr.reduce((a, x) => a + (+fn(x) || 0), 0);
const avg = (arr, fn) => {
  const vals = arr.map(fn).filter(v => Number.isFinite(+v));
  return vals.length ? vals.reduce((a,b)=>a + +b, 0) / vals.length : null;
};
const last = (arr) => arr[arr.length - 1] || {};
const COLORS = {ink:'#101615', muted:'#5d6b65', faint:'#899790', grid:'#e3e9e0', green:'#127a5a', green2:'#7cbea0', coral:'#cf6047', gold:'#aa7722', soft:'#edf2eb'};
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
const activityByDate = new Map();
DATA.activities.forEach(a => {
  const item = activityByDate.get(a.date) || {km:0, min:0, load:0, count:0, acts:[]};
  item.km += +(a.distance_km || 0); item.min += +(a.duration_min || 0); item.load += +(a.training_load || 0); item.count += 1; item.acts.push(a);
  activityByDate.set(a.date, item);
});
function rows(){ return range === 'all' ? DATA.daily : DATA.daily.slice(-Number(range)); }
function acts(){ const start = rows()[0]?.date || ''; return DATA.activities.filter(a => (!start || a.date >= start) && (sportFilter === 'All' || a.sport === sportFilter)); }
function resizeCanvas(canvas){
  const dpr = window.devicePixelRatio || 1;
  const rect = canvas.getBoundingClientRect();
  canvas.width = Math.max(1, Math.floor(rect.width * dpr));
  canvas.height = Math.max(1, Math.floor(rect.height * dpr));
  const ctx = canvas.getContext('2d');
  ctx.setTransform(dpr,0,0,dpr,0,0);
  return {ctx,w:rect.width,h:rect.height};
}
function dailyWithKm(sourceRows=rows()){
  return sourceRows.map(d => {
    const activity = activityByDate.get(d.date) || {};
    return {...d, km: activity.km || 0, activity_min: activity.min || 0, activity_load: activity.load || 0, activity_count: activity.count || 0, acts: activity.acts || []};
  });
}
function niceMax(value){
  if(!value || value <= 0) return 1;
  const p = Math.pow(10, Math.floor(Math.log10(value)));
  const n = value / p;
  return (n <= 2 ? 2 : n <= 5 ? 5 : 10) * p;
}
function movingAvg(arr, key, window=7){
  return arr.map((_, i) => avg(arr.slice(Math.max(0, i-window+1), i+1), x => +x[key]));
}
function pctChange(now, prev){
  if(!Number.isFinite(now) || !Number.isFinite(prev) || prev === 0) return null;
  return ((now - prev) / prev) * 100;
}
function compareByWindow(key, sourceRows=DATA.daily){
  const n = range === 'all' ? Math.min(30, sourceRows.length) : Number(range);
  const current = sourceRows.slice(-n);
  const prev = sourceRows.slice(Math.max(0, sourceRows.length - n*2), Math.max(0, sourceRows.length - n));
  const now = avg(current, x => +x[key]);
  const before = avg(prev, x => +x[key]);
  return pctChange(now, before);
}
function trendText(value, goodUp=true){
  if(value === null) return 'baseline --';
  const abs = Math.abs(value);
  const sign = value >= 0 ? '+' : '-';
  const mood = abs < 3 ? 'steady' : (value > 0) === goodUp ? 'better' : 'watch';
  return `${sign}${abs.toFixed(0)}% ${mood}`;
}
function statusLabel(day){
  const rec = DATA.recovery.recovery_percent || 0;
  const load = +(day.load_ratio || 0);
  if(rec >= 70 && load <= 1.15) return ['Build', 'Recovery supports aerobic work.'];
  if(load > 1.25) return ['Hold', 'Load is elevated; keep intensity controlled.'];
  if(rec < 55) return ['Absorb', 'Recovery is low; bias easy volume or rest.'];
  return ['Maintain', 'Good day for steady training.'];
}
function readinessScore(dayRows){
  const day = last(dayRows), rec = DATA.recovery.recovery_percent || 0;
  const hrv = day.hrv && day.hrv_baseline ? clamp((day.hrv / day.hrv_baseline) * 100, 55, 125) : 82;
  const load = +(day.load_ratio || 1);
  const sleep = day.sleep_min ? clamp(day.sleep_min / 480 * 100, 45, 112) : 78;
  return Math.round(clamp(rec * .42 + hrv * .26 + sleep * .20 + (load <= 1.15 ? 88 : load < 1.35 ? 66 : 48) * .12, 0, 100));
}
function drawRoundRect(ctx,x,y,w,h,r){
  const rr=Math.min(r, Math.abs(w)/2, Math.abs(h)/2);
  ctx.beginPath(); ctx.moveTo(x+rr,y); ctx.arcTo(x+w,y,x+w,y+h,rr); ctx.arcTo(x+w,y+h,x,y+h,rr); ctx.arcTo(x,y+h,x,y,rr); ctx.arcTo(x,y,x+w,y,rr); ctx.closePath();
}
function chartTooltip(canvas){
  let tip = canvas.parentElement.querySelector('.chart-tip');
  if(!tip){ tip = document.createElement('div'); tip.className = 'chart-tip'; canvas.parentElement.appendChild(tip); }
  return tip;
}
function bindHover(canvas, points, render){
  const tip = chartTooltip(canvas);
  canvas.onmousemove = (e) => {
    if(!points.length) return;
    const rect = canvas.getBoundingClientRect();
    const x = e.clientX - rect.left;
    const p = points.reduce((best, item) => Math.abs(item.x-x) < Math.abs(best.x-x) ? item : best, points[0]);
    tip.innerHTML = render(p);
    tip.style.left = `${clamp(p.x, 82, rect.width-82)}px`;
    tip.style.top = `${Math.max(48, p.y || 80)}px`;
    tip.style.opacity = 1;
  };
  canvas.onmouseleave = () => { tip.style.opacity = 0; };
}
function drawHero(){
  const canvas = $('#heroCanvas'); const {ctx,w,h}=resizeCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  const a = dailyWithKm(); const maxHrv = Math.max(...a.map(d=>d.hrv || 0), 1); const maxKm = Math.max(...a.map(d=>d.km || 0), 1);
  const bg = ctx.createLinearGradient(0,0,w,h); bg.addColorStop(0,'rgba(18,122,90,.08)'); bg.addColorStop(.58,'rgba(170,119,34,.08)'); bg.addColorStop(1,'rgba(207,96,71,.07)');
  ctx.fillStyle = bg; ctx.fillRect(0,0,w,h);
  ctx.lineWidth = Math.max(6, Math.min(14, w / Math.max(20, a.length) * .72)); ctx.lineCap = 'round';
  for(let i=0;i<a.length;i++){
    const x = 26 + i * ((w-52)/Math.max(1,a.length-1));
    const y = h - 45 - ((a[i].hrv || 0)/maxHrv) * (h-120);
    const km = a[i].km || 0;
    ctx.strokeStyle = km > 10 ? 'rgba(207,96,71,.42)' : km > 0 ? 'rgba(18,122,90,.38)' : 'rgba(137,151,144,.18)';
    ctx.beginPath(); ctx.moveTo(x, h-34); ctx.lineTo(x, y); ctx.stroke();
  }
  ctx.lineWidth = 4; ctx.strokeStyle = COLORS.ink; ctx.beginPath();
  a.forEach((d,i)=>{
    const x = 26 + i * ((w-52)/Math.max(1,a.length-1));
    const y = h - 56 - ((d.km || 0)/maxKm) * (h-132);
    i ? ctx.lineTo(x,y) : ctx.moveTo(x,y);
  });
  ctx.stroke();
}
function drawBars(canvasId, a, barKey, lineKey){
  const canvas = $(canvasId); const {ctx,w,h}=resizeCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  const pad = {l:46,r:42,t:22,b:34};
  const bw = (w-pad.l-pad.r)/Math.max(1,a.length);
  const innerH = h-pad.t-pad.b, innerW = w-pad.l-pad.r;
  const maxBar = niceMax(Math.max(...a.map(d=>d[barKey] || 0), 1));
  const lineVals = a.map(d=>d[lineKey]).filter(v=>Number.isFinite(+v));
  const minLine = Math.min(...lineVals, .7), maxLine = Math.max(...lineVals, 1.4), span = maxLine-minLine || 1;
  ctx.font = '11px Geist, system-ui'; ctx.textBaseline = 'middle';
  ctx.strokeStyle = COLORS.grid; ctx.lineWidth = 1; ctx.fillStyle = COLORS.faint;
  for(let i=0;i<5;i++){
    const y=pad.t+i*innerH/4, value=maxBar-(maxBar*i/4);
    ctx.beginPath(); ctx.moveTo(pad.l,y); ctx.lineTo(w-pad.r,y); ctx.stroke();
    ctx.fillText(value.toFixed(value>=10?0:1), 8, y);
  }
  ctx.textAlign='right';
  for(let i=0;i<3;i++){
    const y=pad.t+i*innerH/2, value=maxLine-(span*i/2);
    ctx.fillStyle = COLORS.coral; ctx.fillText(value.toFixed(2), w-4, y);
  }
  ctx.textAlign='left';
  const grad = ctx.createLinearGradient(0,pad.t,0,h-pad.b); grad.addColorStop(0,'#26a873'); grad.addColorStop(1,'rgba(18,128,95,.28)');
  a.forEach((d,i)=>{
    const x = pad.l + i*bw + 2;
    const bh = ((d[barKey] || 0)/maxBar)*innerH;
    ctx.fillStyle = d[barKey] ? grad : '#edf1ea';
    drawRoundRect(ctx, x, h-pad.b-bh, Math.max(2,bw-4), Math.max(2,bh), Math.min(5, bw/3)); ctx.fill();
  });
  const ma = movingAvg(a, barKey, 7);
  ctx.strokeStyle = COLORS.faint; ctx.lineWidth = 2; ctx.setLineDash([5,4]); ctx.beginPath();
  ma.forEach((v,i)=>{ if(!Number.isFinite(v)) return; const x=pad.l+i*bw+bw/2; const y=h-pad.b-(v/maxBar)*innerH; i?ctx.lineTo(x,y):ctx.moveTo(x,y); });
  ctx.stroke(); ctx.setLineDash([]);
  const area = ctx.createLinearGradient(0,pad.t,0,h-pad.b); area.addColorStop(0,'rgba(213,95,69,.18)'); area.addColorStop(1,'rgba(213,95,69,0)');
  const pts = [];
  ctx.beginPath();
  a.forEach((d,i)=>{
    const v = Number(d[lineKey]); if(!Number.isFinite(v)) return;
    const x = pad.l + i*bw + bw/2;
    const y = h-pad.b-((v-minLine)/span)*innerH;
    pts.push({x,y,row:d});
    i ? ctx.lineTo(x,y) : ctx.moveTo(x,y);
  });
  if(pts.length){ ctx.lineTo(pts[pts.length-1].x,h-pad.b); ctx.lineTo(pts[0].x,h-pad.b); ctx.closePath(); ctx.fillStyle=area; ctx.fill(); }
  ctx.strokeStyle = COLORS.coral; ctx.lineWidth = 3; ctx.beginPath();
  pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));
  ctx.stroke();
  pts.forEach((p,i)=>{ if(i===pts.length-1 || i%Math.ceil(pts.length/6)===0){ctx.fillStyle='#fff';ctx.beginPath();ctx.arc(p.x,p.y,4,0,Math.PI*2);ctx.fill();ctx.strokeStyle=COLORS.coral;ctx.lineWidth=2;ctx.stroke();}});
  const tickCount = Math.min(5, a.length);
  ctx.fillStyle = COLORS.faint; ctx.font = '11px Geist, system-ui'; ctx.textAlign='center'; ctx.textBaseline='alphabetic';
  for(let i=0;i<tickCount;i++){ const idx=Math.round(i*(a.length-1)/Math.max(1,tickCount-1)); const x=pad.l+idx*bw+bw/2; ctx.fillText(a[idx]?.date?.slice(5) || '', x, h-10); }
  bindHover(canvas, pts.map(p=>({...p,y:Math.min(p.y,h-82)})), p=>`<b>${p.row.date}</b><span><em>Distance</em><strong>${(p.row.km||0).toFixed(1)} km</strong></span><span><em>Load ratio</em><strong>${fmt(p.row.load_ratio)}</strong></span><span><em>Training load</em><strong>${fmt(p.row.training_load)}</strong></span>`);
}
function drawHealth(){
  const a = rows(); const canvas = $('#healthChart'); const {ctx,w,h}=resizeCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  const pad={l:36,r:20,t:22,b:34}; const innerH=h-pad.t-pad.b, innerW=w-pad.l-pad.r;
  const keys=[['hrv','HRV',COLORS.green],['rhr','RHR',COLORS.coral],['load_ratio','Load',COLORS.gold]];
  ctx.strokeStyle=COLORS.grid; ctx.lineWidth=1; ctx.fillStyle=COLORS.faint; ctx.font='11px Geist, system-ui'; ctx.textBaseline='middle';
  for(let i=0;i<5;i++){const y=pad.t+i*innerH/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke(); if(i===0)ctx.fillText('high',6,y); if(i===4)ctx.fillText('low',8,y);}
  const hoverMap = new Map();
  keys.forEach(([key,label,color])=>{
    const vals=a.map(d=>+d[key]).filter(Number.isFinite); if(!vals.length) return;
    const min=Math.min(...vals), max=Math.max(...vals), span=max-min||1;
    const pts=[];
    ctx.strokeStyle=color; ctx.lineWidth=3; ctx.beginPath();
    a.forEach((d,i)=>{ const v=+d[key]; if(!Number.isFinite(v)) return; const x=pad.l+i*(innerW/Math.max(1,a.length-1)); const y=h-pad.b-((v-min)/span)*innerH; pts.push({x,y,row:d,key,label,value:v}); hoverMap.set(d.date,{...(hoverMap.get(d.date)||{}), x, y, row:d, [key]:v}); pts.length>1?ctx.lineTo(x,y):ctx.moveTo(x,y); });
    ctx.stroke();
    const end=pts[pts.length-1]; if(end){ctx.fillStyle=color;ctx.beginPath();ctx.arc(end.x,end.y,4,0,Math.PI*2);ctx.fill();ctx.font='11px Geist, system-ui';ctx.fillText(label,end.x-22,end.y-9);}
  });
  const points = [...hoverMap.values()].sort((a,b)=>a.row.date.localeCompare(b.row.date));
  ctx.fillStyle = COLORS.faint; ctx.textAlign='center'; ctx.textBaseline='alphabetic';
  for(let i=0;i<Math.min(5,a.length);i++){ const idx=Math.round(i*(a.length-1)/Math.max(1,Math.min(5,a.length)-1)); const x=pad.l+idx*(innerW/Math.max(1,a.length-1)); ctx.fillText(a[idx]?.date?.slice(5)||'',x,h-10); }
  bindHover(canvas, points, p=>`<b>${p.row.date}</b><span><em>HRV</em><strong>${fmt(p.hrv,' ms')}</strong></span><span><em>RHR</em><strong>${fmt(p.rhr,' bpm')}</strong></span><span><em>Load</em><strong>${fmt(p.load_ratio)}</strong></span>`);
}
function drawSleep(){
  const a = rows(); const canvas = $('#sleepChart'); const {ctx,w,h}=resizeCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  const pad={l:34,r:12,t:18,b:32}, innerH=h-pad.t-pad.b, innerW=w-pad.l-pad.r;
  const maxSleep = niceMax(Math.max(...a.map(d=>(d.deep_min||0)+(d.light_min||0)+(d.rem_min||0)+(d.awake_min||0)), 480));
  const bw = innerW / Math.max(1,a.length);
  ctx.strokeStyle=COLORS.grid; ctx.lineWidth=1; ctx.fillStyle=COLORS.faint; ctx.font='11px Geist, system-ui';
  for(let i=0;i<4;i++){const y=pad.t+i*innerH/3;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();}
  const segments=[['deep_min','#127a5a'],['rem_min','#7cbea0'],['light_min','#cbd8ce'],['awake_min','#cf6047']];
  const pts=[];
  a.forEach((d,i)=>{
    const x=pad.l+i*bw+2; let y=h-pad.b;
    segments.forEach(([key,color])=>{const mh=((d[key]||0)/maxSleep)*innerH; if(mh>0){ctx.fillStyle=color; drawRoundRect(ctx,x,y-mh,Math.max(2,bw-4),Math.max(1,mh),Math.min(5,bw/3)); ctx.fill(); y-=mh;}});
    pts.push({x:x+bw/2,y:Math.max(36,y),row:d});
  });
  ctx.textAlign='center'; ctx.textBaseline='alphabetic'; ctx.fillStyle=COLORS.faint;
  for(let i=0;i<Math.min(5,a.length);i++){const idx=Math.round(i*(a.length-1)/Math.max(1,Math.min(5,a.length)-1));ctx.fillText(a[idx]?.date?.slice(5)||'',pad.l+idx*bw+bw/2,h-10);}
  bindHover(canvas, pts, p=>`<b>${p.row.date}</b><span><em>Sleep</em><strong>${fmt(p.row.sleep_min ? (p.row.sleep_min/60).toFixed(1) : null,' h')}</strong></span><span><em>Deep</em><strong>${fmt(p.row.deep_min,' min')}</strong></span><span><em>REM</em><strong>${fmt(p.row.rem_min,' min')}</strong></span><span><em>Awake</em><strong>${fmt(p.row.awake_min,' min')}</strong></span>`);
}
function drawWeekChart(){
  const weeks=(DATA.summaries.weekly||[]).slice().reverse().slice(-9);
  const canvas=$('#weekChart'); const {ctx,w,h}=resizeCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  if(!weeks.length) return;
  const pad={l:34,r:34,t:16,b:30}, innerH=h-pad.t-pad.b, innerW=w-pad.l-pad.r, bw=innerW/weeks.length;
  const maxKm=niceMax(Math.max(...weeks.map(x=>x.distance_km||0),1));
  const maxMin=niceMax(Math.max(...weeks.map(x=>x.exercise_min||0),1));
  ctx.strokeStyle=COLORS.grid; ctx.lineWidth=1; for(let i=0;i<4;i++){const y=pad.t+i*innerH/3;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke();}
  const grad=ctx.createLinearGradient(0,pad.t,0,h-pad.b);grad.addColorStop(0,'#127a5a');grad.addColorStop(1,'rgba(18,122,90,.22)');
  weeks.forEach((wk,i)=>{const x=pad.l+i*bw+4,bh=(wk.distance_km||0)/maxKm*innerH;ctx.fillStyle=grad;drawRoundRect(ctx,x,h-pad.b-bh,Math.max(3,bw-8),Math.max(2,bh),6);ctx.fill();});
  ctx.strokeStyle=COLORS.coral;ctx.lineWidth=2.5;ctx.beginPath();
  const pts=weeks.map((wk,i)=>({x:pad.l+i*bw+bw/2,y:h-pad.b-((wk.exercise_min||0)/maxMin)*innerH,row:wk}));
  pts.forEach((p,i)=>i?ctx.lineTo(p.x,p.y):ctx.moveTo(p.x,p.y));ctx.stroke();
  ctx.fillStyle=COLORS.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='center';pts.forEach((p,i)=>{if(i%2===0||i===pts.length-1)ctx.fillText(p.row.key.replace('2026-',''),p.x,h-10);});
  bindHover(canvas, pts, p=>`<b>${p.row.key}</b><span><em>Distance</em><strong>${fmt(p.row.distance_km,' km')}</strong></span><span><em>Activities</em><strong>${fmt(p.row.activities)}</strong></span><span><em>Exercise</em><strong>${fmt(p.row.exercise_min,' min')}</strong></span><span><em>Load</em><strong>${fmt(p.row.load_state)}</strong></span>`);
}
function ring(id, value, max, color){
  const c=$(id); const r=62; const circ=2*Math.PI*r;
  c.style.strokeDasharray = `${Math.max(0,Math.min(1,value/max))*circ} ${circ}`;
  c.style.stroke = color;
}
function renderMetrics(){
  const a=rows(), day=last(DATA.daily), activityRows=acts();
  const dist=sum(activityRows,x=>x.distance_km).toFixed(1);
  const mins=Math.round(sum(activityRows,x=>x.duration_min));
  const hrv=avg(a,x=>x.hrv); const rhr=avg(a,x=>x.rhr); const sleep=avg(a,x=>x.sleep_min);
  const kmTrend = compareByWindow('km', dailyWithKm(DATA.daily));
  const hrvTrend = compareByWindow('hrv');
  const loadTrend = compareByWindow('load_ratio');
  const sleepTrend = compareByWindow('sleep_min');
  const items=[
    ['Distance', `${dist} km`, `${activityRows.length} sessions`, trendText(kmTrend,true), activityRows.map(x=>x.distance_km||0), COLORS.green],
    ['Time', `${mins} min`, `${Math.round(mins/60)}h moving`, trendText(kmTrend,true), activityRows.map(x=>x.duration_min||0), COLORS.gold],
    ['HRV', hrv?`${Math.round(hrv)} ms`:'--', `RHR ${rhr?Math.round(rhr):'--'}`, trendText(hrvTrend,true), a.map(x=>x.hrv||0), COLORS.green],
    ['Sleep', sleep?`${(sleep/60).toFixed(1)} h`:'--', `last ${fmt(day.sleep_min ? (day.sleep_min/60).toFixed(1) : null,'h')}`, trendText(sleepTrend,true), a.map(x=>x.sleep_min||0), COLORS.faint]
  ];
  $('#metrics').innerHTML = items.map((it,idx)=>`<section class="metric" style="--i:${idx};--accent:${it[5]};--delta:${it[3].includes('watch')?COLORS.coral:it[3].includes('better')?COLORS.green:COLORS.muted}"><span>${it[0]}</span><b>${it[1]}</b><span>${it[2]}</span><em>${it[3]}</em><canvas class="spark" data-idx="${idx}"></canvas></section>`).join('');
  document.querySelectorAll('.spark').forEach((c,i)=>drawSpark(c,items[i][4]));
}
function drawSpark(canvas, vals){
  const {ctx,w,h}=resizeCanvas(canvas); ctx.clearRect(0,0,w,h);
  vals = vals.filter(v=>Number.isFinite(+v)).map(Number);
  if(vals.length < 2) return;
  const max=Math.max(...vals,1), min=Math.min(...vals,0), span=max-min||1;
  const grad=ctx.createLinearGradient(0,0,w,0); grad.addColorStop(0,'rgba(18,122,90,.28)'); grad.addColorStop(1,'rgba(170,119,34,.82)');
  ctx.strokeStyle='rgba(16,22,21,.10)'; ctx.lineWidth=1; ctx.beginPath(); ctx.moveTo(0,h-5); ctx.lineTo(w,h-5); ctx.stroke();
  ctx.strokeStyle=grad; ctx.lineWidth=2.2; ctx.beginPath();
  vals.forEach((v,i)=>{const x=i*(w/Math.max(1,vals.length-1)); const y=h-5-((v-min)/span)*(h-10); i?ctx.lineTo(x,y):ctx.moveTo(x,y);}); ctx.stroke();
}
function renderActivities(){
  const allSports=['All', ...new Set(DATA.activities.map(a=>a.sport).filter(Boolean))];
  $('#filters').innerHTML=allSports.map(s=>`<button class="${s===sportFilter?'active':''}" data-sport="${s}">${s}</button>`).join('');
  $('#filters').querySelectorAll('button').forEach(b=>b.onclick=()=>{sportFilter=b.dataset.sport; render();});
  const list=acts().slice(0,18); $('#activityCount').textContent=`${list.length}`;
  $('#activityList').innerHTML=list.length ? list.map((a,i)=>`<article class="activity" data-i="${i}"><div class="badge">${Math.round(a.distance_km||0)}</div><div><h3>${a.location || a.sport}</h3><p>${a.date} · ${a.sport} · ${a.pace}</p></div><div class="num">${fmt(a.training_load,' TL')}</div></article>`).join('') : '<div class="empty">No activities</div>';
  $('#activityList').querySelectorAll('.activity').forEach((el,i)=>el.onclick=()=>openDrawer(list[i]));
}
function openDrawer(a){
  $('#drawerTitle').textContent=a.location || a.sport;
  $('#drawerStats').innerHTML=[
    ['Distance',fmt(a.distance_km,' km')],['Pace',fmt(a.pace)],['Time',fmt(a.duration)],
    ['Avg HR',fmt(a.avg_hr,' bpm')],['Load',fmt(a.training_load,' TL')],['Power',fmt(a.avg_power,' W')]
  ].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
  $('#drawer').classList.add('show');
}
function renderHeat(){
  const a=dailyWithKm(); $('#heatmap').innerHTML=a.map(d=>{const km=d.km||0; const lvl=km>12?4:km>8?3:km>3?2:km>0?1:0; return `<div class="cell" data-lvl="${lvl}" title="${d.date} · ${km.toFixed(1)} km"></div>`}).join('');
  let streak=0, bestStreak=0; a.forEach(d=>{streak=(d.km||0)>0?streak+1:0; bestStreak=Math.max(bestStreak,streak);});
  const biggest=a.slice().sort((x,y)=>(y.km||0)-(x.km||0))[0] || {};
  const rest=a.filter(d=>(d.km||0)===0).length;
  const loadDays=a.filter(d=>(d.load_ratio||0)>1.15).length;
  $('#heatSummary').innerHTML=[
    ['Best streak', `${bestStreak} d`],
    ['Biggest day', `${(biggest.km||0).toFixed(1)} km`],
    ['Rest days', `${rest}`],
    ['High load', `${loadDays} d`]
  ].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');
}
function renderWeeks(){
  const weeks=(DATA.summaries.weekly||[]).slice(0,5);
  $('#weeklyCards').innerHTML=weeks.map(w=>`<div class="summary"><span>${w.key}</span><b>${fmt(w.distance_km,' km')}</b><span>${w.activities} runs · ${w.exercise_min} min</span><div class="progress"><i style="width:${Math.min(100,(w.distance_km||0)/65*100)}%"></i></div></div>`).join('');
}
function renderPreds(){
  const p=DATA.fitness.race_predictions||{};
  $('#preds').innerHTML=Object.entries(p).map(([k,v])=>`<div class="pred"><span>${k}</span><b>${v}</b></div>`).join('');
}
function renderNext(){
  const s=DATA.schedule[0]||{}, w=DATA.workouts[0]||{}, f=DATA.fitness||{};
  const tiles=[['Plan',s.name||'--',fmt(s.distance_km,' km')],['Workout',w.name||'--',fmt(Math.round((w.estimated_time_seconds||0)/60),' min')],['Level',fmt(f.running_level),fmt(f.threshold_pace)]];
  $('#nextUp').innerHTML=tiles.map(t=>`<div class="tile"><span>${t[0]}</span><b>${t[1]}</b><span>${t[2]}</span></div>`).join('');
}
function renderCoach(dayRows){
  const day=last(dayRows), score=readinessScore(dayRows), status=statusLabel(day), week=(DATA.summaries.weekly||[])[0]||{};
  $('#coachScore').textContent=score;
  $('#coachLabel').textContent=`${status[0]} mode · ${status[1]}`;
  const load = +(day.load_ratio || 0), hrvGap = day.hrv && day.hrv_baseline ? Math.round(day.hrv - day.hrv_baseline) : null;
  const sleepHours = day.sleep_min ? (day.sleep_min/60).toFixed(1) : '--';
  const briefs=[
    ['Load balance', load > 1.25 ? 'Acute load is above comfort. Keep the next hard session short.' : load < .85 ? 'Load is light. Add volume only if legs feel fresh.' : 'Load is in the useful training band.'],
    ['Recovery signal', hrvGap === null ? 'HRV baseline is not available for this day.' : hrvGap >= 0 ? `HRV is ${hrvGap} ms above baseline. Quality work is supported.` : `HRV is ${Math.abs(hrvGap)} ms below baseline. Favor easy aerobic work.`],
    ['Weekly shape', week.distance_km ? `${week.distance_km} km this week with ${week.activities} sessions. ${week.note || ''}` : 'Weekly summary will appear after more records.'],
    ['Sleep floor', sleepHours === '--' ? 'Sleep detail is missing for the latest day.' : `${sleepHours} h sleep logged. Treat deep + REM as the recovery floor.`]
  ];
  $('#coachBrief').innerHTML=briefs.map((x,i)=>`<div class="coach-item" style="animation:rise .45s cubic-bezier(.16,1,.3,1) both;animation-delay:${i*55}ms"><b>${x[0]}</b><p>${x[1]}</p></div>`).join('');
}
function focusRow(dayRows){
  if(focusMode === 'hard') return dayRows.slice().sort((a,b)=>(b.activity_load||b.training_load||0)-(a.activity_load||a.training_load||0))[0] || last(dayRows);
  if(focusMode === 'best') return dayRows.slice().sort((a,b)=>(b.hrv||0)-(a.hrv||0))[0] || last(dayRows);
  return last(dayRows);
}
function renderFocus(dayRows){
  const day=focusRow(dayRows), activity=(day.acts||[])[0]||{};
  $('#focusDate').textContent=day.date || 'latest';
  $('#focusGrid').innerHTML=[
    ['Distance', `${(day.km||0).toFixed(1)} km`, activity.location || `${day.activity_count || 0} sessions`],
    ['Recovery', fmt(DATA.recovery.recovery_percent,'%'), DATA.recovery.level || '--'],
    ['HRV / RHR', `${fmt(day.hrv,' ms')} / ${fmt(day.rhr)}`, day.hrv_status || '--'],
    ['Sleep', day.sleep_min ? `${(day.sleep_min/60).toFixed(1)} h` : '--', `deep ${fmt(day.deep_min,'m')} · REM ${fmt(day.rem_min,'m')}`]
  ].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b><span>${x[2]}</span></div>`).join('');
}
function renderHero(){
  const a=rows(), activityRows=acts(), day=last(DATA.daily), meta=DATA.meta||{};
  const dist=sum(activityRows,x=>x.distance_km).toFixed(1);
  const kmTrend = compareByWindow('km', dailyWithKm(DATA.daily));
  const loadTrend = compareByWindow('load_ratio');
  const hrvTrend = compareByWindow('hrv');
  $('#updated').textContent = meta.generated_at || '';
  $('#windowChip').textContent = `${a[0]?.date || '--'} → ${last(a)?.date || '--'}`;
  $('#loadChip').textContent = `Load ${fmt(day.load_ratio)} · ${trendText(loadTrend,false)}`;
  $('#raceChip').textContent = `VO2 ${fmt(DATA.fitness.vo2max)} · HRV ${trendText(hrvTrend,true)}`;
  $('#heroDistance').textContent = `${dist} km`;
  $('#heroSub').textContent = `${activityRows.length} sessions · ${Math.round(sum(activityRows,x=>x.duration_min))} moving minutes · ${trendText(kmTrend,true)}`;
  $('#recoveryText').textContent = fmt(DATA.recovery.recovery_percent,'%');
  $('#recoveryLevel').textContent = DATA.recovery.level || '--';
  $('#recoveryTime').textContent = `full in ${DATA.recovery.estimated_full_recovery || '--'}`;
  $('#vo2Text').textContent = fmt(DATA.fitness.vo2max);
  $('#thresholdText').textContent = DATA.fitness.threshold_pace || '--';
  ring('#recoveryRing', DATA.recovery.recovery_percent || 0, 100, COLORS.green);
  ring('#vo2Ring', DATA.fitness.vo2max || 0, 70, COLORS.gold);
  drawHero();
}
function render(){
  const dayRows = dailyWithKm();
  renderHero(); renderMetrics(); renderActivities(); renderHeat(); renderWeeks(); renderPreds(); renderNext(); renderCoach(dayRows); renderFocus(dayRows);
  $('#rangeLabel').textContent = range === 'all' ? 'all data' : range === '1' ? '1 day' : `${range} days`;
  const totalKm = sum(acts(), x=>x.distance_km);
  const avgLoad = avg(dayRows, x=>x.load_ratio);
  const runDays = dayRows.filter(x=>x.km>0).length;
  $('#loadInsight').innerHTML = [
    ['volume', `${totalKm.toFixed(1)} km`],
    ['run days', `${runDays}/${dayRows.length}`],
    ['avg load', avgLoad ? avgLoad.toFixed(2) : '--'],
    ['trend', trendText(compareByWindow('km', dailyWithKm(DATA.daily)), true)]
  ].map(x=>`<span class="pill">${x[0]} ${x[1]}</span>`).join('');
  const hrv=avg(dayRows,x=>x.hrv), rhr=avg(dayRows,x=>x.rhr), sleep=avg(dayRows,x=>x.sleep_min);
  $('#healthInsight').innerHTML = [
    ['HRV', hrv ? `${Math.round(hrv)} ms` : '--'],
    ['RHR', rhr ? `${Math.round(rhr)} bpm` : '--'],
    ['Sleep', sleep ? `${(sleep/60).toFixed(1)} h` : '--']
  ].map(x=>`<span class="pill">${x[0]} ${x[1]}</span>`).join('');
  const deep=avg(dayRows,x=>x.deep_min), rem=avg(dayRows,x=>x.rem_min), awake=avg(dayRows,x=>x.awake_min);
  $('#sleepInsight').innerHTML = [
    ['deep', deep ? `${Math.round(deep)} min` : '--'],
    ['REM', rem ? `${Math.round(rem)} min` : '--'],
    ['awake', awake ? `${Math.round(awake)} min` : '--']
  ].map(x=>`<span class="pill">${x[0]} ${x[1]}</span>`).join('');
  drawBars('#distanceChart', dayRows, 'km', 'load_ratio');
  drawHealth(); drawSleep(); drawWeekChart();
}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.seg button').forEach(x=>x.classList.remove('active'));b.classList.add('active');range=b.dataset.range;render();});
$('#focusTabs').querySelectorAll('button').forEach(b=>b.onclick=()=>{$('#focusTabs').querySelectorAll('button').forEach(x=>x.classList.remove('active'));b.classList.add('active');focusMode=b.dataset.focus;render();});
$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('show');
window.addEventListener('resize', render);
render();
</script>
</body>
</html>""".replace("__DATA__", json_blob)
    return html_doc


def build_home():
    data = load_data()
    payload = {
        "meta": data.get("meta", {}),
        "daily": data.get("daily", []),
        "activities": data.get("activities", []),
        "summaries": data.get("summaries", {}),
        "recovery": data.get("recovery", {}),
        "fitness": data.get("fitness", {}),
        "schedule": data.get("schedule", []),
        "workouts": data.get("workouts", []),
    }
    json_blob = json.dumps(payload, ensure_ascii=False).replace("</", "<\\/")
    html_doc = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sports Log</title>
<style>
:root{color-scheme:light;--bg:#f6f8fc;--surface:#fff;--surface2:#f8fbff;--ink:#0f1b33;--muted:#55627a;--faint:#8994a8;--line:#e4eaf3;--blue:#2563eb;--blue2:#dbeafe;--green:#16a064;--green2:#dcfce7;--orange:#f28c18;--red:#ef4f5f;--cyan:#1798b8;--shadow:0 18px 48px rgba(21,35,65,.08);--font:Geist,Satoshi,"Cabinet Grotesk",Outfit,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--mono:"JetBrains Mono","SFMono-Regular",ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);font-size:14px;line-height:1.45;letter-spacing:0;-webkit-font-smoothing:antialiased}button{font:inherit}.app{display:grid;grid-template-columns:76px minmax(0,1fr);min-height:100dvh}.rail{position:sticky;top:0;height:100dvh;background:linear-gradient(180deg,#0b4fd8,#071d37);padding:18px 10px;display:flex;flex-direction:column;align-items:center;gap:22px;color:white}.rail-mark{width:44px;height:44px;border-radius:14px;background:#fff;display:grid;place-items:center;color:var(--blue);font-weight:900;font-size:18px;box-shadow:0 14px 30px rgba(0,0,0,.18)}.rail a{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;color:#dbeafe;text-decoration:none;font-size:12px;font-weight:800;border:1px solid transparent}.rail a.active,.rail a:hover{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.16);color:white}.main{padding:26px;max-width:1520px;width:100%;margin:0 auto}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:20px}.title h1{margin:0;font-size:34px;line-height:1;font-weight:850;letter-spacing:-.04em}.date{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px;margin-top:10px}.actions{display:flex;align-items:center;gap:12px}.seg{display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:0 10px 26px rgba(21,35,65,.06)}.seg button{border:0;background:transparent;color:var(--muted);border-radius:9px;padding:8px 12px;font-weight:800;font-size:12px;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),background .18s}.seg button:active{transform:scale(.97)}.seg button.active{background:var(--blue);color:white}.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:14px}.card,.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}.card{padding:18px;min-height:154px;overflow:hidden}.card-head{display:flex;align-items:center;gap:12px}.dot{width:34px;height:34px;border-radius:12px;background:var(--tone,#dbeafe);display:grid;place-items:center;color:var(--color,var(--blue));font-weight:900}.card span,.panel small,.metric-label{color:var(--muted);font-size:12px;font-weight:700}.card b{display:block;font-size:29px;line-height:1.05;margin:18px 0 8px;font-family:var(--mono);letter-spacing:-.04em}.trend{font-size:12px;font-weight:850;color:var(--green)}.trend.down{color:var(--red)}.spark{width:100%;height:36px;margin-top:8px}.layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(340px,1fr);gap:12px}.panel{padding:20px;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:14px}.panel h2{margin:0;font-size:17px;line-height:1.15;font-weight:850;letter-spacing:-.02em}.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px;font-weight:750}.legend i{display:inline-block;width:18px;height:4px;border-radius:999px;margin-right:6px;vertical-align:2px}.chart{position:relative;height:392px}.chart.short{height:262px}.chart canvas{display:block;width:100%;height:100%}.chart-tip{position:absolute;pointer-events:none;z-index:5;min-width:172px;background:#0f1b33;color:white;border-radius:12px;padding:10px 11px;font-size:12px;line-height:1.45;box-shadow:0 18px 50px rgba(15,27,51,.22);transform:translate(-50%,-112%);opacity:0;transition:opacity .12s}.chart-tip b{display:block;margin-bottom:5px}.chart-tip span{display:flex;justify-content:space-between;gap:18px;color:#d6e1f4}.chart-tip em{font-style:normal;color:#9fb0ca}.side-stack{display:grid;gap:12px}.status{display:grid;grid-template-columns:132px 1fr;gap:18px;align-items:center}.gauge{position:relative;width:132px;height:132px}.gauge svg{width:132px;height:132px;transform:rotate(-90deg)}.gauge circle{fill:none;stroke-width:14;stroke-linecap:round}.gauge .bg{stroke:#e7edf7}.gauge .fg{stroke:var(--green);stroke-dasharray:0 999}.gauge b{position:absolute;inset:0;display:grid;place-items:center;font-size:28px;font-family:var(--mono)}.status h3{font-size:28px;line-height:1.08;margin:0 0 8px;letter-spacing:-.04em}.status p{margin:0;color:var(--muted)}.coach-list{display:grid;gap:10px;margin-top:16px}.coach-list div{border-top:1px solid var(--line);padding-top:10px}.coach-list b{display:block;font-size:13px}.coach-list p{margin:3px 0 0;color:var(--muted);font-size:12px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.activity-list{display:grid;gap:9px;max-height:444px;overflow:auto;content-visibility:auto;padding-right:2px}.activity{display:grid;grid-template-columns:46px 1fr auto;gap:12px;align-items:center;border:1px solid var(--line);border-radius:12px;padding:10px;background:var(--surface);cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),border-color .18s}.activity:hover{transform:translateY(-1px);border-color:#cbd8ea}.activity:active{transform:scale(.99)}.badge{width:42px;height:42px;border-radius:50%;background:var(--blue2);color:var(--blue);display:grid;place-items:center;font-family:var(--mono);font-weight:850}.activity h3{margin:0;font-size:13px}.activity p{margin:4px 0 0;color:var(--muted);font-size:12px}.activity .num{font-family:var(--mono);font-weight:850}.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.filters button{border:1px solid var(--line);background:white;color:var(--muted);border-radius:999px;padding:6px 10px;font-size:12px;font-weight:800;cursor:pointer}.filters button.active{background:var(--ink);border-color:var(--ink);color:white}.achievements{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.medal{padding:18px;border-radius:14px;background:var(--surface2);border:1px solid var(--line);text-align:center}.medal i{display:grid;place-items:center;width:54px;height:54px;margin:0 auto 12px;border-radius:18px;background:var(--tone,#dbeafe);color:var(--color,var(--blue));font-style:normal;font-weight:900}.medal b{display:block;font-size:14px}.medal span{display:block;color:var(--muted);font-size:12px;margin-top:6px}.drawer{position:fixed;right:22px;bottom:22px;width:min(430px,calc(100% - 44px));background:#0f1b33;color:white;border-radius:16px;padding:20px;box-shadow:0 28px 90px rgba(15,27,51,.36);transform:translateY(130%);transition:.25s cubic-bezier(.16,1,.3,1);z-index:10}.drawer.show{transform:translateY(0)}.drawer button{position:absolute;right:12px;top:10px;border:0;background:#ffffff18;color:white;border-radius:50%;width:30px;height:30px;cursor:pointer}.drawer h2{margin:0 36px 14px 0}.detail-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.detail-stats div{background:#ffffff12;border-radius:10px;padding:10px}.detail-stats span{display:block;color:#b7c6df;font-size:11px}.detail-stats b{font-family:var(--mono)}@media (prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}@media(max-width:1180px){.cards{grid-template-columns:repeat(3,1fr)}.layout,.grid2{grid-template-columns:1fr}.app{grid-template-columns:1fr}.rail{display:none}.main{padding:18px}}@media(max-width:640px){.topbar{display:block}.actions{margin-top:14px}.cards{grid-template-columns:1fr}.title h1{font-size:28px}.status{grid-template-columns:1fr}.achievements{grid-template-columns:1fr 1fr}.chart{height:318px}.chart.short{height:240px}}
</style>
</head>
<body>
<div class="app">
<aside class="rail"><div class="rail-mark">SL</div><a class="active" href="#overview">OV</a><a href="#load">LD</a><a href="#recovery">RC</a><a href="#activities">AC</a></aside>
<main class="main" id="overview">
  <div class="topbar"><div class="title"><h1><span id="rangeTitle">7天</span>跑步数据概览</h1><div class="date" id="dateLabel">--</div></div><div class="actions"><div class="seg" aria-label="range"><button data-range="7" class="active">7D</button><button data-range="30">30D</button><button data-range="60">60D</button><button data-range="all">ALL</button></div></div></div>
  <section class="cards" id="cards"></section>
  <section class="layout">
    <section class="panel" id="load"><div class="panel-head"><div><h2>距离趋势</h2><small>每日距离柱状图 + 7日移动平均线</small></div><div class="legend"><span><i style="background:var(--blue)"></i>每日距离</span><span><i style="background:var(--green)"></i>7日均线</span></div></div><div class="chart"><canvas id="distanceChart"></canvas></div></section>
    <aside class="side-stack">
      <section class="panel status"><div class="gauge"><svg viewBox="0 0 160 160"><circle class="bg" cx="80" cy="80" r="62"></circle><circle class="fg" id="statusRing" cx="80" cy="80" r="62"></circle></svg><b id="statusScore">--</b></div><div><h3 id="statusTitle">--</h3><p id="statusText">--</p></div></section>
      <section class="panel" id="recovery"><div class="panel-head"><div><h2>跑步能力变化</h2><small>VO2max / load / recovery</small></div></div><div class="chart short"><canvas id="abilityChart"></canvas></div></section>
      <section class="panel"><h2>Coach Summary</h2><div class="coach-list" id="coachList"></div></section>
    </aside>
  </section>
  <section class="grid2">
    <section class="panel"><div class="panel-head"><div><h2>配速趋势</h2><small>活动配速，越高代表越慢</small></div><div class="legend"><span><i style="background:var(--blue)"></i>配速</span><span><i style="background:var(--green)"></i>7次均线</span></div></div><div class="chart short"><canvas id="paceChart"></canvas></div></section>
    <section class="panel"><div class="panel-head"><div><h2>每周跑步总结</h2><small>距离 / 时长 / 次数对比</small></div></div><div class="chart short"><canvas id="weekChart"></canvas></div></section>
    <section class="panel"><div class="panel-head"><div><h2>睡眠与恢复</h2><small>睡眠结构 + HRV 基线</small></div><div class="legend"><span><i style="background:var(--green)"></i>深睡</span><span><i style="background:#bad6c6"></i>REM/浅睡</span><span><i style="background:var(--red)"></i>清醒</span></div></div><div class="chart short"><canvas id="sleepChart"></canvas></div></section>
    <section class="panel" id="activities"><div class="panel-head"><div><h2>最近活动</h2><small id="activityCount">--</small></div></div><div class="filters" id="filters"></div><div class="activity-list" id="activityList"></div></section>
  </section>
  <section class="panel" style="margin-top:12px"><div class="panel-head"><div><h2>阶段成就</h2><small>基于当前区间自动汇总</small></div></div><div class="achievements" id="achievements"></div></section>
</main>
</div>
<aside class="drawer" id="drawer"><button id="closeDrawer">×</button><h2 id="drawerTitle">Activity</h2><div class="detail-stats" id="drawerStats"></div></aside>
<script type="application/json" id="payload">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById('payload').textContent);
let range=7,sportFilter='All';
const $=q=>document.querySelector(q), $$=q=>[...document.querySelectorAll(q)];
const fmt=(v,s='')=>(v===null||v===undefined||v==='')?'--':`${v}${s}`;
const sum=(a,fn)=>a.reduce((n,x)=>n+(+fn(x)||0),0);
const avg=(a,fn)=>{const v=a.map(fn).filter(x=>Number.isFinite(+x));return v.length?v.reduce((m,n)=>m+ +n,0)/v.length:null};
const last=a=>a[a.length-1]||{}, clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const C={ink:'#0f1b33',muted:'#55627a',faint:'#8994a8',grid:'#dce5f2',blue:'#2563eb',green:'#16a064',orange:'#f28c18',red:'#ef4f5f',cyan:'#1798b8'};
const byDate=new Map();DATA.activities.forEach(a=>{const x=byDate.get(a.date)||{km:0,min:0,load:0,cal:0,elev:0,count:0,acts:[]};x.km+=+(a.distance_km||0);x.min+=+(a.duration_min||0);x.load+=+(a.training_load||0);x.cal+=+(a.calories||0);x.elev+=+(a.elevation_gain||0);x.count++;x.acts.push(a);byDate.set(a.date,x)});
function rows(){return range==='all'?DATA.daily:DATA.daily.slice(-Number(range))}
function days(src=rows()){return src.map(d=>{const a=byDate.get(d.date)||{};return {...d,km:a.km||0,min:a.min||0,load:a.load||0,cal:a.cal||0,elev:a.elev||0,count:a.count||0,acts:a.acts||[]}})}
function acts(){const start=rows()[0]?.date||'';return DATA.activities.filter(a=>(!start||a.date>=start)&&(sportFilter==='All'||a.sport===sportFilter))}
function canvas(el){const dpr=devicePixelRatio||1,r=el.getBoundingClientRect();el.width=Math.max(1,r.width*dpr);el.height=Math.max(1,r.height*dpr);const ctx=el.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return{ctx,w:r.width,h:r.height}}
function nice(v){if(!v||v<=0)return 1;const p=10**Math.floor(Math.log10(v)),n=v/p;return(n<=2?2:n<=5?5:10)*p}
function ma(a,key,n=7){return a.map((_,i)=>avg(a.slice(Math.max(0,i-n+1),i+1),x=>+x[key]))}
function change(now,prev){return Number.isFinite(now)&&Number.isFinite(prev)&&prev!==0?(now-prev)/prev*100:null}
function compare(key,src=days(DATA.daily)){const n=range==='all'?Math.min(30,src.length):Number(range),c=src.slice(-n),p=src.slice(Math.max(0,src.length-n*2),Math.max(0,src.length-n));return change(avg(c,x=>+x[key]),avg(p,x=>+x[key]))}
function trend(v,goodUp=true){if(v===null)return 'baseline --';const up=v>=0,ok=Math.abs(v)<1||(up===goodUp);return `<span class="trend ${ok?'':'down'}">${up?'↑':'↓'} ${Math.abs(v).toFixed(1)}%</span>`}
function roundRect(ctx,x,y,w,h,r){r=Math.min(r,w/2,h/2);ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath()}
function tip(c){let t=c.parentElement.querySelector('.chart-tip');if(!t){t=document.createElement('div');t.className='chart-tip';c.parentElement.appendChild(t)}return t}
function hover(c,pts,html){const t=tip(c);c.onmousemove=e=>{if(!pts.length)return;const r=c.getBoundingClientRect(),x=e.clientX-r.left,p=pts.reduce((b,n)=>Math.abs(n.x-x)<Math.abs(b.x-x)?n:b,pts[0]);t.innerHTML=html(p);t.style.left=`${clamp(p.x,90,r.width-90)}px`;t.style.top=`${Math.max(58,p.y||90)}px`;t.style.opacity=1};c.onmouseleave=()=>t.style.opacity=0}
function paceSec(p){const m=String(p||'').match(/(\\d+):(\\d+)/);return m?+m[1]*60+ +m[2]:null}
function paceText(s){if(!Number.isFinite(s))return'--';return `${Math.floor(s/60)}'${String(Math.round(s%60)).padStart(2,'0')}"`}
function drawAxes(ctx,p,w,h,max,labelRight){ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='right';for(let i=0;i<5;i++){const y=p.t+i*(h-p.t-p.b)/4,val=max-(max*i/4);ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText(labelRight?labelRight(val):val.toFixed(val>=10?0:1),p.l-8,y+4)}}
function drawDistance(){
 const a=days(),c=$('#distanceChart'),{ctx,w,h}=canvas(c),p={l:48,r:24,t:18,b:38},iw=w-p.l-p.r,ih=h-p.t-p.b,bw=iw/Math.max(1,a.length),max=nice(Math.max(...a.map(x=>x.km),1)),avg7=ma(a,'km',7),pts=[];
 ctx.clearRect(0,0,w,h);drawAxes(ctx,p,w,h,max);
 a.forEach((d,i)=>{const x=p.l+i*bw+bw*.18,bh=d.km/max*ih;ctx.fillStyle=d.km>0?C.blue:'#eff4fb';roundRect(ctx,x,h-p.b-bh,Math.max(3,bw*.64),Math.max(2,bh),5);ctx.fill()});
 ctx.strokeStyle=C.green;ctx.lineWidth=2.5;ctx.beginPath();avg7.forEach((v,i)=>{const x=p.l+i*bw+bw/2,y=h-p.b-(v/max)*ih;pts.push({x,y,row:a[i],avg:v});i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();
 ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='center';for(let i=0;i<Math.min(6,a.length);i++){const idx=Math.round(i*(a.length-1)/Math.max(1,Math.min(6,a.length)-1));ctx.fillText(a[idx].date.slice(5),p.l+idx*bw+bw/2,h-12)}
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>每日距离</em><strong>${p.row.km.toFixed(1)} km</strong></span><span><em>7日均线</em><strong>${p.avg.toFixed(1)} km</strong></span><span><em>训练负荷</em><strong>${fmt(p.row.load,' TL')}</strong></span>`);
}
function drawAbility(){
 const a=days(),c=$('#abilityChart'),{ctx,w,h}=canvas(c),p={l:42,r:16,t:18,b:30},iw=w-p.l-p.r,topH=(h-p.t-p.b)*.68,stripY=p.t+topH+18,stripH=34,vo2=a.map(x=>+x.vo2max).filter(v=>Number.isFinite(v)&&v>20),lo=Math.max(30,Math.min(...vo2,58)-8),hi=Math.min(75,Math.max(...vo2,58)+8),sp=hi-lo||1,pts=[];
 ctx.clearRect(0,0,w,h);ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='right';
 for(let i=0;i<4;i++){const y=p.t+i*topH/3,val=hi-(hi-lo)*i/3;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText(Math.round(val),p.l-8,y+4)}
 ctx.fillStyle='#f6f9ff';ctx.fillRect(p.l,stripY,iw,stripH);
 a.forEach((d,i)=>{const x=p.l+i*iw/Math.max(1,a.length-1),bw=Math.max(3,iw/Math.max(12,a.length)*.55),load=+(d.load_ratio||0);ctx.fillStyle=load>1.25?C.red:load>=.85?C.green:C.faint;roundRect(ctx,x-bw/2,stripY+stripH-(Math.min(1.5,load)/1.5)*stripH,bw,Math.max(2,(Math.min(1.5,load)/1.5)*stripH),3);ctx.fill()});
 ctx.strokeStyle=C.blue;ctx.lineWidth=2.8;ctx.beginPath();let started=false;a.forEach((d,i)=>{const v=+d.vo2max;if(!Number.isFinite(v)||v<=20){started=false;return}const x=p.l+i*iw/Math.max(1,a.length-1),y=p.t+topH-((v-lo)/sp)*topH;pts.push({x,y,row:d});started?ctx.lineTo(x,y):ctx.moveTo(x,y);started=true});ctx.stroke();
 if(pts.length){const end=pts[pts.length-1];ctx.fillStyle=C.blue;ctx.beginPath();ctx.arc(end.x,end.y,4,0,Math.PI*2);ctx.fill();ctx.fillText('VO2',Math.min(w-28,end.x+22),end.y-8)}
 ctx.fillStyle=C.faint;ctx.textAlign='center';for(let i=0;i<Math.min(4,a.length);i++){const idx=Math.round(i*(a.length-1)/Math.max(1,Math.min(4,a.length)-1));ctx.fillText(a[idx].date.slice(5),p.l+idx*iw/Math.max(1,a.length-1),h-10)}
 ctx.textAlign='left';ctx.fillStyle=C.muted;ctx.fillText('load intensity',p.l,stripY-5);
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>VO2max</em><strong>${fmt(p.row.vo2max)}</strong></span><span><em>Load ratio</em><strong>${fmt(p.row.load_ratio)}</strong></span><span><em>HRV</em><strong>${fmt(p.row.hrv,' ms')}</strong></span>`);
}
function drawPace(){
 const list=acts().slice().reverse().filter(a=>paceSec(a.pace)).map(a=>({...a,paceSec:paceSec(a.pace)})),c=$('#paceChart'),{ctx,w,h}=canvas(c),p={l:48,r:16,t:18,b:34},iw=w-p.l-p.r,ih=h-p.t-p.b;if(!list.length)return;const vals=list.map(x=>x.paceSec),lo=Math.min(...vals)-15,hi=Math.max(...vals)+15,sp=hi-lo||1,avg7=ma(list,'paceSec',7),pts=[];
 ctx.clearRect(0,0,w,h);drawAxes(ctx,p,w,h,hi,v=>paceText(v));ctx.strokeStyle=C.blue;ctx.lineWidth=2.3;ctx.beginPath();list.forEach((d,i)=>{const x=p.l+i*iw/Math.max(1,list.length-1),y=h-p.b-((d.paceSec-lo)/sp)*ih;pts.push({x,y,row:d,avg:avg7[i]});i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();ctx.strokeStyle=C.green;ctx.setLineDash([5,4]);ctx.beginPath();avg7.forEach((v,i)=>{const x=p.l+i*iw/Math.max(1,list.length-1),y=h-p.b-((v-lo)/sp)*ih;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();ctx.setLineDash([]);
 ctx.fillStyle=C.faint;ctx.font='11px Geist';ctx.textAlign='center';for(let i=0;i<Math.min(5,list.length);i++){const idx=Math.round(i*(list.length-1)/Math.max(1,Math.min(5,list.length)-1));ctx.fillText(list[idx].date.slice(5),p.l+idx*iw/Math.max(1,list.length-1),h-10)}
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>配速</em><strong>${p.row.pace}</strong></span><span><em>7次均线</em><strong>${paceText(p.avg)} /km</strong></span><span><em>距离</em><strong>${fmt(p.row.distance_km,' km')}</strong></span>`);
}
function drawWeeks(){
 const weeks=(DATA.summaries.weekly||[]).slice(0,6).reverse(),c=$('#weekChart'),{ctx,w,h}=canvas(c),p={l:38,r:32,t:16,b:34},iw=w-p.l-p.r,ih=h-p.t-p.b,bw=iw/Math.max(1,weeks.length),max=nice(Math.max(...weeks.map(x=>x.distance_km||0),1));ctx.clearRect(0,0,w,h);drawAxes(ctx,p,w,h,max);
 weeks.forEach((wk,i)=>{const x=p.l+i*bw+bw*.12,bh=(wk.distance_km||0)/max*ih;ctx.fillStyle=C.blue;roundRect(ctx,x,h-p.b-bh,bw*.3,bh,5);ctx.fill();ctx.fillStyle=C.orange;roundRect(ctx,x+bw*.36,h-p.b-((wk.activity_min||0)/400)*ih,bw*.22,Math.max(2,((wk.activity_min||0)/400)*ih),5);ctx.fill();ctx.fillStyle=C.green;roundRect(ctx,x+bw*.63,h-p.b-((wk.activities||0)/12)*ih,bw*.22,Math.max(2,((wk.activities||0)/12)*ih),5);ctx.fill();ctx.fillStyle=C.faint;ctx.font='11px Geist';ctx.textAlign='center';ctx.fillText(wk.key.replace('2026-',''),p.l+i*bw+bw/2,h-10)});
}
function drawSleep(){
 const a=days(),c=$('#sleepChart'),{ctx,w,h}=canvas(c),p={l:38,r:12,t:16,b:32},iw=w-p.l-p.r,ih=h-p.t-p.b,bw=iw/Math.max(1,a.length),max=nice(Math.max(...a.map(d=>(d.deep_min||0)+(d.rem_min||0)+(d.light_min||0)+(d.awake_min||0)),480)),pts=[];ctx.clearRect(0,0,w,h);drawAxes(ctx,p,w,h,max,v=>(v/60).toFixed(0)+'h');
 a.forEach((d,i)=>{const x=p.l+i*bw+bw*.2;let y=h-p.b;[['deep_min',C.green],['rem_min','#8fd4aa'],['light_min','#d2dde9'],['awake_min',C.red]].forEach(([k,col])=>{const bh=(d[k]||0)/max*ih;if(bh>0){ctx.fillStyle=col;roundRect(ctx,x,y-bh,Math.max(2,bw*.56),Math.max(1,bh),4);ctx.fill();y-=bh}});pts.push({x:x+bw/2,y:Math.max(50,y),row:d})});hover(c,pts,p=>`<b>${p.row.date}</b><span><em>睡眠</em><strong>${fmt(p.row.sleep_min?(p.row.sleep_min/60).toFixed(1):null,' h')}</strong></span><span><em>深睡</em><strong>${fmt(p.row.deep_min,' min')}</strong></span><span><em>REM</em><strong>${fmt(p.row.rem_min,' min')}</strong></span>`);
}
function drawSpark(el,vals,col=C.blue){const{ctx,w,h}=canvas(el);vals=vals.filter(v=>Number.isFinite(+v)).map(Number);if(vals.length<2)return;const lo=Math.min(...vals),hi=Math.max(...vals),sp=hi-lo||1;ctx.clearRect(0,0,w,h);ctx.strokeStyle=col;ctx.lineWidth=2;ctx.beginPath();vals.forEach((v,i)=>{const x=i*w/Math.max(1,vals.length-1),y=h-4-(v-lo)/sp*(h-8);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}
function readiness(a){const d=last(a),rec=DATA.recovery.recovery_percent||0,hrv=d.hrv&&d.hrv_baseline?clamp(d.hrv/d.hrv_baseline*100,50,120):80,sleep=d.sleep_min?clamp(d.sleep_min/480*100,45,110):75,load=d.load_ratio<=1.15?90:d.load_ratio<1.35?68:45;return Math.round(clamp(rec*.38+hrv*.25+sleep*.22+load*.15,0,100))}
function renderCards(){const a=days(),activity=acts(),total=sum(activity,x=>x.distance_km),time=sum(activity,x=>x.duration_min),cal=sum(activity,x=>x.calories),elev=sum(activity,x=>x.elevation_gain),paces=activity.map(x=>paceSec(x.pace)).filter(Boolean),cards=[['总跑步次数',activity.length,'次',compare('count'),C.blue,'R'],['总距离',total.toFixed(1),'公里',compare('km'),C.green,'D'],['总时长',`${Math.floor(time/60)}:${String(Math.round(time%60)).padStart(2,'0')}`,'小时',compare('min'),C.orange,'T'],['平均配速',paceText(avg(paces,x=>x)),'/公里',change(avg(paces,x=>x),avg(DATA.activities.slice(-activity.length*2,-activity.length).map(x=>paceSec(x.pace)).filter(Boolean),x=>x)),C.blue,'P'],['总消耗',Math.round(cal).toLocaleString(),'千卡',compare('cal'),C.red,'K'],['累计爬升',Math.round(elev).toLocaleString(),'米',compare('elev'),C.cyan,'E']];$('#cards').innerHTML=cards.map((x,i)=>`<article class="card" style="--color:${x[4]};--tone:${x[4]}1a"><div class="card-head"><div class="dot">${x[5]}</div><span>${x[0]}</span></div><b>${x[1]} <small>${x[2]}</small></b>${trend(x[3],x[0]==='平均配速'?false:true)}<canvas class="spark" data-i="${i}"></canvas></article>`).join('');$$('.spark').forEach((c,i)=>drawSpark(c,[a.map(x=>x.count),a.map(x=>x.km),a.map(x=>x.min),paces,a.map(x=>x.cal),a.map(x=>x.elev)][i]||[],cards[i][4]))}
function renderStatus(){const a=days(),score=readiness(a),d=last(a),load=d.load_ratio||0,title=score>=75?'良好':score>=60?'保持':'恢复优先';$('#statusScore').textContent=score;$('#statusTitle').textContent=title;$('#statusText').textContent=load>1.2?'训练负荷偏高，下一次训练建议控制强度。':'训练负荷适中，可以维持节奏。';const r=62,circ=2*Math.PI*r,el=$('#statusRing');el.style.strokeDasharray=`${score/100*circ} ${circ}`;el.style.stroke=score>=75?C.green:score>=60?C.orange:C.red;$('#coachList').innerHTML=[['负荷状态',`当前 load ratio ${fmt(d.load_ratio)}，${d.load_status||'--'}。`],['恢复状态',`恢复 ${fmt(DATA.recovery.recovery_percent,'%')}，${DATA.recovery.level||'--'}。`],['HRV 对比',d.hrv_baseline?`HRV ${d.hrv} ms，相对基线 ${Math.round(d.hrv-d.hrv_baseline)} ms。`:'HRV 基线暂缺。']].map(x=>`<div><b>${x[0]}</b><p>${x[1]}</p></div>`).join('')}
function renderActivities(){const sports=['All',...new Set(DATA.activities.map(a=>a.sport).filter(Boolean))];$('#filters').innerHTML=sports.map(s=>`<button class="${s===sportFilter?'active':''}" data-sport="${s}">${s}</button>`).join('');$$('#filters button').forEach(b=>b.onclick=()=>{sportFilter=b.dataset.sport;render()});const list=acts().slice(0,18);$('#activityCount').textContent=`${list.length} records`;$('#activityList').innerHTML=list.map((a,i)=>`<article class="activity" data-i="${i}"><div class="badge">${Math.round(a.distance_km||0)}</div><div><h3>${a.location||a.sport}</h3><p>${a.date} · ${a.sport} · ${a.pace}</p></div><div class="num">${fmt(a.training_load,' TL')}</div></article>`).join('')||'<div class="empty">No activities</div>';$$('.activity').forEach((el,i)=>el.onclick=()=>openDrawer(list[i]))}
function openDrawer(a){$('#drawerTitle').textContent=a.location||a.sport;$('#drawerStats').innerHTML=[['Distance',fmt(a.distance_km,' km')],['Pace',fmt(a.pace)],['Time',fmt(a.duration)],['Avg HR',fmt(a.avg_hr,' bpm')],['Load',fmt(a.training_load,' TL')],['Power',fmt(a.avg_power,' W')]].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('');$('#drawer').classList.add('show')}
function renderAchievements(){const a=days(),activity=acts(),total=sum(activity,x=>x.distance_km),cal=sum(activity,x=>x.calories),elev=sum(activity,x=>x.elevation_gain);$('#achievements').innerHTML=[['坚持达人',`完成跑步 ${activity.length} 次`,C.blue,'RUN'],['距离达人',`累计 ${total.toFixed(1)} 公里`,C.green,'KM'],['燃脂高手',`累计消耗 ${Math.round(cal)} 千卡`,C.red,'K'],['攀登者',`累计爬升 ${Math.round(elev)} 米`,C.orange,'UP']].map(x=>`<div class="medal" style="--color:${x[2]};--tone:${x[2]}1a"><i>${x[3]}</i><b>${x[0]}</b><span>${x[1]}</span></div>`).join('')}
function render(){const a=days();$('#rangeTitle').textContent=range==='all'?'全部':`${range}天`;$('#dateLabel').textContent=`${a[0]?.date||'--'} – ${last(a).date||'--'}`;renderCards();renderStatus();renderActivities();renderAchievements();drawDistance();drawAbility();drawPace();drawWeeks();drawSleep()}
$$('.seg button').forEach(b=>b.onclick=()=>{$$('.seg button').forEach(x=>x.classList.remove('active'));b.classList.add('active');range=b.dataset.range;render()});$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('show');addEventListener('resize',render);render();
</script>
</body>
</html>""".replace("__DATA__", json_blob)
    return html_doc


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        return

    def send_bytes(self, body, ctype, code=200):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if BASE_PATH and path.startswith(BASE_PATH + "/"):
            path = path[len(BASE_PATH):] or "/"
        if path in ("/", "/index.html", "/data.json"):
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8" if path == "/data.json" else "text/html; charset=utf-8",
            )
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if BASE_PATH and path == BASE_PATH:
            self.send_response(301)
            self.send_header("Location", BASE_PATH + "/")
            self.end_headers()
            return
        if BASE_PATH and path.startswith(BASE_PATH + "/"):
            path = path[len(BASE_PATH):] or "/"
        if path in ("/", "/index.html"):
            self.send_bytes(build_home().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/data.json":
            with open(DATA_FILE, "rb") as f:
                self.send_bytes(f.read(), "application/json; charset=utf-8")
            return
        self.send_bytes(b"not found", "text/plain; charset=utf-8", 404)


def main():
    TCPServer.allow_reuse_address = True
    with TCPServer((HOST, PORT), Handler) as httpd:
        print("Sports Log Web -> http://%s:%s%s/" % (HOST, PORT, BASE_PATH), flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
