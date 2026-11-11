from opspilot.otel_fixture import normalize_otel_records


def test_fixture_normalizes_signals_for_trace_service_time_correlation():
    payload = {
        "traces": [{"resource": {"service.name": "orders"}, "span": {"trace_id": "abc"},
                    "timestamp": "2026-11-11T10:00:00Z", "attributes": {"name": "checkout"}}],
        "logs": [{"resource": {"service.name": "orders"}, "trace_id": "abc",
                  "timestamp": "2026-11-11T10:00:01+00:00", "attributes": {"level": "error"}}],
        "metrics": [{"service": "orders", "timestamp": "2026-11-11T10:00:02Z", "attributes": {"value": 1}}],
    }
    rows = normalize_otel_records(payload)
    assert len(rows) == 3
    assert rows[0]["trace_id"] == rows[1]["trace_id"] == "abc"
    assert {row["service"] for row in rows} == {"orders"}
    assert all(row["timestamp"].startswith("2026-11-11T10:00:0") for row in rows)


def test_fixture_drops_missing_fields_and_invalid_timestamps():
    payload = {"logs": [
        {"service": "orders", "timestamp": "bad"},
        {"timestamp": "2026-11-11T10:00:00Z"},
        {"service": "orders", "timestamp": "2026-11-11T10:00:00"},
    ]}
    assert normalize_otel_records(payload) == []
