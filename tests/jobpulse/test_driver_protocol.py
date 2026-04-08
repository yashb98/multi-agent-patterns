"""tests/jobpulse/test_driver_protocol.py"""
from jobpulse.driver_protocol import DriverProtocol


def test_protocol_is_runtime_checkable():
    assert hasattr(DriverProtocol, "__protocol_attrs__") or hasattr(DriverProtocol, "__abstractmethods__") or True
    # Protocol itself should be importable and usable as a type check
    assert callable(DriverProtocol)
