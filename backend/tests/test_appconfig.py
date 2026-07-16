from pathlib import Path

from app.appconfig import load_config


def test_load_default_config_is_valid() -> None:
    cfg = load_config()  # legge il config.yaml reale del repo
    assert cfg.enabled_sources()
    assert cfg.keywords
    weights = cfg.scoring.normalized_weights()
    assert abs(sum(weights.values()) - 1.0) < 1e-9


def test_load_config_from_path(tmp_path: Path) -> None:
    p = tmp_path / "config.yaml"
    p.write_text(
        """
sources:
  - name: hn
    type: hn
    limit: 5
  - name: gh
    type: github
    enabled: false
keywords: ["ai"]
scoring:
  weights: {heat: 1, fit: 1}
  threshold: 0.5
""",
        encoding="utf-8",
    )
    cfg = load_config(p)
    assert cfg.scoring.threshold == 0.5
    assert cfg.scoring.normalized_weights()["heat"] == 0.5
    # La fonte disabilitata non compare tra quelle attive.
    assert [s.name for s in cfg.enabled_sources()] == ["hn"]
