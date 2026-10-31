"""Accepted clock carry matches the independent full reducer; no processes."""
from copy import deepcopy

import pytest

from scidiscovery.research_loop import accepted_budget as accepted, budget


def stamp(uptime, *, utc=None, boot='A'):
    return dict(at_unix=1000+uptime if utc is None else utc, at_boottime=uptime, boot_id=boot)


def event(seq, uptime, *, phase=None, boot='A', utc=None, observed=0., reanchor=False):
    value = dict(stamp(uptime, utc=utc, boot=boot), seq=seq,
        kind='budget_amend' if phase is None else 'clock',
        uptime_accounting=True, observed_advance_seconds=observed)
    if phase is not None:
        value['phase'] = phase
    if reanchor:
        value['reanchor'] = dict(version=1, checkpoint={}, transfer={})
    return value


@pytest.mark.parametrize('rows,now,floor', [
    ([event(1,11,phase='advance'), event(2,13,observed=2.),
        event(3,15,phase='waiting',observed=4.), event(4,17,phase='paused',observed=4.)], stamp(20), None),
    ([event(1,11,phase='advance'), event(2,13,utc=900,observed=2.)], stamp(19,utc=850), None),
    ([event(1,11,phase='advance'), event(2,15,phase='paused',observed=4.),
        event(3,3,boot='B',utc=1100,phase='paused',observed=4.,reanchor=True)], stamp(6,boot='B',utc=1103), None),
    ([event(1,11,phase='advance'), event(2,13,observed=2.),
        event(3,3,boot='B',utc=1100,phase='paused',observed=7.)], stamp(5,boot='B',utc=1102), 7.),
])
def test_incremental_carry_preserves_clock_compensations(rows, now, floor):
    start = stamp(10)
    carry = accepted._clock(start, 'active')
    if floor is not None:
        carry.update(floor_boot='A', floor=floor)
    for row in rows:
        carry = accepted.clock_step(carry, row)
    reference = budget._clock_durations(start, rows, now, basis='active',
        advance_floors={} if floor is None else dict(A=floor))
    measured = accepted.clock_sample(carry, now)
    assert measured[0] == pytest.approx(reference[0])
    assert measured[1:] == reference[1:]
    before = deepcopy(carry)
    accepted.clock_sample(carry, now)
    assert carry == before, 'Sampling cannot mutate accepted consumption'


def test_new_clock_refuses_same_boot_backwards_uptime():
    carry = accepted.clock_step(accepted._clock(stamp(10), 'active'), event(1,11,phase='advance'))
    with pytest.raises(ValueError, match='backwards'):
        accepted.clock_sample(carry, stamp(9))


def test_elapsed_clock_retains_utc_rollback_refusal():
    carry = accepted._clock(stamp(10), 'elapsed')
    row = dict(stamp(11), seq=1, kind='clock', phase='advance')
    carry = accepted.clock_step(carry, row)
    with pytest.raises(ValueError, match='backwards'):
        accepted.clock_sample(carry, stamp(12,utc=900))
