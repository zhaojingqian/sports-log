# sports-log

COROS sports and recovery dashboard for `https://zzzgry.top/sport/`.

## What it shows

- Profile, devices, recovery, running fitness, race predictions
- Daily health: steps, calories, exercise minutes, stress, sleep
- HRV, resting heart rate, average heart rate
- Training load: short-term load, long-term load, load ratio, COROS status
- Sport records with distance, pace, HR, calories, location and activity ids
- Training calendar entries
- Weekly and monthly summaries

## Run locally

```bash
BASE_PATH=/sport PORT=18081 /root/.pyenv/versions/3.10.13/bin/python3 web_server.py
```

Then open `http://127.0.0.1:18081/sport/`.

## Refresh data

```bash
/root/.pyenv/versions/3.10.13/bin/python3 scripts/refresh_data.py
```

The refresh script first calls the local `cygnusb/coros-mcp` checkout, then recomputes weekly/monthly summaries from `data/dashboard.json`.

Current server integration:

- coros-mcp checkout: `/root/workspace/coros-mcp`
- coros-mcp Python: `/root/workspace/coros-mcp/.venv/bin/python`
- refresh entrypoint: `/root/workspace/sports-log/scripts/refresh_data.py`
- cron: daily at 23:00

Manual auth check:

```bash
/root/workspace/coros-mcp/.venv/bin/coros-mcp auth-status
```

Manual auth:

```bash
cd /root/workspace/coros-mcp
/root/workspace/coros-mcp/.venv/bin/coros-mcp auth
```

Note: `coros-mcp auth` stores a 24-hour web token. For fully unattended daily refresh after token expiry, put `COROS_EMAIL`, `COROS_PASSWORD`, and `COROS_REGION` in `/root/workspace/coros-mcp/.env` with restrictive permissions, or re-authenticate manually before the token expires.

References used for summary rules:

- COROS Fitness Metrics: https://support.coros.com/hc/en-us/articles/360061452651-COROS-Fitness-Metrics-Explained
- ACSM Physical Activity Guidelines: https://acsm.org/education-resources/trending-topics-resources/physical-activity-guidelines/
- TrainingPeaks Form / TSB: https://help.trainingpeaks.com/hc/en-us/articles/204071764-Form-TSB
- coros-mcp: https://github.com/cygnusb/coros-mcp
