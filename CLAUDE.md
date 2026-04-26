# DPUMC Facility Automation System
## Claude Code Project Instructions

This project replaces the Events2HVAC SaaS subscription ($1,200/year) with a custom Python-based facility automation system for Deer Park United Methodist Church. It runs on a dedicated Raspberry Pi 4 (4GB) on the church network. Development is on Ubuntu; deployment is to the Pi via Git.

**Do not over-engineer. Prefer simple and explicit over clever and abstract.**

---

## What This System Does

1. Polls Planning Center Online (PCO) calendar every 5 minutes
2. Issues permanent holds to Honeywell Total Connect Comfort (TCC) thermostats for HVAC scheduling
3. Unlocks/locks doors via UniFi Access API (local network — no internet dependency)
4. Enforces hard safety lockouts during MWS and CrossOver sessions
5. Collects thermostat and weather data to SQLite for trend analysis and predictive maintenance
6. Serves a local read-only status dashboard (Flask)
7. Sends alert emails to admin and/or trustee chair based on alert type

---

## Architecture: Stateless Poller

- **Cron** wakes the poller every 5 minutes. No persistent process, no daemon.
- On reboot, cron resumes automatically. If a reboot occurs mid-event, the next poll detects and recovers.
- Each poll cycle: fetch PCO events → evaluate HVAC actions → evaluate door actions → collect data → exit.

---

## Modular Design — CRITICAL

Each external system has its own module with a standard interface. The core scheduler calls module methods only — never talks to TCC, UniFi, or PCO directly. Swapping an integration means replacing one file.

```
modules/
  calendar_pco.py      # PCO calendar reader
  hvac_tcc.py          # Honeywell TCC HVAC controller
  hvac_mock.py         # Shadow mode — logs commands, sends nothing
  doors_unifi.py       # UniFi Access door controller
  weather.py           # Open-Meteo weather fetcher
  database.py          # SQLite read/write
  alerts.py            # Email alerting
  dashboard.py         # Flask status page
```

**Standard HVAC interface** (both hvac_tcc.py and hvac_mock.py must implement):
```python
def set_occupied(zone_id: str, cool_temp: int, heat_temp: int) -> bool
def set_standby(zone_id: str, cool_temp: int, heat_temp: int) -> bool
def get_status(zone_id: str) -> dict
```

**Standard door interface:**
```python
def unlock(door_id: str) -> bool
def lock(door_id: str) -> bool
def get_status(door_id: str) -> dict
```

---

## Configuration Files

### config.yaml — main configuration
```yaml
shadow_mode: true          # Set false to enable live HVAC commands
adaptive_prestart: false   # Set true when data is sufficient (Phase 2)
default_precool_minutes: 30
door_buffer_before_minutes: 15
door_buffer_after_minutes: 15

email:
  smtp_server: smtp.gmail.com
  smtp_port: 587
  from_address: system@dpumc.org
  admin: admin@dpumc.org
  trustee_chair: trustee@dpumc.org  # update with real address

weather:
  latitude: 29.7030
  longitude: -95.1244  # Deer Park, TX
```

### secrets.yaml — NEVER commit to Git, in .gitignore
```yaml
tcc:
  username: admin@dpumc.org
  password: ""
pco:
  app_id: ""
  secret: ""
unifi:
  base_url: https://192.168.x.x  # UDM Pro local IP
  api_token: ""
email:
  password: ""
```

### mapping.yaml — zone and door assignments (derived from AC_Rooms.xlsx)
See ZONE REFERENCE and DOOR REFERENCE sections below.

---

## HVAC Zone Reference

All 13 TCC units identified by MAC ID (hardware-fixed, not display name).

