#!/usr/bin/env python3
"""Sports Log web server."""

import html
import json
import os
import secrets
import subprocess
import sys
import tempfile
import threading
import time
import cgi
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
from socketserver import ThreadingMixIn
from urllib.parse import parse_qs

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(BASE_DIR, "data", "dashboard.json")
REFRESH_SCRIPT = os.path.join(BASE_DIR, "scripts", "refresh_data.py")
ACTIVITY_DETAIL_SCRIPT = os.path.join(BASE_DIR, "scripts", "activity_detail.py")
REFRESH_TOKEN_FILE = os.path.join(BASE_DIR, ".refresh-token")
PORT = int(os.environ.get("PORT", "18081"))
HOST = os.environ.get("HOST", "127.0.0.1")
BASE_PATH = os.environ.get("BASE_PATH", "").rstrip("/")
PYTHON_BIN = os.environ.get("SPORTS_LOG_PYTHON", sys.executable)
COROS_PYTHON = os.environ.get("COROS_PYTHON", "/root/workspace/coros-mcp/.venv/bin/python")
REFRESH_TIMEOUT = int(os.environ.get("SPORTS_LOG_REFRESH_TIMEOUT", "600"))
DETAIL_TIMEOUT = int(os.environ.get("SPORTS_LOG_DETAIL_TIMEOUT", "45"))
SAFE_REFRESH_COOLDOWN_SECONDS = int(os.environ.get("SPORTS_LOG_SAFE_REFRESH_COOLDOWN", "120"))
FIT_UPLOAD_LIMIT = int(os.environ.get("SPORTS_LOG_FIT_UPLOAD_LIMIT", str(20 * 1024 * 1024)))
REFRESH_RUN_LOCK = threading.Lock()
REFRESH_STATE_LOCK = threading.Lock()
DETAIL_CACHE_LOCK = threading.Lock()
DETAIL_CACHE = {}
LAST_SAFE_REFRESH_AT = 0.0
REFRESH_STATUS = {
    "running": False,
    "ok": None,
    "code": None,
    "mode": None,
    "started_at": None,
    "finished_at": None,
    "message": "",
}


class ThreadedHTTPServer(ThreadingMixIn, HTTPServer):
    daemon_threads = True


def strip_base_path(path):
    if BASE_PATH and path.startswith(BASE_PATH + "/"):
        return path[len(BASE_PATH):] or "/"
    return path


def timestamp():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def text_tail(value, limit=4000):
    if value is None:
        return ""
    if isinstance(value, bytes):
        value = value.decode("utf-8", "replace")
    value = str(value).strip()
    return value[-limit:] if len(value) > limit else value


def get_refresh_token():
    token = os.environ.get("SPORTS_LOG_REFRESH_TOKEN", "").strip()
    if token:
        return token
    if os.path.exists(REFRESH_TOKEN_FILE):
        with open(REFRESH_TOKEN_FILE, encoding="utf-8") as f:
            token = f.read().strip()
        if token:
            return token
    token = secrets.token_urlsafe(32)
    with open(REFRESH_TOKEN_FILE, "w", encoding="utf-8") as f:
        f.write(token + "\n")
    os.chmod(REFRESH_TOKEN_FILE, 0o600)
    return token


def is_refresh_authorized(token):
    return bool(token) and secrets.compare_digest(token.strip(), get_refresh_token())


def refresh_status_snapshot():
    with REFRESH_STATE_LOCK:
        return dict(REFRESH_STATUS)


def public_refresh_status_snapshot():
    status = refresh_status_snapshot()
    return {
        "running": status.get("running"),
        "ok": status.get("ok"),
        "code": status.get("code"),
        "mode": status.get("mode"),
        "started_at": status.get("started_at"),
        "finished_at": status.get("finished_at"),
    }


def refresh_weeks(env_name, default):
    try:
        weeks_value = int(os.environ.get(env_name, str(default)))
    except ValueError:
        weeks_value = default
    return str(max(1, min(weeks_value, 52)))


def run_refresh(mode, mobile_auth, weeks_env, default_weeks):
    code = None
    ok = False
    message = ""
    try:
        weeks = refresh_weeks(weeks_env, default_weeks)
        env = os.environ.copy()
        env["SPORTS_LOG_ALLOW_MOBILE_AUTH"] = "1" if mobile_auth else "0"
        env["SPORTS_LOG_WEEKS"] = weeks
        proc = subprocess.run(
            [PYTHON_BIN, REFRESH_SCRIPT],
            cwd=BASE_DIR,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=REFRESH_TIMEOUT,
        )
        code = proc.returncode
        ok = code == 0
        message = text_tail(proc.stdout) or ("%s refresh completed for %s weeks" % (mode, weeks))
    except subprocess.TimeoutExpired as exc:
        code = 124
        message = text_tail(text_tail(exc.stdout) + "\nfull refresh timed out")
    except Exception as exc:
        code = 1
        message = text_tail("full refresh failed: %s" % exc)
    finally:
        with REFRESH_STATE_LOCK:
            REFRESH_STATUS.update(
                {
                    "running": False,
                    "ok": ok,
                    "code": code,
                    "mode": mode,
                    "finished_at": timestamp(),
                    "message": message,
                }
            )
        REFRESH_RUN_LOCK.release()


def run_full_refresh():
    run_refresh("full", True, "SPORTS_LOG_ALL_WEEKS", 52)


def run_safe_refresh():
    run_refresh("safe", False, "SPORTS_LOG_SAFE_WEEKS", 8)


def start_full_refresh():
    if not REFRESH_RUN_LOCK.acquire(False):
        return False, refresh_status_snapshot()
    with REFRESH_STATE_LOCK:
        REFRESH_STATUS.update(
            {
                "running": True,
                "ok": None,
                "code": None,
                "mode": "full",
                "started_at": timestamp(),
                "finished_at": None,
                "message": "mobile auth full refresh running",
            }
        )
    worker = threading.Thread(target=run_full_refresh, name="sports-log-full-refresh")
    worker.daemon = True
    worker.start()
    return True, refresh_status_snapshot()


def start_safe_refresh():
    global LAST_SAFE_REFRESH_AT
    now = time.time()
    if not REFRESH_RUN_LOCK.acquire(False):
        return False, "already_running", public_refresh_status_snapshot()
    if now - LAST_SAFE_REFRESH_AT < SAFE_REFRESH_COOLDOWN_SECONDS:
        REFRESH_RUN_LOCK.release()
        return False, "cooldown", public_refresh_status_snapshot()
    LAST_SAFE_REFRESH_AT = now
    with REFRESH_STATE_LOCK:
        REFRESH_STATUS.update(
            {
                "running": True,
                "ok": None,
                "code": None,
                "mode": "safe",
                "started_at": timestamp(),
                "finished_at": None,
                "message": "safe refresh running",
            }
        )
    worker = threading.Thread(target=run_safe_refresh, name="sports-log-safe-refresh")
    worker.daemon = True
    worker.start()
    return True, "started", public_refresh_status_snapshot()


def get_activity_detail_payload(activity_id, sport_type):
    if not activity_id:
        return {"ok": False, "error": "missing activity id"}
    cache_key = "%s:%s" % (activity_id, sport_type or 0)
    now = time.time()
    with DETAIL_CACHE_LOCK:
        cached = DETAIL_CACHE.get(cache_key)
        if cached and now - cached["time"] < 12 * 3600:
            return cached["payload"]
    proc = subprocess.run(
        [COROS_PYTHON, ACTIVITY_DETAIL_SCRIPT, str(activity_id), str(sport_type or 0)],
        cwd=BASE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=DETAIL_TIMEOUT,
    )
    if proc.returncode != 0:
        return {"ok": False, "error": text_tail(proc.stdout) or "activity detail fetch failed"}
    try:
        payload = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"ok": False, "error": "invalid activity detail response"}
    with DETAIL_CACHE_LOCK:
        DETAIL_CACHE[cache_key] = {"time": now, "payload": payload}
    return payload


