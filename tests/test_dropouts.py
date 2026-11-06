"""DropoutTracker: the pure bookkeeping behind the per-pump Last contact
and Dropouts diagnostic sensors. One dropout = one offline *episode*
(counted when it starts), however many failed polls it spans; the recent
log holds completed episodes with durations."""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custom_components.jebao_local.dropouts import MAX_EVENTS, DropoutTracker

T0 = datetime(2026, 11, 5, 12, 0, 0, tzinfo=timezone.utc)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_success_only_tracks_last_contact() -> None:
    t = DropoutTracker()
    t.record_success(at(0))
    t.record_success(at(10))
    assert t.last_contact == at(10)
    assert t.count == 0
    assert t.offline_since is None
    assert t.events == []


def test_one_episode_spanning_many_failed_polls_counts_once() -> None:
    t = DropoutTracker()
    t.record_success(at(0))
    for s in (10, 20, 30, 40):
        t.record_failure(at(s))
    assert t.count == 1
    assert t.offline_since == at(10)
    assert t.events == []  # not completed yet
    assert t.last_contact == at(0)  # unchanged while offline


def test_recovery_completes_the_episode_with_duration() -> None:
    t = DropoutTracker()
    t.record_success(at(0))
    t.record_failure(at(10))
    t.record_failure(at(20))
    t.record_success(at(130))
    assert t.count == 1
    assert t.offline_since is None
    assert t.last_contact == at(130)
    assert t.events == [
        {"start": at(10).isoformat(), "end": at(130).isoformat(), "duration_s": 120,
         "kind": "unknown"}
    ]


def test_second_episode_counts_again() -> None:
    t = DropoutTracker()
    t.record_success(at(0))
    t.record_failure(at(10))
    t.record_success(at(20))
    t.record_failure(at(30))
    t.record_success(at(40))
    assert t.count == 2
    assert len(t.events) == 2


def test_offline_from_boot_counts_as_a_dropout() -> None:
    t = DropoutTracker()
    t.record_failure(at(0))
    assert t.count == 1
    assert t.offline_since == at(0)
    assert t.last_contact is None


def test_event_log_is_trimmed() -> None:
    t = DropoutTracker()
    for i in range(MAX_EVENTS + 7):
        t.record_failure(at(i * 20))
        t.record_success(at(i * 20 + 10))
    assert t.count == MAX_EVENTS + 7
    assert len(t.events) == MAX_EVENTS
    # Oldest were dropped: the first surviving event is episode 8.
    assert t.events[0]["start"] == at(7 * 20).isoformat()


def test_seed_restores_only_a_blank_tracker() -> None:
    t = DropoutTracker()
    events = [{"start": at(0).isoformat(), "end": at(5).isoformat(), "duration_s": 5}]
    t.seed(3, events, at(5), None)
    assert t.count == 3
    assert t.events == events
    assert t.last_contact == at(5)
    # A live tracker refuses the seed.
    t.record_failure(at(100))
    t.seed(99, [], None, None)
    assert t.count == 4
    assert t.offline_since == at(100)


def test_seed_with_open_episode_closes_on_next_success() -> None:
    t = DropoutTracker()
    t.seed(1, [], at(0), at(10))
    t.record_success(at(70))
    assert t.count == 1
    assert t.events[-1]["duration_s"] == 60
    assert t.offline_since is None


def test_attributes_shape() -> None:
    t = DropoutTracker()
    t.record_success(at(0))
    t.record_failure(at(10))
    attrs = t.as_attributes()
    assert attrs["last_contact"] == at(0).isoformat()
    assert attrs["offline_since"] == at(10).isoformat()
    assert attrs["recent"] == []


# --- restoring across a Home Assistant restart -----------------------------
# The coordinator's first refresh runs in async_setup_entry, *before* the
# platforms are forwarded - so by the time the Dropouts sensor restores its
# state, the tracker has already recorded that first poll. These pin the
# merge, because a seed that bails out here loses the whole history.


def test_restore_survives_the_first_poll_that_lands_before_it() -> None:
    t = DropoutTracker()
    t.record_success(at(100))  # the config-entry first refresh
    t.seed(count=7, events=[{"start": "x", "end": "y", "duration_s": 5}],
           last_contact=at(0), offline_since=None)
    assert t.count == 7, "restored dropout history was discarded"
    assert len(t.events) == 1
    assert t.last_contact == at(100), "the newer live contact should win"


def test_an_outage_still_running_across_a_restart_is_not_counted_twice() -> None:
    t = DropoutTracker()
    t.record_failure(at(100))  # first poll after the restart: still down
    t.seed(count=3, events=[], last_contact=at(0), offline_since=at(50))
    assert t.count == 3, "the same episode was counted again after the restart"
    assert t.offline_since == at(50), "the outage should keep its original start"
    assert t.last_contact == at(0)

    # ...and when it finally recovers, the duration spans the restart.
    t.record_success(at(200))
    assert t.events == [{"start": at(50).isoformat(), "end": at(200).isoformat(), "duration_s": 150,
                         "kind": "unknown"}]


def test_an_outage_that_ended_during_the_restart_is_logged() -> None:
    t = DropoutTracker()
    t.record_success(at(100))  # back already by the first poll
    t.seed(count=2, events=[], last_contact=at(0), offline_since=at(50))
    assert t.count == 2
    assert t.offline_since is None
    assert t.events == [{"start": at(50).isoformat(), "end": at(100).isoformat(), "duration_s": 50}]


def test_a_pump_down_at_boot_adds_to_the_restored_count() -> None:
    t = DropoutTracker()
    t.record_failure(at(100))  # a genuinely new outage
    t.seed(count=4, events=[], last_contact=at(0), offline_since=None)
    assert t.count == 5
    assert t.offline_since == at(100)


def test_seeding_twice_keeps_the_first_restore() -> None:
    t = DropoutTracker()
    t.record_success(at(100))
    t.seed(count=7, events=[], last_contact=at(0), offline_since=None)
    t.seed(count=999, events=[], last_contact=at(0), offline_since=None)
    assert t.count == 7


# --- latency and failure kind ----------------------------------------------


def test_latency_is_recorded_and_kept_between_polls() -> None:
    t = DropoutTracker()
    t.record_success(at(0), latency_ms=112)
    assert t.last_latency_ms == 112
    t.record_success(at(30))  # a caller that didn't measure
    assert t.last_latency_ms == 112, "an unmeasured poll should not wipe it"


def test_the_failure_kind_is_kept_for_the_current_outage_and_cleared_on_recovery() -> None:
    t = DropoutTracker()
    t.record_success(at(0), latency_ms=100)
    t.record_failure(at(30), "no_response")
    assert t.failure_kind == "no_response"
    t.record_success(at(60), latency_ms=120)
    assert t.failure_kind is None


def test_an_outage_takes_the_most_recent_failure_kind() -> None:
    """A pump can stop answering TCP first and only later fall off the
    network entirely - the later reading is the useful one."""
    t = DropoutTracker()
    t.record_success(at(0))
    t.record_failure(at(30), "no_response")
    t.record_failure(at(60), "off_network")
    assert t.count == 1, "still one episode"
    assert t.failure_kind == "off_network"
    t.record_success(at(90))
    assert t.events[-1]["kind"] == "off_network"


def test_attributes_expose_latency_and_last_failure() -> None:
    t = DropoutTracker()
    t.record_success(at(0), latency_ms=98)
    t.record_failure(at(30), "off_network")
    a = t.as_attributes()
    assert a["latency_ms"] == 98
    assert a["last_failure"] == "off_network"
