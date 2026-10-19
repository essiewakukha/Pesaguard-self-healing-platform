from datetime import UTC, datetime, timedelta

from watch_release import Sample, Summary

T0 = datetime(2026, 10, 17, 21, 0, 0, tzinfo=UTC)


def at(seconds: int) -> datetime:
    return T0 + timedelta(seconds=seconds)


def test_summary_tracks_each_version_window():
    s = Summary()
    s.add(Sample(at(0), 201, "v1.1.0", 20))
    s.add(Sample(at(1), 201, "v1.2.0", 20))
    s.add(Sample(at(2), 500, "v1.2.0", 20))
    s.add(Sample(at(3), 201, "v1.1.0", 20))
    s.add(Sample(at(4), 0, "no-response", 5000))

    v = s.versions()
    assert v["v1.1.0"]["first"] == at(0)
    assert v["v1.1.0"]["last"] == at(3)
    assert v["v1.2.0"] == {"first": at(1), "last": at(2), "requests": 2, "failures": 1}
    assert v["no-response"]["failures"] == 1


def test_report_mentions_totals():
    s = Summary()
    s.add(Sample(at(0), 201, "v1.1.0", 20))
    s.add(Sample(at(1), 500, "v1.2.0", 20))
    text = s.report()
    assert "Requests: 2" in text
    assert "Failed: 1" in text
    assert "v1.2.0" in text


def test_empty_report():
    assert Summary().report() == "No requests recorded."
