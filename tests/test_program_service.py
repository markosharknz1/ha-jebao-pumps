"""Service-level tests for set_program / clear_program.

These drive the real registered handlers (captured from a fake service
registry) rather than the compiler underneath, because the things most
likely to go wrong live here and not in the arithmetic: leaving a previous
program's slots behind so two schedules fight, or writing a perfect
program onto a pump whose timer is switched off so none of it runs.

`_resolve_coordinator` is patched out - resolving a device_id needs a real
Home Assistant device registry, and it isn't what's under test.
"""
import sys
from pathlib import Path

import pytest

pytest.importorskip("homeassistant")

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

from custom_components.jebao_local import services as services_mod  # noqa: E402
from custom_components.jebao_local.jebao_gizwits.schedule import (  # noqa: E402
    SLOT_COUNT,
    decode_slot,
    encode_slot,
    slot_attr_name,
)
from custom_components.jebao_local.jebao_gizwits.schema import load_by_product_key  # noqa: E402

WAVEMAKER_PRODUCT_KEY = "54114ccdac1e41c0bb17e222887c07ba"


class _Call:
    def __init__(self, **data):
        self.data = data


class _FakeCoordinator:
    def __init__(self):
        self.schema = load_by_product_key(WAVEMAKER_PRODUCT_KEY)
        self.did = "aabbcc"
        # A real pump always reports a current mode; steps that don't name
        # one inherit it by label (see services._live_mode_label).
        self.data = {"Mode": 0}  # base wavemaker: 0 = classic wave
        self.writes = []

    async def async_write(self, values):
        self.writes.append(values)
        self.data.update(values)

    @property
    def written(self):
        """Every attribute written, flattened across batches."""
        out = {}
        for batch in self.writes:
            out.update(batch)
        return out


class _FakeServices:
    def __init__(self):
        self.handlers = {}

    def async_register(self, domain, name, handler, schema=None):
        self.handlers[name] = (handler, schema)


class _FakeHass:
    def __init__(self):
        self.data = {}
        self.services = _FakeServices()


@pytest.fixture
def call_service(monkeypatch):
    hass = _FakeHass()
    coordinator = _FakeCoordinator()
    monkeypatch.setattr(services_mod, "_resolve_coordinator", lambda *_a, **_k: coordinator)
    services_mod.async_register_services(hass)

    async def _call(name, **data):
        handler, schema = hass.services.handlers[name]
        await handler(_Call(**(schema(data) if schema else data)))

    _call.coordinator = coordinator
    return _call


def _timer_attr(coordinator):
    return services_mod._timer_switch_attr(coordinator)


@pytest.mark.asyncio
async def test_a_two_step_program_lands_in_consecutive_slots(call_service):
    coordinator = call_service.coordinator
    await call_service(
        "set_program",
        device_id="d1",
        steps=[{"speed": 100, "hours": 4}, {"speed": 40, "hours": 6}],
        start="08:00",
    )

    written = coordinator.written
    first = decode_slot(0, written[slot_attr_name(0)])
    second = decode_slot(1, written[slot_attr_name(1)])
    assert (first.start_hour, first.end_hour, first.flow) == (8, 12, 100)
    assert (second.start_hour, second.end_hour, second.flow) == (12, 18, 40)


@pytest.mark.asyncio
async def test_a_previous_programs_leftover_slots_are_cleared(call_service):
    coordinator = call_service.coordinator
    # Pretend the pump already holds a five-period program.
    for index in range(5):
        coordinator.data[slot_attr_name(index)] = encode_slot(
            index, 0, index + 1, 0, 1, 50, slot_len=8
        )

    await call_service(
        "set_program", device_id="d1", steps=[{"speed": 100, "hours": 2}]
    )

    written = coordinator.written
    # Slot 0 is the new program; 1-4 must be blanked, or the old periods
    # keep firing alongside it.
    assert decode_slot(0, written[slot_attr_name(0)]).flow == 100
    for index in range(1, 5):
        assert decode_slot(index, written[slot_attr_name(index)]) is None


@pytest.mark.asyncio
async def test_slots_that_were_already_empty_are_not_rewritten(call_service):
    coordinator = call_service.coordinator
    await call_service(
        "set_program", device_id="d1", steps=[{"speed": 100, "hours": 2}]
    )
    slot_writes = [k for k in coordinator.written if k.startswith("AutoTime")]
    assert slot_writes == [slot_attr_name(0)]  # not all 48