def get_fit_import_payload(file_item):
    if file_item is None or not getattr(file_item, "filename", ""):
        return {"ok": False, "error": "missing FIT file"}
    filename = os.path.basename(file_item.filename)
    raw = file_item.file.read()
    if not raw:
        return {"ok": False, "error": "empty FIT file"}
    if len(raw) > FIT_UPLOAD_LIMIT:
        return {"ok": False, "error": "FIT file is larger than %.1f MB" % (FIT_UPLOAD_LIMIT / 1024 / 1024)}
    try:
        from scripts.fit_activity import FitParseError, parse_fit_file
    except Exception as exc:
        return {"ok": False, "error": "FIT parser is unavailable: %s" % exc}
    suffix = ".fit" if filename.lower().endswith(".fit") else ".bin"
    try:
        with tempfile.NamedTemporaryFile(prefix="sports-log-fit-", suffix=suffix, delete=True) as tmp:
            tmp.write(raw)
            tmp.flush()
            return parse_fit_file(tmp.name, filename=filename)
    except FitParseError as exc:
        return {"ok": False, "error": str(exc)}
    except Exception as exc:
        return {"ok": False, "error": "FIT import failed: %s" % exc}


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
<title>COROS运动记录</title>
<link rel="icon" href="https://coros.com/favicon.ico" sizes="any">
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
<title>COROS运动记录</title>
<link rel="icon" href="https://coros.com/favicon.ico" sizes="any">
<style>
:root{color-scheme:light;--bg:#f4f7fb;--surface:#fff;--surface2:#f8fbff;--ink:#111827;--muted:#526174;--faint:#8a94a6;--line:#dfe7f0;--line2:#eef3f8;--blue:#2f6fed;--blue2:#e7f0ff;--green:#18a66a;--green2:#e5f7ed;--orange:#f59e0b;--red:#ef4f5f;--cyan:#0ea5b7;--violet:#6d5dfc;--sleep-light:#78b89a;--sleep-awake:#f47f86;--shadow:0 14px 38px rgba(17,24,39,.07);--font:Inter,Geist,Satoshi,"Cabinet Grotesk",Outfit,ui-sans-serif,system-ui,-apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;--mono:"JetBrains Mono","SFMono-Regular",ui-monospace,SFMono-Regular,Menlo,monospace}
*{box-sizing:border-box}html{scroll-behavior:smooth}[hidden]{display:none!important}body{margin:0;background:var(--bg);color:var(--ink);font-family:var(--font);font-size:14px;line-height:1.45;letter-spacing:0;-webkit-font-smoothing:antialiased}button{font:inherit}.app{display:grid;grid-template-columns:76px minmax(0,1fr);min-height:100dvh}.rail{position:sticky;top:0;height:100dvh;background:linear-gradient(180deg,#0b4fd8,#071d37);padding:18px 10px;display:flex;flex-direction:column;align-items:center;gap:22px;color:white}.rail-mark{width:44px;height:44px;border-radius:14px;background:#fff;display:grid;place-items:center;color:var(--blue);font-weight:900;font-size:18px;box-shadow:0 14px 30px rgba(0,0,0,.18)}.rail-spacer{flex:1}.rail a,.rail-sync{width:42px;height:42px;border-radius:12px;display:grid;place-items:center;color:#dbeafe;text-decoration:none;font-size:12px;font-weight:800;border:1px solid transparent}.rail-sync{background:transparent;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),background .18s,border-color .18s,color .18s}.rail-sync:active{transform:scale(.96)}.rail a.active,.rail a:hover,.rail-sync:hover,.rail-sync.busy{background:rgba(255,255,255,.14);border-color:rgba(255,255,255,.16);color:white}.rail-sync.done{background:rgba(22,160,100,.22);border-color:rgba(99,220,157,.36);color:white}.rail-sync.err{background:rgba(239,79,95,.24);border-color:rgba(255,155,166,.38);color:white}.main{padding:26px;max-width:1520px;width:100%;margin:0 auto}.topbar{display:flex;align-items:flex-start;justify-content:space-between;gap:18px;margin-bottom:20px}.title h1{margin:0;font-size:34px;line-height:1;font-weight:850;letter-spacing:-.04em}.date{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:14px;margin-top:10px}.actions{display:flex;align-items:center;gap:12px}.seg{display:flex;gap:4px;padding:4px;border:1px solid var(--line);border-radius:12px;background:var(--surface);box-shadow:0 10px 26px rgba(21,35,65,.06)}.seg button{border:0;background:transparent;color:var(--muted);border-radius:9px;padding:8px 12px;font-weight:800;font-size:12px;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),background .18s}.seg button:active{transform:scale(.97)}.seg button.active{background:var(--blue);color:white}.cards{display:grid;grid-template-columns:repeat(6,minmax(0,1fr));gap:12px;margin-bottom:14px}.card,.panel{background:var(--surface);border:1px solid var(--line);border-radius:12px;box-shadow:var(--shadow)}.card{padding:18px;min-height:154px;overflow:hidden}.card-head{display:flex;align-items:center;gap:12px}.dot{width:34px;height:34px;border-radius:12px;background:var(--tone,#dbeafe);display:grid;place-items:center;color:var(--color,var(--blue));font-weight:900}.card span,.panel small,.metric-label{color:var(--muted);font-size:12px;font-weight:700}.card b{display:block;font-size:29px;line-height:1.05;margin:18px 0 8px;font-family:var(--mono);letter-spacing:-.04em}.trend{font-size:12px;font-weight:850;color:var(--green)}.trend.down{color:var(--red)}.spark{width:100%;height:36px;margin-top:8px}.layout{display:grid;grid-template-columns:minmax(0,2fr) minmax(340px,1fr);gap:12px}.panel{padding:20px;min-width:0}.panel-head{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;margin-bottom:14px}.panel h2{margin:0;font-size:17px;line-height:1.15;font-weight:850;letter-spacing:-.02em}.legend{display:flex;gap:16px;flex-wrap:wrap;color:var(--muted);font-size:12px;font-weight:750}.legend i{display:inline-block;width:18px;height:4px;border-radius:999px;margin-right:6px;vertical-align:2px}.chart{position:relative;height:392px}.chart.short{height:262px}.chart canvas{display:block;width:100%;height:100%}.chart-tip{position:absolute;pointer-events:none;z-index:5;min-width:172px;background:#0f1b33;color:white;border-radius:12px;padding:10px 11px;font-size:12px;line-height:1.45;box-shadow:0 18px 50px rgba(15,27,51,.22);transform:translate(-50%,-112%);opacity:0;transition:opacity .12s}.chart-tip b{display:block;margin-bottom:5px}.chart-tip span{display:flex;justify-content:space-between;gap:18px;color:#d6e1f4}.chart-tip em{font-style:normal;color:#9fb0ca}.side-stack{display:grid;gap:12px}.status{display:grid;grid-template-columns:132px 1fr;gap:18px;align-items:center}.gauge{position:relative;width:132px;height:132px}.gauge svg{width:132px;height:132px;transform:rotate(-90deg)}.gauge circle{fill:none;stroke-width:14;stroke-linecap:round}.gauge .bg{stroke:#e7edf7}.gauge .fg{stroke:var(--green);stroke-dasharray:0 999}.gauge b{position:absolute;inset:0;display:grid;place-items:center;font-size:28px;font-family:var(--mono)}.status h3{font-size:28px;line-height:1.08;margin:0 0 8px;letter-spacing:-.04em}.status p{margin:0;color:var(--muted)}.coach-list{display:grid;gap:10px;margin-top:16px}.coach-list div{border-top:1px solid var(--line);padding-top:10px}.coach-list b{display:block;font-size:13px}.coach-list p{margin:3px 0 0;color:var(--muted);font-size:12px}.grid2{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:12px}.activity-list{display:grid;gap:9px;max-height:444px;overflow:auto;content-visibility:auto;padding-right:2px}.activity{display:grid;grid-template-columns:46px 1fr auto;gap:12px;align-items:center;border:1px solid var(--line);border-radius:12px;padding:10px;background:var(--surface);cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),border-color .18s}.activity:hover{transform:translateY(-1px);border-color:#cbd8ea}.activity:active{transform:scale(.99)}.badge{width:42px;height:42px;border-radius:50%;background:var(--blue2);color:var(--blue);display:grid;place-items:center;font-family:var(--mono);font-weight:850}.activity h3{margin:0;font-size:13px}.activity p{margin:4px 0 0;color:var(--muted);font-size:12px}.activity .num{font-family:var(--mono);font-weight:850}.filters{display:flex;gap:6px;flex-wrap:wrap;margin-bottom:12px}.filters button{border:1px solid var(--line);background:white;color:var(--muted);border-radius:999px;padding:6px 10px;font-size:12px;font-weight:800;cursor:pointer}.filters button.active{background:var(--ink);border-color:var(--ink);color:white}.achievements{display:grid;grid-template-columns:repeat(4,1fr);gap:12px}.medal{padding:18px;border-radius:14px;background:var(--surface2);border:1px solid var(--line);text-align:center}.medal i{display:grid;place-items:center;width:54px;height:54px;margin:0 auto 12px;border-radius:18px;background:var(--tone,#dbeafe);color:var(--color,var(--blue));font-style:normal;font-weight:900}.medal b{display:block;font-size:14px}.medal span{display:block;color:var(--muted);font-size:12px;margin-top:6px}.drawer{position:fixed;right:22px;bottom:22px;width:min(760px,calc(100% - 44px));max-height:min(760px,calc(100dvh - 44px));overflow:auto;background:#0f1b33;color:white;border-radius:16px;padding:20px;box-shadow:0 28px 90px rgba(15,27,51,.36);transform:translateY(130%);transition:.25s cubic-bezier(.16,1,.3,1);z-index:10}.drawer.show{transform:translateY(0)}.drawer button{position:absolute;right:12px;top:10px;border:0;background:#ffffff18;color:white;border-radius:50%;width:30px;height:30px;cursor:pointer}.drawer h2{margin:0 36px 14px 0}.detail-stats{display:grid;grid-template-columns:repeat(4,1fr);gap:8px}.detail-stats div{background:#ffffff12;border-radius:10px;padding:10px}.detail-stats span{display:block;color:#b7c6df;font-size:11px}.detail-stats b{font-family:var(--mono)}.detail-extra{display:grid;gap:12px;margin-top:14px}.detail-section{border-top:1px solid #ffffff18;padding-top:12px}.detail-section h3{margin:0 0 9px;font-size:13px}.detail-table{width:100%;border-collapse:collapse;font-size:12px}.detail-table th,.detail-table td{padding:7px 6px;border-bottom:1px solid #ffffff14;text-align:right;white-space:nowrap}.detail-table th:first-child,.detail-table td:first-child{text-align:left}.detail-table th{color:#b7c6df;font-weight:750}.zone-row{display:grid;grid-template-columns:52px 1fr 42px;gap:8px;align-items:center;margin:6px 0;color:#d6e1f4;font-size:12px}.zone-bar{height:8px;border-radius:999px;background:#ffffff14;overflow:hidden}.zone-bar i{display:block;height:100%;border-radius:999px;background:var(--green);width:0}.detail-note{background:#ffffff10;border-radius:10px;padding:10px;color:#d6e1f4;font-size:12px}.detail-loading{color:#b7c6df;font-size:12px}.detail-error{color:#fecdd3;font-size:12px}@media (prefers-reduced-motion:reduce){*,*:before,*:after{animation:none!important;transition:none!important}}@media(max-width:1180px){.cards{grid-template-columns:repeat(3,1fr)}.layout,.grid2{grid-template-columns:1fr}.app{grid-template-columns:1fr}.rail{display:none}.main{padding:18px}}@media(max-width:640px){.topbar{display:block}.actions{margin-top:14px}.cards{grid-template-columns:1fr}.title h1{font-size:28px}.status{grid-template-columns:1fr}.achievements{grid-template-columns:1fr 1fr}.chart{height:318px}.chart.short{height:240px}.detail-stats{grid-template-columns:repeat(2,1fr)}}
.title h1,.card b,.panel h2,.status h3,.activity h3,.medal b{letter-spacing:0}
body{background:linear-gradient(180deg,#f8fbff 0%,#f3f8f6 48%,#fbfaf7 100%);text-rendering:optimizeLegibility}
.rail{background:linear-gradient(180deg,#0b54cc 0%,#0b335f 54%,#091b2a 100%);box-shadow:inset -1px 0 0 rgba(255,255,255,.12)}
.rail-mark{border-radius:8px;color:#0b54cc;font-size:14px;letter-spacing:0}.rail a,.rail-sync{border-radius:8px}.main{padding:26px;max-width:1400px}
.topbar{align-items:center;margin-bottom:16px}.title h1{font-size:30px;font-weight:820}.date{font-weight:650;color:var(--muted)}
.seg{border-radius:8px;border-color:var(--line);background:rgba(255,255,255,.86);box-shadow:0 10px 28px rgba(17,24,39,.06)}.seg button{border-radius:6px;padding:8px 11px}.seg button.active{background:var(--ink)}
.cards{gap:10px;margin-bottom:12px}.card,.panel{border-radius:8px;border-color:var(--line);background:rgba(255,255,255,.9);box-shadow:var(--shadow)}
.card{padding:16px;min-height:142px}.card:hover,.panel:hover{border-color:#cbd8e6}.dot{border-radius:8px}.card b{font-size:26px;margin:16px 0 8px}.card small{font-size:12px;color:var(--muted)}.trend{display:inline-flex;align-items:center;border-radius:999px;background:var(--green2);padding:3px 7px}.trend.down{background:#fff0f2}
.layout{grid-template-columns:minmax(0,1.45fr) minmax(330px,.95fr);gap:12px;align-items:start}.primary-stack,.insight-stack{display:grid;gap:12px;align-content:start;min-width:0}.panel{padding:18px}.panel-head{margin-bottom:10px}.panel h2{font-size:16px;font-weight:810;letter-spacing:0}.panel small{font-weight:650;color:var(--muted)}.legend{gap:12px}.legend i{height:3px;width:20px}
.chart{height:320px}.chart.distance{height:292px}.chart.short{height:238px}.chart.weekly{height:332px}.chart.sleep{height:352px}.chart-tip{border:1px solid rgba(255,255,255,.08);border-radius:8px;background:rgba(10,18,32,.96);box-shadow:0 18px 48px rgba(15,27,51,.24)}
.status{background:linear-gradient(135deg,rgba(255,255,255,.94),rgba(242,249,245,.92));grid-template-columns:118px 1fr}.gauge,.gauge svg{width:118px;height:118px}.status h3{font-size:25px}
.activity{border-radius:8px;background:#fff;grid-template-columns:44px 1fr auto}.activity:hover{background:#f9fcff;border-color:#b9c9dc;transform:translateY(-2px)}.badge{width:38px;height:38px;border-radius:8px;background:var(--blue2);color:#1356c5}.filters button{border-radius:8px;background:#fff}.filters button.active{background:var(--ink)}
.medal{border-radius:8px;background:#fbfdff}.medal i{border-radius:8px}.drawer{border-radius:10px;background:#111b2d}.drawer button{border-radius:8px}.detail-section{overflow-x:auto}.detail-stats div{border-radius:8px}.zone-row{grid-template-columns:56px 1fr 54px}
.hero-panel{display:grid;grid-template-columns:minmax(0,1.32fr) minmax(340px,.92fr);gap:12px;margin-bottom:12px;padding:20px;border:1px solid var(--line);border-radius:8px;background:linear-gradient(135deg,rgba(255,255,255,.96) 0%,rgba(244,249,246,.94) 54%,rgba(240,246,255,.92) 100%);box-shadow:var(--shadow);overflow:hidden;position:relative}.hero-panel,.hero-main,.hero-kpis,.card,.panel{min-width:0}.hero-panel:before{content:"";position:absolute;left:0;top:0;bottom:0;width:3px;background:linear-gradient(180deg,var(--blue),var(--green))}.hero-main{position:relative;z-index:1}.eyebrow{display:inline-flex;border:1px solid var(--line);background:#fff;border-radius:999px;padding:5px 9px;color:var(--muted);font-size:11px;font-weight:800}.hero-main h2{margin:14px 0 8px;font-size:40px;line-height:.95;font-family:var(--mono);font-weight:850;letter-spacing:-.02em}.hero-main p{max-width:680px;margin:0;color:var(--muted);font-size:13px;font-weight:650;overflow-wrap:anywhere}.hero-kpis{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:8px}.hero-kpis div{border:1px solid var(--line2);background:rgba(255,255,255,.74);border-radius:8px;padding:12px;min-width:0}.hero-kpis span{display:block;color:var(--faint);font-size:11px;font-weight:750}.hero-kpis b{display:block;margin-top:4px;font-size:19px;font-family:var(--mono);letter-spacing:-.02em}
.fit-lab{margin-bottom:12px;overflow:hidden}.fit-head{display:flex;align-items:flex-start;justify-content:space-between;gap:14px;margin-bottom:14px}.fit-head h2{font-size:17px}.fit-upload{position:relative;display:inline-flex;align-items:center;justify-content:center;min-height:38px;border:1px solid var(--ink);background:var(--ink);color:white;border-radius:8px;padding:8px 13px;font-size:12px;font-weight:850;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),background .18s}.fit-upload:active{transform:scale(.97)}.fit-upload input{position:absolute;inset:0;opacity:0;cursor:pointer}.fit-empty{display:grid;place-items:center;min-height:174px;border:1px dashed #cbd8e6;border-radius:8px;background:linear-gradient(135deg,#fbfdff,#f5faf7);color:var(--muted);font-size:13px;font-weight:750}.fit-empty.busy{background:linear-gradient(90deg,#f8fbff,#eef5ff,#f8fbff);background-size:240% 100%;animation:fitShimmer 1.2s linear infinite}.fit-workbench{display:grid;gap:14px}.fit-summary{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px}.fit-summary div{border-top:1px solid var(--line);padding-top:10px;min-width:0}.fit-summary span,.fit-lap-detail span{display:block;color:var(--faint);font-size:11px;font-weight:760}.fit-summary b{display:block;margin-top:4px;font-size:20px;font-family:var(--mono);letter-spacing:-.02em;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}.fit-toolbar{display:grid;grid-template-columns:auto 1fr;gap:10px;align-items:start}.fit-axis{box-shadow:none}.fit-metrics{display:flex;gap:6px;flex-wrap:wrap}.fit-metrics button{border:1px solid var(--line);background:#fff;color:var(--muted);border-radius:8px;padding:7px 9px;font-size:12px;font-weight:820;cursor:pointer;transition:transform .18s cubic-bezier(.16,1,.3,1),border-color .18s,background .18s}.fit-metrics button:active{transform:scale(.97)}.fit-metrics button.active{border-color:var(--metric);background:color-mix(in srgb,var(--metric) 10%,white);color:var(--ink)}.fit-grid{display:grid;grid-template-columns:minmax(0,1.42fr) minmax(280px,.58fr);gap:12px;align-items:start}.fit-chart-shell{min-width:0}.fit-chart{height:404px;border-top:1px solid var(--line);padding-top:12px}.fit-insights{display:grid;gap:8px}.fit-insights div{border-top:1px solid var(--line);padding-top:10px}.fit-insights b{display:block;font-size:15px}.fit-insights p{margin:3px 0 0;color:var(--muted);font-size:12px}.fit-splits{display:grid;grid-template-columns:minmax(0,1.35fr) minmax(280px,.65fr);gap:12px;align-items:start}.fit-table-wrap{overflow:auto;border-top:1px solid var(--line);padding-top:8px}.fit-table{width:100%;border-collapse:collapse;font-size:12px}.fit-table th,.fit-table td{padding:8px 7px;border-bottom:1px solid var(--line2);text-align:right;white-space:nowrap}.fit-table th:first-child,.fit-table td:first-child{text-align:left}.fit-table th{color:var(--faint);font-weight:850}.fit-table tr{cursor:pointer}.fit-table tbody tr:hover,.fit-table tbody tr.active{background:#f7fbff}.fit-cell{display:grid;gap:2px}.fit-cell small{font-size:10px;color:var(--faint);font-family:var(--mono)}.fit-lap-detail{border-top:1px solid var(--line);padding-top:10px}.fit-lap-detail h3{margin:0 0 10px;font-size:16px}.fit-ranges{display:grid;gap:8px}.fit-range{display:grid;grid-template-columns:74px minmax(0,1fr) 78px;gap:8px;align-items:center}.fit-range b{font-family:var(--mono);font-size:12px;text-align:right}.fit-bar{position:relative;height:7px;background:#edf2f7;border-radius:999px;overflow:hidden}.fit-bar i{position:absolute;top:0;height:100%;border-radius:999px;background:var(--metric,var(--blue));left:0;width:0}.fit-error{border:1px solid #fecdd3;background:#fff5f6;color:#9f1239;border-radius:8px;padding:12px;font-size:13px;font-weight:760}.fit-file-name{color:var(--muted);font-family:var(--mono);font-size:11px;overflow-wrap:anywhere}@keyframes fitShimmer{from{background-position:0 0}to{background-position:240% 0}}
@media(max-width:640px){.main{padding:14px}.title h1{font-size:26px}.card,.panel{padding:14px}.chart{height:286px}.chart.distance{height:260px}.chart.short{height:226px}.chart.weekly{height:318px}.chart.sleep{height:334px}}
@media(max-width:900px){.hero-panel{grid-template-columns:1fr}.hero-kpis{grid-template-columns:repeat(2,1fr)}}
@media(max-width:640px){.hero-main h2{font-size:36px}.hero-main p{font-size:12px;line-height:1.48}.hero-kpis div{padding:10px}.hero-kpis b{font-size:17px}.panel-head{display:block}.legend{margin-top:8px}}
@media(min-width:1181px){.activity-panel{grid-column:1/-1}}
@media(max-width:1180px){.layout,.grid2{grid-template-columns:1fr}.layout{align-items:stretch}.cards{grid-template-columns:repeat(3,minmax(0,1fr))}}
@media(max-width:640px){html,body{overflow-x:hidden}.main{width:100%;overflow:hidden}.cards,.layout,.grid2,.hero-panel{grid-template-columns:minmax(0,1fr)}.status{grid-template-columns:1fr}.hero-kpis{grid-template-columns:repeat(2,minmax(0,1fr))}.hero-main,.hero-main p{max-width:100%;white-space:normal;word-break:break-all}}
@media(max-width:980px){.fit-grid,.fit-splits,.fit-toolbar{grid-template-columns:1fr}.fit-summary{grid-template-columns:repeat(3,minmax(0,1fr))}.fit-chart{height:340px}}
@media(max-width:640px){.fit-head{display:block}.fit-upload{margin-top:10px;width:100%}.fit-summary{grid-template-columns:repeat(2,minmax(0,1fr))}.fit-chart{height:300px}.fit-range{grid-template-columns:64px minmax(0,1fr)}.fit-range b{grid-column:2;text-align:left}}
</style>
</head>
<body>
<div class="app">
<aside class="rail"><div class="rail-mark">SL</div><a class="active" href="#overview">OV</a><a href="#load">LD</a><a href="#recovery">RC</a><a href="#activities">AC</a><a href="#fit">FIT</a><span class="rail-spacer"></span><button class="rail-sync" id="pullAllBtn" type="button" title="Mobile auth 拉取全部 COROS 数据">ALL</button></aside>
<main class="main" id="overview">
  <div class="topbar"><div class="title"><h1><span id="rangeTitle">7天</span>跑步数据概览</h1><div class="date" id="dateLabel">--</div></div><div class="actions"><div class="seg" aria-label="range"><button data-range="7" class="active">7D</button><button data-range="30">30D</button><button data-range="60">60D</button><button data-range="all">ALL</button></div></div></div>
  <section class="hero-panel"><div class="hero-main"><span class="eyebrow">COROS PERFORMANCE LOG</span><h2 id="heroMetric">--</h2><p id="heroNarrative">--</p></div><div class="hero-kpis" id="heroKpis"></div></section>
  <section class="cards" id="cards"></section>
  <section class="panel fit-lab" id="fit">
    <div class="fit-head"><div><h2>FIT 数据分析</h2><small>导入跑步 FIT 文件，查看秒级曲线和分段范围</small><div class="fit-file-name" id="fitFileName"></div></div><label class="fit-upload"><input id="fitFile" type="file" accept=".fit"><span>导入 FIT</span></label></div>
    <div class="fit-empty" id="fitEmpty">选择 .fit 文件后开始分析</div>
    <div class="fit-workbench" id="fitWorkbench" hidden>
      <div class="fit-summary" id="fitSummary"></div>
      <div class="fit-toolbar"><div class="seg fit-axis" id="fitAxis"><button data-axis="time" class="active">时间</button><button data-axis="distance">距离</button></div><div class="fit-metrics" id="fitMetrics"></div></div>
      <div class="fit-grid"><div class="fit-chart-shell"><div class="chart fit-chart"><canvas id="fitChart"></canvas></div></div><div class="fit-insights" id="fitInsights"></div></div>
      <div class="fit-splits"><div class="fit-table-wrap"><table class="fit-table"><thead><tr><th>#</th><th>距离</th><th>时间</th><th>配速</th><th>心率</th><th>功率</th><th>步频</th><th>踏地</th><th>垂直</th><th>垂直比</th></tr></thead><tbody id="fitLapRows"></tbody></table></div><aside class="fit-lap-detail" id="fitLapDetail"></aside></div>
    </div>
  </section>
  <section class="layout">
    <div class="primary-stack">
      <section class="panel distance-panel" id="load"><div class="panel-head"><div><h2>距离趋势</h2><small>每日距离折线 + 7日移动平均线</small></div><div class="legend"><span><i style="background:var(--blue)"></i>每日距离</span><span><i style="background:var(--green)"></i>7日均线</span></div></div><div class="chart distance"><canvas id="distanceChart"></canvas></div></section>
      <section class="panel pace-panel"><div class="panel-head"><div><h2>配速趋势</h2><small>活动配速，越高代表越慢</small></div><div class="legend"><span><i style="background:var(--blue)"></i>配速</span><span><i style="background:var(--green)"></i>7次均线</span></div></div><div class="chart short"><canvas id="paceChart"></canvas></div></section>
    </div>
    <aside class="insight-stack">
      <section class="panel status"><div class="gauge"><svg viewBox="0 0 160 160"><circle class="bg" cx="80" cy="80" r="62"></circle><circle class="fg" id="statusRing" cx="80" cy="80" r="62"></circle></svg><b id="statusScore">--</b></div><div><h3 id="statusTitle">--</h3><p id="statusText">--</p></div></section>
      <section class="panel" id="recovery"><div class="panel-head"><div><h2>跑步能力变化</h2><small>细线每日值 + 粗线7次均线</small></div><div class="legend"><span><i style="background:var(--blue)"></i>VO2</span><span><i style="background:var(--green)"></i>HRV</span><span><i style="background:var(--orange)"></i>Load</span></div></div><div class="chart short"><canvas id="abilityChart"></canvas></div></section>
      <section class="panel"><h2>Coach Summary</h2><div class="coach-list" id="coachList"></div></section>
    </aside>
  </section>
  <section class="grid2">
    <section class="panel"><div class="panel-head"><div><h2>每周跑步总结</h2><small>三泳道折线：距离 / 时长 / 次数</small></div><div class="legend"><span><i style="background:var(--blue)"></i>距离</span><span><i style="background:var(--orange)"></i>时长</span><span><i style="background:var(--green)"></i>次数</span></div></div><div class="chart weekly"><canvas id="weekChart"></canvas></div></section>
    <section class="panel"><div class="panel-head"><div><h2>睡眠与恢复</h2><small>上方睡眠结构，下方恢复趋势</small></div><div class="legend"><span><i style="background:var(--sleep-light)"></i>浅睡</span><span><i style="background:var(--green)"></i>深睡</span><span><i style="background:var(--violet)"></i>REM</span><span><i style="background:var(--sleep-awake)"></i>清醒</span><span><i style="background:var(--cyan)"></i>HRV</span><span><i style="background:var(--orange)"></i>RHR</span></div></div><div class="chart sleep"><canvas id="sleepChart"></canvas></div></section>
    <section class="panel activity-panel" id="activities"><div class="panel-head"><div><h2>最近活动</h2><small id="activityCount">--</small></div></div><div class="filters" id="filters"></div><div class="activity-list" id="activityList"></div></section>
  </section>
  <section class="panel" style="margin-top:12px"><div class="panel-head"><div><h2>阶段成就</h2><small>基于当前区间自动汇总</small></div></div><div class="achievements" id="achievements"></div></section>
</main>
</div>
<aside class="drawer" id="drawer"><button id="closeDrawer">×</button><h2 id="drawerTitle">Activity</h2><div class="detail-stats" id="drawerStats"></div><div class="detail-extra" id="drawerDetail"></div></aside>
<script type="application/json" id="payload">__DATA__</script>
<script>
const DATA=JSON.parse(document.getElementById('payload').textContent);
let range=7,sportFilter='All';
let fitData=null,fitAxisMode='time',fitSelectedKeys=[],fitActiveLapIndex=0;
const $=q=>document.querySelector(q), $$=q=>[...document.querySelectorAll(q)];
const fmt=(v,s='')=>(v===null||v===undefined||v==='')?'--':`${v}${s}`;
const sum=(a,fn)=>a.reduce((n,x)=>n+(+fn(x)||0),0);
const avg=(a,fn)=>{const v=a.map(fn).filter(x=>Number.isFinite(+x));return v.length?v.reduce((m,n)=>m+ +n,0)/v.length:null};
const last=a=>a[a.length-1]||{}, clamp=(v,a,b)=>Math.max(a,Math.min(b,v));
const C={ink:'#111827',muted:'#526174',faint:'#8a94a6',grid:'#dfe7f0',soft:'#f4f7fb',blue:'#2f6fed',green:'#18a66a',orange:'#f59e0b',red:'#ef4f5f',cyan:'#0ea5b7',violet:'#6d5dfc',sleepLight:'#78b89a',sleepAwake:'#f47f86'};
const sleep=ms=>new Promise(resolve=>setTimeout(resolve,ms));
function setPullAllState(kind,label,title){const b=$('#pullAllBtn');if(!b)return;b.classList.remove('busy','done','err');if(kind)b.classList.add(kind);b.textContent=label;b.title=title||'Mobile auth 拉取全部 COROS 数据';b.disabled=kind==='busy'}
function getAdminToken(force=false){let token=force?'':localStorage.getItem('sportsLogAdminToken');if(!token){token=prompt('输入 Sports Log 管理 token');if(token)localStorage.setItem('sportsLogAdminToken',token)}return token}
async function refreshJson(path,token,options={}){const res=await fetch(path,{...options,cache:'no-store',headers:{...(options.headers||{}),'X-Refresh-Token':token}});const body=await res.json().catch(()=>({}));if(res.status===401){localStorage.removeItem('sportsLogAdminToken');throw new Error('管理 token 无效')}if(!res.ok)throw new Error(body.error||body.message||`HTTP ${res.status}`);return body}
async function pollFullRefresh(token){for(let i=0;i<300;i++){const body=await refreshJson('refresh-status',token);const st=body.status||{};if(!st.running){if(st.ok){setPullAllState('done','OK','全量数据已更新');await sleep(700);location.reload();return}throw new Error(st.message||'全量拉取失败')}await sleep(2000)}throw new Error('全量拉取超时，请稍后查看服务日志')}
async function startFullRefresh(){let token=getAdminToken();if(!token)return;if(!confirm('将用 COROS mobile auth 拉取最多 52 周数据，可能让手机 App 重新登录。继续？'))return;setPullAllState('busy','RUN','全量数据拉取中');try{await refreshJson('refresh-all',token,{method:'POST'});await pollFullRefresh(token)}catch(err){setPullAllState('err','ERR',err.message);alert(err.message);setTimeout(()=>setPullAllState('', 'ALL'),2400)}}
async function safeRefreshJson(path,options={}){const res=await fetch(path,{...options,cache:'no-store'});const body=await res.json().catch(()=>({}));if(!res.ok)throw new Error(body.error||`HTTP ${res.status}`);return body}
async function pollSafeRefresh(){for(let i=0;i<180;i++){const body=await safeRefreshJson('refresh-safe-status');const st=body.status||{};if(!st.running){if(st.ok){await sleep(500);location.reload()}return}await sleep(2000)}}
async function startSafeRefresh(){try{const body=await safeRefreshJson('refresh-safe',{method:'POST'});const st=body.status||{};if(body.started||st.running)pollSafeRefresh()}catch(err){console.debug('safe refresh skipped',err)}}
const byDate=new Map();DATA.activities.forEach(a=>{const x=byDate.get(a.date)||{km:0,min:0,load:0,cal:0,elev:0,count:0,acts:[]};x.km+=+(a.distance_km||0);x.min+=+(a.duration_min||0);x.load+=+(a.training_load||0);x.cal+=+(a.calories||0);x.elev+=+(a.elevation_gain||0);x.count++;x.acts.push(a);byDate.set(a.date,x)});
function rows(){return range==='all'?DATA.daily:DATA.daily.slice(-Number(range))}
function carryMetricRows(list){const fields=['hrv','hrv_baseline','rhr','load_ratio','load_status','short_load','long_load','tired_rate','vo2max','lthr','ltsp','stamina_level','stamina_level_7d'],last={},out=list.map(d=>{const row={...d};fields.forEach(k=>{const v=row[k];if(v===null||v===undefined||v===''){if(last[k]!==undefined)row[k]=last[k]}else last[k]=v});return row});fields.forEach(k=>{const first=out.find(r=>r[k]!==null&&r[k]!==undefined&&r[k]!=='')?.[k];if(first===undefined)return;for(const r of out){if(r[k]===null||r[k]===undefined||r[k]==='')r[k]=first;else break}});return out}
function days(src=rows()){return carryMetricRows(src.map(d=>{const a=byDate.get(d.date)||{};return {...d,km:a.km||0,min:a.min||0,load:a.load||0,cal:a.cal||0,elev:a.elev||0,count:a.count||0,acts:a.acts||[]}}))}
function acts(){const start=rows()[0]?.date||'';return DATA.activities.filter(a=>(!start||a.date>=start)&&(sportFilter==='All'||a.sport===sportFilter))}
function canvas(el){const dpr=devicePixelRatio||1,r=el.getBoundingClientRect();el.width=Math.max(1,r.width*dpr);el.height=Math.max(1,r.height*dpr);const ctx=el.getContext('2d');ctx.setTransform(dpr,0,0,dpr,0,0);return{ctx,w:r.width,h:r.height}}
function nice(v){if(!v||v<=0)return 1;const p=10**Math.floor(Math.log10(v)),n=v/p;return(n<=2?2:n<=5?5:10)*p}
function niceTop(v){if(!v||v<=0)return 1;const p=10**Math.floor(Math.log10(v)),n=v/p,s=n<=1?1:n<=1.5?1.5:n<=2?2:n<=3?3:n<=5?5:n<=7.5?7.5:10;return s*p}
function hours(min){return Number.isFinite(+min)?(+min/60).toFixed(1):'--'}
function drawLinePath(ctx,pts){ctx.beginPath();pts.forEach((pt,i)=>i?ctx.lineTo(pt.x,pt.y):ctx.moveTo(pt.x,pt.y))}
function drawDot(ctx,pt,color,r=3.2,alpha=1){ctx.save();ctx.globalAlpha=alpha;ctx.fillStyle='#fff';ctx.strokeStyle=color;ctx.lineWidth=1.8;ctx.beginPath();ctx.arc(pt.x,pt.y,r,0,Math.PI*2);ctx.fill();ctx.stroke();ctx.restore()}
function ma(a,key,n=7){return a.map((_,i)=>avg(a.slice(Math.max(0,i-n+1),i+1),x=>+x[key]))}
function change(now,prev){return Number.isFinite(now)&&Number.isFinite(prev)&&prev!==0?(now-prev)/prev*100:null}
function splitWindow(src,requested){
 const full=src.filter(Boolean),n=Math.min(requested||full.length,full.length);let current=full.slice(-n),prev=full.slice(Math.max(0,full.length-n*2),Math.max(0,full.length-n));
 if(!prev.length&&current.length>=4){const half=Math.floor(current.length/2);prev=current.slice(0,half);current=current.slice(half)}
 return{current,prev};
}
function compare(key,src=days(DATA.daily)){const n=range==='all'?Math.min(30,src.length):Number(range),w=splitWindow(src,n);return change(avg(w.current,x=>+x[key]),avg(w.prev,x=>+x[key]))}
function comparePace(list){const n=list.length,w=splitWindow(DATA.activities.filter(a=>range==='all'||!rows()[0]?.date||a.date>=rows()[0].date),n);return change(avg(w.current.map(x=>paceSec(x.pace)).filter(Boolean),x=>x),avg(w.prev.map(x=>paceSec(x.pace)).filter(Boolean),x=>x))}
function trend(v,goodUp=true){if(v===null)return 'baseline --';const up=v>=0,ok=Math.abs(v)<1||(up===goodUp);return `<span class="trend ${ok?'':'down'}">${up?'↑':'↓'} ${Math.abs(v).toFixed(1)}%</span>`}
function roundRect(ctx,x,y,w,h,r){r=Math.min(r,w/2,h/2);ctx.beginPath();ctx.moveTo(x+r,y);ctx.arcTo(x+w,y,x+w,y+h,r);ctx.arcTo(x+w,y+h,x,y+h,r);ctx.arcTo(x,y+h,x,y,r);ctx.arcTo(x,y,x+w,y,r);ctx.closePath()}
function tip(c){let t=c.parentElement.querySelector('.chart-tip');if(!t){t=document.createElement('div');t.className='chart-tip';c.parentElement.appendChild(t)}return t}
function hover(c,pts,html){const t=tip(c);c.onmousemove=e=>{if(!pts.length)return;const r=c.getBoundingClientRect(),x=e.clientX-r.left,p=pts.reduce((b,n)=>Math.abs(n.x-x)<Math.abs(b.x-x)?n:b,pts[0]);t.innerHTML=html(p);t.style.left=`${clamp(p.x,90,r.width-90)}px`;t.style.top=`${Math.max(58,p.y||90)}px`;t.style.opacity=1};c.onmouseleave=()=>t.style.opacity=0}
function paceSec(p){const m=String(p||'').match(/(\\d+):(\\d+)/);return m?+m[1]*60+ +m[2]:null}
function paceText(s){if(!Number.isFinite(s))return'--';return `${Math.floor(s/60)}'${String(Math.round(s%60)).padStart(2,'0')}"`}
function durationText(s){if(!Number.isFinite(+s))return'--';const total=Math.round(+s),h=Math.floor(total/3600),m=Math.floor((total%3600)/60),sec=total%60;return h?`${h}:${String(m).padStart(2,'0')}:${String(sec).padStart(2,'0')}`:`${m}:${String(sec).padStart(2,'0')}`}
function drawSmoothPath(ctx,pts){if(!pts.length)return;ctx.beginPath();ctx.moveTo(pts[0].x,pts[0].y);if(pts.length===1)return;if(pts.length===2){ctx.lineTo(pts[1].x,pts[1].y);return}for(let i=1;i<pts.length-1;i++){const mx=(pts[i].x+pts[i+1].x)/2,my=(pts[i].y+pts[i+1].y)/2;ctx.quadraticCurveTo(pts[i].x,pts[i].y,mx,my)}const last=pts[pts.length-1];ctx.lineTo(last.x,last.y)}
function fitSmoothPoints(points,metric){if(points.length<4)return points;const windows={heart_rate:7,pace_sec_per_km:15,speed_mps:13,power_w:11,cadence_spm:9,step_length_cm:11,stance_time_ms:13,vertical_oscillation_mm:13,vertical_ratio_pct:13,altitude_m:9},win=Math.min(windows[metric.key]||9,points.length%2?points.length:points.length-1),half=Math.floor(win/2);return points.map((pt,i)=>{let total=0,weight=0;for(let j=Math.max(0,i-half);j<=Math.min(points.length-1,i+half);j++){const w=half+1-Math.abs(i-j);total+=points[j].y*w;weight+=w}return{...pt,yRaw:pt.y,y:total/weight}})}
function fitMetric(key){return fitData?.metrics?.find(m=>m.key===key)||null}
function fitValid(metric,value){const n=+value;return Number.isFinite(n)&&(metric?.key==='altitude_m'||n>0)}
function fitValue(metric,value){if(!metric||!fitValid(metric,value))return'--';if(metric.format==='pace')return paceText(+value);const p=Number.isFinite(+metric.precision)?+metric.precision:1;return p===0?String(Math.round(+value)):(+value).toFixed(p)}
function fitWithUnit(key,value){const m=fitMetric(key),v=fitValue(m,value);return v==='--'?'--':`${v}${m?.format==='pace'?' /km':m?.unit?' '+m.unit:''}`}
function fitRangeText(metric,stat){if(!metric||!stat)return'';const a=fitValue(metric,stat.min),b=fitValue(metric,stat.max);return a==='--'||b==='--'?'':`${a}-${b}${metric.format==='pace'?'':metric.unit?' '+metric.unit:''}`}
function fitLapCell(lap,key,fallback){const metric=fitMetric(key),stat=lap.stats?.[key],value=stat?.avg??fallback,text=fitValue(metric,value),range=fitRangeText(metric,stat);return `<span class="fit-cell"><b>${esc(text)}${text!=='--'&&metric?.format==='pace'?' /km':text!=='--'&&metric?.unit?' '+esc(metric.unit):''}</b><small>${esc(range||'--')}</small></span>`}
function fitInsightCard(title,value,sub=''){return `<div><b>${esc(title)}</b><p>${esc(value||'--')}</p>${sub?`<p>${esc(sub)}</p>`:''}</div>`}
function renderFitSummary(){
 const a=fitData.activity||{};
 $('#fitFileName').textContent=a.file_name||'';
 $('#fitSummary').innerHTML=[
  ['距离',fmt(a.distance_km,' km')],
  ['时间',a.duration||durationText(a.duration_sec)],
  ['平均配速',a.pace||fitWithUnit('pace_sec_per_km',a.pace_sec_per_km)],
  ['均心 / 峰心',`${fmt(a.avg_hr)} / ${fmt(a.max_hr)} bpm`],
  ['功率 / 步频',`${fmt(a.avg_power_w,' W')} / ${fmt(a.avg_cadence_spm,' spm')}`],
  ['跑姿',`${fmt(a.avg_stance_time_ms,' ms')} · ${fmt(a.avg_vertical_oscillation_mm,' mm')} · ${fmt(a.avg_vertical_ratio_pct,'%')}`]
 ].map(x=>`<div><span>${esc(x[0])}</span><b>${esc(x[1])}</b></div>`).join('');
}
function renderFitMetrics(){
 const defaults=['heart_rate','pace_sec_per_km','power_w','stance_time_ms','vertical_oscillation_mm','vertical_ratio_pct'];
 if(!fitSelectedKeys.length)fitSelectedKeys=defaults.filter(k=>fitMetric(k)).slice(0,6);
 $('#fitMetrics').innerHTML=(fitData.metrics||[]).map(m=>`<button type="button" class="${fitSelectedKeys.includes(m.key)?'active':''}" style="--metric:${m.color}" data-metric="${m.key}">${esc(m.label)}</button>`).join('');
 $$('#fitMetrics button').forEach(btn=>btn.onclick=()=>{const key=btn.dataset.metric;if(fitSelectedKeys.includes(key)){fitSelectedKeys=fitSelectedKeys.filter(k=>k!==key)}else{fitSelectedKeys=[...fitSelectedKeys,key]}renderFitMetrics();drawFitChart();renderFitLapDetail()});
}
function renderFitInsights(){
 const ins=fitData.insights||{},vr=Array.isArray(ins.vertical_ratio_range)?`${ins.vertical_ratio_range[0]}-${ins.vertical_ratio_range[1]}%`:'--',drift=Number.isFinite(+ins.heart_rate_drift_bpm)?`${ins.heart_rate_drift_bpm>0?'+':''}${ins.heart_rate_drift_bpm} bpm`:'--',paceDrift=Number.isFinite(+ins.pace_drift_sec_per_km)?`${ins.pace_drift_sec_per_km>0?'+':''}${ins.pace_drift_sec_per_km}s /km`:'--';
 $('#fitInsights').innerHTML=[
  fitInsightCard('最快分段',ins.fastest_lap?`L${ins.fastest_lap.index} · ${ins.fastest_lap.pace}`:'--',ins.fastest_lap?`${ins.fastest_lap.distance_km} km`:''),
  fitInsightCard('功率最高',ins.strongest_lap?`L${ins.strongest_lap.index} · ${ins.strongest_lap.power_w} W`:'--',ins.strongest_lap?.pace||''),
  fitInsightCard('后半程变化',`心率 ${drift}`,`配速 ${paceDrift}`),
  fitInsightCard('跑姿范围',`垂直比 ${fmt(ins.vertical_ratio_avg,'%')}`,`范围 ${vr} · 踏地 ${fmt(ins.stance_time_avg_ms,' ms')}`)
 ].join('');
}
function drawFitChart(){
 if(!fitData||!$('#fitChart'))return;
 const c=$('#fitChart'),{ctx,w,h}=canvas(c),p={l:46,r:18,t:18,b:36},iw=w-p.l-p.r,ih=h-p.t-p.b,records=fitData.records||[],xKey=fitAxisMode==='distance'?'distance_km':'sec',selected=(fitData.metrics||[]).filter(m=>fitSelectedKeys.includes(m.key));
 ctx.clearRect(0,0,w,h);ctx.strokeStyle=C.grid;ctx.lineWidth=1;for(let i=0;i<5;i++){const y=p.t+i*ih/4;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke()}ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='left';ctx.fillText(selected.length?'相对刻度 · 平滑曲线':'选择指标',p.l,p.t+4);
 const xs=records.map(r=>+r[xKey]).filter(Number.isFinite),minX=Math.min(...xs,0),maxX=Math.max(...xs,1),spanX=maxX-minX||1;
 selected.forEach(metric=>{
  const lo=+metric.min,hi=+metric.max,span=hi-lo||1,segments=[];let seg=[],lastSmooth=null;ctx.strokeStyle=metric.color;ctx.lineWidth=2.35;ctx.lineJoin='round';ctx.lineCap='round';
  records.forEach(row=>{const xVal=+row[xKey],v=+row[metric.key];if(!Number.isFinite(xVal)||!fitValid(metric,v)){if(seg.length)segments.push(seg);seg=[];return}const ratio=metric.invert?1-(v-lo)/span:(v-lo)/span,x=p.l+((xVal-minX)/spanX)*iw,y=h-p.b-clamp(ratio,0,1)*ih;seg.push({x,y,row,value:v,metric})});if(seg.length)segments.push(seg);
  segments.forEach(points=>{const smooth=fitSmoothPoints(points,metric);if(!smooth.length)return;ctx.save();ctx.globalAlpha=.96;drawSmoothPath(ctx,smooth);ctx.stroke();ctx.restore();lastSmooth=smooth[smooth.length-1]});
  if(lastSmooth){drawDot(ctx,lastSmooth,metric.color,3.4,.95);ctx.fillStyle=metric.color;ctx.font='11px Geist, system-ui';ctx.fillText(metric.label,clamp(lastSmooth.x+6,p.l,w-54),clamp(lastSmooth.y-6,p.t+10,h-p.b-8))}
 });
 const ticks=Math.min(6,records.length);ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='center';for(let i=0;i<ticks;i++){const idx=Math.round(i*(records.length-1)/Math.max(1,ticks-1)),row=records[idx]||{},x=p.l+(((+row[xKey]||0)-minX)/spanX)*iw,label=fitAxisMode==='distance'?`${(+row.distance_km||0).toFixed(1)}k`:durationText(row.sec);ctx.fillText(label,clamp(x,28,w-28),h-11)}
 const pts=records.filter(r=>Number.isFinite(+r[xKey])).map(r=>({x:p.l+((+r[xKey]-minX)/spanX)*iw,y:h*.48,row:r}));
 hover(c,pts,pt=>`<b>${fitAxisMode==='distance'?`${(+pt.row.distance_km||0).toFixed(2)} km`:durationText(pt.row.sec)}</b>${selected.map(m=>`<span><em>${esc(m.label)}</em><strong>${esc(fitWithUnit(m.key,pt.row[m.key]))}</strong></span>`).join('')}`);
}
function renderFitLapRows(){
 const laps=fitData.laps||[];
 $('#fitLapRows').innerHTML=laps.map((lap,i)=>`<tr class="${i===fitActiveLapIndex?'active':''}" data-lap="${i}"><td>L${esc(lap.index)}</td><td>${fmt(lap.distance_km,' km')}</td><td>${esc(lap.duration||'--')}</td><td>${fitLapCell(lap,'pace_sec_per_km',lap.pace_sec_per_km)}</td><td>${fitLapCell(lap,'heart_rate',lap.avg_hr)}</td><td>${fitLapCell(lap,'power_w',lap.avg_power_w)}</td><td>${fitLapCell(lap,'cadence_spm',lap.avg_cadence_spm)}</td><td>${fitLapCell(lap,'stance_time_ms',lap.avg_stance_time_ms)}</td><td>${fitLapCell(lap,'vertical_oscillation_mm',lap.avg_vertical_oscillation_mm)}</td><td>${fitLapCell(lap,'vertical_ratio_pct',lap.avg_vertical_ratio_pct)}</td></tr>`).join('');
 $$('#fitLapRows tr').forEach(row=>row.onclick=()=>{fitActiveLapIndex=+row.dataset.lap;renderFitLapRows();renderFitLapDetail()});
}
function renderFitLapDetail(){
 if(!fitData)return;const lap=(fitData.laps||[])[fitActiveLapIndex]||{},stats=lap.stats||{},metrics=(fitData.metrics||[]).filter(m=>stats[m.key]);
 $('#fitLapDetail').innerHTML=`<h3>L${esc(lap.index||'--')} · ${esc(lap.pace||'--')}</h3><div class="fit-ranges">${metrics.map(m=>{const st=stats[m.key],span=(+m.max-+m.min)||1,left=clamp(((+st.min-+m.min)/span)*100,0,100),width=clamp(((+st.max-+st.min)/span)*100,2,100-left);return `<div class="fit-range" style="--metric:${m.color}"><span>${esc(m.label)}</span><div class="fit-bar"><i style="left:${left}%;width:${width}%"></i></div><b>${esc(fitValue(m,st.avg))}${m.format==='pace'?' /km':m.unit?' '+esc(m.unit):''}</b></div>`}).join('')}</div>`;
}
function renderFit(){
 if(!fitData)return;$('#fitEmpty').hidden=true;$('#fitEmpty').classList.remove('busy');$('#fitWorkbench').hidden=false;renderFitSummary();renderFitMetrics();renderFitInsights();renderFitLapRows();renderFitLapDetail();drawFitChart();
}
async function uploadFit(file){
 if(!file)return;const empty=$('#fitEmpty');fitData=null;fitSelectedKeys=[];fitActiveLapIndex=0;$('#fitWorkbench').hidden=true;empty.hidden=false;empty.className='fit-empty busy';empty.textContent='正在解析 FIT 数据...';$('#fitFileName').textContent=file.name;
 const form=new FormData();form.append('file',file,file.name);
 try{const res=await fetch('fit-import',{method:'POST',body:form,cache:'no-store'}),body=await res.json().catch(()=>({}));if(!res.ok||!body.ok)throw new Error(body.error||`HTTP ${res.status}`);fitData=body;fitAxisMode='time';$$('#fitAxis button').forEach(b=>b.classList.toggle('active',b.dataset.axis===fitAxisMode));renderFit()}
 catch(err){empty.className='fit-error';empty.hidden=false;empty.textContent=`FIT 导入失败：${err.message}`;$('#fitWorkbench').hidden=true}
}
function initFit(){
 const input=$('#fitFile');if(input)input.onchange=e=>uploadFit(e.target.files?.[0]);
 $$('#fitAxis button').forEach(btn=>btn.onclick=()=>{fitAxisMode=btn.dataset.axis;$$('#fitAxis button').forEach(b=>b.classList.toggle('active',b===btn));drawFitChart()});
}
function drawAxes(ctx,p,w,h,max,labelRight){ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='right';for(let i=0;i<5;i++){const y=p.t+i*(h-p.t-p.b)/4,val=max-(max*i/4);ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke();ctx.fillText(labelRight?labelRight(val):val.toFixed(val>=10?0:1),p.l-8,y+4)}}
function drawDistance(){
 const a=days(),c=$('#distanceChart'),{ctx,w,h}=canvas(c),p={l:48,r:18,t:16,b:34},iw=w-p.l-p.r,ih=h-p.t-p.b,den=Math.max(1,a.length-1),avg7=ma(a,'km',7),pts=[];
 ctx.clearRect(0,0,w,h);if(!a.length)return;
 const rawMax=Math.max(...a.map(x=>+x.km||0),...avg7.filter(v=>Number.isFinite(v)),1),max=niceTop(rawMax*1.08);
 drawAxes(ctx,p,w,h,max);
 const daily=a.map((d,i)=>({x:p.l+i*iw/den,y:h-p.b-((+d.km||0)/max)*ih,row:d,avg:avg7[i]}));
 const smooth=avg7.map((v,i)=>({x:p.l+i*iw/den,y:h-p.b-((+v||0)/max)*ih,row:a[i],avg:v}));
 const grad=ctx.createLinearGradient(0,p.t,0,h-p.b);grad.addColorStop(0,'rgba(47,111,237,.16)');grad.addColorStop(1,'rgba(47,111,237,0)');
 drawLinePath(ctx,daily);ctx.lineTo(daily[daily.length-1]?.x||p.l,h-p.b);ctx.lineTo(daily[0]?.x||p.l,h-p.b);ctx.closePath();ctx.fillStyle=grad;ctx.fill();
 ctx.save();ctx.globalAlpha=.48;ctx.strokeStyle=C.blue;ctx.lineWidth=1.5;ctx.lineJoin='round';ctx.lineCap='round';drawLinePath(ctx,daily);ctx.stroke();ctx.restore();
 ctx.strokeStyle=C.green;ctx.lineWidth=2.8;ctx.lineJoin='round';ctx.lineCap='round';drawLinePath(ctx,smooth);ctx.stroke();
 daily.forEach(pt=>{if(pt.row.km>0)drawDot(ctx,pt,C.blue,2.9,.9)});
 const latest=smooth[smooth.length-1];if(latest){ctx.beginPath();ctx.fillStyle=C.green;ctx.arc(latest.x,latest.y,4.2,0,Math.PI*2);ctx.fill()}
 ctx.fillStyle=C.faint;ctx.font='11px Inter, Geist, system-ui';ctx.textAlign='center';const ticks=Math.min(6,a.length);for(let i=0;i<ticks;i++){const idx=Math.round(i*(a.length-1)/Math.max(1,ticks-1)),x=clamp(p.l+idx*iw/den,28,w-28);ctx.fillText(a[idx].date.slice(5),x,h-11)}
 smooth.forEach(pt=>pts.push(pt));
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>每日距离</em><strong>${p.row.km.toFixed(1)} km</strong></span><span><em>7日均线</em><strong>${Number.isFinite(p.avg)?p.avg.toFixed(1):'--'} km</strong></span><span><em>训练负荷</em><strong>${fmt(p.row.load,' TL')}</strong></span>`);
}
function drawAbility(){
 const a=days(),c=$('#abilityChart'),{ctx,w,h}=canvas(c),p={l:58,r:18,t:14,b:30},iw=w-p.l-p.r,lanes=[{key:'vo2max',label:'VO2',unit:'',color:C.blue,min:30,max:75,fmt:v=>Math.round(v)},{key:'hrv',label:'HRV',unit:' ms',color:C.green,fmt:v=>Math.round(v)},{key:'load_ratio',label:'LOAD',unit:'',color:C.orange,min:.7,max:1.4,fmt:v=>Number(v).toFixed(2)}],gap=10,laneH=(h-p.t-p.b-gap*(lanes.length-1))/lanes.length,den=Math.max(1,a.length-1),avgByKey={};
 ctx.clearRect(0,0,w,h);ctx.font='11px Geist, system-ui';ctx.lineCap='round';ctx.lineJoin='round';
 lanes.forEach((lane,li)=>{const y0=p.t+li*(laneH+gap),avg7=ma(a,lane.key,7),vals=[...a.map(d=>+d[lane.key]),...avg7].filter(v=>Number.isFinite(v)&&v>0),fallback=lane.key==='load_ratio'?[1]:[50],raw=vals.length?vals:fallback;let lo=lane.min??Math.min(...raw),hi=lane.max??Math.max(...raw),pad=(hi-lo||1)*.18;lo=lane.min??lo-pad;hi=lane.max??hi+pad;if(hi===lo)hi=lo+1;const sp=hi-lo;avgByKey[lane.key]=avg7;
  ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.setLineDash([]);for(let g=0;g<3;g++){const y=y0+g*laneH/2;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke()}
  if(lane.key==='load_ratio'){const by=y0+laneH-((1-lo)/sp)*laneH;ctx.strokeStyle='#cbd5e1';ctx.setLineDash([4,4]);ctx.beginPath();ctx.moveTo(p.l,by);ctx.lineTo(w-p.r,by);ctx.stroke();ctx.setLineDash([])}
  const line=[],smooth=[];a.forEach((d,i)=>{const v=+d[lane.key],av=+avg7[i],x=p.l+i*iw/den;if(Number.isFinite(v)&&v>0)line.push({x,y:y0+laneH-((v-lo)/sp)*laneH,row:d,value:v,lane});if(Number.isFinite(av)&&av>0)smooth.push({x,y:y0+laneH-((av-lo)/sp)*laneH,row:d,value:av,lane})});
  if(line.length>1){const grad=ctx.createLinearGradient(0,y0,0,y0+laneH);grad.addColorStop(0,lane.color+'22');grad.addColorStop(1,lane.color+'00');ctx.beginPath();line.forEach((pt,i)=>i?ctx.lineTo(pt.x,pt.y):ctx.moveTo(pt.x,pt.y));ctx.lineTo(line[line.length-1].x,y0+laneH);ctx.lineTo(line[0].x,y0+laneH);ctx.closePath();ctx.fillStyle=grad;ctx.fill()}
  ctx.save();ctx.globalAlpha=.34;ctx.strokeStyle=lane.color;ctx.lineWidth=1.3;ctx.beginPath();line.forEach((pt,i)=>i?ctx.lineTo(pt.x,pt.y):ctx.moveTo(pt.x,pt.y));ctx.stroke();ctx.restore();
  ctx.strokeStyle=lane.color;ctx.lineWidth=2.8;ctx.beginPath();smooth.forEach((pt,i)=>i?ctx.lineTo(pt.x,pt.y):ctx.moveTo(pt.x,pt.y));ctx.stroke();
  const latest=smooth[smooth.length-1]||line[line.length-1];ctx.fillStyle=lane.color;if(latest){ctx.beginPath();ctx.arc(latest.x,latest.y,3.8,0,Math.PI*2);ctx.fill()}
  const valueX=lane.label.length>3?38:27;ctx.textAlign='left';ctx.fillStyle=C.muted;ctx.font='10px Geist, system-ui';ctx.fillText(lane.label,0,y0+laneH/2+4);ctx.font='12px JetBrains Mono, monospace';ctx.fillStyle=C.ink;ctx.fillText(latest?lane.fmt(latest.value)+lane.unit:'--',valueX,y0+laneH/2+4);
 });
 ctx.fillStyle=C.faint;ctx.font='11px Geist, system-ui';ctx.textAlign='center';for(let i=0;i<Math.min(4,a.length);i++){const idx=Math.round(i*(a.length-1)/Math.max(1,Math.min(4,a.length)-1)),x=clamp(p.l+idx*iw/Math.max(1,a.length-1),28,w-28);ctx.fillText(a[idx].date.slice(5),x,h-10)}
 const pts=a.map((row,i)=>({x:p.l+i*iw/den,y:h*.48,row,i}));
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>VO2max</em><strong>${fmt(p.row.vo2max)} · 7均 ${Number.isFinite(avgByKey.vo2max?.[p.i])?Math.round(avgByKey.vo2max[p.i]):'--'}</strong></span><span><em>HRV</em><strong>${fmt(p.row.hrv,' ms')} · 7均 ${Number.isFinite(avgByKey.hrv?.[p.i])?Math.round(avgByKey.hrv[p.i])+' ms':'--'}</strong></span><span><em>Load</em><strong>${fmt(p.row.load_ratio)} · 7均 ${Number.isFinite(avgByKey.load_ratio?.[p.i])?avgByKey.load_ratio[p.i].toFixed(2):'--'}</strong></span>`);
}
function drawPace(){
 const list=acts().slice().reverse().filter(a=>paceSec(a.pace)).map(a=>({...a,paceSec:paceSec(a.pace)})),c=$('#paceChart'),{ctx,w,h}=canvas(c),p={l:48,r:16,t:18,b:34},iw=w-p.l-p.r,ih=h-p.t-p.b;if(!list.length)return;const vals=list.map(x=>x.paceSec),lo=Math.min(...vals)-15,hi=Math.max(...vals)+15,sp=hi-lo||1,avg7=ma(list,'paceSec',7),pts=[];
 ctx.clearRect(0,0,w,h);drawAxes(ctx,p,w,h,hi,v=>paceText(v));ctx.strokeStyle=C.blue;ctx.lineWidth=2.3;ctx.beginPath();list.forEach((d,i)=>{const x=p.l+i*iw/Math.max(1,list.length-1),y=h-p.b-((d.paceSec-lo)/sp)*ih;pts.push({x,y,row:d,avg:avg7[i]});i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();ctx.strokeStyle=C.green;ctx.setLineDash([5,4]);ctx.beginPath();avg7.forEach((v,i)=>{const x=p.l+i*iw/Math.max(1,list.length-1),y=h-p.b-((v-lo)/sp)*ih;i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke();ctx.setLineDash([]);
 ctx.fillStyle=C.faint;ctx.font='11px Geist';ctx.textAlign='center';for(let i=0;i<Math.min(5,list.length);i++){const idx=Math.round(i*(list.length-1)/Math.max(1,Math.min(5,list.length)-1)),x=clamp(p.l+idx*iw/Math.max(1,list.length-1),28,w-28);ctx.fillText(list[idx].date.slice(5),x,h-10)}
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>配速</em><strong>${p.row.pace}</strong></span><span><em>7次均线</em><strong>${paceText(p.avg)} /km</strong></span><span><em>距离</em><strong>${fmt(p.row.distance_km,' km')}</strong></span>`);
}
function drawWeeks(){
 const raw=DATA.summaries.weekly||[],isPartial=wk=>wk&&wk.days&&wk.days<7,currentPartial=isPartial(raw[0])?raw[0]:null,completed=raw.filter(wk=>!isPartial(wk)).slice(0,8).reverse(),weeks=currentPartial?[...completed,currentPartial]:completed.slice(-8),c=$('#weekChart'),{ctx,w,h}=canvas(c),p={l:62,r:18,t:16,b:34},iw=w-p.l-p.r,den=Math.max(1,weeks.length-1),pts=[];
 const series=[
  {label:'距离',unit:' km',color:C.blue,value:wk=>+wk.distance_km||0,fmt:v=>v.toFixed(1)},
  {label:'时长',unit:' h',color:C.orange,value:wk=>((+wk.activity_min||+wk.exercise_min||0)/60),fmt:v=>v.toFixed(1)},
  {label:'次数',unit:' 次',color:C.green,value:wk=>+wk.activities||0,fmt:v=>Math.round(v)}
 ];
 ctx.clearRect(0,0,w,h);if(!weeks.length)return;ctx.lineCap='round';ctx.lineJoin='round';
 const gap=18,laneH=(h-p.t-p.b-gap*(series.length-1))/series.length;
 series.forEach((s,si)=>{
  const y0=p.t+si*(laneH+gap),scaleRows=weeks.filter(wk=>!isPartial(wk)),scaleVals=(scaleRows.length?scaleRows:weeks).map(s.value),max=niceTop(Math.max(...scaleVals,1)*1.08);
  ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.setLineDash([]);for(let g=0;g<3;g++){const y=y0+g*laneH/2;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke()}
  ctx.textAlign='left';ctx.font='11px Inter, Geist, system-ui';ctx.fillStyle=C.muted;ctx.fillText(s.label,0,y0+laneH/2+4);
  ctx.font='12px JetBrains Mono, monospace';ctx.fillStyle=C.ink;ctx.fillText(s.fmt(s.value(weeks[weeks.length-1]))+s.unit,28,y0+laneH/2+4);
  const line=weeks.map((wk,i)=>{const value=s.value(wk),x=p.l+i*iw/den,y=y0+laneH-(value/max)*laneH;return{x,y,row:wk,value,s,partial:isPartial(wk)}});
  for(let i=1;i<line.length;i++){const prev=line[i-1],cur=line[i],ghost=prev.partial||cur.partial;ctx.save();ctx.globalAlpha=ghost ? .55 : 1;ctx.strokeStyle=s.color;ctx.lineWidth=ghost?2:2.4;ctx.setLineDash(ghost?[5,5]:[]);ctx.beginPath();ctx.moveTo(prev.x,prev.y);ctx.lineTo(cur.x,cur.y);ctx.stroke();ctx.restore()}
  line.forEach(pt=>drawDot(ctx,pt,s.color,pt.partial?3.1:3.4,pt.partial ? .55 : 1));
 });
 ctx.fillStyle=C.faint;ctx.font='11px Inter, Geist, system-ui';ctx.textAlign='center';weeks.forEach((wk,i)=>{const label=String(wk.key||'').replace(/^\\d{4}-/,'');const x=p.l+i*iw/den;ctx.fillText(label+(isPartial(wk)?'*':''),clamp(x,30,w-30),h-10);pts.push({x,y:h*.5,row:wk,partial:isPartial(wk)})});
 hover(c,pts,p=>`<b>${p.row.key}${p.partial?' · 当前周未完成':''}</b><span><em>距离</em><strong>${(+p.row.distance_km||0).toFixed(1)} km</strong></span><span><em>时长</em><strong>${hours(+p.row.activity_min||+p.row.exercise_min)} h</strong></span><span><em>次数</em><strong>${fmt(p.row.activities,' 次')}</strong></span><span><em>覆盖天数</em><strong>${fmt(p.row.days,' 天')}</strong></span>`);
}
function drawSleep(){
 const a=days(),c=$('#sleepChart'),{ctx,w,h}=canvas(c),p={l:58,r:16,t:16,b:30},iw=w-p.l-p.r,den=Math.max(1,a.length-1),pts=[];
 const sleepSeries=[{key:'light_min',label:'浅睡',color:C.sleepLight},{key:'deep_min',label:'深睡',color:C.green},{key:'rem_min',label:'REM',color:C.violet},{key:'awake_min',label:'清醒',color:C.sleepAwake}];
 const recSeries=[{key:'hrv',label:'HRV',unit:' ms',color:C.cyan,fmt:v=>Math.round(v)},{key:'rhr',label:'RHR',unit:' bpm',color:C.orange,fmt:v=>Math.round(v),invert:true,note:'低更好'}];
 ctx.clearRect(0,0,w,h);if(!a.length)return;ctx.lineCap='round';ctx.lineJoin='round';
 const totalH=h-p.t-p.b,gap=24,sleepH=totalH*.54,recTop=p.t+sleepH+gap,recH=totalH-sleepH-gap,recGap=12,recLaneH=(recH-recGap)/2;
 const sleepVals=a.flatMap(d=>sleepSeries.map(s=>+d[s.key])).filter(v=>Number.isFinite(v)&&v>0),sleepMax=niceTop(Math.max(...sleepVals,60)/60*1.12)*60;
 function drawLaneGrid(label,y0,hh,maxLabel){
  ctx.strokeStyle=C.grid;ctx.lineWidth=1;ctx.setLineDash([]);for(let i=0;i<3;i++){const y=y0+i*hh/2;ctx.beginPath();ctx.moveTo(p.l,y);ctx.lineTo(w-p.r,y);ctx.stroke()}
  ctx.fillStyle=C.muted;ctx.font='11px Inter, Geist, system-ui';ctx.textAlign='left';ctx.fillText(label,0,y0+hh/2+4);
  if(maxLabel){ctx.fillStyle=C.faint;ctx.font='10px JetBrains Mono, monospace';ctx.textAlign='right';ctx.fillText(maxLabel,p.l-8,y0+5)}
 }
 function brokenSeries(s,y0,hh,lo,hi,width=2){
  const sp=hi-lo||1;ctx.strokeStyle=s.color;ctx.lineWidth=width;ctx.setLineDash(s.dash||[]);ctx.beginPath();let started=false,latest=null;
  a.forEach((d,i)=>{const v=+d[s.key];if(!Number.isFinite(v)||v<=0){started=false;return}const ratio=s.invert?1-(v-lo)/sp:(v-lo)/sp,x=p.l+i*iw/den,y=y0+hh-clamp(ratio,0,1)*hh;latest={x,y,row:d,value:v,s};if(started)ctx.lineTo(x,y);else{ctx.moveTo(x,y);started=true}});
  ctx.stroke();ctx.setLineDash([]);if(latest)drawDot(ctx,latest,s.color,3.1,.92);return latest;
 }
 drawLaneGrid('睡眠结构',p.t,sleepH,(sleepMax/60).toFixed(1)+'h');
 sleepSeries.forEach(s=>brokenSeries(s,p.t,sleepH,0,sleepMax,1.9));
 recSeries.forEach((s,i)=>{
  const y0=recTop+i*(recLaneH+recGap),vals=a.map(d=>+d[s.key]).filter(v=>Number.isFinite(v)&&v>0);drawLaneGrid(s.label,y0,recLaneH,'');
  if(vals.length<2)return;let lo=Math.min(...vals),hi=Math.max(...vals),pad=(hi-lo||1)*.18;lo-=pad;hi+=pad;const latest=brokenSeries(s,y0,recLaneH,lo,hi,2.1);
  ctx.fillStyle=C.ink;ctx.font='12px JetBrains Mono, monospace';ctx.textAlign='left';ctx.fillText(latest?s.fmt(latest.value)+s.unit:'--',28,y0+recLaneH/2+4);
  if(s.note){ctx.fillStyle=C.faint;ctx.font='10px Inter, Geist, system-ui';ctx.fillText(s.note,28,y0+recLaneH/2+17)}
 });
 ctx.fillStyle=C.faint;ctx.font='11px Inter, Geist, system-ui';ctx.textAlign='center';const ticks=Math.min(5,a.length);for(let i=0;i<ticks;i++){const idx=Math.round(i*(a.length-1)/Math.max(1,ticks-1)),x=clamp(p.l+idx*iw/den,28,w-28);ctx.fillText(a[idx].date.slice(5),x,h-9)}a.forEach((row,i)=>pts.push({x:p.l+i*iw/den,y:h*.45,row}));
 hover(c,pts,p=>`<b>${p.row.date}</b><span><em>总睡眠</em><strong>${p.row.sleep_min?hours(p.row.sleep_min)+' h':'--'}</strong></span><span><em>浅睡</em><strong>${p.row.light_min?hours(p.row.light_min)+' h':'--'}</strong></span><span><em>深睡 / REM</em><strong>${p.row.deep_min?hours(p.row.deep_min)+' h':'--'} / ${p.row.rem_min?hours(p.row.rem_min)+' h':'--'}</strong></span><span><em>清醒</em><strong>${p.row.awake_min?Math.round(p.row.awake_min)+' min':'--'}</strong></span><span><em>HRV / RHR</em><strong>${fmt(p.row.hrv,' ms')} / ${fmt(p.row.rhr,' bpm')}</strong></span>`);
}
function drawSpark(el,vals,col=C.blue){const{ctx,w,h}=canvas(el);vals=vals.filter(v=>Number.isFinite(+v)).map(Number);if(vals.length<2)return;const lo=Math.min(...vals),hi=Math.max(...vals),sp=hi-lo||1;ctx.clearRect(0,0,w,h);ctx.strokeStyle=col;ctx.lineWidth=2;ctx.beginPath();vals.forEach((v,i)=>{const x=i*w/Math.max(1,vals.length-1),y=h-4-(v-lo)/sp*(h-8);i?ctx.lineTo(x,y):ctx.moveTo(x,y)});ctx.stroke()}
function readiness(a){const d=last(a),rec=DATA.recovery.recovery_percent||0,hrv=d.hrv&&d.hrv_baseline?clamp(d.hrv/d.hrv_baseline*100,50,120):80,sleep=d.sleep_min?clamp(d.sleep_min/480*100,45,110):75,load=d.load_ratio<=1.15?90:d.load_ratio<1.35?68:45;return Math.round(clamp(rec*.38+hrv*.25+sleep*.22+load*.15,0,100))}
function renderHero(){const a=days(),activity=acts(),total=sum(activity,x=>x.distance_km),time=sum(activity,x=>x.duration_min),score=readiness(a),longest=Math.max(0,...activity.map(x=>+x.distance_km||0)),dailyAvg=avg(a,x=>x.km)||0,load=last(a).load_ratio,status=score>=75?'节奏健康':score>=60?'保持连续':'恢复优先';$('#heroMetric').textContent=`${total.toFixed(1)} km`;$('#heroNarrative').textContent=`${activity.length} 次训练 · ${Math.floor(time/60)}:${String(Math.round(time%60)).padStart(2,'0')} 小时 · 恢复 ${score} · ${status}`;$('#heroKpis').innerHTML=[['最长单次',`${longest.toFixed(1)} km`],['日均距离',`${dailyAvg.toFixed(1)} km`],['Load Ratio',fmt(load)],['恢复评分',score]].map(x=>`<div><span>${x[0]}</span><b>${x[1]}</b></div>`).join('')}
function renderCards(){const a=days(),activity=acts(),total=sum(activity,x=>x.distance_km),time=sum(activity,x=>x.duration_min),cal=sum(activity,x=>x.calories),elev=sum(activity,x=>x.elevation_gain),paces=activity.map(x=>paceSec(x.pace)).filter(Boolean),cards=[['总跑步次数',activity.length,'次',compare('count'),C.blue,'R'],['总距离',total.toFixed(1),'公里',compare('km'),C.green,'D'],['总时长',`${Math.floor(time/60)}:${String(Math.round(time%60)).padStart(2,'0')}`,'小时',compare('min'),C.orange,'T'],['平均配速',paceText(avg(paces,x=>x)),'/公里',comparePace(activity),C.blue,'P'],['总消耗',Math.round(cal).toLocaleString(),'千卡',compare('cal'),C.red,'K'],['累计爬升',Math.round(elev).toLocaleString(),'米',compare('elev'),C.cyan,'E']];$('#cards').innerHTML=cards.map((x,i)=>`<article class="card" style="--color:${x[4]};--tone:${x[4]}1a"><div class="card-head"><div class="dot">${x[5]}</div><span>${x[0]}</span></div><b>${x[1]} <small>${x[2]}</small></b>${trend(x[3],x[0]==='平均配速'?false:true)}<canvas class="spark" data-i="${i}"></canvas></article>`).join('');$$('.spark').forEach((c,i)=>drawSpark(c,[a.map(x=>x.count),a.map(x=>x.km),a.map(x=>x.min),paces,a.map(x=>x.cal),a.map(x=>x.elev)][i]||[],cards[i][4]))}
function renderStatus(){const a=days(),score=readiness(a),d=last(a),load=d.load_ratio||0,title=score>=75?'良好':score>=60?'保持':'恢复优先';$('#statusScore').textContent=score;$('#statusTitle').textContent=title;$('#statusText').textContent=load>1.2?'训练负荷偏高，下一次训练建议控制强度。':'训练负荷适中，可以维持节奏。';const r=62,circ=2*Math.PI*r,el=$('#statusRing');el.style.strokeDasharray=`${score/100*circ} ${circ}`;el.style.stroke=score>=75?C.green:score>=60?C.orange:C.red;$('#coachList').innerHTML=[['负荷状态',`当前 load ratio ${fmt(d.load_ratio)}，${d.load_status||'--'}。`],['恢复状态',`恢复 ${fmt(DATA.recovery.recovery_percent,'%')}，${DATA.recovery.level||'--'}。`],['HRV 对比',d.hrv_baseline?`HRV ${d.hrv} ms，相对基线 ${Math.round(d.hrv-d.hrv_baseline)} ms。`:'HRV 基线暂缺。']].map(x=>`<div><b>${x[0]}</b><p>${x[1]}</p></div>`).join('')}
function renderActivities(){const sports=['All',...new Set(DATA.activities.map(a=>a.sport).filter(Boolean))];$('#filters').innerHTML=sports.map(s=>`<button class="${s===sportFilter?'active':''}" data-sport="${s}">${s}</button>`).join('');$$('#filters button').forEach(b=>b.onclick=()=>{sportFilter=b.dataset.sport;render()});const list=acts().slice(0,18);$('#activityCount').textContent=`${list.length} records`;$('#activityList').innerHTML=list.map((a,i)=>`<article class="activity" data-i="${i}"><div class="badge">${Math.round(a.distance_km||0)}</div><div><h3>${a.location||a.sport}</h3><p>${a.date} · ${a.sport} · ${a.pace}</p></div><div class="num">${fmt(a.training_load,' TL')}</div></article>`).join('')||'<div class="empty">No activities</div>';$$('.activity').forEach((el,i)=>el.onclick=()=>openDrawer(list[i]))}
const esc=v=>String(v??'').replace(/[&<>"']/g,m=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[m]));
function statGrid(items){return items.map(x=>`<div><span>${esc(x[0])}</span><b>${esc(x[1])}</b></div>`).join('')}
function verticalRatio(v){const n=+v;if(!Number.isFinite(n))return '--';const pct=n>30?n/10:n;return `${pct.toFixed(1)}%`}
function renderLapTable(laps){
 if(!laps?.length)return '<section class="detail-section"><h3>分段数据</h3><p class="detail-loading">暂无分段数据</p></section>';
 return `<section class="detail-section"><h3>分段数据</h3><table class="detail-table"><thead><tr><th>#</th><th>距离</th><th>时间</th><th>配速</th><th>均心</th><th>峰心</th><th>步频</th><th>步幅</th><th>踏地</th><th>垂直振幅</th><th>垂直比</th><th>功率</th><th>爬升</th></tr></thead><tbody>${laps.map(l=>`<tr><td>${esc(l.index??'')}</td><td>${fmt(l.distance_km,' km')}</td><td>${esc(l.duration||'--')}</td><td>${esc(l.pace||'--')}</td><td>${fmt(l.avg_hr,'')}</td><td>${fmt(l.max_hr,'')}</td><td>${fmt(l.avg_cadence,'')}</td><td>${fmt(l.stride_length_cm,' cm')}</td><td>${fmt(l.ground_time_ms,' ms')}</td><td>${fmt(l.stride_height_mm,' mm')}</td><td>${verticalRatio(l.stride_ratio)}</td><td>${fmt(l.avg_power,'')}</td><td>${fmt(l.elev_gain,' m')}</td></tr>`).join('')}</tbody></table></section>`;
}
function renderZones(zones){
 if(!zones?.length)return '';
 return `<section class="detail-section"><h3>心率区间</h3>${zones.map(z=>`<div class="zone-row"><span>Z${esc(z.index??'')}</span><div class="zone-bar" title="${esc(z.range||'')}"><i style="width:${clamp(+z.percent||0,0,100)}%"></i></div><b>${fmt(z.percent,'%')}</b></div>`).join('')}</section>`;
}
function renderDetailSections(detail){
 const s=detail.summary||{},w=detail.weather||{},weather=w.temperature_c!==null&&w.temperature_c!==undefined?`${w.temperature_c}°C · 湿度 ${fmt(w.humidity,'%')}`:'--';
 const metrics=[['最佳公里',s.best_km||'--'],['有氧效果',fmt(s.aerobic_effect)],['无氧效果',fmt(s.anaerobic_effect)],['VO2max',fmt(s.vo2max)],['热量',fmt(s.calories,' kcal')],['平均步频',fmt(s.avg_cadence,' spm')],['步幅',fmt(s.avg_stride_length_cm,' cm')],['触地',fmt(s.ground_time_ms,' ms')],['爬升 / 下降',`${fmt(s.elev_gain,' m')} / ${fmt(s.descent,' m')}`],['天气',weather]];
 return `<section class="detail-section"><h3>训练摘要</h3><div class="detail-stats">${statGrid(metrics)}</div></section>${renderLapTable(detail.laps||[])}${renderZones(detail.hr_zones||[])}${detail.note?`<section class="detail-section"><h3>记录</h3><div class="detail-note">${esc(detail.note)}</div></section>`:''}`;
}
async function openDrawer(a){
 $('#drawerTitle').textContent=a.location||a.sport||'Activity';
 $('#drawerStats').innerHTML=statGrid([['Distance',fmt(a.distance_km,' km')],['Pace',fmt(a.pace)],['Time',fmt(a.duration)],['Avg HR',fmt(a.avg_hr,' bpm')],['Load',fmt(a.training_load,' TL')],['Power',fmt(a.avg_power,' W')],['Elevation',fmt(a.elevation_gain,' m')],['Date',fmt(a.date)]]);
 const detailEl=$('#drawerDetail');detailEl.innerHTML='<div class="detail-loading">正在读取分段与训练详情...</div>';$('#drawer').classList.add('show');
 if(!a.label_id){detailEl.innerHTML='<div class="detail-error">这条活动缺少 COROS activity id，无法读取详情。</div>';return}
 try{const res=await fetch(`activity-detail?id=${encodeURIComponent(a.label_id)}&sport_type=${encodeURIComponent(a.sport_type||0)}`,{cache:'no-store'}),body=await res.json();if(!body.ok)throw new Error(body.error||'detail unavailable');detailEl.innerHTML=renderDetailSections(body.detail||{})}
 catch(err){detailEl.innerHTML=`<div class="detail-error">详情读取失败：${esc(err.message)}</div>`}
}
function renderAchievements(){const a=days(),activity=acts(),total=sum(activity,x=>x.distance_km),cal=sum(activity,x=>x.calories),elev=sum(activity,x=>x.elevation_gain);$('#achievements').innerHTML=[['坚持达人',`完成跑步 ${activity.length} 次`,C.blue,'RUN'],['距离达人',`累计 ${total.toFixed(1)} 公里`,C.green,'KM'],['燃脂高手',`累计消耗 ${Math.round(cal)} 千卡`,C.red,'K'],['攀登者',`累计爬升 ${Math.round(elev)} 米`,C.orange,'UP']].map(x=>`<div class="medal" style="--color:${x[2]};--tone:${x[2]}1a"><i>${x[3]}</i><b>${x[0]}</b><span>${x[1]}</span></div>`).join('')}
function render(){const a=days();$('#rangeTitle').textContent=range==='all'?'全部':`${range}天`;$('#dateLabel').textContent=`${a[0]?.date||'--'} – ${last(a).date||'--'}`;renderHero();renderCards();renderStatus();renderActivities();renderAchievements();drawDistance();drawAbility();drawPace();drawWeeks();drawSleep();if(fitData)drawFitChart()}
$$('.actions .seg button').forEach(b=>b.onclick=()=>{$$('.actions .seg button').forEach(x=>x.classList.remove('active'));b.classList.add('active');range=b.dataset.range;render()});$('#closeDrawer').onclick=()=>$('#drawer').classList.remove('show');const pullAll=$('#pullAllBtn');if(pullAll)pullAll.onclick=startFullRefresh;addEventListener('resize',render);initFit();render();startSafeRefresh();
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

    def send_json(self, payload, code=200):
        self.send_bytes(json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8", code)

    def authorized(self):
        return is_refresh_authorized(self.headers.get("X-Refresh-Token", ""))

    def do_HEAD(self):
        path = self.path.split("?", 1)[0]
        if BASE_PATH and path == BASE_PATH:
            self.send_response(301)
            self.send_header("Location", BASE_PATH + "/")
            self.end_headers()
            return
        if BASE_PATH and path.startswith(BASE_PATH + "/"):
            path = path[len(BASE_PATH):] or "/"
        if path in ("/", "/index.html", "/data.json", "/refresh-status", "/refresh-safe-status", "/activity-detail"):
            self.send_response(200)
            self.send_header(
                "Content-Type",
                "application/json; charset=utf-8" if path in ("/data.json", "/refresh-status", "/refresh-safe-status", "/activity-detail") else "text/html; charset=utf-8",
            )
            self.end_headers()
            return
        self.send_response(404)
        self.end_headers()

    def do_GET(self):
        raw_path = self.path.split("?", 1)[0]
        query = parse_qs(self.path.split("?", 1)[1]) if "?" in self.path else {}
        path = raw_path
        if BASE_PATH and path == BASE_PATH:
            self.send_response(301)
            self.send_header("Location", BASE_PATH + "/")
            self.end_headers()
            return
        path = strip_base_path(path)
        if path in ("/", "/index.html"):
            self.send_bytes(build_home().encode("utf-8"), "text/html; charset=utf-8")
            return
        if path == "/data.json":
            with open(DATA_FILE, "rb") as f:
                self.send_bytes(f.read(), "application/json; charset=utf-8")
            return
        if path == "/refresh-status":
            if not self.authorized():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            self.send_json({"ok": True, "status": refresh_status_snapshot()})
            return
        if path == "/refresh-safe-status":
            self.send_json({"ok": True, "status": public_refresh_status_snapshot()})
            return
        if path == "/activity-detail":
            activity_id = (query.get("id") or [""])[0]
            try:
                sport_type = int((query.get("sport_type") or ["0"])[0] or 0)
            except ValueError:
                sport_type = 0
            self.send_json(get_activity_detail_payload(activity_id, sport_type), 200)
            return
        self.send_bytes(b"not found", "text/plain; charset=utf-8", 404)

    def do_POST(self):
        path = strip_base_path(self.path.split("?", 1)[0])
        if path == "/fit-import":
            ctype, _ = cgi.parse_header(self.headers.get("Content-Type", ""))
            if ctype != "multipart/form-data":
                self.send_json({"ok": False, "error": "expected multipart FIT upload"}, 400)
                return
            try:
                length = int(self.headers.get("Content-Length", "0") or 0)
            except ValueError:
                length = 0
            if length <= 0:
                self.send_json({"ok": False, "error": "empty request"}, 400)
                return
            if length > FIT_UPLOAD_LIMIT:
                self.send_json({"ok": False, "error": "FIT upload is larger than %.1f MB" % (FIT_UPLOAD_LIMIT / 1024 / 1024)}, 413)
                return
            form = cgi.FieldStorage(
                fp=self.rfile,
                headers=self.headers,
                environ={
                    "REQUEST_METHOD": "POST",
                    "CONTENT_TYPE": self.headers.get("Content-Type", ""),
                    "CONTENT_LENGTH": str(length),
                },
            )
            file_item = form["file"] if "file" in form else None
            if isinstance(file_item, list):
                file_item = file_item[0] if len(file_item) else None
            payload = get_fit_import_payload(file_item)
            self.send_json(payload, 200 if payload.get("ok") else 400)
            return
        if path == "/refresh-safe":
            started, reason, status = start_safe_refresh()
            self.send_json({"ok": True, "started": started, "reason": reason, "status": status}, 202 if started else 200)
            return
        if path == "/refresh-all":
            if not self.authorized():
                self.send_json({"ok": False, "error": "unauthorized"}, 401)
                return
            started, status = start_full_refresh()
            if not started:
                self.send_json({"ok": False, "error": "refresh already running", "status": status}, 409)
                return
            self.send_json({"ok": True, "started": True, "status": status}, 202)
            return
        self.send_json({"ok": False, "error": "not found"}, 404)


def main():
    ThreadedHTTPServer.allow_reuse_address = True
    with ThreadedHTTPServer((HOST, PORT), Handler) as httpd:
        print("Sports Log Web -> http://%s:%s%s/" % (HOST, PORT, BASE_PATH), flush=True)
        httpd.serve_forever()


if __name__ == "__main__":
    main()
