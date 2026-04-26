"""Planning Center calendar reader. Produces the Event objects the engines consume."""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

log = logging.getLogger(__name__)


@dataclass
class Event:
    id: str
    name: str
    start: datetime
    end: datetime
    rooms: list[str] = field(default_factory=list)

    def is_active(self, now: datetime) -> bool:
        return self.start <= now <= self.end

    def overlaps(self, window_start: datetime, window_end: datetime) -> bool:
        return self.start < window_end and self.end > window_start


def _parse_dt(value: str) -> datetime:
    """Parse an ISO-8601 datetime from PCO into an aware datetime (UTC)."""
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _matches_any(name: str, patterns: list[str]) -> bool:
    lowered = name.lower()
    return any(p.lower() in lowered for p in patterns)


def is_mws_event(event: Event, config: dict) -> bool:
    patterns = config.get("pco", {}).get("mws_event_patterns", [])
    return _matches_any(event.name, patterns)


def is_crossover_event(event: Event, config: dict) -> bool:
    patterns = config.get("pco", {}).get("crossover_event_patterns", [])
    return _matches_any(event.name, patterns)


def fetch_events(config: dict, secrets: dict, now: datetime | None = None) -> list[Event]:
    """Fetch active-or-soon event instances with room bookings resolved."""
    pco_cfg = config.get("pco", {})
    pco_secrets = secrets.get("pco", {})
    app_id = pco_secrets.get("app_id")
    secret = pco_secrets.get("secret")
    if not app_id or not secret:
        log.error("PCO credentials missing")
        return []

    now = now or datetime.now(timezone.utc)
    window_start = now - timedelta(hours=pco_cfg.get("fetch_window_hours_behind", 2))
    window_end = now + timedelta(hours=pco_cfg.get("fetch_window_hours_ahead", 24))

    import pypco  # lazy — keeps Event importable without the SDK installed
    pco = pypco.PCO(app_id, secret)

    params = {
        "where[starts_at][lte]": window_end.isoformat(),
        "where[ends_at][gte]": window_start.isoformat(),
        "include": "event,resource_bookings",
        "per_page": 100,
    }

    events: list[Event] = []
    try:
        for instance in pco.iterate("/calendar/v2/event_instances", **params):
            event = _build_event(instance)
            if event is not None:
                events.append(event)
    except Exception as exc:
        log.exception("PCO fetch failed: %s", exc)
        return []

    return events


def _build_event(payload: dict) -> Event | None:
    """Build an Event from a single PCO event_instance payload (with included data)."""
    try:
        data = payload["data"]
        attrs = data["attributes"]
        instance_id = data["id"]

        included = {(item["type"], item["id"]): item for item in payload.get("included", [])}

        # Event name comes from the parent Event resource
        event_rel = data.get("relationships", {}).get("event", {}).get("data") or {}
        event_obj = included.get((event_rel.get("type"), event_rel.get("id")))
        name = event_obj["attributes"]["name"] if event_obj else attrs.get("name", "")

        start = _parse_dt(attrs["starts_at"])
        end = _parse_dt(attrs["ends_at"])

        rooms: list[str] = []
        bookings = data.get("relationships", {}).get("resource_bookings", {}).get("data", [])
        for ref in bookings:
            booking = included.get((ref.get("type"), ref.get("id")))
            if not booking:
                continue
            resource_ref = (
                booking.get("relationships", {}).get("resource", {}).get("data") or {}
            )
            resource = included.get((resource_ref.get("type"), resource_ref.get("id")))
            if resource:
                room_name = resource.get("attributes", {}).get("name")
                if room_name:
                    rooms.append(room_name)

        return Event(id=instance_id, name=name, start=start, end=end, rooms=rooms)
    except (KeyError, ValueError) as exc:
        log.warning("Skipping malformed PCO instance: %s", exc)
        return None
