"""UniFi Access door controller and door-state engine.

Two halves:

* `compute_door_plan` — a pure function that turns (events, now, config, mapping)
  into a DoorPlan describing which doors should unlock, lock, or trigger a
  conflict alert. This is where the safety-lockout logic lives and is what the
  tests in tests/test_doors_unifi.py exercise.

* `DoorsUnifi` — the I/O shell. Talks to the UDM Pro via the local Access API
  and records every command to the database.

The safety lockout policy comes straight from CLAUDE.md:

  - MWS active → MWS-protected doors are suppressed from unlock and forced lock
  - CrossOver active → FLC-protected doors are suppressed from unlock and forced lock
  - Lockout triggers come from PCO calendar presence only — no hardcoded windows
  - Interior doors default unlocked when no lockout applies; otherwise they lock
  - Conflicts (an active booking wants a locked-out door) produce alerts
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta

import requests

from . import calendar_pco, database

log = logging.getLogger(__name__)


@dataclass
class DoorPlan:
    doors_to_unlock: set[str] = field(default_factory=set)
    doors_to_lock: set[str] = field(default_factory=set)
    conflicts: list[dict] = field(default_factory=list)
    mws_active: bool = False
    crossover_active: bool = False


def _door_active_window(event, now: datetime, config: dict) -> bool:
    before = timedelta(minutes=config.get("door_buffer_before_minutes", 15))
    after = timedelta(minutes=config.get("door_buffer_after_minutes", 15))
    return event.start - before <= now <= event.end + after


def _doors_for_event(event, mapping: dict) -> list[tuple[str, str]]:
    """Return (door_key, unifi_id) pairs for every door this event wants unlocked."""
    room_map = mapping.get("pco_room_to_doors", {})
    door_defs = mapping.get("doors", {})
    keys: set[str] = set()
    for room in event.rooms:
        keys.update(room_map.get(room, []))
    out: list[tuple[str, str]] = []
    for key in keys:
        spec = door_defs.get(key)
        if spec:
            out.append((key, spec["unifi_id"]))
    return out


def compute_door_plan(events, now: datetime, config: dict, mapping: dict) -> DoorPlan:
    door_defs: dict = mapping.get("doors", {})

    mws_active = any(
        calendar_pco.is_mws_event(e, config) and e.is_active(now) for e in events
    )
    crossover_active = any(
        calendar_pco.is_crossover_event(e, config) and e.is_active(now) for e in events
    )

    locked_out: set[str] = set()
    for spec in door_defs.values():
        did = spec["unifi_id"]
        if mws_active and spec.get("mws_lockout"):
            locked_out.add(did)
        if crossover_active and spec.get("crossover_lockout"):
            locked_out.add(did)

    plan = DoorPlan(mws_active=mws_active, crossover_active=crossover_active)

    wanted_unlocked: set[str] = set()
    for event in events:
        if not _door_active_window(event, now, config):
            continue
        for key, did in _doors_for_event(event, mapping):
            if did in locked_out:
                plan.conflicts.append({
                    "event_id": event.id,
                    "event_name": event.name,
                    "door_key": key,
                    "door_id": did,
                    "rooms": list(event.rooms),
                })
            else:
                wanted_unlocked.add(did)

    for key, spec in door_defs.items():
        did = spec["unifi_id"]
        is_interior = spec.get("type") == "interior"
        event_triggered = spec.get("event_triggered", False)

        if is_interior and event_triggered:
            if did in locked_out:
                plan.doors_to_lock.add(did)
            else:
                plan.doors_to_unlock.add(did)
            continue

        if event_triggered:
            if did in wanted_unlocked:
                plan.doors_to_unlock.add(did)
            else:
                plan.doors_to_lock.add(did)
            continue

        if did in locked_out:
            plan.doors_to_lock.add(did)

    return plan


class DoorsUnifi:
    """UniFi Access local API client."""

    def __init__(self, mapping: dict, secrets: dict, timeout: int = 10):
        self._mapping = mapping
        unifi = secrets.get("unifi", {})
        self._base_url = unifi.get("base_url", "").rstrip("/")
        self._token = unifi.get("api_token")
        self._timeout = timeout

    def _headers(self) -> dict:
        return {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }

    def _remote_unlock(self, door_id: str) -> None:
        url = f"{self._base_url}/api/v1/developer/doors/{door_id}/unlock"
        resp = requests.put(url, headers=self._headers(), verify=False,
                            timeout=self._timeout)
        resp.raise_for_status()

    def _remote_lock(self, door_id: str) -> None:
        url = f"{self._base_url}/api/v1/developer/doors/{door_id}/lock"
        resp = requests.put(url, headers=self._headers(), verify=False,
                            timeout=self._timeout)
        resp.raise_for_status()

    def unlock(self, door_id: str) -> bool:
        error: str | None = None
        try:
            self._remote_unlock(door_id)
            success = True
        except Exception as exc:
            log.exception("Door unlock failed: %s", door_id)
            error = str(exc)
            success = False
        database.record_command(
            system="door", target_id=door_id, action="unlock",
            parameters={}, success=success, shadow_mode=False,
            error_message=error,
        )
        return success

    def lock(self, door_id: str) -> bool:
        error: str | None = None
        try:
            self._remote_lock(door_id)
            success = True
        except Exception as exc:
            log.exception("Door lock failed: %s", door_id)
            error = str(exc)
            success = False
        database.record_command(
            system="door", target_id=door_id, action="lock",
            parameters={}, success=success, shadow_mode=False,
            error_message=error,
        )
        return success

    def get_status(self, door_id: str) -> dict:
        url = f"{self._base_url}/api/v1/developer/doors/{door_id}"
        try:
            resp = requests.get(url, headers=self._headers(), verify=False,
                                timeout=self._timeout)
            resp.raise_for_status()
            return resp.json().get("data", {})
        except Exception:
            log.exception("Door status fetch failed: %s", door_id)
            return {}
