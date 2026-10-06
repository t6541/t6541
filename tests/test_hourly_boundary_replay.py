import pandas as pd

from quantbot.hourly_boundary_replay import build_hourly_samples


def test_hourly_replay_builds_requested_samples_and_uses_first_five_minutes():
    dates=pd.date_range("2026-01-01",periods=25*60+65,freq="1min",tz="UTC")
    close=pd.Series([100+i*.01 for i in range(len(dates))],dtype=float)
    frame=pd.DataFrame({"date":dates,"open":close-.02,"high":close+.1,"low":close-.1,
                        "close":close,"volume":1.0})
    rows=build_hourly_samples(frame,hours=2)
    assert len(rows)==2
    assert rows[-1]["beijing_hour"].endswith("+08:00")
    assert "states" in rows[-1]["evidence_json"]
