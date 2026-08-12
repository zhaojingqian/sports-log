"""Stable application boundary around the upstream coros-mcp package."""

import asyncio
import os
from datetime import datetime, timedelta

from dotenv import load_dotenv

from sports_log.settings import COROS_ENV_FILE


class CorosGateway:
    def __init__(self, server_module=None):
        load_dotenv(str(COROS_ENV_FILE))
        if server_module is None:
            from coros_mcp import server as server_module

        self.server = server_module

    async def ensure_authenticated(self):
        auth = await self.server.check_coros_auth()
        if auth.get("authenticated"):
            return auth

        email = os.environ.get("COROS_EMAIL")
        password = os.environ.get("COROS_PASSWORD")
        region = os.environ.get("COROS_REGION", "eu")
        if not (email and password):
            raise RuntimeError(auth.get("message") or auth.get("error") or "coros-mcp is not authenticated")

        login = await self.server.authenticate_coros(email=email, password=password, region=region)
        if not login.get("authenticated"):
            raise RuntimeError(login.get("error") or "coros-mcp auto-auth failed")
        auth = await self.server.check_coros_auth()
        if not auth.get("authenticated"):
            raise RuntimeError(auth.get("message") or auth.get("error") or "coros-mcp is not authenticated")
        return auth

    async def fetch_activity_pages(self, start_day, end_day):
        page_size = self._bounded_int("SPORTS_LOG_ACTIVITY_PAGE_SIZE", 100, 1, 100)
        max_pages = self._bounded_int("SPORTS_LOG_ACTIVITY_MAX_PAGES", 50, 1, None)
        page = 1
        total = None
        activities = []
        while page <= max_pages:
            payload = await self.server.list_activities(
                start_day=start_day,
                end_day=end_day,
                page=page,
                size=page_size,
            )
            self._raise_payload_error("activities", payload)
            items = payload.get("activities", [])
            activities.extend(items)
            total = payload.get("total_count", total)
            if not items or len(items) < page_size:
                break
            if total is not None and len(activities) >= int(total):
                break
            page += 1
        return {
            "activities": activities,
            "total_count": total if total is not None else len(activities),
            "page": page,
            "page_size": page_size,
            "truncated": bool(total is not None and len(activities) < int(total)),
        }

    async def fetch_snapshot(self, weeks):
        auth = await self.ensure_authenticated()
        today = datetime.now().date()
        start_day = (today - timedelta(weeks=weeks)).strftime("%Y%m%d")
        end_day = today.strftime("%Y%m%d")
        schedule_end = (today + timedelta(days=14)).strftime("%Y%m%d")

        daily, activities, schedule, workouts = await asyncio.gather(
            self.server.get_daily_metrics(weeks=weeks),
            self.fetch_activity_pages(start_day, end_day),
            self.server.list_planned_activities(start_day=end_day, end_day=schedule_end),
            self.server.list_workout_templates(),
        )

        sleep = await self._fetch_sleep(auth, weeks)
        payloads = {
            "daily": daily,
            "sleep": sleep,
            "activities": activities,
            "schedule": schedule,
            "workouts": workouts,
        }
        for name, payload in payloads.items():
            self._raise_payload_error(name, payload)

        return {
            "auth": auth,
            **payloads,
            "cache": await self.server.get_cache_status(),
        }

    async def fetch_activity_detail(self, activity_id, sport_type):
        payload = await self.server.get_activity_detail(
            activity_id=activity_id,
            sport_type=sport_type,
        )
        self._raise_payload_error("activity detail", payload)
        return payload

    async def _fetch_sleep(self, auth, weeks):
        if os.environ.get("SPORTS_LOG_ALLOW_MOBILE_AUTH") == "1":
            email = os.environ.get("COROS_EMAIL")
            password = os.environ.get("COROS_PASSWORD")
            region = os.environ.get("COROS_REGION", "eu")
            if not (email and password):
                print("warning: mobile auth requested but COROS_EMAIL/COROS_PASSWORD are missing")
                return {"records": []}
            mobile = await self.server.authenticate_coros_mobile(
                email=email,
                password=password,
                region=region,
            )
            if not mobile.get("authenticated"):
                print("warning: coros mobile auth failed; sleep phases may be stale")
                return {"records": []}
            return await self.server.get_sleep_data(weeks=weeks)

        mobile_status = auth.get("mobile_token_status", "")
        can_fetch = auth.get("mobile_authenticated") or "refresh" in mobile_status
        if os.environ.get("SPORTS_LOG_FETCH_SLEEP", "1") != "1" or not can_fetch:
            print("sleep phase fetch skipped: no reusable mobile token")
            return {"records": []}
        payload = await self.server.get_sleep_data(weeks=weeks)
        if payload.get("error"):
            print("warning: sleep refresh skipped: %s" % payload.get("error"))
            return {"records": []}
        return payload

    @staticmethod
    def _bounded_int(name, default, minimum, maximum):
        try:
            value = int(os.environ.get(name, str(default)))
        except ValueError:
            value = default
        value = max(minimum, value)
        return min(value, maximum) if maximum is not None else value

    @staticmethod
    def _raise_payload_error(name, payload):
        if isinstance(payload, dict) and payload.get("error"):
            raise RuntimeError("%s: %s" % (name, payload["error"]))
