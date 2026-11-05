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
        {"start": at(10).isoformat(), "end": at(130).isoformat(), "duration_s": 120}
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
