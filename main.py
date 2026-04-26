"""Cron entry point — one stateless poll cycle.

Invoked every 5 minutes by cron. Each run:

 1. Load config / secrets / mapping
 2. Initialize the SQLite database (idempotent)
 3. Fetch current weather (best-effort)
 4. Fetch PCO calendar events
 5. Decide the desired HVAC state per zone and issue commands that differ
    from the last successful command for that zone
 6. Compute the door plan, issue door commands that differ from last successful
 7. Send lockout-conflict alerts for anything compute_door_plan flagged
 8. Collect live thermostat readings (skipped in shadow mode)
 9. Write a poll_log row
"""

import logging
import logging.handlers
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from modules import (
    alerts,
    calendar_pco,
    database,
    doors_unifi,
    hvac_mock,
    hvac_tcc,
    weather,
)

PROJECT_ROOT = Path(__file__).resolve().parent
LOG_PATH = PROJECT_ROOT / "logs" / "facility.log"

log = logging.getLogger("dpumc")


def _setup_logging() -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=2_000_000, backupCount=5
    )
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    ))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)


def _load_yaml(path: Path) -> dict:
    with open(path) as f:
        return yaml.safe_load(f) or {}


def _zones_for_event(event, mapping: dict) -> set[str]:
    zones: set[str] = set()
    room_map = mapping.get("pco_room_to_zones", {})
    for room in event.rooms:
        zones.update(room_map.get(room, []))
    return zones


def _desired_hvac_state(zone_id: str, events: list, mapping: dict,
                        now: datetime, precool: timedelta) -> tuple[str, int, int]:
    zone_spec = mapping["zones"][zone_id]
    for event in events:
        if zone_id not in _zones_for_event(event, mapping):
            continue
        if event.start - precool <= now <= event.end:
            return (
                "set_occupied",
                zone_spec["cool_occupied"],
                zone_spec["heat_occupied"],
            )
    return (
        "set_standby",
        zone_spec["cool_unoccupied"],
        zone_spec["heat_unoccupied"],
    )


def _needs_hvac_command(zone_id: str, action: str, cool: int, heat: int) -> bool:
    last = database.last_successful_command("hvac", zone_id)
    if not last:
        return True
    if last["action"] != action:
        return True
    params = last["parameters"]
    return params.get("cool_temp") != cool or params.get("heat_temp") != heat


def _needs_door_command(door_id: str, action: str) -> bool:
    last = database.last_successful_command("door", door_id)
    return last is None or last["action"] != action


def _run_hvac(events, mapping, config, secrets, errors: list[str]) -> None:
    shadow = config.get("shadow_mode", True)
    if shadow:
        hvac = hvac_mock.HVACMock(mapping)
    else:
        hvac = hvac_tcc.HVACTCC(mapping, secrets)

    precool = timedelta(minutes=config.get("default_precool_minutes", 30))
    now = datetime.now(timezone.utc)

    for zone_id in mapping.get("zones", {}):
        action, cool, heat = _desired_hvac_state(
            zone_id, events, mapping, now, precool
        )
        if not _needs_hvac_command(zone_id, action, cool, heat):
            continue
        ok = getattr(hvac, action)(zone_id, cool, heat)
        if not ok:
            errors.append(f"hvac:{zone_id}:{action}")
            alerts.send_alert(
                "hvac_command_failure",
                subject=f"HVAC command failed: {zone_id}",
                body=(f"Zone: {zone_id}\nAction: {action}\n"
                      f"Cool: {cool}  Heat: {heat}\n"
                      f"Shadow: {shadow}"),
                config=config,
                secrets=secrets,
            )

    if not shadow:
        for zone_id in mapping.get("zones", {}):
            hvac.get_status(zone_id)


def _run_doors(events, mapping, config, secrets, errors: list[str]) -> None:
    now = datetime.now(timezone.utc)
    plan = doors_unifi.compute_door_plan(events, now, config, mapping)
    client = doors_unifi.DoorsUnifi(mapping, secrets)

    for did in plan.doors_to_unlock:
        if not _needs_door_command(did, "unlock"):
            continue
        if not client.unlock(did):
            errors.append(f"door_unlock:{did}")
            alerts.send_alert(
                "door_command_failure",
                subject=f"Door unlock failed: {did}",
                body=f"Door: {did}\nAction: unlock",
                config=config, secrets=secrets,
            )

    for did in plan.doors_to_lock:
        if not _needs_door_command(did, "lock"):
            continue
        if not client.lock(did):
            errors.append(f"door_lock:{did}")
            alerts.send_alert(
                "door_command_failure",
                subject=f"Door lock failed: {did}",
                body=f"Door: {did}\nAction: lock",
                config=config, secrets=secrets,
            )

    for conflict in plan.conflicts:
        alerts.send_alert(
            "lockout_conflict",
            subject=f"Lockout conflict: {conflict['event_name']}",
            body=(
                f"Event: {conflict['event_name']} (id {conflict['event_id']})\n"
                f"Rooms: {', '.join(conflict['rooms'])}\n"
                f"Wanted door: {conflict['door_key']} ({conflict['door_id']})\n"
                f"MWS active: {plan.mws_active}  "
                f"CrossOver active: {plan.crossover_active}\n\n"
                "The unlock was suppressed by the safety lockout."
            ),
            config=config, secrets=secrets,
        )


def poll() -> int:
    start = time.monotonic()
    config = _load_yaml(PROJECT_ROOT / "config.yaml")
    secrets = _load_yaml(PROJECT_ROOT / "secrets.yaml")
    mapping = _load_yaml(PROJECT_ROOT / "mapping.yaml")

    database.init_db()
    errors: list[str] = []

    if weather.poll(config) is None:
        errors.append("weather:fetch_failed")

    events = calendar_pco.fetch_events(config, secrets)
    log.info("Fetched %d PCO events", len(events))

    _run_hvac(events, mapping, config, secrets, errors)
    _run_doors(events, mapping, config, secrets, errors)

    duration_ms = int((time.monotonic() - start) * 1000)
    database.record_poll(duration_ms, len(events), errors or None)
    log.info("Poll complete in %d ms, errors=%s", duration_ms, errors)
    return 0 if not errors else 1


def main() -> int:
    _setup_logging()
    try:
        return poll()
    except Exception:
        log.critical("Poll crashed:\n%s", traceback.format_exc())
        return 2


if __name__ == "__main__":
    sys.exit(main())
