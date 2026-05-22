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
:root{color-scheme:light;--ink:#101615;--muted:#5f6f68;--faint:#8a9891;--paper:#f7f8f4;--panel:#fffefa;--panel2:#f7faf7;--line:#dfe5dc;--line2:#edf1ea;--green:#12805f;--teal:#147f9f;--blue:#4267d5;--coral:#d55f45;--gold:#bd8425;--violet:#7660ca;--shadow:0 18px 54px rgba(25,36,31,.09);--font:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}
*{box-sizing:border-box}html{scroll-behavior:smooth}body{margin:0;background:linear-gradient(180deg,#fbfcf8 0,#f0f4ee 48%,#f8f3ec 100%);color:var(--ink);font-family:var(--font);letter-spacing:0;font-size:14px;line-height:1.45;-webkit-font-smoothing:antialiased;text-rendering:optimizeLegibility}
button{font:inherit}.shell{max-width:1248px;margin:0 auto;padding:18px}.top{display:flex;align-items:center;justify-content:space-between;gap:12px;margin-bottom:12px}.brand{display:flex;align-items:center;gap:10px}.mark{width:34px;height:34px;border-radius:50%;background:conic-gradient(from 210deg,var(--green),var(--teal),var(--blue),var(--coral),var(--gold),var(--green));box-shadow:inset 0 0 0 7px #fff9}.brand h1{font-size:18px;line-height:1.1;margin:0;font-weight:760;letter-spacing:-.01em}.brand span{font-size:11px;color:var(--faint)}.seg{display:flex;background:rgba(255,254,250,.9);border:1px solid var(--line);border-radius:10px;padding:3px;gap:2px;box-shadow:0 8px 28px rgba(20,28,24,.06)}.seg button{border:0;background:transparent;border-radius:7px;padding:6px 9px;color:var(--muted);cursor:pointer;font-size:12px;font-weight:660;min-width:42px}.seg button.active{background:var(--ink);color:white}
.hero{display:grid;grid-template-columns:minmax(0,1.36fr) minmax(316px,.64fr);gap:12px;margin-bottom:12px}.stage{position:relative;min-height:330px;border:1px solid var(--line);border-radius:10px;background:linear-gradient(135deg,#fffef8 0,#e9f6f0 46%,#e9f0ff 70%,#faeadf 100%);overflow:hidden;padding:20px;box-shadow:var(--shadow)}.stage:after{content:"";position:absolute;inset:auto 0 0 0;height:38%;background:linear-gradient(180deg,transparent,rgba(255,255,255,.54))}.stage canvas{position:absolute;inset:0;width:100%;height:100%}.hero-copy{position:relative;z-index:1;display:flex;flex-direction:column;height:100%;justify-content:space-between}.kicker{display:flex;gap:7px;flex-wrap:wrap}.chip{display:inline-flex;align-items:center;border:1px solid rgba(255,255,255,.74);background:rgba(255,255,255,.66);backdrop-filter:blur(8px);border-radius:999px;padding:6px 9px;font-size:11px;font-weight:650;color:#40504a}.headline{margin-top:58px}.headline strong{display:block;font-size:clamp(46px,7vw,76px);font-weight:800;letter-spacing:-.055em;line-height:.88}.headline span{display:block;color:var(--muted);margin-top:10px;font-size:13px;font-weight:560}.side{display:grid;gap:12px}.ring-card,.panel,.metric{border:1px solid var(--line);border-radius:10px;background:rgba(255,254,250,.9);box-shadow:var(--shadow)}.ring-card{padding:18px;display:grid;grid-template-columns:132px 1fr;gap:14px;align-items:center}.ring{position:relative;width:128px;height:128px}.ring svg{width:128px;height:128px;transform:rotate(-90deg)}.ring circle{fill:none;stroke-width:12;stroke-linecap:round}.ring .bg{stroke:#e7ece4}.ring .fg{stroke:var(--green);stroke-dasharray:0 999}.ring b{position:absolute;inset:0;display:grid;place-items:center;font-size:30px;font-weight:780;letter-spacing:-.035em}.ring-card h2{margin:0 0 6px;font-size:11px;text-transform:uppercase;letter-spacing:.1em;color:var(--faint);font-weight:740}.ring-card .big{font-size:25px;line-height:1.08;font-weight:780;letter-spacing:-.03em}
.metrics{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:10px;margin-bottom:12px}.metric{padding:14px;min-height:116px;position:relative;overflow:hidden}.metric:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:var(--accent,var(--green))}.metric span,.panel h2 small{display:block;color:var(--faint);font-size:11px;font-weight:650}.metric b{display:block;font-size:26px;line-height:1.04;font-weight:780;letter-spacing:-.04em;margin:8px 0 6px}.metric em{font-style:normal;color:var(--delta,var(--muted));font-size:11px;font-weight:720}.spark{width:100%;height:30px;margin-top:5px}.grid{display:grid;grid-template-columns:1.24fr .76fr;gap:12px}.panel{padding:16px;min-width:0}.panel h2{display:flex;align-items:flex-end;justify-content:space-between;margin:0;font-size:14px;font-weight:760;letter-spacing:-.01em}.panel-head{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;margin-bottom:10px}.legend{display:flex;align-items:center;gap:10px;flex-wrap:wrap;color:var(--faint);font-size:11px;font-weight:650}.legend i{display:inline-block;width:16px;height:4px;border-radius:999px;margin-right:5px;vertical-align:2px}.chart-note{display:flex;gap:8px;flex-wrap:wrap;margin:-2px 0 10px}.pill{border:1px solid var(--line2);background:var(--panel2);border-radius:999px;padding:5px 8px;color:var(--muted);font-size:11px;font-weight:680}.canvas-wrap{height:306px;position:relative}.canvas-wrap canvas{width:100%;height:100%;display:block}.chart-tip{position:absolute;pointer-events:none;z-index:4;min-width:150px;border:1px solid rgba(16,22,21,.12);background:rgba(17,24,22,.94);color:white;border-radius:9px;padding:8px 9px;font-size:11px;line-height:1.45;box-shadow:0 16px 38px rgba(0,0,0,.20);transform:translate(-50%,-112%);opacity:0;transition:opacity .12s ease}.chart-tip b{display:block;font-size:12px;margin-bottom:3px}.chart-tip span{display:flex;justify-content:space-between;gap:18px;color:#d8e0dc}.chart-tip em{font-style:normal;color:#aab8b2}.chart-tip strong{font-weight:760;color:white}.trio{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.tile{border:1px solid var(--line2);border-radius:9px;background:var(--panel2);padding:11px;min-width:0}.tile b{display:block;font-size:18px;line-height:1.1;font-weight:760;letter-spacing:-.025em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.tile span,.summary span,.pred span{display:block;color:var(--faint);font-size:11px;font-weight:650}.heat{display:grid;grid-template-columns:repeat(19,1fr);gap:4px}.cell{aspect-ratio:1;border-radius:4px;background:#e7ece4;position:relative}.cell[data-lvl="1"]{background:#bfe1cd}.cell[data-lvl="2"]{background:#75c898}.cell[data-lvl="3"]{background:#208c67}.cell[data-lvl="4"]{background:#0d6048}.activity-list{display:grid;gap:8px;max-height:478px;overflow:auto;padding-right:2px}.activity{display:grid;grid-template-columns:50px 1fr auto;gap:10px;align-items:center;border:1px solid var(--line2);background:#fffdfa;border-radius:9px;padding:9px;cursor:pointer;transition:transform .16s ease,border-color .16s ease,background .16s ease}.activity:hover{border-color:#bdcac1;background:#fbfffb;transform:translateY(-1px)}.badge{width:42px;height:42px;border-radius:50%;display:grid;place-items:center;background:#e8f5ef;color:#126046;font-size:14px;font-weight:780}.activity h3{margin:0;font-size:13px;line-height:1.25;font-weight:720;letter-spacing:-.01em}.activity p{margin:3px 0 0;color:var(--faint);font-size:11px}.activity .num{text-align:right;font-size:12px;font-weight:780}.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px}.filters button{border:1px solid var(--line);background:#fffdfa;border-radius:999px;padding:5px 9px;color:var(--muted);cursor:pointer;font-size:11px;font-weight:660}.filters button.active{background:var(--ink);color:white;border-color:var(--ink)}
.wide{grid-column:1/-1}.summary-row{display:grid;grid-template-columns:repeat(5,1fr);gap:8px}.summary{border:1px solid var(--line2);border-radius:9px;background:#fffdfa;padding:12px;min-height:104px}.summary b{display:block;font-size:21px;line-height:1.1;font-weight:780;letter-spacing:-.03em;margin:5px 0}.progress{height:7px;background:#e8eee6;border-radius:999px;overflow:hidden;margin-top:10px}.progress i{display:block;height:100%;background:linear-gradient(90deg,var(--green),var(--gold));width:0}.preds{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.pred{border:1px solid var(--line2);border-radius:9px;padding:11px;background:var(--panel2)}.pred b{display:block;font-size:19px;font-weight:780;letter-spacing:-.025em}.muted{color:var(--muted);font-size:12px}.detail-drawer{position:fixed;right:18px;bottom:18px;width:min(420px,calc(100% - 36px));background:#101615;color:white;border-radius:10px;padding:18px;box-shadow:0 24px 80px #0007;transform:translateY(130%);transition:.25s ease;z-index:5}.detail-drawer.show{transform:translateY(0)}.detail-drawer button{position:absolute;right:12px;top:10px;border:0;background:#ffffff18;color:white;border-radius:50%;width:30px;height:30px;cursor:pointer}.detail-drawer h2{margin:0 36px 12px 0;font-size:18px;letter-spacing:-.02em}.detail-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:8px}.detail-stats div{background:#ffffff12;border-radius:8px;padding:10px}.detail-stats span{display:block;color:#b9c6c0;font-size:11px}.detail-stats b{font-size:15px}.empty{color:var(--muted);padding:18px;font-size:12px}
@media (max-width:980px){.hero,.grid{grid-template-columns:1fr}.metrics{grid-template-columns:repeat(2,1fr)}.summary-row,.preds{grid-template-columns:repeat(2,1fr)}}
@media (max-width:560px){.shell{padding:12px}.top{align-items:flex-start;gap:10px}.seg{overflow:auto;max-width:100%}.seg button{min-width:38px;padding:6px 8px}.hero{gap:10px}.stage{min-height:286px}.headline{margin-top:42px}.ring-card{grid-template-columns:1fr}.metrics,.summary-row,.preds{grid-template-columns:1fr}.activity{grid-template-columns:44px 1fr}.activity .num{grid-column:2;text-align:left}.heat{grid-template-columns:repeat(10,1fr)}.panel-head{display:block}.legend{margin-top:7px}.canvas-wrap{height:280px}}
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
<section class="grid">
  <section class="panel">
    <div class="panel-head"><h2>Training Load <small id="rangeLabel"></small></h2><div class="legend"><span><i style="background:linear-gradient(90deg,#12805f,#48b989)"></i>km</span><span><i style="background:#d55f45"></i>load</span><span><i style="background:#4267d5"></i>7d avg</span></div></div>
    <div class="chart-note" id="loadInsight"></div><div class="canvas-wrap"><canvas id="distanceChart"></canvas></div>
  </section>
  <section class="panel"><h2>Recent Activities <small id="activityCount"></small></h2><div class="filters" id="filters"></div><div class="activity-list" id="activityList"></div></section>
  <section class="panel">
    <div class="panel-head"><h2>Recovery Signals</h2><div class="legend"><span><i style="background:#147f9f"></i>HRV</span><span><i style="background:#d55f45"></i>RHR</span><span><i style="background:#bd8425"></i>load</span></div></div>
    <div class="chart-note" id="healthInsight"></div><div class="canvas-wrap"><canvas id="healthChart"></canvas></div>
  </section>
  <section class="panel"><h2>Run Heat</h2><div class="heat" id="heatmap"></div></section>
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
const $ = (q) => document.querySelector(q);
const fmt = (v, suffix='') => (v === null || v === undefined || v === '') ? '--' : `${v}${suffix}`;
const sum = (arr, fn) => arr.reduce((a, x) => a + (+fn(x) || 0), 0);
const avg = (arr, fn) => {
  const vals = arr.map(fn).filter(v => Number.isFinite(+v));
  return vals.length ? vals.reduce((a,b)=>a + +b, 0) / vals.length : null;
};
const last = (arr) => arr[arr.length - 1] || {};
const COLORS = {ink:'#101615', muted:'#5f6f68', faint:'#8a9891', grid:'#e3e9e0', green:'#12805f', teal:'#147f9f', blue:'#4267d5', coral:'#d55f45', gold:'#bd8425', violet:'#7660ca'};
const clamp = (v, lo, hi) => Math.max(lo, Math.min(hi, v));
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
  const map = new Map();
  DATA.activities.forEach(a => map.set(a.date, (map.get(a.date)||0) + (a.distance_km||0)));
  return sourceRows.map(d => ({...d, km: map.get(d.date) || 0}));
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
  const bg = ctx.createLinearGradient(0,0,w,h); bg.addColorStop(0,'rgba(18,128,95,.08)'); bg.addColorStop(.55,'rgba(66,103,213,.08)'); bg.addColorStop(1,'rgba(213,95,69,.08)');
  ctx.fillStyle = bg; ctx.fillRect(0,0,w,h);
  ctx.lineWidth = Math.max(6, Math.min(14, w / Math.max(20, a.length) * .72)); ctx.lineCap = 'round';
  for(let i=0;i<a.length;i++){
    const x = 26 + i * ((w-52)/Math.max(1,a.length-1));
    const y = h - 45 - ((a[i].hrv || 0)/maxHrv) * (h-120);
    const km = a[i].km || 0;
    ctx.strokeStyle = km > 10 ? 'rgba(213,95,69,.45)' : km > 0 ? 'rgba(18,128,95,.38)' : 'rgba(66,103,213,.18)';
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
  ctx.font = '11px Inter, system-ui'; ctx.textBaseline = 'middle';
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
  ctx.strokeStyle = COLORS.blue; ctx.lineWidth = 2; ctx.setLineDash([5,4]); ctx.beginPath();
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
  ctx.fillStyle = COLORS.faint; ctx.font = '11px Inter, system-ui'; ctx.textAlign='center'; ctx.textBaseline='alphabetic';
  for(let i=0;i<tickCount;i++){ const idx=Math.round(i*(a.length-1)/Math.max(1,tickCount-1)); const x=pad.l+idx*bw+bw/2; ctx.fillText(a[idx]?.date?.slice(5) || '', x, h-10); }
  bindHover(canvas, pts.map(p=>({...p,y:Math.min(p.y,h-82)})), p=>`<b>${p.row.date}</b><span><em>Distance</em><strong>${(p.row.km||0).toFixed(1)} km</strong></span><span><em>Load ratio</em><strong>${fmt(p.row.load_ratio)}</strong></span><span><em>Training load</em><strong>${fmt(p.row.training_load)}</strong></span>`);
}
function drawHealth(){
  const a = rows(); const canvas = $('#healthChart'); const {ctx,w,h}=resizeCanvas(canvas);
  ctx.clearRect(0,0,w,h);
  const pad={l:36,r:20,t:22,b:34}; const innerH=h-pad.t-pad.b, innerW=w-pad.l-pad.r;
  const keys=[['hrv','HRV',COLORS.teal],['rhr','RHR',COLORS.coral],['load_ratio','Load',COLORS.gold]];
  ctx.strokeStyle=COLORS.grid; ctx.lineWidth=1; ctx.fillStyle=COLORS.faint; ctx.font='11px Inter, system-ui'; ctx.textBaseline='middle';
  for(let i=0;i<5;i++){const y=pad.t+i*innerH/4;ctx.beginPath();ctx.moveTo(pad.l,y);ctx.lineTo(w-pad.r,y);ctx.stroke(); if(i===0)ctx.fillText('high',6,y); if(i===4)ctx.fillText('low',8,y);}
  const hoverMap = new Map();
  keys.forEach(([key,label,color])=>{
    const vals=a.map(d=>+d[key]).filter(Number.isFinite); if(!vals.length) return;
    const min=Math.min(...vals), max=Math.max(...vals), span=max-min||1;
    const pts=[];
    ctx.strokeStyle=color; ctx.lineWidth=3; ctx.beginPath();
    a.forEach((d,i)=>{ const v=+d[key]; if(!Number.isFinite(v)) return; const x=pad.l+i*(innerW/Math.max(1,a.length-1)); const y=h-pad.b-((v-min)/span)*innerH; pts.push({x,y,row:d,key,label,value:v}); hoverMap.set(d.date,{...(hoverMap.get(d.date)||{}), x, y, row:d, [key]:v}); pts.length>1?ctx.lineTo(x,y):ctx.moveTo(x,y); });
    ctx.stroke();
    const end=pts[pts.length-1]; if(end){ctx.fillStyle=color;ctx.beginPath();ctx.arc(end.x,end.y,4,0,Math.PI*2);ctx.fill();ctx.font='11px Inter, system-ui';ctx.fillText(label,end.x-22,end.y-9);}
  });
  const points = [...hoverMap.values()].sort((a,b)=>a.row.date.localeCompare(b.row.date));
  ctx.fillStyle = COLORS.faint; ctx.textAlign='center'; ctx.textBaseline='alphabetic';
  for(let i=0;i<Math.min(5,a.length);i++){ const idx=Math.round(i*(a.length-1)/Math.max(1,Math.min(5,a.length)-1)); const x=pad.l+idx*(innerW/Math.max(1,a.length-1)); ctx.fillText(a[idx]?.date?.slice(5)||'',x,h-10); }
  bindHover(canvas, points, p=>`<b>${p.row.date}</b><span><em>HRV</em><strong>${fmt(p.hrv,' ms')}</strong></span><span><em>RHR</em><strong>${fmt(p.rhr,' bpm')}</strong></span><span><em>Load</em><strong>${fmt(p.load_ratio)}</strong></span>`);
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
    ['Time', `${mins} min`, `${Math.round(mins/60)}h moving`, trendText(kmTrend,true), activityRows.map(x=>x.duration_min||0), COLORS.blue],
    ['HRV', hrv?`${Math.round(hrv)} ms`:'--', `RHR ${rhr?Math.round(rhr):'--'}`, trendText(hrvTrend,true), a.map(x=>x.hrv||0), COLORS.teal],
    ['Sleep', sleep?`${(sleep/60).toFixed(1)} h`:'--', `last ${fmt(day.sleep_min ? (day.sleep_min/60).toFixed(1) : null,'h')}`, trendText(sleepTrend,true), a.map(x=>x.sleep_min||0), COLORS.violet||'#7660ca']
  ];
  $('#metrics').innerHTML = items.map((it,idx)=>`<section class="metric" style="--accent:${it[5]};--delta:${it[3].includes('watch')?COLORS.coral:it[3].includes('better')?COLORS.green:COLORS.muted}"><span>${it[0]}</span><b>${it[1]}</b><span>${it[2]}</span><em>${it[3]}</em><canvas class="spark" data-idx="${idx}"></canvas></section>`).join('');
  document.querySelectorAll('.spark').forEach((c,i)=>drawSpark(c,items[i][4]));
}
function drawSpark(canvas, vals){
  const {ctx,w,h}=resizeCanvas(canvas); ctx.clearRect(0,0,w,h);
  vals = vals.filter(v=>Number.isFinite(+v)).map(Number);
  if(vals.length < 2) return;
  const max=Math.max(...vals,1), min=Math.min(...vals,0), span=max-min||1;
  const grad=ctx.createLinearGradient(0,0,w,0); grad.addColorStop(0,'rgba(18,128,95,.28)'); grad.addColorStop(1,'rgba(66,103,213,.95)');
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
  const map=new Map(); DATA.activities.forEach(a=>map.set(a.date,(map.get(a.date)||0)+(a.distance_km||0)));
  const a=rows(); $('#heatmap').innerHTML=a.map(d=>{const km=map.get(d.date)||0; const lvl=km>12?4:km>8?3:km>3?2:km>0?1:0; return `<div class="cell" data-lvl="${lvl}" title="${d.date} · ${km.toFixed(1)} km"></div>`}).join('');
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
  ring('#recoveryRing', DATA.recovery.recovery_percent || 0, 100, '#15936b');
  ring('#vo2Ring', DATA.fitness.vo2max || 0, 70, '#1d8ea5');
  drawHero();
}
function render(){
  renderHero(); renderMetrics(); renderActivities(); renderHeat(); renderWeeks(); renderPreds(); renderNext();
  $('#rangeLabel').textContent = range === 'all' ? 'all data' : range === '1' ? '1 day' : `${range} days`;
  const dayRows = dailyWithKm();
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
  drawBars('#distanceChart', dayRows, 'km', 'load_ratio');
  drawHealth();
}
document.querySelectorAll('.seg button').forEach(b=>b.onclick=()=>{document.querySelectorAll('.seg button').forEach(x=>x.classList.remove('active'));b.classList.add('active');range=b.dataset.range;render();});
$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('show');
window.addEventListener('resize', render);
render();
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
