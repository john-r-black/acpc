"""Safety-lockout tests for modules/doors_unifi.py.

These tests exercise compute_door_plan against the real mapping.yaml so we are
proving the policy with production UniFi IDs. Run from the project root:

    python -m unittest tests.test_doors_unifi
"""

import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

from modules.calendar_pco import Event
from modules.doors_unifi import compute_door_plan


PROJECT_ROOT = Path(__file__).resolve().parent.parent


def load_mapping() -> dict:
    with open(PROJECT_ROOT / "mapping.yaml") as f:
        return yaml.safe_load(f)


def load_config() -> dict:
    with open(PROJECT_ROOT / "config.yaml") as f:
        return yaml.safe_load(f)


MAPPING = load_mapping()
CONFIG = load_config()

DOOR_IDS = {key: spec["unifi_id"] for key, spec in MAPPING["doors"].items()}

MWS_PROTECTED = {
    DOOR_IDS["mws_interior"],
    DOOR_IDS["mws_front"],
    DOOR_IDS["front_interior"],
    DOOR_IDS["fp_interior"],
    DOOR_IDS["flc_gym"],
    DOOR_IDS["flc_back"],
    DOOR_IDS["concourse"],
    DOOR_IDS["mws_back"],
}
CROSSOVER_PROTECTED = {
    DOOR_IDS["flc_gym"],
    DOOR_IDS["flc_back"],
    DOOR_IDS["concourse"],
}

NOW = datetime(2026, 4, 20, 18, 0, tzinfo=timezone.utc)


def event(name: str, rooms: list[str], start=NOW, end=NOW + timedelta(hours=1),
          event_id: str | None = None) -> Event:
    return Event(
        id=event_id or f"evt-{name}",
        name=name,
        start=start,
        end=end,
        rooms=rooms,
    )


class NoEventsTests(unittest.TestCase):
    def test_no_events_leaves_exterior_locked_and_interior_unlocked(self):
        plan = compute_door_plan([], NOW, CONFIG, MAPPING)
        self.assertFalse(plan.mws_active)
        self.assertFalse(plan.crossover_active)
        self.assertFalse(plan.conflicts)

        # Event-triggered exterior doors default locked
        self.assertIn(DOOR_IDS["front_exterior"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["east_door"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["flc_gym"], plan.doors_to_lock)

        # Interior doors default unlocked
        self.assertIn(DOOR_IDS["front_interior"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["fp_interior"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["mws_interior"], plan.doors_to_unlock)

        # Staff-only + schedule-only doors are untouched when no lockouts active
        self.assertNotIn(DOOR_IDS["concourse"], plan.doors_to_lock)
        self.assertNotIn(DOOR_IDS["concourse"], plan.doors_to_unlock)
        self.assertNotIn(DOOR_IDS["flc_closets"], plan.doors_to_lock)
        self.assertNotIn(DOOR_IDS["flc_closets"], plan.doors_to_unlock)
        self.assertNotIn(DOOR_IDS["mws_back"], plan.doors_to_lock)
        self.assertNotIn(DOOR_IDS["mws_back"], plan.doors_to_unlock)
        self.assertNotIn(DOOR_IDS["mws_front"], plan.doors_to_lock)
        self.assertNotIn(DOOR_IDS["mws_front"], plan.doors_to_unlock)


class NormalEventTests(unittest.TestCase):
    def test_food_pantry_event_unlocks_fp_exterior(self):
        events = [event("Food Pantry Distribution", ["Food Pantry - 107"])]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["front_exterior"], plan.doors_to_lock)
        self.assertFalse(plan.conflicts)

    def test_sanctuary_event_unlocks_east_and_front_exterior(self):
        events = [event("Sunday Service", ["Sanctuary"])]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["east_door"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["front_exterior"], plan.doors_to_unlock)
        self.assertNotIn(DOOR_IDS["fp_exterior"], plan.doors_to_unlock)

    def test_gym_event_unlocks_flc_gym_when_no_lockout(self):
        events = [event("Basketball Practice", ["Gym - F104"])]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["flc_gym"], plan.doors_to_unlock)
        self.assertFalse(plan.conflicts)


class BufferWindowTests(unittest.TestCase):
    def test_unlock_starts_15_minutes_before_event(self):
        start = NOW + timedelta(minutes=14)
        events = [event("Soon", ["Food Pantry - 107"], start=start,
                        end=start + timedelta(hours=1))]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_unlock)

    def test_event_still_too_far_out_stays_locked(self):
        start = NOW + timedelta(minutes=30)
        events = [event("Later", ["Food Pantry - 107"], start=start,
                        end=start + timedelta(hours=1))]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_lock)

    def test_event_just_ended_still_in_buffer(self):
        start = NOW - timedelta(hours=1)
        end = NOW - timedelta(minutes=10)
        events = [event("Recent", ["Food Pantry - 107"], start=start, end=end)]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_unlock)

    def test_event_past_buffer_locks_back_down(self):
        start = NOW - timedelta(hours=2)
        end = NOW - timedelta(minutes=20)
        events = [event("Older", ["Food Pantry - 107"], start=start, end=end)]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_lock)


