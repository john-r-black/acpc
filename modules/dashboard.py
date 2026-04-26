"""Read-only Flask status dashboard.

Runs as a separate long-lived process (systemd service). No authentication —
local network only. Polls PCO once per page load for the current lockout state;
everything else reads from SQLite.
"""

from datetime import datetime, timezone
from pathlib import Path

import yaml
from flask import Flask

from . import calendar_pco, database, doors_unifi

PROJECT_ROOT = Path(__file__).resolve().parent.parent

app = Flask(__name__)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


PAGE = """<!doctype html>
<html>
<head>
  <meta charset="utf-8">
  <meta http-equiv="refresh" content="30">
  <title>DPUMC Facility Status</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 20px; color: #222; }}
    h1 {{ margin-top: 0; }}
    h2 {{ border-bottom: 1px solid #ddd; padding-bottom: 4px; margin-top: 28px; }}
    table {{ border-collapse: collapse; width: 100%; margin: 8px 0; font-size: 14px; }}
    th, td {{ padding: 6px 10px; text-align: left; border-bottom: 1px solid #eee; }}
    th {{ background: #f5f5f5; font-weight: 600; }}
    .banner {{ padding: 10px 14px; border-radius: 6px; margin-bottom: 14px; font-weight: 600; }}
    .banner.ok {{ background: #e7f7ec; color: #175c2e; }}
    .banner.lock {{ background: #fdecea; color: #8a1c13; }}
    .shadow {{ background: #fff4cc; color: #7a5b00; padding: 6px 10px;
               border-radius: 4px; display: inline-block; margin-bottom: 12px; }}
    .fail {{ color: #b4281c; }}
    .ok {{ color: #1b7a36; }}
    .muted {{ color: #888; font-size: 12px; }}
  </style>
</head>
<body>
  <h1>DPUMC Facility Automation</h1>
  <p class="muted">Updated {updated} UTC · auto-refresh 30s</p>
  {shadow_banner}
  <div class="banner {banner_class}">{banner_text}</div>

  <h2>Active Events</h2>
  {events_table}

  <h2>Zones</h2>
  {zones_table}

  <h2>Weather</h2>
  {weather_block}

  <h2>Last Poll</h2>
  {poll_block}

  <h2>Recent Commands</h2>
  {commands_table}

  <h2>Recent Alerts</h2>
  {alerts_table}
</body>
</html>
"""


def _render_events(events, now) -> str:
    active = [e for e in events if e.is_active(now)]
    if not active:
        return '<p class="muted">No active events.</p>'
    rows = "".join(
        f"<tr><td>{e.name}</td><td>{e.start.isoformat(timespec='minutes')}</td>"
        f"<td>{e.end.isoformat(timespec='minutes')}</td>"
        f"<td>{', '.join(e.rooms) or '—'}</td></tr>"
        for e in active
    )
    return ("<table><tr><th>Name</th><th>Start</th><th>End</th>"
            f"<th>Rooms</th></tr>{rows}</table>")


def _render_zones(mapping) -> str:
    readings = {r["zone_id"]: r for r in database.latest_thermostat_readings()}
    rows = []
    for zone_id, spec in mapping.get("zones", {}).items():
        r = readings.get(zone_id, {})
        last_cmd = database.last_successful_command("hvac", zone_id) or {}
        cmd_params = last_cmd.get("parameters", {})
        rows.append(
            f"<tr><td>{zone_id}</td>"
            f"<td>{spec.get('tcc_name', '')}</td>"
            f"<td>{r.get('indoor_temp') or '—'}</td>"
            f"<td>{r.get('indoor_humidity') or '—'}</td>"
            f"<td>{last_cmd.get('action', '—')}</td>"
            f"<td>cool {cmd_params.get('cool_temp', '—')} / "
            f"heat {cmd_params.get('heat_temp', '—')}</td>"
            f"<td>{r.get('timestamp', '—')}</td></tr>"
        )
    body = "".join(rows)
    return (
        "<table><tr><th>Zone</th><th>TCC name</th><th>Indoor °F</th>"
        "<th>Humidity %</th><th>Last action</th><th>Setpoints</th>"
        f"<th>Last reading</th></tr>{body}</table>"
    )


