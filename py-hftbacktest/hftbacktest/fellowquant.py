"""FellowQuant-facing simulation API for the private hftbacktest fork."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Mapping

from .fellowquant_providers import build_provider_adapter

ARTIFACT_SCHEMAS = {
    "manifest.json": "fq.backtest.manifest.v1",
    "provider_validation.json": "fq.provider_validation.v1",
    "execution_ledger.jsonl": "execution_ledger.v1",
    "strategy_telemetry.jsonl": "fq.strategy_telemetry.v1",
    "allocator_decisions.jsonl": "fq.allocator_decisions.v1",
    "simulated_vault_runtime_state.jsonl": "fq.simulated_vault_runtime_state.v1",
    "nav_timeline.jsonl": "fq.vault_nav_timeline.v1",
    "projection.json": "fq.vault_projection.v1",
    "metrics.json": "fq.backtest_metrics.v1",
    "replay_hashes.json": "fq.replay_hashes.v1",
}


def run_fq_simulation(
    manifest_path: str | Path,
    artifact_dir: str | Path,
    *,
    provider_credentials: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Run a FellowQuant simulation manifest and write canonical artifacts.

    This is the stable API consumed by the `backtest` worker. The current
    implementation establishes deterministic manifests and artifact contracts;
    deeper event replay, matching, accounting, and Tardis ingestion plug into
    this boundary inside the fork.
    """
    manifest = _read_manifest(Path(manifest_path))
    output_dir = Path(artifact_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    canonical_manifest = _canonical_json(manifest)
    manifest_hash = _sha256(canonical_manifest)

    _write_json(output_dir / "manifest.json", manifest)
    provider_validation = _provider_validation(manifest, provider_credentials)
    _write_json(output_dir / "provider_validation.json", provider_validation)
    _write_jsonl(output_dir / "execution_ledger.jsonl", [])
    _write_jsonl(output_dir / "strategy_telemetry.jsonl", [])
    _write_jsonl(output_dir / "allocator_decisions.jsonl", [])
    _write_jsonl(output_dir / "simulated_vault_runtime_state.jsonl", [])
    _write_jsonl(output_dir / "nav_timeline.jsonl", [_initial_nav_event(manifest)])
    _write_json(output_dir / "projection.json", _projection(manifest))

    validation_error = _provider_validation_error(provider_validation)
    summary_metrics = _summary_metrics(manifest, manifest_hash, provider_validation)
    _write_json(output_dir / "metrics.json", summary_metrics)

    artifact_hashes = _artifact_hashes(output_dir)
    replay_hashes = {
        "schema_version": ARTIFACT_SCHEMAS["replay_hashes.json"],
        "manifest_hash": manifest_hash,
        "artifact_hashes": artifact_hashes,
    }
    _write_json(output_dir / "replay_hashes.json", replay_hashes)

    artifact_manifest = {
        "schema_version": "fq.backtest.artifact_manifest.v1",
        "artifacts": {
            name: {
                "schema_version": schema,
                "path": name,
                "sha256": _sha256_file(output_dir / name),
            }
            for name, schema in ARTIFACT_SCHEMAS.items()
        },
    }

    return {
        "status": "failed" if validation_error else "completed",
        "summary_metrics": summary_metrics,
        "artifact_manifest": artifact_manifest,
        "trade_stats": {"fill_count": 0, "order_count": 0},
        "is_partial": False,
        "error_message": validation_error,
    }


def _read_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("simulation manifest must be a JSON object")
    if payload.get("schema_version") != "fq.backtest.manifest.v1":
        raise ValueError("unsupported simulation manifest schema_version")
    return payload


def _provider_validation(
    manifest: dict[str, Any],
    provider_credentials: Mapping[str, Any] | None,
) -> dict[str, Any]:
    provider = manifest.get("provider") or {}
    return (
        build_provider_adapter(provider, provider_credentials)
        .validate_manifest(manifest)
        .as_artifact()
    )


def _initial_nav_event(manifest: dict[str, Any]) -> dict[str, Any]:
    vault = manifest.get("simulated_vault") or {}
    time_range = manifest.get("time_range") or {}
    initial_capital = float(vault.get("initial_capital", 0.0))
    return {
        "schema_version": ARTIFACT_SCHEMAS["nav_timeline.jsonl"],
        "timestamp": time_range.get("start") or _manifest_timestamp(manifest),
        "simulated_vault_id": vault.get("simulated_vault_id"),
        "base_currency": vault.get("base_currency", "USDC"),
        "total_equity": initial_capital,
        "share_price": 1.0,
        "sleeve_equity": _sleeve_equity(vault, initial_capital),
    }


def _projection(manifest: dict[str, Any]) -> dict[str, Any]:
    vault = manifest.get("simulated_vault") or {}
    return {
        "schema_version": ARTIFACT_SCHEMAS["projection.json"],
        "simulation_mode": manifest.get("simulation_mode"),
        "template_vault_id": manifest.get("template_vault_id"),
        "simulated_vault_id": vault.get("simulated_vault_id"),
        "initial_capital": vault.get("initial_capital"),
        "base_currency": vault.get("base_currency", "USDC"),
        "sleeves": vault.get("sleeves", []),
        "created_at": _manifest_timestamp(manifest),
    }


def _provider_validation_error(provider_validation: dict[str, Any]) -> str | None:
    status = provider_validation.get("status")
    provider = provider_validation.get("provider", "unknown")
    if status == "configured":
        return None
    if status == "missing_credentials":
        return f"provider '{provider}' is missing credentials"
    if status == "unsupported":
        return f"provider '{provider}' is not supported"
    return f"provider '{provider}' validation failed with status '{status}'"


def _summary_metrics(
    manifest: dict[str, Any],
    manifest_hash: str,
    provider_validation: dict[str, Any],
) -> dict[str, Any]:
    vault = manifest.get("simulated_vault") or {}
    sleeves = vault.get("sleeves") or []
    return {
        "schema_version": ARTIFACT_SCHEMAS["metrics.json"],
        "manifest_hash": manifest_hash,
        "simulation_mode": manifest.get("simulation_mode"),
        "provider": (manifest.get("provider") or {}).get("name", "unknown"),
        "provider_validation_status": provider_validation.get("status"),
        "sleeve_count": len(sleeves),
        "initial_capital": float(vault.get("initial_capital", 0.0)),
        "total_pnl": 0.0,
        "total_return": 0.0,
        "max_drawdown": 0.0,
    }


def _sleeve_equity(vault: dict[str, Any], initial_capital: float) -> dict[str, float]:
    sleeves = vault.get("sleeves") or []
    return {
        str(sleeve.get("sleeve_id")): initial_capital
        * float(sleeve.get("target_weight_bps", 0))
        / 10_000
        for sleeve in sleeves
    }


def _artifact_hashes(output_dir: Path) -> dict[str, str]:
    return {
        path.name: _sha256_file(path)
        for path in sorted(output_dir.iterdir())
        if path.is_file() and path.name != "replay_hashes.json"
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str) + "\n",
        encoding="utf-8",
    )


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    body = "".join(
        json.dumps(row, sort_keys=True, separators=(",", ":"), default=str) + "\n"
        for row in rows
    )
    path.write_text(body, encoding="utf-8")


def _canonical_json(payload: dict[str, Any]) -> bytes:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode(
        "utf-8"
    )


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path) -> str:
    return _sha256(path.read_bytes())


def _manifest_timestamp(manifest: dict[str, Any]) -> str:
    reproducibility = manifest.get("reproducibility") or {}
    if reproducibility.get("manifest_created_at"):
        return str(reproducibility["manifest_created_at"])
    time_range = manifest.get("time_range") or {}
    if time_range.get("start"):
        return str(time_range["start"])
    return "1970-01-01T00:00:00Z"
