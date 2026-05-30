from tools import registry


def test_dispatch_forwards_query_logs_arguments(monkeypatch):
    received = {}

    def fake_query_logs(**kwargs):
        received.update(kwargs)
        return "logs result"

    monkeypatch.setattr(registry, "_query_logs", fake_query_logs)

    result = registry.dispatch(
        "query_logs",
        {
            "query": "UNKNOWN_TOPIC_OR_PARTITION",
            "namespace": "si",
            "app": "multi-system-processor",
            "since_minutes": 120,
            "limit": 10,
        },
        {"default_namespace": "default"},
    )

    assert result == "logs result"
    assert received == {
        "query": "UNKNOWN_TOPIC_OR_PARTITION",
        "namespace": "si",
        "app": "multi-system-processor",
        "since_minutes": 120,
        "limit": 10,
    }


def test_dispatch_uses_query_logs_defaults(monkeypatch):
    received = {}

    def fake_query_logs(**kwargs):
        received.update(kwargs)
        return "logs result"

    monkeypatch.setattr(registry, "_query_logs", fake_query_logs)

    registry.dispatch(
        "query_logs",
        {"query": "rebalance"},
        {"default_namespace": "si"},
    )

    assert received == {
        "query": "rebalance",
        "namespace": "si",
        "app": None,
        "since_minutes": 60,
        "limit": 200,
    }