def _render_weather() -> str:
    w = database.latest_weather()
    if not w:
        return '<p class="muted">No weather data yet.</p>'
    return (
        f"<p>{w.get('outdoor_temp')} °F · {w.get('outdoor_humidity')}% RH · "
        f"dewpoint {w.get('dewpoint')} · wind {w.get('wind_speed')} mph · "
        f"cloud {w.get('cloud_cover')}% · precip {w.get('precipitation')} in"
        f'<br><span class="muted">at {w.get("timestamp")}</span></p>'
    )


def _render_poll() -> str:
    p = database.latest_poll()
    if not p:
        return '<p class="muted">No poll recorded.</p>'
    errors = p.get("errors") or "—"
    return (
        f"<p>{p.get('timestamp')} · {p.get('duration_ms')} ms · "
        f"{p.get('events_count')} events · errors: {errors}</p>"
    )


def _render_commands(limit: int = 30) -> str:
    rows = database.recent_commands(limit=limit)
    if not rows:
        return '<p class="muted">No commands recorded.</p>'
    body = []
    for r in rows:
        cls = "ok" if r["success"] else "fail"
        shadow = "[SHADOW] " if r["shadow_mode"] else ""
        body.append(
            f"<tr><td>{r['timestamp']}</td>"
            f"<td>{shadow}{r['system']}</td>"
            f"<td>{r['target_id']}</td>"
            f"<td>{r['action']}</td>"
            f"<td>{r['parameters']}</td>"
            f"<td class='{cls}'>{'OK' if r['success'] else 'FAIL'}</td>"
            f"<td>{r['error_message'] or ''}</td></tr>"
        )
    return (
        "<table><tr><th>Time</th><th>System</th><th>Target</th><th>Action</th>"
        "<th>Params</th><th>Result</th><th>Error</th></tr>"
        + "".join(body) + "</table>"
    )


def _render_alerts(limit: int = 20) -> str:
    rows = database.recent_alerts(limit=limit)
    if not rows:
        return '<p class="muted">No alerts.</p>'
    body = []
    for r in rows:
        cls = "ok" if r["sent"] else "fail"
        body.append(
            f"<tr><td>{r['timestamp']}</td>"
            f"<td>{r['alert_type']}</td>"
            f"<td>{r['severity']}</td>"
            f"<td>{r['recipient']}</td>"
            f"<td class='{cls}'>{'sent' if r['sent'] else 'unsent'}</td>"
            f"<td><pre style='margin:0;white-space:pre-wrap'>"
            f"{r['message']}</pre></td></tr>"
        )
    return (
        "<table><tr><th>Time</th><th>Type</th><th>Severity</th>"
        "<th>Recipient</th><th>Status</th><th>Message</th></tr>"
        + "".join(body) + "</table>"
    )


@app.route("/")
def index() -> str:
    config = _load_yaml(PROJECT_ROOT / "config.yaml")
    secrets = _load_yaml(PROJECT_ROOT / "secrets.yaml")
    mapping = _load_yaml(PROJECT_ROOT / "mapping.yaml")
    now = datetime.now(timezone.utc)

    events = calendar_pco.fetch_events(config, secrets, now)
    plan = doors_unifi.compute_door_plan(events, now, config, mapping)

    if plan.mws_active and plan.crossover_active:
        banner_text = "MWS AND CROSSOVER ACTIVE — lockouts engaged"
        banner_class = "lock"
    elif plan.mws_active:
        banner_text = "MWS active — interior / MWS-area lockouts engaged"
        banner_class = "lock"
    elif plan.crossover_active:
        banner_text = "CrossOver active — FLC lockouts engaged"
        banner_class = "lock"
    else:
        banner_text = "Normal operation — no lockouts active"
        banner_class = "ok"

    shadow_banner = (
        '<div class="shadow">Shadow mode — HVAC commands are not being sent</div>'
        if config.get("shadow_mode", True) else ""
    )

    return PAGE.format(
        updated=now.isoformat(timespec="seconds"),
        shadow_banner=shadow_banner,
        banner_text=banner_text,
        banner_class=banner_class,
        events_table=_render_events(events, now),
        zones_table=_render_zones(mapping),
        weather_block=_render_weather(),
        poll_block=_render_poll(),
        commands_table=_render_commands(),
        alerts_table=_render_alerts(),
    )


@app.route("/healthz")
def healthz() -> tuple[str, int]:
    p = database.latest_poll()
    if not p:
        return "no poll yet", 503
    return f"ok {p['timestamp']}", 200


def run(host: str = "0.0.0.0", port: int = 8080) -> None:
    app.run(host=host, port=port, debug=False)


if __name__ == "__main__":
    run()
