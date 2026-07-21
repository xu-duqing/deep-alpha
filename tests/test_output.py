import json

import pandas as pd

from deep_alpha.output import emit, render_frame


def test_csv_output():
    frame = pd.DataFrame([{"datetime": pd.Timestamp("2024-01-02"), "symbol": "SH600519", "close": None}])
    assert render_frame(frame, "csv") == "datetime,symbol,close\n2024-01-02,SH600519,\n"


def test_json_null_output():
    frame = pd.DataFrame([{"datetime": pd.Timestamp("2024-01-02"), "close": None}])
    value = json.loads(render_frame(frame, "json"))
    assert value[0]["close"] is None


def test_emit_ignores_predictable_symlink(tmp_path):
    output = tmp_path / "result.csv"
    victim = tmp_path / "victim"
    victim.write_text("safe")
    (tmp_path / ".result.csv.tmp").symlink_to(victim)
    emit("result", str(output))
    assert output.read_text() == "result"
    assert victim.read_text() == "safe"
