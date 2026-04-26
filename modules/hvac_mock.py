"""Shadow-mode HVAC — logs commands, sends nothing.

Implements the same interface as hvac_tcc.HVACTCC so main.py can swap one for the other.
In shadow mode real thermostat reads don't happen; get_status returns the last
commanded setpoints (or an empty dict for unseen zones).
"""

import logging

from . import database

log = logging.getLogger(__name__)


class HVACMock:
    def __init__(self, mapping: dict):
        self._mapping = mapping
        self._last_command: dict[str, dict] = {}

    def _record(self, zone_id: str, action: str, cool_temp: int, heat_temp: int) -> bool:
        self._last_command[zone_id] = {
            "action": action,
            "cool_setpoint": cool_temp,
            "heat_setpoint": heat_temp,
        }
        database.record_command(
            system="hvac",
            target_id=zone_id,
            action=action,
            parameters={"cool_temp": cool_temp, "heat_temp": heat_temp},
            success=True,
            shadow_mode=True,
        )
        log.info("[SHADOW] %s %s cool=%s heat=%s", action, zone_id, cool_temp, heat_temp)
        return True

    def set_occupied(self, zone_id: str, cool_temp: int, heat_temp: int) -> bool:
        return self._record(zone_id, "set_occupied", cool_temp, heat_temp)

    def set_standby(self, zone_id: str, cool_temp: int, heat_temp: int) -> bool:
        return self._record(zone_id, "set_standby", cool_temp, heat_temp)

    def get_status(self, zone_id: str) -> dict:
        return dict(self._last_command.get(zone_id, {}))
