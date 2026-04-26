"""Honeywell Total Connect Comfort controller via pyhtcc.

Stateless: every public call re-authenticates to TCC. Avoids session-expiry
issues with the unofficial API. Thermostats are identified by MAC ID only —
display names are not stable enough to trust.
"""

import logging
from typing import Any

from . import database

log = logging.getLogger(__name__)


class HVACTCC:
    def __init__(self, mapping: dict, secrets: dict):
        self._mapping = mapping
        self._username = secrets.get("tcc", {}).get("username")
        self._password = secrets.get("tcc", {}).get("password")

    def _mac_for(self, zone_id: str) -> str | None:
        zone = self._mapping.get("zones", {}).get(zone_id)
        return zone.get("mac_id") if zone else None

    def _connect(self):
        from pyhtcc import PyHTCC  # lazy — not needed to construct the class
        return PyHTCC(self._username, self._password)

    def _find_zone(self, htcc, mac_id: str):
        """Locate a Zone object whose MAC ID matches (case-insensitive)."""
        target = mac_id.lower().replace(":", "")
        for zone_info in htcc.get_zones_info():
            raw_mac = zone_info.get("MacID") or zone_info.get("macID") or ""
            if raw_mac.lower().replace(":", "") == target:
                name = zone_info.get("Name") or zone_info.get("name")
                return htcc.get_zone_by_name(name)
        return None

    def _apply_setpoints(self, zone_id: str, action: str,
                         cool_temp: int, heat_temp: int) -> bool:
        mac = self._mac_for(zone_id)
        if not mac:
            self._log_result(zone_id, action, cool_temp, heat_temp, False,
                             error=f"unknown zone {zone_id}")
            return False

        error: str | None = None
        try:
            htcc = self._connect()
            zone = self._find_zone(htcc, mac)
            if zone is None:
                raise RuntimeError(f"MAC {mac} not found in TCC account")
            zone.set_permanent_cool_setpoint(cool_temp)
            zone.set_permanent_heat_setpoint(heat_temp)
            success = True
        except Exception as exc:
            log.exception("TCC %s failed for %s", action, zone_id)
            error = str(exc)
            success = False

        self._log_result(zone_id, action, cool_temp, heat_temp, success, error=error)
        return success

    def _log_result(self, zone_id: str, action: str, cool_temp: int, heat_temp: int,
                    success: bool, error: str | None = None) -> None:
        database.record_command(
            system="hvac",
            target_id=zone_id,
            action=action,
            parameters={"cool_temp": cool_temp, "heat_temp": heat_temp},
            success=success,
            shadow_mode=False,
            error_message=error,
        )

    def set_occupied(self, zone_id: str, cool_temp: int, heat_temp: int) -> bool:
        return self._apply_setpoints(zone_id, "set_occupied", cool_temp, heat_temp)

    def set_standby(self, zone_id: str, cool_temp: int, heat_temp: int) -> bool:
        return self._apply_setpoints(zone_id, "set_standby", cool_temp, heat_temp)

    def get_status(self, zone_id: str) -> dict:
        mac = self._mac_for(zone_id)
        if not mac:
            return {}
        try:
            htcc = self._connect()
            zone = self._find_zone(htcc, mac)
            if zone is None:
                return {}
            info: dict[str, Any] = zone.zone_info or {}
            latest = info.get("latestData", {})
            ui = latest.get("uiData", {})
            fan = latest.get("fanData", {})
            status = {
                "indoor_temp": ui.get("DispTemperature"),
                "indoor_humidity": ui.get("IndoorHumidity"),
                "cool_setpoint": ui.get("CoolSetpoint"),
                "heat_setpoint": ui.get("HeatSetpoint"),
                "mode": ui.get("SystemSwitchPosition"),
                "fan_mode": fan.get("fanMode"),
                "is_heating": ui.get("EquipmentOutputStatus") == 1,
                "is_cooling": ui.get("EquipmentOutputStatus") == 2,
            }
            database.record_thermostat_reading(zone_id, status)
            return status
        except Exception as exc:
            log.exception("TCC status fetch failed for %s", zone_id)
            return {}
