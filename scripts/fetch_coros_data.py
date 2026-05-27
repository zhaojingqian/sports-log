#!/usr/bin/env python3
"""Fetch COROS data through the local cygnusb/coros-mcp checkout.

Run with /root/workspace/coros-mcp/.venv/bin/python. The script imports the
MCP tool functions directly and normalizes their data into dashboard.json.
"""

import asyncio
import json
import os
import sys
from datetime import datetime, timedelta

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(BASE_DIR, "data", "dashboard.json")
COROS_MCP_DIR = os.environ.get("COROS_MCP_DIR", "/root/workspace/coros-mcp")

sys.path.insert(0, COROS_MCP_DIR)

from dotenv import load_dotenv  # noqa: E402

load_dotenv(os.path.join(COROS_MCP_DIR, ".env"))


def ymd(day):
    if not day:
        return ""
    text = str(day)
    if "-" in text:
        return text[:10]
    return "%s-%s-%s" % (text[:4], text[4:6], text[6:8])


def compact_day(day):
    return day.replace("-", "")


def safe_int(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def safe_float(value, digits=2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def minutes_label(seconds):
    if not seconds:
        return "0:00"
    seconds = int(seconds)
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def pace_label(duration_seconds, distance_meters):
    if not duration_seconds or not distance_meters:
        return "—"
    km = distance_meters / 1000
    if km <= 0:
        return "—"
    pace = int(round(duration_seconds / km))
    minutes, seconds = divmod(pace, 60)
    return "%d:%02d /km" % (minutes, seconds)


def load_status(ratio):
    if ratio is None:
        return ""
    if ratio >= 1.3:
        return "High strain"
    if ratio >= 1.0:
        return "Optimized"
    if ratio >= 0.85:
        return "Maintaining"
    return "Recovery"


def hrv_status(hrv, baseline):
    if hrv is None or baseline is None:
        return ""
    if hrv >= baseline * 1.12:
        return "Above normal"
    if hrv <= baseline * 0.88:
        return "Below normal"
    return "Normal"


def load_dashboard():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_dashboard(data):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, DATA_FILE)


async def fetch_all(weeks):
    import server

    auth = await server.check_coros_auth()
    if not auth.get("authenticated"):
        email = os.environ.get("COROS_EMAIL")
        password = os.environ.get("COROS_PASSWORD")
        region = os.environ.get("COROS_REGION", "eu")
        if not (email and password):
            raise RuntimeError(auth.get("message") or auth.get("error") or "coros-mcp is not authenticated")
        login = await server.authenticate_coros(email=email, password=password, region=region)
        if not login.get("authenticated"):
            raise RuntimeError(login.get("error") or "coros-mcp auto-auth failed")
        auth = await server.check_coros_auth()
        if not auth.get("authenticated"):
            raise RuntimeError(auth.get("message") or auth.get("error") or "coros-mcp is not authenticated")

    today = datetime.now().date()
    start = today - timedelta(weeks=weeks)
    start_day = start.strftime("%Y%m%d")
    end_day = today.strftime("%Y%m%d")

    daily, acts, schedule, workouts = await asyncio.gather(
        server.get_daily_metrics(weeks=weeks),
        server.list_activities(start_day=start_day, end_day=end_day, page=1, size=100),
        server.list_planned_activities(start_day=today.strftime("%Y%m%d"), end_day=(today + timedelta(days=14)).strftime("%Y%m%d")),
        server.list_workouts(),
    )
    sleep = {"records": []}
    if os.environ.get("SPORTS_LOG_ALLOW_MOBILE_AUTH") == "1":
        email = os.environ.get("COROS_EMAIL")
        password = os.environ.get("COROS_PASSWORD")
        region = os.environ.get("COROS_REGION", "eu")
        if not (email and password):
            print("warning: mobile auth requested but COROS_EMAIL/COROS_PASSWORD are missing")
        else:
            mobile = await server.authenticate_coros_mobile(email=email, password=password, region=region)
            if not mobile.get("authenticated"):
                print("warning: coros mobile auth failed; sleep phases may be stale")
            else:
                sleep = await server.get_sleep_data(weeks=weeks)
    else:
        print("sleep phase fetch skipped: mobile auth disabled to avoid logging out the phone app")
    for name, payload in [
        ("daily", daily),
        ("sleep", sleep),
        ("activities", acts),
        ("schedule", schedule),
        ("workouts", workouts),
    ]:
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError("%s: %s" % (name, payload["error"]))
    cache = await server.get_cache_status()
    return auth, daily, sleep, acts, schedule, workouts, cache


def normalize_daily(existing_rows, daily_payload, sleep_payload):
    existing = {row.get("date"): row for row in existing_rows}
    sleep_by_date = {ymd(row.get("date")): row for row in sleep_payload.get("records", [])}
    rows = []
    for raw in daily_payload.get("records", []):
        date = ymd(raw.get("date"))
        base = dict(existing.get(date, {}))
        sleep = sleep_by_date.get(date, {})
        phases = sleep.get("phases") or {}
        distance_m = raw.get("distance")
        duration_s = raw.get("duration")
        hrv = safe_int(raw.get("avg_sleep_hrv"))
        baseline = safe_int(raw.get("baseline"))
        ratio = safe_float(raw.get("training_load_ratio"), 2)
        base.update(
            {
                "date": date,
                "sleep_score": safe_int(sleep.get("quality_score")) if sleep else base.get("sleep_score"),
                "sleep_min": safe_int(sleep.get("total_duration_minutes")) if sleep else base.get("sleep_min"),
                "deep_min": safe_int(phases.get("deep_minutes")) if sleep else base.get("deep_min"),
                "light_min": safe_int(phases.get("light_minutes")) if sleep else base.get("light_min"),
                "rem_min": safe_int(phases.get("rem_minutes")) if sleep else base.get("rem_min"),
                "awake_min": safe_int(phases.get("awake_minutes")) if sleep else base.get("awake_min"),
                "nap_min": safe_int(phases.get("nap_minutes")) if sleep else base.get("nap_min"),
                "hrv": hrv,
                "hrv_baseline": baseline,
                "hrv_status": hrv_status(hrv, baseline),
                "rhr": safe_int(raw.get("rhr")),
                "short_load": safe_int(raw.get("ati")),
                "long_load": safe_int(raw.get("cti")),
                "load_ratio": ratio,
                "load_status": load_status(ratio),
                "training_load": safe_int(raw.get("training_load")),
                "tired_rate": safe_float(raw.get("tired_rate"), 1),
                "daily_distance_km": safe_float((distance_m or 0) / 1000, 2),
                "daily_duration_min": safe_int((duration_s or 0) / 60),
                "vo2max": safe_int(raw.get("vo2max")),
                "lthr": safe_int(raw.get("lthr")),
                "ltsp": safe_int(raw.get("ltsp")),
                "stamina_level": safe_float(raw.get("stamina_level"), 1),
                "stamina_level_7d": safe_float(raw.get("stamina_level_7d"), 1),
            }
        )
        rows.append(base)
    return sorted(rows, key=lambda r: r.get("date", ""))


def normalize_activities(payload):
    rows = []
    for raw in payload.get("activities", []):
        start = raw.get("start_time") or ""
        date = start[:10] if "-" in start else ymd(start[:8])
        duration = raw.get("duration_seconds") or 0
        distance = raw.get("distance_meters") or 0
        rows.append(
            {
                "date": date,
                "sport": raw.get("sport_name") or raw.get("name") or "Activity",
                "location": raw.get("name") or raw.get("sport_name") or "",
                "duration": minutes_label(duration),
                "duration_min": safe_float(duration / 60, 2),
                "distance_km": safe_float(distance / 1000, 2),
                "pace": pace_label(duration, distance),
                "avg_hr": safe_int(raw.get("avg_hr")),
                "max_hr": safe_int(raw.get("max_hr")),
                "calories": safe_int((raw.get("calories") or 0) / 1000),
                "training_load": safe_int(raw.get("training_load")),
                "avg_power": safe_int(raw.get("avg_power")),
                "elevation_gain": safe_int(raw.get("elevation_gain")),
                "label_id": str(raw.get("activity_id") or ""),
                "sport_type": safe_int(raw.get("sport_type")),
            }
        )
    return sorted(rows, key=lambda r: (r.get("date", ""), r.get("label_id", "")), reverse=True)


def normalize_schedule(payload):
    schedule = payload.get("schedule") or {}
    rows = []
    for item in schedule.get("entities", []) if isinstance(schedule, dict) else []:
        sport_data = item.get("sportData") or {}
        rows.append(
            {
                "date": ymd(str(item.get("happenDay") or item.get("happen_day") or "")),
                "name": item.get("name") or item.get("trainingName") or sport_data.get("name") or item.get("sportName") or "Planned activity",
                "distance_km": safe_float((sport_data.get("distance") or 0) / 100000, 2) if sport_data else None,
                "estimated_time": minutes_label(sport_data.get("duration")) if sport_data.get("duration") else "",
                "load": safe_int(item.get("trainingLoad") or item.get("load") or sport_data.get("trainingLoad")),
                "raw": item,
            }
        )
    return rows


def update_fitness(data, daily_rows):
    latest = next((r for r in reversed(daily_rows) if r.get("vo2max")), None)
    if not latest:
        return
    fitness = data.setdefault("fitness", {})
    fitness["vo2max"] = latest.get("vo2max")
    fitness["threshold_pace"] = (
        "%d:%02d /km" % divmod(int(latest.get("ltsp")), 60)
        if latest.get("ltsp")
        else fitness.get("threshold_pace")
    )
    fitness["running_level"] = latest.get("stamina_level") or fitness.get("running_level")


def main():
    weeks = int(os.environ.get("SPORTS_LOG_WEEKS", "8"))
    data = load_dashboard()
    auth, daily, sleep, acts, schedule, workouts, cache = asyncio.run(fetch_all(weeks))
    daily_rows = normalize_daily(data.get("daily", []), daily, sleep)
    data["daily"] = daily_rows
    data["activities"] = normalize_activities(acts)
    data["schedule"] = normalize_schedule(schedule)
    data["workouts"] = workouts.get("workouts", [])
    data["coros_cache"] = cache
    update_fitness(data, daily_rows)
    meta = data.setdefault("meta", {})
    meta["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    meta["window_start"] = daily_rows[0]["date"] if daily_rows else meta.get("window_start")
    meta["window_end"] = daily_rows[-1]["date"] if daily_rows else meta.get("window_end")
    meta["source"] = "cygnusb/coros-mcp"
    meta["automation_status"] = "coros-mcp authenticated; daily refresh enabled"
    meta["coros_auth"] = {
        "user_id": auth.get("user_id"),
        "region": auth.get("region"),
        "expires_in_hours": auth.get("expires_in_hours"),
        "mobile_authenticated": auth.get("mobile_authenticated"),
    }
    save_dashboard(data)
    print("fetched COROS data via coros-mcp: %s daily, %s activities" % (len(data["daily"]), len(data["activities"])))


if __name__ == "__main__":
    main()