| zone_id | TCC Name | MAC ID | Cool Occ | Cool Unocc | Heat Occ | Heat Unocc |
|---|---|---|---|---|---|---|
| admin | Admin | 48A2E62818B8 | 72 | 80 | 67 | 55 |
| choir | Choir | 48A2E62332A9 | 69 | 80 | 65 | 55 |
| concourse_e | Concourse-E | B82CA0A17EFD | 72 | 80 | 67 | 55 |
| concourse_w | Concourse-W | 48A2E62332AE | 72 | 80 | 67 | 55 |
| east_wing | East Wing | 48A2E6A26C23 | 73 | 80 | 67 | 55 |
| sanctuary_e | Sanctuary-E | 48A2E62332B0 | 71 | 78 | 67 | 55 |
| sanctuary_w | Sanctuary-W | 48A2E60F63B4 | 71 | 78 | 67 | 55 |
| mws_n | MWS-N | 48A2E62332AB | 70 | 80 | 65 | 55 |
| mws_s | MWS-S | 48A2E6233273 | 70 | 80 | 65 | 55 |
| flc_up | FLC-Up | 48A2E6182D28 | 72 | 80 | 67 | 55 |
| flc_down | FLC-Down | 48A2E6182D3A | 72 | 80 | 67 | 55 |
| gym_e | Gym-E | 48A2E6182D3E | 72 | 80 | 67 | 55 |
| gym_w | Gym-W | 48A2E62817B0 | 72 | 80 | 67 | 55 |

### PCO Room → Zone(s) Mapping

| PCO Room Name | Zone(s) |
|---|---|
| Offices - 100-105 | admin |
| Choir - 112-113 | choir |
| 111 | choir |
| Chapel - 110 | concourse_e |
| 118-119 - East Concourse | concourse_e |
| 120 - Concourse | concourse_e, concourse_w |
| 121-122 - West Concourse | concourse_w |
| 106+108 | east_wing |
| Food Pantry - 107 | east_wing |
| 114 | sanctuary_e |
| 115 | sanctuary_e |
| Sanctuary | sanctuary_e, sanctuary_w |
| Library - 116 | sanctuary_w |
| Cry Room - 117 | sanctuary_w |
| Kitchen - 123 | mws_n |
| Children's Wing - 124-128 | mws_n |
| MWS - 129-137 | mws_s |
| F-Upstairs | flc_up |
| Youth Room - F101 | flc_down |
| Kitchen - F102 | flc_down |
| Gym - F104 | gym_e, gym_w |

---

## Door Reference

All door IDs are confirmed from UniFi Access. Do not use door names for API calls — use IDs.

| Door Name | UniFi ID | Type | Event Triggered | MWS Lockout | CrossOver Lockout |
|---|---|---|---|---|---|
| Front Exterior | aa3b4674-26e4-4e13-b2de-c01b574d3955 | Exterior | Yes | No | No |
| East Door | a1dda2ce-ebc0-410c-8683-d4f0a8343752 | Exterior | Yes | No | No |
| FP Exterior | 651f1d1f-a450-499d-8b87-2c754e128dce | Exterior | Yes | No | No |
| Front Interior | 9f00ec14-0722-438d-b4a6-369377e45466 | Interior | Yes (when no MWS) | Yes | No |
| FP Interior | cefccd2e-bdfa-4a21-86e3-6c8ab17bae3b | Interior | Yes (when no MWS) | Yes | No |
| MWS Interior | ec2ccc5f-46d0-409e-9478-009c91a47b33 | Interior | Yes (when no MWS) | Yes | No |
| MWS Front | 23adbe59-b0a6-4f48-8e4f-cf150bb7389f | Exterior | Never — staff only | Yes | No |
| FLC Gym | 45764eb5-54fc-4a6c-97fb-7ccde66ef5e9 | FLC Entry | Yes (F101, F102, F104) | Yes | Yes |
| FLC Back | 068fe2dd-0e8a-46c7-aee2-e941729c2a1a | Sidewalk | Weekly schedule only | Yes | Yes |
| Concourse | 3709510c-c196-4c3a-ad07-3329e6cedb87 | Sidewalk | Weekly schedule only | Yes | Yes |
| MWS Back | 1ca88a37-bd84-4173-bd0c-3b7970edcd0a | Exterior | Never — staff only | Yes | No |
| FLC Closets | be673221-46ef-42fb-a182-0b9724522b35 | Interior | Never — staff only | No | No |

