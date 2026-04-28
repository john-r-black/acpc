# ACPC — Deployment Handoff (2026-04-27)

This document is a handoff to the next Claude Code session that picks up ACPC work. Written from the `fred` operator-console session on the eve of moving the Pi to the church. The hardware setup is done; what's left is install + configure + deploy.

## Current state of the Pi

| Detail | Value |
|---|---|
| Hostname | `1421acpc` |
| OS | Raspberry Pi OS Lite 64-bit (Trixie / Debian 13) |
| Hardware | Pi 4B Rev 1.5, 2 GB RAM, 256 GB USB SSD (ORICO enclosure) |
| User | `acpc` (UID 1001), passwordless sudo, key-auth from `1421home` |
| Password (fallback) | `Kagewa41421!` |
| Currently at | John's home, plugged into UDM Pro port 2, IP `192.168.3.10` (VLAN 1421) |
| SSH alias | `acpc` in `~/.ssh/config` → `acpc@192.168.3.10` |
| MAC | `88:a2:9e:a4:7a:ff` (eth0) |
| Application installed? | **No.** Fresh image, only OS + user. |

The user `1421MCP` (used on the production MCP Pi) is **not** present here — Pi OS Trixie's `userconf` rejects usernames with uppercase letters or leading digits. Use `acpc` instead. Don't try to recreate `1421MCP` on this box.

## Why a separate Pi for ACPC

There are two Pis in this fleet:

- **Production MCP Pi** (`1421mcp`, 8 GB, at home, `192.168.3.7`) — runs all the long-running MCP daemons (Google, PCO, UniFi, HTTC, etc.) plus sd26 and ticket-scout, exposed via Cloudflare Tunnel.
- **ACPC Pi** (this one, 2 GB, going to church) — runs only the church facility automation (HVAC + door control). Stateless cron poller, no inbound traffic.

See `~/code_projects/fred/references/pi-infrastructure.md` for the full fleet doc.

## Project layout

ACPC repo is at `~/code_projects/acpc/` (also at `github.com/john-r-black/acpc`). Layout:

- `main.py` — cron entry point, one stateless poll cycle per invocation
- `modules/` — `calendar_pco`, `hvac_tcc`, `hvac_mock`, `doors_unifi`, `weather`, `database`, `alerts`, `dashboard`
- `config.yaml` — main configuration; **defaults to `shadow_mode: true`** (logs commands but doesn't send them)
- `mapping.yaml` — zone/door mapping
- `secrets.yaml.example` — template; copy to `secrets.yaml` and fill in
- `requirements.txt` — pyhtcc, pypco, PyYAML, requests, Flask
- `tests/` — pytest tests
- `CLAUDE.md` — full architecture/interfaces/zone reference (READ THIS FIRST in the next session)
- `README.md` — setup steps

## Deployment plan

### Phase 1 — On the Pi at home, before the move

Run these tonight or tomorrow morning before unplugging. Network access is required for `apt` and `pip`; the Pi has it at home, may not at the church depending on which VLAN the office switch port lands on.

```bash
# From this Ubuntu box (1421home):
ssh acpc

# On the Pi:
git clone https://github.com/john-r-black/acpc.git
cd acpc

# Trixie ships without venv by default
sudo apt update
sudo apt install -y python3-venv python3-dev

python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Set timezone (Pi OS defaults to BST/UTC; church is in Houston TX = America/Chicago)
sudo timedatectl set-timezone America/Chicago

# Pre-fill the secrets that aren't church-LAN-specific:
cp secrets.yaml.example secrets.yaml
# Edit secrets.yaml and fill:
#   - tcc.username / tcc.password    (Honeywell admin@dpumc.org login)
#   - pco.app_id / pco.secret        (PCO API credentials)
#   - email.password                 (Gmail SMTP for alerts)
# Leave UniFi section blank — needs church-side token.
```

### Phase 2 — Move morning

1. **Power down cleanly** (don't yank the cord with a live SQLite DB):
   ```
   ssh acpc 'sudo poweroff'
   ```
2. **Unplug, transport to the church.**
3. **Confirm which VLAN the office switch port lands on** before plugging in. The Pi needs to be on a subnet that can reach the church UDM Pro at `192.168.1.1` (for door API calls). Most likely target: VLAN 1 (`192.168.1.0/24` — DPUMC Network) since that's where the UDM Pro / Access infrastructure lives.
4. **Plug into the switch and power on.**

### Phase 3 — Find it on the church network

ACPC won't show up at `192.168.3.10` anymore — that was a home-side dnsmasq reservation tied to its MAC. At the church, it'll get a DHCP lease from the church UDM Pro on whatever VLAN the port lands on.

Two ways to find the new IP:

- **From this Ubuntu box, via `unifi-mcp-church` MCP:** list clients on the default site, look for hostname `1421acpc` or MAC `88:a2:9e:a4:7a:ff`. This is the easiest because the MCP server is already running on the production Pi at home.
- **From a machine at the church:** `arp-scan -l` on the office subnet, look for the MAC.

### Phase 4 — SSH from home to the moved Pi

The home↔church IPsec site-to-site VPN is permanent and routes `192.168.1.0/24` and `10.1.10.0/24` between sites. From `1421home`:

```bash
ssh acpc@<new-church-ip>
```

Once the new IP is known, update the SSH alias in `~/.ssh/config`:

```
Host acpc
  HostName <new-church-ip>
  User acpc
  IdentityFile ~/.ssh/id_ed25519
  IdentitiesOnly yes
```

(Or add a second alias `acpc-church` and keep the old `acpc` for reference.)

### Phase 5 — Configure UniFi door API + first run

1. **Generate UniFi API token in the church Network controller:**
   - SSH-tunnel or local-LAN browser to `https://192.168.1.1`
   - Settings → Control Plane → Integrations → Create API Key
   - Copy the token (it's only shown once)
2. **Add to `secrets.yaml`:**
   ```yaml
   unifi:
     base_url: https://192.168.1.1
     api_token: "<paste-token-here>"
   ```
3. **First test run in shadow mode** (config default):
   ```bash
   cd ~/acpc && source venv/bin/activate && python main.py
   ```
   Watch `logs/facility.log`. Confirm:
   - PCO calendar events fetched
   - HVAC commands "would-be sent" make sense for current/upcoming events
   - Door commands "would-be sent" make sense
   - No tracebacks
4. **Run a few more cycles** (over an hour or two) to catch edge cases.
5. **Flip to live mode:**
   ```yaml
   # config.yaml
   shadow_mode: false
   ```
6. **Add cron entry** (run as `acpc`, not root):
   ```
   crontab -e
   # Add:
   */5 * * * * cd /home/acpc/acpc && /home/acpc/acpc/venv/bin/python main.py
   ```

### Phase 6 — Optional dashboard

`modules/dashboard.py` is a Flask app. If you want a local read-only status page:

- Wire it up as a `systemd` service (separate from the cron poller)
- Bind to `0.0.0.0:<port>` so it's reachable from other church-LAN machines, or `127.0.0.1` if access is via SSH tunnel only
- No public exposure needed unless trustees want to view from outside the church (in which case Cloudflare Tunnel + Cloudflare Access; defer until that requirement is real)

## Will ACPC need Cloudflare Tunnel at the church? **No.**

ACPC only initiates outbound traffic:
- PCO calendar (HTTPS → api.planningcenteronline.com)
- Honeywell TCC (HTTPS → mytotalconnectcomfort.com)
- UniFi Access door API (LAN → 192.168.1.1)
- Open-Meteo weather (HTTPS)
- Gmail SMTP (587)

No inbound endpoints — different from the production MCP Pi which serves Claude requests via `mcp.1421mcps.com`.

For remote admin access, the existing IPsec tunnel is sufficient. Add Cloudflare only if/when a public-dashboard requirement materializes.

## Things to flag before going live

1. **WiFi fallback won't work at church.** The reflash baked in SSID `1421me` (home WiFi). At the church there's no `1421me`, so wlan0 fails to connect. Fine if ethernet stays up — but if it drops, the Pi is offline. Either:
   - Disable `1421me` autoconnect (`sudo nmcli connection modify 1421me connection.autoconnect no`) and accept ethernet-only
   - Add the church AV/guest WiFi as a fallback NetworkManager connection
2. **Time zone matters for cron + HVAC schedules.** Set `America/Chicago` in Phase 1.
3. **`config.yaml` shadow_mode default is `true`** — protects you on first deploy. Don't flip to `false` until logs look right.
4. **Lockout enforcement during MWS / CrossOver sessions** is a hard safety requirement (see `CLAUDE.md`). The shadow-mode runs should explicitly verify lockout logic fires.
5. **Cron runs as `acpc` user, not root.** SQLite DB and logs live under `/home/acpc/acpc/`. If you need different paths, edit `config.yaml` and ensure permissions match.

## Useful references in the broader workspace

- `~/code_projects/acpc/CLAUDE.md` — architecture, interfaces, zone reference (start here)
- `~/code_projects/fred/references/pi-infrastructure.md` — full Pi fleet doc (production + ACPC)
- `~/code_projects/fred/references/2026-04-11_network_church.md` — church VLANs, IPsec tunnel, UniFi devices
- `~/code_projects/fred/references/2026-04-11_network_home.md` — home network, IPsec home side
- `~/code_projects/fred/references/remote-access.md` — RustDesk + IPsec tunnel patterns

## When this file's job is done

After ACPC is live and stable, this `next_steps.md` is dated. Either delete it or move it to `resources/archive/` with a date suffix.
