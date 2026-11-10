from types import SimpleNamespace

import central_node.heartbeat_monitor as heartbeat_module
from central_node.heartbeat_monitor import HeartbeatMonitor


class _StopAudit(Exception):
    pass


def test_stale_node_is_marked_dead_and_notified(monkeypatch, capsys):
    monitor = HeartbeatMonitor()
    events = []
    monitor.register_heartbeat("127.0.0.1:5001")
    monitor.active_nodes["127.0.0.1:5001"]["last_seen"] = 0
    monitor.on_node_change = lambda node_id, status: events.append((node_id, status))

    calls = 0

    def stop_after_one_audit(_interval):
        nonlocal calls
        calls += 1
        if calls > 1:
            raise _StopAudit

    fake_time = SimpleNamespace(sleep=stop_after_one_audit, time=lambda: 100)
    monkeypatch.setattr(heartbeat_module, "time", fake_time)
    monkeypatch.setattr(heartbeat_module, "DATA_NODE_TIMEOUT", 9)

    try:
        monitor._audit_loop()
    except _StopAudit:
        pass

    assert monitor.get_healthy_nodes() == []
    assert monitor.active_nodes["127.0.0.1:5001"]["status"] == "DEAD"
    assert events == [("127.0.0.1:5001", "DEAD")]
    assert "FALLA DETECTADA" in capsys.readouterr().out


def test_recovered_node_is_notified_again():
    monitor = HeartbeatMonitor()
    events = []
    monitor.on_node_change = lambda node_id, status: events.append((node_id, status))
    monitor.register_heartbeat("127.0.0.1:5001")
    monitor.active_nodes["127.0.0.1:5001"]["status"] = "DEAD"

    monitor.register_heartbeat("127.0.0.1:5001")

    assert monitor.get_healthy_nodes() == ["127.0.0.1:5001"]
    assert events == [
        ("127.0.0.1:5001", "ACTIVE"),
        ("127.0.0.1:5001", "ACTIVE"),
    ]