**Partner doors:** Concourse and FLC Back lock and unlock together — always. Their `mws_lockout` and `crossover_lockout` flags must stay identical. Encoded via `partner_of:` fields in `mapping.yaml` and enforced by `tests/test_doors_unifi.py`.

### PCO Room → Door(s) Mapping

| PCO Room Name | Unlocks Door(s) |
|---|---|
| Food Pantry - 107 | FP Exterior |
| 106+108 | East Door |
| Choir - 112-113 | East Door |
| Chapel - 110 | East Door |
| 111 | East Door |
| 114 | East Door |
| 115 | East Door |
| 118-119 - East Concourse | East Door |
| 120 - Concourse | East Door, Front Exterior |
| 121-122 - West Concourse | Front Exterior |
| Library - 116 | Front Exterior |
| Sanctuary | East Door, Front Exterior |
| Kitchen - 123 | Front Exterior |
| Offices - 100-105 | Front Exterior |
| Youth Room - F101 | FLC Gym |
| Kitchen - F102 | FLC Gym |
| Gym - F104 | FLC Gym |
| Front Interior | Unlocked when no MWS active |
| FP Interior | Unlocked when no MWS active |
| MWS Interior | Unlocked when no MWS active |

---

## Safety Lockout — HIGHEST PRIORITY LOGIC

**This check runs before any unlock command, every poll cycle, no exceptions.**

```python
# Pseudocode — implement exactly this priority order
def door_engine_poll(events, current_time):
    mws_active = any(e for e in events if is_mws_event(e) and is_active(e, current_time))
    crossover_active = any(e for e in events if is_crossover_event(e) and is_active(e, current_time))

    MWS_PROTECTED = [
        "ec2ccc5f-46d0-409e-9478-009c91a47b33",  # MWS Interior
        "23adbe59-b0a6-4f48-8e4f-cf150bb7389f",  # MWS Front
        "9f00ec14-0722-438d-b4a6-369377e45466",  # Front Interior
        "cefccd2e-bdfa-4a21-86e3-6c8ab17bae3b",  # FP Interior
        "45764eb5-54fc-4a6c-97fb-7ccde66ef5e9",  # FLC Gym
        "068fe2dd-0e8a-46c7-aee2-e941729c2a1a",  # FLC Back
        "3709510c-c196-4c3a-ad07-3329e6cedb87",  # Concourse (partner of FLC Back)
        "1ca88a37-bd84-4173-bd0c-3b7970edcd0a",  # MWS Back
    ]
    CROSSOVER_PROTECTED = [
        "45764eb5-54fc-4a6c-97fb-7ccde66ef5e9",  # FLC Gym
        "068fe2dd-0e8a-46c7-aee2-e941729c2a1a",  # FLC Back
        "3709510c-c196-4c3a-ad07-3329e6cedb87",  # Concourse (partner of FLC Back)
    ]

    locked_doors = set()
    if mws_active:
        locked_doors.update(MWS_PROTECTED)
    if crossover_active:
        locked_doors.update(CROSSOVER_PROTECTED)

    # Check for conflicting bookings and alert
    for event in events:
        if is_active(event, current_time):
            for door_id in get_doors_for_event(event):
                if door_id in locked_doors:
                    send_alert("LOCKOUT CONFLICT", event, door_id, recipient="admin")

    # Normal door logic — never unlock a door in locked_doors
    for event in events:
        process_event_doors(event, exclude=locked_doors)
```

---

## Alert Routing

