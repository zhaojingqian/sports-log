#!/usr/bin/env python3
"""Parse FIT running files into chart-ready Sports Log data."""

from __future__ import annotations

import json
import math
import os
import sys
import warnings
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

try:
    import fitdecode
except ImportError as exc:  # pragma: no cover - exercised on servers without deps.
    fitdecode = None
    FITDECODE_IMPORT_ERROR = exc
else:
    FITDECODE_IMPORT_ERROR = None


class FitParseError(RuntimeError):
    """Raised when a FIT file cannot be parsed into activity data."""


METRICS = [
    {
        "key": "heart_rate",
        "label": "心率",
        "unit": "bpm",
        "precision": 0,
        "color": "#ef4f5f",
        "group": "心肺",
    },
    {
        "key": "pace_sec_per_km",
        "label": "配速",
        "unit": "/km",
        "precision": 0,
        "color": "#2f6fed",
        "group": "速度",
        "format": "pace",
        "invert": True,
    },
    {
        "key": "speed_mps",
        "label": "速度",
        "unit": "m/s",
        "precision": 2,
        "color": "#0ea5b7",
        "group": "速度",
    },
    {
        "key": "power_w",
        "label": "功率",
        "unit": "W",
        "precision": 0,
        "color": "#f59e0b",
        "group": "强度",
    },
    {
        "key": "cadence_spm",
        "label": "步频",
        "unit": "spm",
        "precision": 0,
        "color": "#18a66a",
        "group": "跑姿",
    },
    {
        "key": "step_length_cm",
        "label": "步幅",
        "unit": "cm",
        "precision": 0,
        "color": "#6d5dfc",
        "group": "跑姿",
    },
    {
        "key": "stance_time_ms",
        "label": "踏地",
        "unit": "ms",
        "precision": 0,
        "color": "#9b6b43",
        "group": "跑姿",
        "invert": True,
    },
    {
        "key": "vertical_oscillation_mm",
        "label": "垂直振幅",
        "unit": "mm",
        "precision": 0,
        "color": "#7c3aed",
        "group": "跑姿",
        "invert": True,
    },
    {
        "key": "vertical_ratio_pct",
        "label": "垂直比",
        "unit": "%",
        "precision": 1,
        "color": "#0f766e",
        "group": "跑姿",
        "invert": True,
    },
    {
        "key": "altitude_m",
        "label": "海拔",
        "unit": "m",
        "precision": 1,
        "color": "#64748b",
        "group": "环境",
    },
]

METRIC_BY_KEY = {metric["key"]: metric for metric in METRICS}


def clean_number(value: Any) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        if math.isfinite(float(value)):
            return float(value)
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def rounded(value: Any, digits: int = 2) -> Optional[float]:
    number = clean_number(value)
    if number is None:
        return None
    if digits <= 0:
        return int(round(number))
    return round(number, digits)


def local_iso(value: Any) -> Optional[str]:
    if not isinstance(value, datetime):
        return None
    return value.astimezone().replace(microsecond=0).isoformat()


def seconds_between(value: Any, start: Any) -> Optional[float]:
    if not isinstance(value, datetime) or not isinstance(start, datetime):
        return None
    return round((value - start).total_seconds(), 3)


def speed_to_pace(speed_mps: Any) -> Optional[float]:
    speed = clean_number(speed_mps)
    if not speed or speed <= 0:
        return None
    return round(1000.0 / speed, 2)


def meters_to_km(value: Any) -> Optional[float]:
    meters = clean_number(value)
    if meters is None:
        return None
    return round(meters / 1000.0, 4)


def cadence_to_spm(value: Any) -> Optional[float]:
    cadence = clean_number(value)
    if cadence is None:
        return None
    # COROS FIT running cadence is commonly strides/min. UI readers expect steps/min.
    if 0 < cadence < 130:
        cadence *= 2
    return round(cadence, 1)


def step_length_to_cm(value: Any) -> Optional[float]:
    length = clean_number(value)
    if length is None:
        return None
    if length > 300:
        length /= 10.0
    elif length < 5:
        length *= 100.0
    return round(length, 1)


def fields_to_dict(frame: Any) -> Dict[str, Any]:
    return {field.name: field.value for field in frame.fields}


