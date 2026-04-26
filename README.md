# DPUMC Facility Automation

Custom Python-based facility automation for Deer Park United Methodist Church, replacing the Events2HVAC SaaS subscription. Runs as a stateless cron-driven poller on a Raspberry Pi 4.

See `CLAUDE.md` for architecture, interfaces, zone/door reference, and rollout phases.

## Setup

1. `pip install -r requirements.txt`
2. Copy `secrets.yaml.example` to `secrets.yaml` and fill in credentials.
3. Review `config.yaml` and `mapping.yaml`.
4. Run `python main.py` manually to verify, then schedule via cron.