| Alert Type | admin@dpumc.org | Trustee Chair |
|---|---|---|
| HVAC command failure / retry | Yes | No |
| Door command failure | Yes | No |
| Safety lockout conflict | Yes | No |
| Zone efficiency degradation | Yes | Yes |
| Indoor humidity anomaly | Yes | Yes |
| Pre-start failure | Yes | Yes |
| Paired unit divergence | Yes | Yes |
| Weekly summary digest | Yes | Yes |

---

## Project File Structure

```
dpumc-facility-automation/
├── CLAUDE.md                    # This file
├── README.md
├── .gitignore                   # Must include secrets.yaml, *.db, logs/
├── requirements.txt
├── config.yaml                  # Main config — committed to Git
├── secrets.yaml                 # Credentials — NEVER committed
├── mapping.yaml                 # Zone and door assignments
├── main.py                      # Entry point — called by cron
├── modules/
│   ├── __init__.py
│   ├── calendar_pco.py
│   ├── hvac_tcc.py
│   ├── hvac_mock.py
│   ├── doors_unifi.py
│   ├── weather.py
│   ├── database.py
│   ├── alerts.py
│   └── dashboard.py
├── data/
│   └── facility.db              # SQLite database — not committed
├── logs/                        # Operational logs — not committed
└── tests/
    └── test_*.py
```

---

## Rollout Phases

**Current phase: Shadow Mode**
- `shadow_mode: true` in config.yaml
- HVAC commands logged only — nothing sent to TCC
- Door management active (including safety lockouts)
- Data collection active

**Phase 2: Parallel Mode (July)**
- `shadow_mode: false`
- Both this system and Events2HVAC run simultaneously
- Verify commands match

**Phase 3: Cutover (August)**
- Events2HVAC subscription lapses
- This system is sole operator

**Phase 4: Adaptive Pre-Start (Month 6+)**
- `adaptive_prestart: true` once sufficient data exists
- Weather-adjusted pre-start times per zone

---

## Key Technical Decisions Already Made

- **Thermostat identification:** MAC ID only — never display name
- **TCC session:** Re-authenticate on every poll cycle (stateless, avoids session expiry issues)
- **Retry logic:** Context-aware — check if command is still relevant before resending
- **Door lockout:** Suppression of unlock commands, not active lock commands
- **Lockout trigger:** PCO calendar presence only — no hardcoded time windows
- **Dashboard:** Flask, read-only, local network only, no authentication
- **Database:** SQLite — no server required
- **Scheduler:** Linux cron — no daemon required
- **Weather:** Open-Meteo API — free, no key required, latitude 29.7030 / longitude -95.1244

## Do Not Revisit These Decisions Without Good Reason
- Do not switch to Home Assistant
- Do not add cloud hosting
- Do not add authentication to the dashboard
- Do not use thermostat display names instead of MAC IDs
- Do not hardcode MWS/CrossOver time windows — always use PCO calendar

---

## TCC Notes

- Portal: mytotalconnectcomfort.com
- Library: pyhtcc (pip install pyhtcc)
- TCC is an unofficial API — re-authenticate every session
- Known issue: Resideo platform has ~2 outages/week, usually brief
- Permanent hold is the mechanism — set occupied temp at start, standby temp at end
- The hold persists on the thermostat even if TCC goes offline after it is set

## UniFi Access Notes

- API is local network only — no internet required
- Base URL: https://[UDM Pro IP]/api/v1/developer/
- Bearer token authentication — generate from UniFi Access application
- Do not use cloud/UI.com authentication — use local API token
- MWS Back and FLC Closets are staff-only doors — never event-triggered

## PCO Notes

- Library: pypco (pip install pypco)
- Authentication: Personal Access Token (not OAuth)
- Calendar API: https://api.planningcenteronline.com/calendar/v2/events
- MWS events and CrossOver events are identified by name matching in PCO
- Confirm exact PCO event name strings during deployment setup
