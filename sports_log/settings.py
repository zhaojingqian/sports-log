"""Runtime paths shared by the web app and background jobs."""

import os
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent
WORKSPACE_ROOT = Path(os.environ.get("WORKSPACE_ROOT", PROJECT_ROOT.parents[1])).resolve()

DATA_FILE = PROJECT_ROOT / "data" / "dashboard.json"
COROS_MCP_ROOT = Path(
    os.environ.get("COROS_MCP_ROOT", WORKSPACE_ROOT / "integrations" / "coros-mcp")
).resolve()
COROS_PYTHON = Path(
    os.environ.get("COROS_PYTHON", COROS_MCP_ROOT / ".venv" / "bin" / "python")
)
COROS_ENV_FILE = Path(
    os.environ.get("COROS_ENV_FILE", WORKSPACE_ROOT / ".env.d" / "coros.env")
).resolve()


def validate_coros_runtime():
    """Return a clear configuration error instead of failing deep in a job."""
    if not COROS_MCP_ROOT.is_dir():
        return "COROS MCP checkout not found: %s" % COROS_MCP_ROOT
    if not COROS_PYTHON.is_file():
        return "COROS Python runtime not found: %s" % COROS_PYTHON
    return ""
