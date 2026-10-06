from pathlib import Path

from quantbot.pipeline import run_pipeline


def test_pipeline_produces_audit_artifacts(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = tmp_path / "config.toml"
    source = Path(__file__).parents[1] / "configs" / "default.toml"
    config.write_text(source.read_text(encoding="utf-8").replace('root = "artifacts"', f'root = "{tmp_path.as_posix()}/artifacts"').replace('start = "2020-01-01"', 'start = "2023-01-01"').replace('end = "2024-12-31"', 'end = "2023-08-31"'), encoding="utf-8")
    out = run_pipeline(config)
    for name in ["manifest.json", "config.toml", "market.csv", "factors.csv", "target_weights.csv", "backtest_daily.csv", "metrics.json", "report.html"]:
        assert (out / name).exists()
