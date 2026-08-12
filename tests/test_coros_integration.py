import os
import sys
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from sports_log.integrations.coros import CorosGateway
from sports_log.settings import COROS_MCP_ROOT, COROS_PYTHON, WORKSPACE_ROOT


class FakeCorosServer:
    def __init__(self):
        self.activity_pages = []

    async def check_coros_auth(self):
        return {"authenticated": True, "mobile_authenticated": False}

    async def get_daily_metrics(self, weeks):
        return {"records": [{"date": "20260811"}], "weeks": weeks}

    async def list_activities(self, start_day, end_day, page, size):
        self.activity_pages.append(page)
        return {
            "activities": [{"activity_id": "a1"}],
            "total_count": 1,
        }

    async def list_planned_activities(self, start_day, end_day):
        return {"schedule": {"entities": []}}

    async def list_workout_templates(self):
        return {"workouts": [{"id": "w1"}]}

    async def get_cache_status(self):
        return {"daily_records": {"count": 1}}

    async def get_activity_detail(self, activity_id, sport_type):
        return {"summary": {"labelId": activity_id, "sportType": sport_type}}


class SettingsTests(unittest.TestCase):
    def test_workspace_layout_defaults_are_centralized(self):
        self.assertEqual(WORKSPACE_ROOT, PROJECT_ROOT.parents[1])
        self.assertEqual(COROS_MCP_ROOT, WORKSPACE_ROOT / "integrations" / "coros-mcp")
        self.assertEqual(COROS_PYTHON, COROS_MCP_ROOT / ".venv" / "bin" / "python")


class CorosGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_snapshot_uses_packaged_upstream_contract(self):
        old_fetch_sleep = os.environ.get("SPORTS_LOG_FETCH_SLEEP")
        os.environ["SPORTS_LOG_FETCH_SLEEP"] = "0"
        try:
            server = FakeCorosServer()
            snapshot = await CorosGateway(server).fetch_snapshot(weeks=8)
        finally:
            if old_fetch_sleep is None:
                os.environ.pop("SPORTS_LOG_FETCH_SLEEP", None)
            else:
                os.environ["SPORTS_LOG_FETCH_SLEEP"] = old_fetch_sleep

        self.assertEqual(snapshot["daily"]["weeks"], 8)
        self.assertEqual(snapshot["activities"]["total_count"], 1)
        self.assertEqual(snapshot["workouts"]["workouts"][0]["id"], "w1")
        self.assertEqual(server.activity_pages, [1])

    async def test_activity_detail_is_wrapped(self):
        detail = await CorosGateway(FakeCorosServer()).fetch_activity_detail("a1", 100)
        self.assertEqual(detail["summary"]["labelId"], "a1")

    async def test_upstream_errors_become_gateway_errors(self):
        server = FakeCorosServer()

        async def failed_detail(activity_id, sport_type):
            return {"error": "expired"}

        server.get_activity_detail = failed_detail
        with self.assertRaisesRegex(RuntimeError, "activity detail: expired"):
            await CorosGateway(server).fetch_activity_detail("a1", 100)


if __name__ == "__main__":
    unittest.main()
