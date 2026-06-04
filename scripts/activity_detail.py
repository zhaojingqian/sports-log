#!/usr/bin/env python3
"""Fetch and normalize one COROS activity detail record."""

import asyncio
import json
import os
import sys

COROS_MCP_DIR = os.environ.get("COROS_MCP_DIR", "/root/workspace/coros-mcp")
sys.path.insert(0, COROS_MCP_DIR)


def safe_float(value, digits=2):
    if value is None:
        return None
    try:
        return round(float(value), digits)
    except (TypeError, ValueError):
        return None


def safe_int(value):
    if value is None:
        return None
    try:
        return int(round(float(value)))
    except (TypeError, ValueError):
        return None


def distance_km(value):
    value = safe_float(value, 3)
    return round(value / 100000, 3) if value is not None else None


def seconds_from_centiseconds(value):
    value = safe_float(value, 2)
    return round(value / 100, 2) if value is not None else None


def duration_label(seconds):
    if seconds is None:
        return ""
    seconds = int(round(seconds))
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return "%d:%02d:%02d" % (hours, minutes, secs)
    return "%d:%02d" % (minutes, secs)


def pace_label(seconds):
    seconds = safe_float(seconds, 2)
    if seconds is None or seconds <= 0:
        return ""
    seconds = int(round(seconds))
    minutes, secs = divmod(seconds, 60)
    return "%d:%02d /km" % (minutes, secs)


def normalize_lap(item):
    seconds = seconds_from_centiseconds(item.get("time"))
    pace = safe_float(item.get("avgPace"), 2) or safe_float(item.get("adjustedPace"), 2)
    return {
        "index": safe_int(item.get("lapIndex") or item.get("rowIndex")),
        "distance_km": distance_km(item.get("distance")),
        "duration": duration_label(seconds),
        "duration_seconds": seconds,
        "pace": pace_label(pace),
        "pace_seconds": pace,
        "avg_hr": safe_int(item.get("avgHr")),
        "max_hr": safe_int(item.get("maxHr")),
        "avg_cadence": safe_int(item.get("avgCadence")),
        "avg_power": safe_int(item.get("avgPower")),
        "elev_gain": safe_float(item.get("elevGain"), 1),
        "descent": safe_float(item.get("totalDescent"), 1),
        "stride_length_cm": safe_int(item.get("avgStrideLength")),
        "ground_time_ms": safe_int(item.get("groundTime")),
        "stride_height_mm": safe_int(item.get("strideHeight")),
        "stride_ratio": safe_int(item.get("strideRatio")),
    }


def normalize_hr_zones(zone_list):
    for zone in zone_list or []:
        if zone.get("zoneType") != 2:
            continue
        items = []
        for item in zone.get("zoneItemList") or []:
            seconds = safe_int(item.get("second")) or 0
            items.append(
                {
                    "index": safe_int(item.get("zoneIndex")),
                    "range": "%s-%s" % (item.get("leftScope"), item.get("rightScope")),
                    "seconds": seconds,
                    "duration": duration_label(seconds),
                    "percent": safe_int(item.get("percent")) or 0,
                }
            )
        return items
    return []


def normalize_weather(weather):
    if not isinstance(weather, dict):
        return {}
    return {
        "temperature_c": safe_float((weather.get("temperature") or 0) / 10, 1),
        "feels_like_c": safe_float((weather.get("bodyFeelTemp") or 0) / 10, 1),
        "humidity": safe_int((weather.get("humidity") or 0) / 10),
        "wind_speed": safe_float((weather.get("windSpeed") or 0) / 10, 1),
    }


def normalize_detail(detail):
    summary = detail.get("summary") or {}
    lap_groups = [
        lap for lap in detail.get("lapList") or [] if lap.get("type") != -1 and lap.get("lapItemList")
    ]
    lap_group = None
    if lap_groups:
        lap_group = sorted(
            lap_groups,
            key=lambda lap: (-len(lap.get("lapItemList") or []), safe_float(lap.get("lapDistance")) or 0),
        )[0]
    laps = [normalize_lap(item) for item in (lap_group or {}).get("lapItemList") or []]
    total_seconds = seconds_from_centiseconds(summary.get("totalTime") or summary.get("workoutTime"))
    note = (detail.get("sportFeelInfo") or {}).get("sportNote") or ""
    return {
        "summary": {
            "name": summary.get("name") or "",
            "distance_km": distance_km(summary.get("distance")),
            "duration": duration_label(total_seconds),
            "duration_seconds": total_seconds,
            "avg_pace": pace_label(summary.get("adjustedPace") or summary.get("avgPace")),
            "best_km": pace_label(summary.get("bestKm")),
            "avg_hr": safe_int(summary.get("avgHr")),
            "max_hr": safe_int(summary.get("maxHr")),
            "avg_cadence": safe_int(summary.get("avgCadence")),
            "max_cadence": safe_int(summary.get("maxCadence")),
            "avg_power": safe_int(summary.get("avgPower")),
            "max_power": safe_int(summary.get("maxPower")),
            "elev_gain": safe_float(summary.get("elevGain"), 1),
            "descent": safe_float(summary.get("totalDescent"), 1),
            "training_load": safe_int(summary.get("trainingLoad")),
            "aerobic_effect": safe_float(summary.get("aerobicEffect"), 1),
            "anaerobic_effect": safe_float(summary.get("anaerobicEffect"), 1),
            "vo2max": safe_int(summary.get("currentVo2Max")),
            "calories": safe_int((summary.get("calories") or 0) / 1000),
            "avg_stride_length_cm": safe_int(summary.get("avgStepLen")),
            "ground_time_ms": safe_int(summary.get("avgGroundTime")),
        },
        "laps": laps,
        "hr_zones": normalize_hr_zones(detail.get("zoneList")),
        "weather": normalize_weather(detail.get("weather")),
        "note": note,
    }


async def main():
    if len(sys.argv) < 2:
        raise SystemExit("usage: activity_detail.py ACTIVITY_ID [SPORT_TYPE]")
    activity_id = sys.argv[1]
    sport_type = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2] else 0
    import server

    detail = await server.get_activity_detail(activity_id=activity_id, sport_type=sport_type)
    if isinstance(detail, dict) and detail.get("error"):
        print(json.dumps({"ok": False, "error": detail["error"]}, ensure_ascii=False))
        return 1
    print(json.dumps({"ok": True, "detail": normalize_detail(detail)}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
