"""Agent mouse ownership must suppress false operator-pause detection."""

import time

from god_worker.mouse import AgentMouseGuard, agent_mouse_section, is_agent_controlling_mouse


def test_guard_active_during_section_and_settle():
    guard = AgentMouseGuard()
    assert not guard.is_active()
    guard.begin()
    assert guard.is_active()
    guard.end(settle_s=0.2)
    assert guard.is_active()  # still in settle window
    time.sleep(0.25)
    assert not guard.is_active()


def test_nested_sections_keep_active_until_outer_ends():
    with agent_mouse_section(settle_s=0.05):
        assert is_agent_controlling_mouse()
        with agent_mouse_section(settle_s=0.05):
            assert is_agent_controlling_mouse()
        assert is_agent_controlling_mouse()
    time.sleep(0.1)
    assert not is_agent_controlling_mouse()


def test_install_visible_mouse_marks_agent_motion():
    events: list[dict] = []

    class FakePy:
        FAILSAFE = False
        PAUSE = 0
        _pos = type("P", (), {"x": 0, "y": 0})()

        def position(self):
            return self._pos

        def moveTo(self, x, y, *args, **kwargs):
            self._pos = type("P", (), {"x": int(x), "y": int(y)})()

        def click(self, *args, **kwargs):
            return None

        def dragTo(self, *args, **kwargs):
            return None

        def size(self):
            return (100, 100)

    from god_worker.mouse import install_visible_mouse

    py = FakePy()
    install_visible_mouse(py, events.append, duration=0.01)
    assert not is_agent_controlling_mouse()
    py.moveTo(50, 60, duration=0.01)
    # Immediately after move, settle window keeps ownership.
    assert is_agent_controlling_mouse()
    time.sleep(0.4)
    assert not is_agent_controlling_mouse()
