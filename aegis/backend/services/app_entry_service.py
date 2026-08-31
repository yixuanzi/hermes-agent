"""Portal-backed application entry directory for the Aegis console."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import httpx
from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, ValidationError

from aegis.backend.config import AegisSettings
from aegis.backend.models import AppEntryListResponse, AppEntryResponse, AppEntrySource


PORTAL_SUBSCRIPTION_SERVICES_PATH = "/api/v1/public/subscription-services"
PORTAL_TIMEOUT_SECONDS = 10.0


class _PortalSubscription(BaseModel):
    """The subset of the Portal public response used by App Entry."""

    model_config = ConfigDict(extra="ignore")

    subscription_id: str
    subscription_no: str
    subscription_status: str
    effective_to: str
    product_service_code: str
    product_service_name: str
    sub_app_entry_url: str | None = None
    sub_entry_url: str | None = None


@dataclass(frozen=True)
class _SelectedEntry:
    url: str | None
    source: AppEntrySource | None


def _safe_http_url(value: Any) -> str | None:
    """Return a usable external URL, ignoring blank or unsafe values."""
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    parsed = urlparse(normalized)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        return None
    return normalized


def _select_entry(subscription: _PortalSubscription) -> _SelectedEntry:
    app_url = _safe_http_url(subscription.sub_app_entry_url)
    if app_url:
        return _SelectedEntry(app_url, "app")

    subscription_url = _safe_http_url(subscription.sub_entry_url)
    if subscription_url:
        return _SelectedEntry(subscription_url, "subscription")

    return _SelectedEntry(None, None)


class AppEntryService:
    """Fetch and reduce the Portal subscription directory for one Aegis instance."""

    def __init__(self, settings: AegisSettings):
        self.settings = settings

    def _validate_configuration(self) -> None:
        if not self.settings.aegis_org_code:
            raise HTTPException(status_code=503, detail="App Entry organization is not configured.")
        if not self.settings.public_subscription_api_key:
            raise HTTPException(status_code=503, detail="App Entry Portal API key is not configured.")
        parsed = urlparse(self.settings.portal_base_url)
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            raise HTTPException(status_code=503, detail="App Entry Portal URL is not configured correctly.")

    async def list_entries(self) -> AppEntryListResponse:
        self._validate_configuration()
        endpoint = f"{self.settings.portal_base_url}{PORTAL_SUBSCRIPTION_SERVICES_PATH}"

        try:
            async with httpx.AsyncClient(timeout=PORTAL_TIMEOUT_SECONDS) as client:
                response = await client.get(
                    endpoint,
                    params={"organization_code": self.settings.aegis_org_code},
                    headers={"X-API-Key": self.settings.public_subscription_api_key},
                )
        except httpx.HTTPError as exc:
            raise HTTPException(status_code=502, detail="Unable to reach the Portal App Entry service.") from exc

        if response.status_code != 200:
            raise HTTPException(
                status_code=502,
                detail=f"Portal App Entry service returned HTTP {response.status_code}.",
            )

        try:
            payload = response.json()
            raw_entries = payload["data"]
            if not isinstance(raw_entries, list):
                raise TypeError("Portal data is not a list")
            subscriptions = [_PortalSubscription.model_validate(item) for item in raw_entries]
        except (TypeError, KeyError, ValueError, ValidationError) as exc:
            raise HTTPException(status_code=502, detail="Portal App Entry response is invalid.") from exc

        entries: list[AppEntryResponse] = []
        for subscription in subscriptions:
            if subscription.subscription_status not in {"active", "expiring"}:
                raise HTTPException(status_code=502, detail="Portal App Entry response is invalid.")
            selected = _select_entry(subscription)
            entries.append(
                AppEntryResponse(
                    subscription_id=subscription.subscription_id,
                    subscription_no=subscription.subscription_no,
                    subscription_status=subscription.subscription_status,
                    effective_to=subscription.effective_to,
                    product_service_code=subscription.product_service_code,
                    product_service_name=subscription.product_service_name,
                    entry_url=selected.url,
                    entry_source=selected.source,
                )
            )

        return AppEntryListResponse(
            organization_code=self.settings.aegis_org_code,
            entries=entries,
        )
