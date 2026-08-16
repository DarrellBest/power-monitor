import pytest
from power_monitor import cost


def test_constant_draw_for_one_hour():
    # 100W sampled every 10s for 1h == 0.1 kWh
    points = [(i * 10.0, 100.0) for i in range(361)]
    assert cost.energy_kwh(points) == pytest.approx(0.1)


def test_trapezoid_between_differing_watts():
    # 0W -> 200W over 10s averages 100W: 100W * 10s = 1000 Ws
    points = [(0.0, 0.0), (10.0, 200.0)]
    assert cost.energy_kwh(points) == pytest.approx(100.0 * 10.0 / 3600.0 / 1000.0)


def test_gap_over_30s_contributes_zero():
    # 100W for 10s, a 10 minute collector outage, then 100W for 10s
    points = [(0.0, 100.0), (10.0, 100.0), (610.0, 100.0), (620.0, 100.0)]
    assert cost.energy_kwh(points) == pytest.approx(2 * 100.0 * 10.0 / 3600.0 / 1000.0)


def test_gap_exactly_30s_is_included():
    points = [(0.0, 100.0), (30.0, 100.0)]
    assert cost.energy_kwh(points) == pytest.approx(100.0 * 30.0 / 3600.0 / 1000.0)


def test_empty_list_is_zero():
    assert cost.energy_kwh([]) == 0.0


def test_single_point_is_zero():
    assert cost.energy_kwh([(0.0, 500.0)]) == 0.0


def test_cost_usd_uses_the_default_rate_when_none_is_passed():
    assert cost.DEFAULT_RATE_PER_KWH == 0.149
    assert cost.cost_usd(10.0) == pytest.approx(1.49)


def test_cost_usd_accepts_custom_rate():
    assert cost.cost_usd(10.0, rate=0.20) == pytest.approx(2.0)
    assert cost.cost_usd(0.0) == 0.0