@pytest.mark.asyncio
async def test_the_pumps_timer_is_switched_on_so_the_program_actually_runs(call_service):
    coordinator = call_service.coordinator
    attr = _timer_attr(coordinator)
    assert attr is not None, "this product should have a timer master switch"
    coordinator.data[attr] = False

    await call_service("set_program", device_id="d1", steps=[{"speed": 100, "hours": 2}])

    assert coordinator.written.get(attr) is True


@pytest.mark.asyncio
async def test_an_already_enabled_timer_is_not_rewritten(call_service):
    coordinator = call_service.coordinator
    attr = _timer_attr(coordinator)
    coordinator.data[attr] = True

    await call_service("set_program", device_id="d1", steps=[{"speed": 100, "hours": 2}])

    assert attr not in coordinator.written


@pytest.mark.asyncio
async def test_writes_are_batched_rather_than_one_round_trip_per_slot(call_service):
    coordinator = call_service.coordinator
    await call_service(
        "set_program",
        device_id="d1",
        steps=[{"speed": 100, "minutes": 60}, {"speed": 40, "minutes": 60}],
        repeat=True,
    )
    slot_count = len([k for k in coordinator.written if k.startswith("AutoTime")])
    assert slot_count == 24
    # 24 slots in batches of 6, not 24 separate commands.
    assert len(coordinator.writes) < slot_count


@pytest.mark.asyncio
async def test_a_step_can_name_its_mode_instead_of_numbering_it(call_service):
    coordinator = call_service.coordinator
    await call_service(
        "set_program",
        device_id="d1",
        steps=[{"speed": 100, "hours": 2, "mode": "constant flow"}],
    )
    written = decode_slot(0, coordinator.written[slot_attr_name(0)])
    assert written.mode == 4  # base wavemaker: 4 = constant flow


@pytest.mark.asyncio
async def test_an_unknown_mode_name_is_refused_and_lists_the_real_ones(call_service):
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError, match="no mode called"):
        await call_service(
            "set_program",
            device_id="d1",
            steps=[{"speed": 100, "hours": 2, "mode": "turbo"}],
        )


@pytest.mark.asyncio
async def test_a_step_with_no_length_is_refused(call_service):
    from homeassistant.exceptions import ServiceValidationError

    with pytest.raises(ServiceValidationError, match="needs a length"):
        await call_service("set_program", device_id="d1", steps=[{"speed": 100}])


@pytest.mark.asyncio
async def test_clear_program_blanks_every_slot_that_held_something(call_service):
    coordinator = call_service.coordinator
    for index in range(3):
        coordinator.data[slot_attr_name(index)] = encode_slot(
            index, 0, index + 1, 0, 1, 50, slot_len=8
        )

    await call_service("clear_program", device_id="d1")

    written = coordinator.written
    assert len(written) == 3
    assert all(decode_slot(i, written[slot_attr_name(i)]) is None for i in range(3))


@pytest.mark.asyncio
async def test_clearing_an_already_empty_schedule_writes_nothing(call_service):
    coordinator = call_service.coordinator
    await call_service("clear_program", device_id="d1")
    assert coordinator.writes == []


# --- the mode-numbering trap ------------------------------------------------
# A slot's mode byte is not numbered like the live Mode attribute. On a base
# wavemaker, live Mode 0 is "classic wave" but slot mode 0 is STOP - so a
# naive copy of the number would program the pump to stop. These pin that.


@pytest.mark.asyncio
@pytest.mark.parametrize("live_mode,slot_mode", [(0, 1), (1, 2), (2, 3), (3, 4)])
async def test_an_inherited_mode_is_carried_by_name_not_by_number(call_service, live_mode, slot_mode):
    coordinator = call_service.coordinator
    coordinator.data["Mode"] = live_mode

    await call_service("set_program", device_id="d1", steps=[{"speed": 100, "hours": 2}])

    written = decode_slot(0, coordinator.written[slot_attr_name(0)])
    assert written.mode == slot_mode
    assert written.mode != 0, "slot mode 0 is stop - the pump would halt"


@pytest.mark.asyncio
async def test_no_live_mode_and_several_choices_asks_rather_than_guesses(call_service):
    from homeassistant.exceptions import ServiceValidationError

    call_service.coordinator.data.pop("Mode")
    with pytest.raises(ServiceValidationError, match="give each step a mode"):
        await call_service("set_program", device_id="d1", steps=[{"speed": 100, "hours": 2}])


@pytest.mark.asyncio
async def test_naming_every_mode_works_even_without_a_live_mode(call_service):
    coordinator = call_service.coordinator
    coordinator.data.pop("Mode")
    await call_service(
        "set_program", device_id="d1",
        steps=[{"speed": 100, "hours": 2, "mode": "sine wave"}],
    )
    assert decode_slot(0, coordinator.written[slot_attr_name(0)]).mode == 2