class MWSLockoutTests(unittest.TestCase):
    def _mws_event(self):
        return event("MWS - Midweek Studies", ["MWS - 129-137"])

    def test_mws_active_locks_protected_doors(self):
        plan = compute_door_plan([self._mws_event()], NOW, CONFIG, MAPPING)
        self.assertTrue(plan.mws_active)
        for did in MWS_PROTECTED:
            self.assertIn(did, plan.doors_to_lock,
                          f"expected {did} locked under MWS")

    def test_mws_does_not_affect_exterior_doors_outside_lockout_set(self):
        plan = compute_door_plan([self._mws_event()], NOW, CONFIG, MAPPING)
        # Exterior doors without mws_lockout should still default locked
        # (no events want them) but not because of MWS
        self.assertIn(DOOR_IDS["front_exterior"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["east_door"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_lock)

    def test_mws_interior_doors_are_locked_not_unlocked(self):
        plan = compute_door_plan([self._mws_event()], NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["front_interior"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["fp_interior"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["mws_interior"], plan.doors_to_lock)

    def test_mws_front_locks_under_mws(self):
        plan = compute_door_plan([self._mws_event()], NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["mws_front"], plan.doors_to_lock)
        self.assertNotIn(DOOR_IDS["mws_front"], plan.doors_to_unlock)

    def test_mws_back_locks_under_mws(self):
        plan = compute_door_plan([self._mws_event()], NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["mws_back"], plan.doors_to_lock)

    def test_concourse_locks_under_mws_as_flc_back_partner(self):
        plan = compute_door_plan([self._mws_event()], NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["concourse"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["flc_back"], plan.doors_to_lock)

    def test_mws_pattern_match_is_case_insensitive_substring(self):
        e = event("Midweek Studies Week 5", ["MWS - 129-137"])
        plan = compute_door_plan([e], NOW, CONFIG, MAPPING)
        self.assertTrue(plan.mws_active)


class CrossOverLockoutTests(unittest.TestCase):
    def _crossover_event(self):
        return event("CrossOver Kids", ["Gym - F104"])

    def test_crossover_active_locks_flc_doors(self):
        plan = compute_door_plan([self._crossover_event()], NOW, CONFIG, MAPPING)
        self.assertTrue(plan.crossover_active)
        for did in CROSSOVER_PROTECTED:
            self.assertIn(did, plan.doors_to_lock,
                          f"expected {did} locked under CrossOver")

    def test_crossover_does_not_lock_interior_doors(self):
        plan = compute_door_plan([self._crossover_event()], NOW, CONFIG, MAPPING)
        # Interior doors have crossover_lockout=false, so they remain default-unlocked
        self.assertIn(DOOR_IDS["front_interior"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["fp_interior"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["mws_interior"], plan.doors_to_unlock)

    def test_crossover_does_not_lock_mws_back(self):
        plan = compute_door_plan([self._crossover_event()], NOW, CONFIG, MAPPING)
        # mws_back has mws_lockout=true, crossover_lockout=false
        self.assertNotIn(DOOR_IDS["mws_back"], plan.doors_to_lock)

    def test_crossover_locks_concourse_as_flc_back_partner(self):
        plan = compute_door_plan([self._crossover_event()], NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["concourse"], plan.doors_to_lock)
        self.assertIn(DOOR_IDS["flc_back"], plan.doors_to_lock)

    def test_crossover_conflict_with_gym_booking(self):
        # CrossOver event AND a separate gym booking active at the same time
        events = [
            self._crossover_event(),
            event("Basketball Rental", ["Gym - F104"], event_id="evt-conflict"),
        ]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        # FLC Gym must stay locked, and the conflict must be reported
        self.assertIn(DOOR_IDS["flc_gym"], plan.doors_to_lock)
        self.assertNotIn(DOOR_IDS["flc_gym"], plan.doors_to_unlock)
        self.assertTrue(any(c["door_id"] == DOOR_IDS["flc_gym"]
                            for c in plan.conflicts))


class CombinedLockoutTests(unittest.TestCase):
    def test_both_lockouts_union(self):
        events = [
            event("MWS Week 3", ["MWS - 129-137"]),
            event("CrossOver", ["Gym - F104"]),
        ]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertTrue(plan.mws_active)
        self.assertTrue(plan.crossover_active)
        for did in MWS_PROTECTED | CROSSOVER_PROTECTED:
            self.assertIn(did, plan.doors_to_lock)


class LockoutTimingTests(unittest.TestCase):
    def test_mws_ended_no_longer_locks(self):
        ended = event(
            "MWS",
            ["MWS - 129-137"],
            start=NOW - timedelta(hours=3),
            end=NOW - timedelta(hours=1),
        )
        plan = compute_door_plan([ended], NOW, CONFIG, MAPPING)
        self.assertFalse(plan.mws_active)
        # Interior doors should be back to default-unlocked
        self.assertIn(DOOR_IDS["front_interior"], plan.doors_to_unlock)
        self.assertIn(DOOR_IDS["mws_interior"], plan.doors_to_unlock)

    def test_mws_scheduled_but_not_active_does_not_lock(self):
        upcoming = event(
            "MWS",
            ["MWS - 129-137"],
            start=NOW + timedelta(hours=2),
            end=NOW + timedelta(hours=4),
        )
        plan = compute_door_plan([upcoming], NOW, CONFIG, MAPPING)
        self.assertFalse(plan.mws_active)
        self.assertIn(DOOR_IDS["front_interior"], plan.doors_to_unlock)


class ConflictDetectionTests(unittest.TestCase):
    def test_mws_event_with_exterior_door_unlock_no_conflict(self):
        """The MWS event itself books MWS-129-137 which doesn't map to any
        locked-out door (mws_interior isn't in the pco_room_to_doors map),
        so the MWS event shouldn't produce a conflict against its own rooms."""
        events = [event("MWS", ["MWS - 129-137"])]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertFalse(plan.conflicts)

    def test_mws_plus_food_pantry_no_conflict_fp_exterior_still_unlocks(self):
        # FP Exterior is not lockout-protected, so a concurrent food pantry
        # event under MWS should still unlock fp_exterior without conflict
        events = [
            event("MWS", ["MWS - 129-137"]),
            event("Food Pantry", ["Food Pantry - 107"]),
        ]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        self.assertIn(DOOR_IDS["fp_exterior"], plan.doors_to_unlock)
        self.assertFalse(plan.conflicts)

    def test_crossover_plus_youth_event_conflicts_on_flc_gym(self):
        events = [
            event("CrossOver", ["Gym - F104"]),
            event("Youth Group", ["Youth Room - F101"], event_id="evt-youth"),
        ]
        plan = compute_door_plan(events, NOW, CONFIG, MAPPING)
        # FLC Gym stays locked, youth-group booking reports a conflict
        self.assertIn(DOOR_IDS["flc_gym"], plan.doors_to_lock)
        conflict_events = [c["event_id"] for c in plan.conflicts]
        self.assertIn("evt-youth", conflict_events)


class PartnerDoorTests(unittest.TestCase):
    """Partner doors lock and unlock together — their lockout flags must match."""

    def test_partner_pointers_are_reciprocal(self):
        doors = MAPPING["doors"]
        for key, spec in doors.items():
            partner = spec.get("partner_of")
            if not partner:
                continue
            self.assertIn(partner, doors, f"{key} points at missing partner {partner}")
            self.assertEqual(
                doors[partner].get("partner_of"),
                key,
                f"{key} ↔ {partner} partner link is not reciprocal",
            )

    def test_partners_share_lockout_flags(self):
        doors = MAPPING["doors"]
        for key, spec in doors.items():
            partner = spec.get("partner_of")
            if not partner:
                continue
            pspec = doors[partner]
            self.assertEqual(
                spec.get("mws_lockout"),
                pspec.get("mws_lockout"),
                f"{key}/{partner} mws_lockout flags disagree",
            )
            self.assertEqual(
                spec.get("crossover_lockout"),
                pspec.get("crossover_lockout"),
                f"{key}/{partner} crossover_lockout flags disagree",
            )


class MappingIntegrityTests(unittest.TestCase):
    def test_twelve_doors_configured(self):
        # 12 hubs are active on the church Access system (as of 2026-04-20)
        self.assertEqual(len(MAPPING["doors"]), 12)

    def test_all_door_ids_are_unique(self):
        ids = [spec["unifi_id"] for spec in MAPPING["doors"].values()]
        self.assertEqual(len(ids), len(set(ids)))


if __name__ == "__main__":
    unittest.main()
