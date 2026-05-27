from __future__ import annotations

import json
from pathlib import Path

from hftbacktest.fellowquant import run_fq_simulation


def _write_manifest(path: Path) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": "fq.backtest.manifest.v1",
                "simulation_mode": "strategy_backtest",
                "provider": {"name": "tardis"},
                "time_range": {
                    "start": "2026-01-01T00:00:00Z",
                    "end": "2026-01-01T01:00:00Z",
                },
                "simulated_vault": {
                    "simulated_vault_id": "bt-1:strategy",
                    "initial_capital": 1000.0,
                    "base_currency": "USDC",
                    "sleeves": [
                        {
                            "sleeve_id": "strategy-1",
                            "strategy_type": "multi_venue_maker",
                            "target_weight_bps": 10000,
                            "venues": ["binance-usds-m"],
                            "symbols": ["BTCUSDT"],
                            "config": {},
                        }
                    ],
                },
            }
        ),
        encoding="utf-8",
    )


def test_run_fq_simulation_writes_canonical_artifacts(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    artifact_dir = tmp_path / "artifacts"
    _write_manifest(manifest_path)

    summary = run_fq_simulation(
        manifest_path,
        artifact_dir,
        provider_credentials={"tardis_api_key": "unit-secret"},
    )
    repeat_summary = run_fq_simulation(
        manifest_path,
        tmp_path / "repeat_artifacts",
        provider_credentials={"tardis_api_key": "unit-secret"},
    )

    assert summary["status"] == "completed"
    assert summary["summary_metrics"]["sleeve_count"] == 1
    assert (artifact_dir / "execution_ledger.jsonl").exists()
    assert (artifact_dir / "strategy_telemetry.jsonl").exists()
    assert (artifact_dir / "nav_timeline.jsonl").exists()
    provider_validation = json.loads((artifact_dir / "provider_validation.json").read_text())
    assert provider_validation["provider"] == "tardis"
    assert provider_validation["status"] == "configured"
    assert "TARDIS_API_KEY" not in (artifact_dir / "manifest.json").read_text()
    assert "unit-secret" not in (artifact_dir / "provider_validation.json").read_text()
    replay_hashes = json.loads((artifact_dir / "replay_hashes.json").read_text())
    assert replay_hashes["manifest_hash"] == summary["summary_metrics"]["manifest_hash"]
    assert (
        repeat_summary["artifact_manifest"]["artifacts"]
        == summary["artifact_manifest"]["artifacts"]
    )


def test_run_fq_simulation_fails_when_tardis_credentials_are_missing(
    tmp_path: Path,
) -> None:
    manifest_path = tmp_path / "manifest.json"
    artifact_dir = tmp_path / "artifacts"
    _write_manifest(manifest_path)

    summary = run_fq_simulation(manifest_path, artifact_dir)

    assert summary["status"] == "failed"
    assert summary["error_message"] == "provider 'tardis' is missing credentials"
    provider_validation = json.loads((artifact_dir / "provider_validation.json").read_text())
    assert provider_validation["status"] == "missing_credentials"
