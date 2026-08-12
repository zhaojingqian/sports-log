#!/usr/bin/env python3
"""Normalize COROS gateway data into the stable sports-log dashboard schema."""

import asyncio
import json
import os
import sys
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

from sports_log.integrations.coros import CorosGateway  # noqa: E402
from sports_log.settings import DATA_FILE  # noqa: E402


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
    with DATA_FILE.open(encoding="utf-8") as f:
        return json.load(f)


def save_dashboard(data):
    tmp = DATA_FILE.with_suffix(DATA_FILE.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(str(tmp), str(DATA_FILE))


async def fetch_all(weeks):
    snapshot = await CorosGateway().fetch_snapshot(weeks)
    return (
        snapshot["auth"],
        snapshot["daily"],
        snapshot["sleep"],
        snapshot["activities"],
        snapshot["schedule"],
        snapshot["workouts"],
        snapshot["cache"],
    )


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
        rhr = safe_int(raw.get("rhr"))
        short_load = safe_int(raw.get("ati"))
        long_load = safe_int(raw.get("cti"))
        training_load = safe_int(raw.get("training_load"))
        tired_rate = safe_float(raw.get("tired_rate"), 1)
        vo2max = safe_int(raw.get("vo2max"))
        lthr = safe_int(raw.get("lthr"))
        ltsp = safe_int(raw.get("ltsp"))
        stamina = safe_float(raw.get("stamina_level"), 1)
        stamina_7d = safe_float(raw.get("stamina_level_7d"), 1)
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
                "hrv": hrv if hrv is not None else base.get("hrv"),
                "hrv_baseline": baseline if baseline is not None else base.get("hrv_baseline"),
                "hrv_status": hrv_status(hrv, baseline) or base.get("hrv_status"),
                "rhr": rhr if rhr is not None else base.get("rhr"),
                "short_load": short_load if short_load is not None else base.get("short_load"),
                "long_load": long_load if long_load is not None else base.get("long_load"),
                "load_ratio": ratio if ratio is not None else base.get("load_ratio"),
                "load_status": load_status(ratio) or base.get("load_status"),
                "training_load": training_load if training_load is not None else base.get("training_load"),
                "tired_rate": tired_rate if tired_rate is not None else base.get("tired_rate"),
                "daily_distance_km": safe_float((distance_m or 0) / 1000, 2),
                "daily_duration_min": safe_int((duration_s or 0) / 60),
                "vo2max": vo2max if vo2max is not None else base.get("vo2max"),
                "lthr": lthr if lthr is not None else base.get("lthr"),
                "ltsp": ltsp if ltsp is not None else base.get("ltsp"),
                "stamina_level": stamina if stamina is not None else base.get("stamina_level"),
                "stamina_level_7d": stamina_7d if stamina_7d is not None else base.get("stamina_level_7d"),
            }
        )
        rows.append(base)
    return carry_forward_daily(sorted(rows, key=lambda r: r.get("date", "")))


def carry_forward_daily(rows):
    carry_fields = [
        "hrv",
        "hrv_baseline",
        "rhr",
        "short_load",
        "long_load",
        "load_ratio",
        "load_status",
        "tired_rate",
        "vo2max",
        "lthr",
        "ltsp",
        "stamina_level",
        "stamina_level_7d",
    ]
    last_values = {}
    for row in rows:
        for field in carry_fields:
            value = row.get(field)
            if value is None or value == "":
                if field in last_values:
                    row[field] = last_values[field]
            else:
                last_values[field] = value
    for field in carry_fields:
        first_value = next((row.get(field) for row in rows if row.get(field) is not None and row.get(field) != ""), None)
        if first_value is None:
            continue
        for row in rows:
            if row.get(field) is None or row.get(field) == "":
                row[field] = first_value
            else:
                break
    for row in rows:
        row["hrv_status"] = hrv_status(row.get("hrv"), row.get("hrv_baseline")) or row.get("hrv_status", "")
        row["load_status"] = load_status(row.get("load_ratio")) or row.get("load_status", "")
    return rows


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
