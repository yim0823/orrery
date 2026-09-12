from orrery.scenarios import load_scenario


def test_example_scenario_loads():
    s = load_scenario("fixtures/scenario-example.yaml")
    assert s.id == "GEN-0001"
    assert s.answer.escalate_within_s == 180
    assert s.taxonomy["observation"] == "alerts_missing"
