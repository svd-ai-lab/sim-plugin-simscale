from sim.testing import assert_protocol_conformance

from sim_plugin_simscale import SimScaleDriver


def test_protocol_conformance() -> None:
    assert_protocol_conformance(SimScaleDriver)
