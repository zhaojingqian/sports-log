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
    day = latest(data)
    recent = data.get("daily", [])[-30:]
    acts = data.get("activities", [])
    profile = data.get("profile", {})
    recovery = data.get("recovery", {})
    fitness = data.get("fitness", {})
    meta = data.get("meta", {})
    total_distance = round(sum(a.get("distance_km") or 0 for a in acts), 1)
    total_minutes = int(sum(a.get("duration_min") or 0 for a in acts))
    race = fitness.get("race_predictions", {})
    devices = "".join(
        '<li><strong>%s</strong><span>%s · %s · 保修至 %s</span></li>'
        % (esc(d.get("name")), esc(d.get("model")), esc(d.get("serial")), esc(d.get("warranty_expires")))
        for d in data.get("devices", [])
    )
    schedule = "".join(
        '<li><strong>%s</strong><span>%s · %s km · TL %s</span></li>'
        % (esc(s.get("date")), esc(s.get("name")), esc(s.get("distance_km")), esc(s.get("load")))
        for s in data.get("schedule", [])
    ) or "<li><span>暂无计划</span></li>"
    status_class = "warn" if "requires" in meta.get("automation_status", "") else "ok"
    html_doc = """<!doctype html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Sports Log · COROS</title>
<style>
:root{color-scheme:light;--ink:#16211d;--muted:#66736e;--line:#d8e1dc;--paper:#f6f4ed;--panel:#ffffff;--green:#167a5b;--blue:#2766a6;--red:#bd4a3a;--gold:#b07a21}
*{box-sizing:border-box}body{margin:0;font-family:Inter,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;background:var(--paper);color:var(--ink);letter-spacing:0}
a{color:inherit}.shell{max-width:1220px;margin:0 auto;padding:22px}.hero{display:grid;grid-template-columns:1.3fr .7fr;gap:18px;align-items:stretch;margin-bottom:18px}
.hero-main{background:linear-gradient(135deg,#e9f4ef,#f7f1df);border:1px solid var(--line);border-radius:8px;padding:28px;min-height:260px;display:flex;flex-direction:column;justify-content:space-between}
.hero h1{font-size:46px;line-height:1;margin:0 0 12px}.hero p{margin:0;color:var(--muted);font-size:16px;max-width:720px}.hero-side{background:#101817;color:white;border-radius:8px;padding:22px;display:grid;gap:12px}
.pill{display:inline-flex;width:max-content;align-items:center;border:1px solid rgba(255,255,255,.25);border-radius:999px;padding:6px 10px;color:#d9eee7;font-size:13px}
.stats{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:12px;margin:18px 0}.stat{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:16px;min-height:102px}.stat span{display:block;color:var(--muted);font-size:13px}.stat strong{display:block;font-size:28px;margin:8px 0 4px}.stat small{color:var(--muted)}
.grid{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:14px}.panel,.chart{background:var(--panel);border:1px solid var(--line);border-radius:8px;padding:18px;min-width:0}.wide{grid-column:1/-1}.panel h2,.chart h3{margin:0 0 14px;font-size:18px}.list{list-style:none;margin:0;padding:0;display:grid;gap:10px}.list li{display:flex;justify-content:space-between;gap:12px;border-bottom:1px solid #edf1ee;padding-bottom:10px}.list li:last-child{border-bottom:0}.list span{color:var(--muted);text-align:right}
.race{display:grid;grid-template-columns:repeat(2,1fr);gap:10px}.race div{background:#f2f6f4;border:1px solid var(--line);border-radius:6px;padding:12px}.race b{display:block;font-size:24px}.race span{color:var(--muted);font-size:13px}
.bars{height:150px;display:flex;align-items:end;gap:4px;border-bottom:1px solid var(--line);padding-top:12px}.bar{height:130px;flex:1;display:flex;align-items:end;justify-content:center;position:relative}.bar i{display:block;width:100%%;background:linear-gradient(180deg,var(--green),#8abf7f);border-radius:3px 3px 0 0}.bar b{position:absolute;bottom:-20px;font-size:10px;color:var(--muted)}
svg{width:100%%;height:150px}svg line{stroke:var(--line)}svg polyline{fill:none;stroke-width:4;stroke-linecap:round;stroke-linejoin:round}
.table-wrap{overflow:auto}table{width:100%%;border-collapse:collapse;font-size:14px}th,td{text-align:left;border-bottom:1px solid #e6ece8;padding:10px;vertical-align:top}th{color:var(--muted);font-weight:650;white-space:nowrap}td{min-width:88px}.ok{color:var(--green)}.warn{color:var(--red)}
.foot{color:var(--muted);font-size:13px;margin:18px 0 4px}.sources{display:flex;gap:10px;flex-wrap:wrap}.sources a{color:var(--blue);font-size:13px}
@media (max-width:900px){.hero,.grid{grid-template-columns:1fr}.stats{grid-template-columns:repeat(2,1fr)}.hero h1{font-size:34px}.shell{padding:14px}.list li{display:block}.list span{text-align:left;display:block;margin-top:4px}}
@media (max-width:520px){.stats{grid-template-columns:1fr}.race{grid-template-columns:1fr}}
</style>
</head>
<body>
<main class="shell">
<section class="hero">
  <div class="hero-main">
    <div>
      <h1>Sports Log</h1>
      <p>把 COROS MCP 连接到的健康、恢复、训练负荷、跑力评估、运动记录和训练计划集中放在一个页面里。每天 23:00 更新当天数据，周/月自动汇总。</p>
    </div>
    <div class="foot">当前窗口：%s 至 %s · 更新时间：%s · <span class="%s">%s</span></div>
  </div>
  <aside class="hero-side">
    <span class="pill">%s · %s kg · %s cm</span>
    <div><strong style="font-size:52px">%s%%</strong><br><span>%s · 完全恢复约 %s</span></div>
    <div>VO2max <strong style="font-size:34px">%s</strong> 阈值配速 %s</div>
  </aside>
</section>
<section class="stats">%s</section>
<section class="grid">
  <section class="panel"><h2>比赛预测</h2><div class="race">%s</div></section>
  <section class="panel"><h2>设备</h2><ul class="list">%s</ul></section>
  <section class="panel"><h2>训练计划</h2><ul class="list">%s</ul></section>
  %s
  %s
  %s
  %s
  %s
  %s
  %s
  %s
  %s
</section>
<p class="foot">汇总规则参考 COROS 训练负荷/训练状态、ACSM/CDC 成人活动建议、TrainingPeaks TSB 负荷平衡思路。这里用于个人训练观察，不构成医疗建议。</p>
<div class="sources">%s</div>
</main>
</body>
</html>""" % (
        esc(meta.get("window_start")),
        esc(meta.get("window_end")),
        esc(meta.get("generated_at")),
        status_class,
        esc(meta.get("automation_status")),
        esc(profile.get("nickname")),
        esc(profile.get("weight_kg")),
        esc(profile.get("height_cm")),
        esc(recovery.get("recovery_percent")),
        esc(recovery.get("level")),
        esc(recovery.get("estimated_full_recovery")),
        esc(fitness.get("vo2max")),
        esc(fitness.get("threshold_pace")),
        "".join(
            [
                stat_card("今日步数", fmt(day.get("steps")), "压力 %s · 睡眠分 %s" % (fmt(day.get("stress")), fmt(day.get("sleep_score")))),
                stat_card("30天跑量", "%.1f km" % total_distance, "%s 次运动 · %s min" % (len(acts), total_minutes)),
                stat_card("HRV / 静息心率", "%s ms" % fmt(day.get("hrv")), "%s · RHR %s bpm" % (fmt(day.get("hrv_status")), fmt(day.get("rhr")))),
                stat_card("训练负荷比", fmt(day.get("load_ratio")), "%s · %s/%s" % (fmt(day.get("load_status")), fmt(day.get("short_load")), fmt(day.get("long_load")))),
            ]
        ),
        "".join('<div><span>%s</span><b>%s</b></div>' % (esc(k), esc(v)) for k, v in race.items()),
        devices,
        schedule,
        workout_panel(data.get("workouts", [])),
        cache_panel(data),
        bars(recent, "steps", "步数趋势"),
        bars(recent, "sleep_score", "睡眠分趋势", scale=100),
        trend_svg(recent, "hrv", "HRV 趋势", "#2766a6"),
        trend_svg(recent, "load_ratio", "训练负荷比", "#b07a21"),
        summary_table("周汇总", data.get("summaries", {}).get("weekly", [])),
        summary_table("月汇总", data.get("summaries", {}).get("monthly", [])),
        activity_table(acts),
        "".join('<a href="%s">%s</a>' % (esc(r.get("url")), esc(r.get("name"))) for r in meta.get("references", [])),
    )
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
