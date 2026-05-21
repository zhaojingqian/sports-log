#!/usr/bin/env python3
"""Refresh and summarize sports-log data.

The project keeps COROS data in data/dashboard.json so the web layer stays
simple and robust. This script recomputes weekly/monthly summaries and is the
single place to plug in unattended coros-mcp fetching once the server is
authenticated.
"""

import argparse
import json
import os
import subprocess
from collections import Counter, defaultdict
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FILE = os.path.join(BASE_DIR, "data", "dashboard.json")
COROS_PYTHON = os.environ.get("COROS_PYTHON", "/root/workspace/coros-mcp/.venv/bin/python")
COROS_FETCHER = os.path.join(BASE_DIR, "scripts", "fetch_coros_data.py")


def load_data():
    with open(DATA_FILE, encoding="utf-8") as f:
        return json.load(f)


def save_data(data):
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, DATA_FILE)


def mean(values, digits=1):
    values = [v for v in values if isinstance(v, (int, float))]
    if not values:
        return None
    return round(sum(values) / len(values), digits)


def iso_week(date_text):
    d = datetime.strptime(date_text, "%Y-%m-%d").date()
    y, w, _ = d.isocalendar()
    return "%04d-W%02d" % (y, w)


def month_key(date_text):
    return date_text[:7]


def bucket_records(data, key_func):
    buckets = defaultdict(lambda: {"daily": [], "activities": []})
    for row in data.get("daily", []):
        buckets[key_func(row["date"])]["daily"].append(row)
    for act in data.get("activities", []):
        buckets[key_func(act["date"])]["activities"].append(act)
    return buckets


def classify_load(avg_ratio):
    if avg_ratio is None:
        return "No load data"
    if avg_ratio >= 1.3:
        return "High strain"
    if avg_ratio >= 1.0:
        return "Productive"
    if avg_ratio >= 0.85:
        return "Maintaining"
    return "Recovery / low load"


def build_note(kind, summary):
    active_min = summary["exercise_min"]
    load_state = summary["load_state"]
    sleep = summary["avg_sleep_score"]
    hrv = summary["avg_hrv"]
    parts = []
    if kind == "weekly":
        if active_min >= 150:
            parts.append("达到 ACSM/CDC 每周有氧活动基线。")
        else:
            parts.append("本周活动分钟偏少，可补轻松跑或低强度有氧。")
    if load_state == "High strain":
        parts.append("负荷比偏高，下一周期留意恢复。")
    elif load_state == "Productive":
        parts.append("负荷处在较有建设性的区间。")
    elif load_state.startswith("Recovery"):
        parts.append("训练刺激偏低，适合恢复或重新起量。")
    if sleep is not None and sleep < 70:
        parts.append("睡眠分偏低，强度课前先观察主观状态。")
    if hrv is not None and hrv < 80:
        parts.append("HRV 均值偏低，注意压力和恢复。")
    return "".join(parts)


def summarize_bucket(key, bucket, kind):
    daily = bucket["daily"]
    acts = bucket["activities"]
    sport_counts = Counter(a.get("sport", "Unknown") for a in acts)
    distance = round(sum(a.get("distance_km") or 0 for a in acts), 2)
    duration = round(sum(a.get("duration_min") or 0 for a in acts), 1)
    daily_exercise = int(sum(d.get("exercise_min") or 0 for d in daily))
    exercise_min = daily_exercise if daily_exercise else int(duration)
    summary = {
        "key": key,
        "days": len(daily),
        "activities": len(acts),
        "distance_km": distance,
        "activity_min": duration,
        "exercise_min": exercise_min,
        "steps": int(sum(d.get("steps") or 0 for d in daily)),
        "calories": int(sum(d.get("calories") or 0 for d in daily)),
        "avg_sleep_score": mean([d.get("sleep_score") for d in daily]),
        "avg_sleep_hours": mean([(d.get("sleep_min") or 0) / 60 for d in daily]),
        "avg_stress": mean([d.get("stress") for d in daily]),
        "avg_hrv": mean([d.get("hrv") for d in daily]),
        "avg_rhr": mean([d.get("rhr") for d in daily]),
        "avg_load_ratio": mean([d.get("load_ratio") for d in daily], 2),
        "load_status_mix": dict(sport_counts),
        "top_sport": sport_counts.most_common(1)[0][0] if sport_counts else "",
    }
    summary["load_state"] = classify_load(summary["avg_load_ratio"])
    summary["note"] = build_note(kind, summary)
    return summary


def recompute(data):
    weekly = [
        summarize_bucket(key, bucket, "weekly")
        for key, bucket in sorted(bucket_records(data, iso_week).items(), reverse=True)
    ]
    monthly = [
        summarize_bucket(key, bucket, "monthly")
        for key, bucket in sorted(bucket_records(data, month_key).items(), reverse=True)
    ]
    data.setdefault("summaries", {})["weekly"] = weekly
    data.setdefault("summaries", {})["monthly"] = monthly
    data.setdefault("meta", {})["generated_at"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    return data


def fetch_coros_if_available():
    if os.environ.get("SPORTS_LOG_SKIP_COROS") == "1":
        return
    if not (os.path.exists(COROS_PYTHON) and os.path.exists(COROS_FETCHER)):
        print("coros fetch skipped: coros-mcp environment not found")
        return
    proc = subprocess.run(
        [COROS_PYTHON, COROS_FETCHER],
        cwd=BASE_DIR,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    print(proc.stdout.strip())
    if proc.returncode != 0:
        raise SystemExit(proc.returncode)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-coros", action="store_true", help="Only report whether coros-mcp is installed.")
    args = parser.parse_args()
    if args.check_coros:
        rc = os.system("command -v coros-mcp >/dev/null 2>&1")
        print("coros-mcp installed" if rc == 0 else "coros-mcp not installed")
        return 0 if rc == 0 else 1
    fetch_coros_if_available()
    data = recompute(load_data())
    save_data(data)
    print("refreshed %s" % DATA_FILE)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