def average(values: Iterable[Any]) -> Optional[float]:
    nums = [clean_number(value) for value in values]
    nums = [value for value in nums if value is not None]
    if not nums:
        return None
    return sum(nums) / len(nums)


def metric_stats(rows: List[Dict[str, Any]], key: str) -> Optional[Dict[str, Any]]:
    vals = [clean_number(row.get(key)) for row in rows]
    vals = [value for value in vals if value is not None]
    if key != "altitude_m":
        vals = [value for value in vals if value > 0]
    if not vals:
        return None
    spec = METRIC_BY_KEY[key]
    precision = int(spec.get("precision", 1))
    return {
        "avg": rounded(sum(vals) / len(vals), precision),
        "min": rounded(min(vals), precision),
        "max": rounded(max(vals), precision),
        "count": len(vals),
    }


def pace_label(seconds: Any) -> Optional[str]:
    sec = clean_number(seconds)
    if sec is None or sec <= 0:
        return None
    total = int(round(sec))
    return "%d:%02d /km" % (total // 60, total % 60)


def duration_label(seconds: Any) -> Optional[str]:
    sec = clean_number(seconds)
    if sec is None:
        return None
    total = int(round(sec))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return "%d:%02d:%02d" % (h, m, s) if h else "%d:%02d" % (m, s)


def normalize_record(raw: Dict[str, Any], start_time: Any, carry: Dict[str, Any]) -> Dict[str, Any]:
    timestamp = raw.get("timestamp")
    distance_km = meters_to_km(raw.get("distance"))
    if distance_km is None:
        distance_km = carry.get("distance_km")
    else:
        carry["distance_km"] = distance_km
    speed = clean_number(raw.get("enhanced_speed", raw.get("speed")))
    altitude = clean_number(raw.get("enhanced_altitude", raw.get("altitude")))
    return {
        "timestamp": local_iso(timestamp),
        "sec": seconds_between(timestamp, start_time),
        "distance_km": distance_km,
        "heart_rate": rounded(raw.get("heart_rate"), 0),
        "pace_sec_per_km": speed_to_pace(speed),
        "speed_mps": rounded(speed, 3),
        "power_w": rounded(raw.get("power"), 0),
        "cadence_spm": cadence_to_spm(raw.get("cadence")),
        "step_length_cm": step_length_to_cm(raw.get("step_length")),
        "stance_time_ms": rounded(raw.get("stance_time"), 0),
        "vertical_oscillation_mm": rounded(raw.get("vertical_oscillation"), 1),
        "vertical_ratio_pct": rounded(raw.get("vertical_ratio"), 1),
        "altitude_m": rounded(altitude, 1),
    }


def normalize_lap(raw: Dict[str, Any], index: int) -> Dict[str, Any]:
    duration = clean_number(raw.get("total_timer_time") or raw.get("total_elapsed_time"))
    distance_km = meters_to_km(raw.get("total_distance"))
    speed = clean_number(raw.get("enhanced_avg_speed", raw.get("avg_speed")))
    pace_sec = speed_to_pace(speed)
    if not pace_sec and distance_km and duration:
        pace_sec = round(duration / distance_km, 2)
    return {
        "index": index,
        "message_index": raw.get("message_index"),
        "start_time": local_iso(raw.get("start_time")),
        "end_time": local_iso(raw.get("timestamp")),
        "duration_sec": rounded(duration, 2),
        "duration": duration_label(duration),
        "distance_km": rounded(distance_km, 3),
        "pace_sec_per_km": pace_sec,
        "pace": pace_label(pace_sec),
        "avg_hr": rounded(raw.get("avg_heart_rate"), 0),
        "min_hr": rounded(raw.get("min_heart_rate"), 0),
        "max_hr": rounded(raw.get("max_heart_rate"), 0),
        "avg_power_w": rounded(raw.get("avg_power"), 0),
        "avg_cadence_spm": cadence_to_spm(raw.get("avg_running_cadence")),
        "max_cadence_spm": cadence_to_spm(raw.get("max_running_cadence")),
        "avg_step_length_cm": step_length_to_cm(raw.get("avg_step_length")),
        "avg_stance_time_ms": rounded(raw.get("avg_stance_time"), 0),
        "avg_vertical_oscillation_mm": rounded(raw.get("avg_vertical_oscillation"), 1),
        "avg_vertical_ratio_pct": rounded(raw.get("avg_vertical_ratio"), 1),
        "calories": rounded(raw.get("total_calories"), 0),
        "ascent_m": rounded(raw.get("total_ascent"), 0),
        "descent_m": rounded(raw.get("total_descent"), 0),
        "_start_dt": raw.get("start_time"),
        "_end_dt": raw.get("timestamp"),
    }


def normalize_session(raw: Dict[str, Any], filename: str, records_count: int, laps_count: int) -> Dict[str, Any]:
    duration = clean_number(raw.get("total_timer_time") or raw.get("total_elapsed_time"))
    distance_km = meters_to_km(raw.get("total_distance"))
    speed = clean_number(raw.get("enhanced_avg_speed", raw.get("avg_speed")))
    pace_sec = speed_to_pace(speed)
    if not pace_sec and distance_km and duration:
        pace_sec = round(duration / distance_km, 2)
    return {
        "file_name": filename,
        "sport": raw.get("sport") or "running",
        "sub_sport": raw.get("sub_sport"),
        "started_at": local_iso(raw.get("start_time")),
        "ended_at": local_iso(raw.get("timestamp")),
        "duration_sec": rounded(duration, 2),
        "duration": duration_label(duration),
        "distance_km": rounded(distance_km, 3),
        "pace_sec_per_km": pace_sec,
        "pace": pace_label(pace_sec),
        "calories": rounded(raw.get("total_calories"), 0),
        "ascent_m": rounded(raw.get("total_ascent"), 0),
        "descent_m": rounded(raw.get("total_descent"), 0),
        "avg_hr": rounded(raw.get("avg_heart_rate"), 0),
        "min_hr": rounded(raw.get("min_heart_rate"), 0),
        "max_hr": rounded(raw.get("max_heart_rate"), 0),
        "avg_power_w": rounded(raw.get("avg_power"), 0),
        "avg_cadence_spm": cadence_to_spm(raw.get("avg_running_cadence")),
        "max_cadence_spm": cadence_to_spm(raw.get("max_running_cadence")),
        "avg_step_length_cm": step_length_to_cm(raw.get("avg_step_length")),
        "avg_stance_time_ms": rounded(raw.get("avg_stance_time"), 0),
        "avg_vertical_oscillation_mm": rounded(raw.get("avg_vertical_oscillation"), 1),
        "avg_vertical_ratio_pct": rounded(raw.get("avg_vertical_ratio"), 1),
        "records_count": records_count,
        "laps_count": laps_count,
    }


def attach_lap_record_stats(laps: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> None:
    metric_keys = [metric["key"] for metric in METRICS]
    record_pairs = []
    for record in records:
        ts = record.get("_dt")
        if isinstance(ts, datetime):
            record_pairs.append((ts, record))
    for lap in laps:
        start, end = lap.pop("_start_dt", None), lap.pop("_end_dt", None)
        if not isinstance(start, datetime) or not isinstance(end, datetime):
            lap_rows = []
        else:
            lap_rows = [row for ts, row in record_pairs if start <= ts <= end]
        lap["record_count"] = len(lap_rows)
        lap["stats"] = {
            key: stats for key in metric_keys if (stats := metric_stats(lap_rows, key))
        }


def metric_catalog(records: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    catalog = []
    for metric in METRICS:
        stats = metric_stats(records, metric["key"])
        if not stats:
            continue
        item = dict(metric)
        item.update(stats)
        catalog.append(item)
    return catalog


def session_insights(activity: Dict[str, Any], laps: List[Dict[str, Any]], records: List[Dict[str, Any]]) -> Dict[str, Any]:
    valid_laps = [lap for lap in laps if clean_number(lap.get("pace_sec_per_km"))]
    fastest = min(valid_laps, key=lambda lap: lap["pace_sec_per_km"], default=None)
    power_laps = [lap for lap in laps if clean_number(lap.get("avg_power_w"))]
    strongest = max(power_laps, key=lambda lap: lap["avg_power_w"], default=None)
    half = max(1, len(records) // 2)
    first, second = records[:half], records[half:]
    first_hr, second_hr = average(row.get("heart_rate") for row in first), average(row.get("heart_rate") for row in second)
    first_pace = average(row.get("pace_sec_per_km") for row in first)
    second_pace = average(row.get("pace_sec_per_km") for row in second)
    vr_stats = metric_stats(records, "vertical_ratio_pct") or {}
    stance_stats = metric_stats(records, "stance_time_ms") or {}
    return {
        "fastest_lap": {
            "index": fastest.get("index"),
            "pace": fastest.get("pace"),
            "distance_km": fastest.get("distance_km"),
        } if fastest else None,
        "strongest_lap": {
            "index": strongest.get("index"),
            "power_w": strongest.get("avg_power_w"),
            "pace": strongest.get("pace"),
        } if strongest else None,
        "heart_rate_drift_bpm": rounded((second_hr - first_hr), 1) if first_hr is not None and second_hr is not None else None,
        "pace_drift_sec_per_km": rounded((second_pace - first_pace), 1) if first_pace is not None and second_pace is not None else None,
        "vertical_ratio_avg": vr_stats.get("avg"),
        "vertical_ratio_range": [vr_stats.get("min"), vr_stats.get("max")] if vr_stats else None,
        "stance_time_avg_ms": stance_stats.get("avg"),
        "summary": "%s · %s · %s" % (
            activity.get("distance_km") and ("%s km" % activity["distance_km"]) or "--",
            activity.get("duration") or "--",
            activity.get("pace") or "--",
        ),
    }


def sample_records(records: List[Dict[str, Any]], limit: int = 6000) -> List[Dict[str, Any]]:
    if len(records) <= limit:
        return records
    step = max(1, math.ceil(len(records) / limit))
    sampled = records[::step]
    if sampled[-1] is not records[-1]:
        sampled.append(records[-1])
    return sampled


def parse_fit_file(path: str, filename: Optional[str] = None) -> Dict[str, Any]:
    if fitdecode is None:
        raise FitParseError("缺少 fitdecode 依赖，请先安装 requirements.txt 中的依赖。") from FITDECODE_IMPORT_ERROR
    if not os.path.exists(path):
        raise FitParseError("FIT 文件不存在。")
    filename = filename or os.path.basename(path)
    raw_session: Dict[str, Any] = {}
    raw_records: List[Dict[str, Any]] = []
    raw_laps: List[Dict[str, Any]] = []
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            with fitdecode.FitReader(path) as fit:
                for frame in fit:
                    if not isinstance(frame, fitdecode.FitDataMessage):
                        continue
                    fields = fields_to_dict(frame)
                    if frame.name == "session":
                        raw_session = fields
                    elif frame.name == "record":
                        raw_records.append(fields)
                    elif frame.name == "lap":
                        raw_laps.append(fields)
        except Exception as exc:
            raise FitParseError("FIT 文件解析失败：%s" % exc) from exc
    if not raw_records and not raw_session:
        raise FitParseError("FIT 文件里没有可用的跑步记录。")
    start_time = raw_session.get("start_time") or next((row.get("timestamp") for row in raw_records if row.get("timestamp")), None)
    if not start_time:
        raise FitParseError("FIT 文件缺少开始时间。")
    carry: Dict[str, Any] = {}
    records = []
    for raw in raw_records:
        row = normalize_record(raw, start_time, carry)
        row["_dt"] = raw.get("timestamp")
        if row.get("sec") is not None:
            records.append(row)
    records.sort(key=lambda row: row.get("sec") or 0)
    laps = [normalize_lap(raw, idx + 1) for idx, raw in enumerate(raw_laps)]
    attach_lap_record_stats(laps, records)
    activity = normalize_session(raw_session, filename, len(records), len(laps))
    metrics = metric_catalog(records)
    insights = session_insights(activity, laps, records)
    public_records = []
    for row in sample_records(records):
        item = dict(row)
        item.pop("_dt", None)
        public_records.append(item)
    return {
        "ok": True,
        "activity": activity,
        "metrics": metrics,
        "records": public_records,
        "laps": laps,
        "insights": insights,
        "meta": {
            "parser": "fitdecode",
            "record_count": len(records),
            "returned_record_count": len(public_records),
            "sampled": len(public_records) < len(records),
        },
    }


def main(argv: List[str]) -> int:
    if len(argv) < 2:
        print("usage: fit_activity.py <activity.fit>", file=sys.stderr)
        return 2
    try:
        payload = parse_fit_file(argv[1])
    except FitParseError as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
        return 1
    print(json.dumps(payload, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
