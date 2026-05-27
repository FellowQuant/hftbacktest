"""Provider adapters for FellowQuant hftbacktest simulations."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping, Protocol


@dataclass(frozen=True)
class ProviderCredentials:
    """Provider secrets supplied out-of-band from the persisted manifest."""

    tardis_api_key: str | None = None

    @classmethod
    def from_payload(
        cls,
        payload: "ProviderCredentials | Mapping[str, Any] | None",
    ) -> "ProviderCredentials":
        if isinstance(payload, ProviderCredentials):
            return payload
        if payload is None:
            return cls()
        return cls(tardis_api_key=_non_empty_string(payload.get("tardis_api_key")))


@dataclass(frozen=True)
class ProviderValidationResult:
    provider: str
    status: str
    checks: dict[str, str] = field(default_factory=dict)
    capabilities: dict[str, Any] = field(default_factory=dict)

    def as_artifact(self) -> dict[str, Any]:
        return {
            "schema_version": "fq.provider_validation.v1",
            "provider": self.provider,
            "status": self.status,
            "checks": self.checks,
            "capabilities": self.capabilities,
        }


class MarketDataProvider(Protocol):
    name: str

    def validate_manifest(self, manifest: dict[str, Any]) -> ProviderValidationResult:
        """Validate provider readiness for the requested simulation manifest."""


class TardisProviderAdapter:
    """Tardis.dev tick/L2/L3 data adapter boundary."""

    name = "tardis"

    def __init__(self, credentials: ProviderCredentials) -> None:
        self._credentials = credentials

    def validate_manifest(self, manifest: dict[str, Any]) -> ProviderValidationResult:
        provider = manifest.get("provider") or {}
        has_api_key = bool(self._credentials.tardis_api_key)
        status = "configured" if has_api_key else "missing_credentials"
        return ProviderValidationResult(
            provider=self.name,
            status=status,
            checks={
                "credentials": "present" if has_api_key else "missing",
                "timestamp_precision": "provider_native",
                "sequence_gaps": "pending_replay_scan",
                "book_reconstruction": "pending_replay_scan",
                "trade_alignment": "pending_replay_scan",
                "funding_cadence": "pending_replay_scan",
                "coverage": "pending_replay_scan",
                "licensing": "user_api_key_required",
            },
            capabilities={
                "granularity": "tick",
                "supports_l2": True,
                "supports_l3": True,
                "supports_trades": True,
                "supports_quotes": True,
                "supports_derivatives": True,
                "exchange_ids": list(provider.get("exchange_ids") or []),
                "products": list(provider.get("products") or []),
            },
        )


class UnknownProviderAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    def validate_manifest(self, manifest: dict[str, Any]) -> ProviderValidationResult:
        del manifest
        return ProviderValidationResult(
            provider=self.name,
            status="unsupported",
            checks={"provider_registry": "unsupported"},
        )


def build_provider_adapter(
    provider: dict[str, Any],
    credentials: ProviderCredentials | Mapping[str, Any] | None = None,
) -> MarketDataProvider:
    name = str(provider.get("name") or "tardis").lower()
    if name == "tardis":
        return TardisProviderAdapter(ProviderCredentials.from_payload(credentials))
    return UnknownProviderAdapter(name)


def _non_empty_string(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None
