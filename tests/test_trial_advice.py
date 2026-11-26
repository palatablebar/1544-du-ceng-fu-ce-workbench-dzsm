from dataclasses import replace

import pytest

from platebath.deposit_factors.trial import (
    ADOPT, APPROVED, ELIMINATED, MAINTAIN, NO_OPTION, PENDING,
    Scenario, improves, label, resolve,
)
from platebath.pipeline import execute, open_fixture, preview


def scenario(case='换小电流档并减时', work=2, estimate=2.0, lower=1.0, upper=1.5,
             out_of_training=False, hard_pause=False):
    return Scenario(case, work, estimate, lower, upper, out_of_training, hard_pause)


def test_improvement_requires_upper_below_original_lower():
    assert improves(scenario(upper=1.4), 1.5)
    assert not improves(scenario(upper=1.5), 1.5)
    assert not improves(scenario(upper=1.6), 1.5)
    assert not improves(scenario(upper=None), 1.5)
    assert not improves(scenario(), None)


def test_rounding_cannot_flip_a_marginal_call():
    # Two boundaries printed at four decimals are equal, but the unrounded
    # comparison must still decide strictly.
    base_lower = 1.893787403
    better = scenario(upper=1.8937874029)
    tie = scenario(upper=base_lower)
    assert round(better.upper, 4) == round(base_lower, 4)
    assert improves(better, base_lower)
    assert not improves(tie, base_lower)
    assert resolve((tie,), base_lower=base_lower, calibrated=True).status == MAINTAIN
    adopted = resolve((better,), base_lower=base_lower, calibrated=True)
    assert adopted.status == ADOPT and adopted.case == better.case


def test_cheapest_confirmed_improvement_wins():
    costly = scenario(case='重调酸碱换滤芯', work=3, upper=1.2)
    cheap = scenario(case='换小电流档并减时', work=1, upper=1.4)
    stalled = scenario(case='降温试镀延长', work=0, upper=1.9)
    outcome = resolve((costly, stalled, cheap), base_lower=1.5, calibrated=True)
    assert outcome.status == ADOPT
    assert (outcome.case, outcome.work) == (cheap.case, cheap.work)


def test_cheapest_is_not_adopted_until_interval_improves():
    point_winner = scenario(case='换小电流档并减时', work=0, estimate=.4, upper=1.9)
    real_winner = scenario(case='重调酸碱换滤芯', work=3, estimate=1.4, upper=1.2)
    outcome = resolve((point_winner, real_winner), base_lower=1.5, calibrated=True)
    assert (outcome.status, outcome.case) == (ADOPT, real_winner.case)
    assert label(point_winner, 1.5, True)[0] == '未改善'


def test_missing_calibration_is_pending_not_adopted():
    outcome = resolve((scenario(upper=1.0),), base_lower=None, calibrated=False)
    assert outcome.status == PENDING and outcome.case is None
    assert label(scenario(upper=1.0), None, False)[0] == PENDING


def test_out_of_training_and_hard_pause_are_eliminated_before_interval():
    outside = scenario(upper=.1, out_of_training=True)
    overcurrent = scenario(upper=.1, hard_pause=True)
    assert label(outside, 1.5, True) == (ELIMINATED, '调整后超出训练范围')
    assert label(overcurrent, 1.5, True) == (ELIMINATED, '调整后触发超流暂停')
    # Even a missing-calibration row cannot keep an impossible adjustment.
    assert resolve((outside,), base_lower=None, calibrated=False).status == ELIMINATED
    mixed = resolve((outside, scenario(case='换小电流档并减时', upper=1.0)), base_lower=1.5, calibrated=True)
    assert mixed.status == ADOPT


def test_no_candidate_keeps_the_original_bench_plan():
    assert resolve((), base_lower=1.5, calibrated=True).status == NO_OPTION
    keep = scenario(upper=1.8)
    assert resolve((keep,), base_lower=1.5, calibrated=True).status == MAINTAIN


def test_resolution_does_not_depend_on_candidate_order():
    first = scenario(case='换小电流档并减时', work=2, upper=1.2)
    second = scenario(case='重调酸碱换滤芯', work=2, upper=1.3)
    forward = resolve((first, second), base_lower=1.5, calibrated=True)
    backward = resolve((second, first), base_lower=1.5, calibrated=True)
    assert forward == backward


def test_every_ticket_has_queryable_trial_advice():
    report = preview()
    stage = report['sections'][7]
    signed = [item for item in stage['records'] if '签认单' in item]
    assert {item['签认单'] for item in signed} == {row['key'] for row in report['rows']}
    for row in report['rows']:
        advice = row['trial_advice']
        assert advice['试镀建议'] in {ADOPT, PENDING, ELIMINATED, MAINTAIN, NO_OPTION}
        assert advice['理由']
        matching = next(item for item in signed if item['签认单'] == row['key'])
        assert matching['理由'] == advice['理由']
        for study in row['joint_experiments']:
            assert study['签认'] in {APPROVED, '未改善', PENDING, ELIMINATED}


def test_adopted_trial_is_the_cheapest_interval_winner():
    report = preview()
    for row in report['rows']:
        advice = row['trial_advice']
        if advice['试镀建议'] != ADOPT:
            continue
        # APPROVED is decided on unrounded bounds, so the cheapest winner never
        # depends on the four-decimal display values.
        winners = [study for study in row['joint_experiments'] if study['签认'] == APPROVED]
        assert winners
        cheapest = min((study['用量'], study['联动推演']) for study in winners)
        assert (advice['用量'], advice['情形']) == cheapest
        chosen = next(study for study in winners if study['联动推演'] == advice['情形'])
        assert advice['原区间'][0] is not None and chosen['调整后区间'][1] is not None


def test_reordering_tickets_keeps_advice_and_original_disposition():
    dataset = open_fixture()
    forward = execute(dataset['records'])
    backward = execute(list(reversed(dataset['records'])))
    assert forward['run_id'] == backward['run_id']
    base = {row['key']: (row['state'], row['estimate'], row['cost'], row['reason']) for row in forward['rows']}
    flipped = {row['key']: (row['state'], row['estimate'], row['cost'], row['reason']) for row in backward['rows']}
    assert base == flipped
    advice = {row['key']: row['trial_advice'] for row in forward['rows']}
    assert advice == {row['key']: row['trial_advice'] for row in backward['rows']}


def test_trial_advice_never_rewrites_the_ticket_decision():
    report = preview()
    for row in report['rows']:
        assert row['estimate'] == row['trial_advice']['原估计'] or abs(row['estimate'] - row['trial_advice']['原估计']) < 1e-9
        if row['trial_advice']['试镀建议'] == ADOPT:
            assert row['state'] in {'已分派', '候补', '观察', '人工判读', '暂停'}


def test_stage_route_matches_run_for_trial_advice():
    from platebath.entry import app
    web = app.test_client()
    run = web.post('/api/platebath/run', json={'capacity': 7, 'strategy': 'focus'}).get_json()
    found = web.get('/api/platebath/stages/coating_disposition?capacity=7&strategy=focus').get_json()
    assert found == {**run['sections'][7], 'run_id': run['run_id']}
    assert any('签认单' in item for item in found['records'])
