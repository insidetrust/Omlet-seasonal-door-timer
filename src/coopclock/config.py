"""Configuration loading for coopclock."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

import yaml

from .schedule import Bounds, Offsets


@dataclass(frozen=True)
class Location:
    """Where the coop is."""

    name: str
    latitude: float
    longitude: float
    timezone: str


@dataclass(frozen=True)
class ApiSettings:
    """API endpoint and verification behaviour."""

    base_url: str
    verify_timeout_sec: int
    verify_interval_sec: int


@dataclass(frozen=True)
class Config:
    """Full application configuration."""

    location: Location
    offsets: Offsets
    bounds: Bounds
    api: ApiSettings
    device_id: str
    device_name: str
    rounding_min: int


def load(path: str | Path) -> Config:
    """Load configuration from a YAML file.

    Args:
        path: Path to the YAML config.

    Returns:
        A populated `Config`.
    """
    data = yaml.safe_load(Path(path).read_text())
    loc, off, bnd, api, dev = (
        data["location"],
        data["offsets"],
        data["bounds"],
        data["api"],
        data["device"],
    )
    return Config(
        location=Location(
            name=loc["name"],
            latitude=float(loc["latitude"]),
            longitude=float(loc["longitude"]),
            timezone=loc["timezone"],
        ),
        offsets=Offsets(
            open_after_sunrise_min=int(off["open_after_sunrise_min"]),
            close_after_sunset_min=int(off["close_after_sunset_min"]),
            close_min_after_civil_dusk_min=int(off["close_min_after_civil_dusk_min"]),
        ),
        bounds=Bounds(
            earliest_open=bnd["earliest_open"],
            latest_open=bnd["latest_open"],
            earliest_close=bnd["earliest_close"],
            latest_close=bnd["latest_close"],
            min_open_hours=float(bnd["min_open_hours"]),
            max_minutes_after_sunset=int(bnd["max_minutes_after_sunset"]),
        ),
        api=ApiSettings(
            base_url=api["base_url"],
            verify_timeout_sec=int(api["verify_timeout_sec"]),
            verify_interval_sec=int(api["verify_interval_sec"]),
        ),
        device_id=dev["device_id"],
        device_name=dev.get("name", ""),
        rounding_min=int(data.get("rounding_min", 5)),
    )


def load_api_key() -> str:
    """Resolve the Omlet API key.

    Order: `OMLET_API_KEY` env var, then AWS Secrets Manager using the secret
    named in `OMLET_SECRET_ID`. The key is never logged (API-7).

    Returns:
        The API key.

    Raises:
        RuntimeError: If no source yields a key.
    """
    key = os.environ.get("OMLET_API_KEY")
    if key:
        return key.strip()

    secret_id = os.environ.get("OMLET_SECRET_ID")
    if secret_id:
        import boto3  # imported lazily so local runs need no AWS deps

        client = boto3.client("secretsmanager")
        value = client.get_secret_value(SecretId=secret_id)["SecretString"]
        # Accept either a bare string or {"api_key": "..."}
        if value.lstrip().startswith("{"):
            import json

            return json.loads(value)["api_key"].strip()
        return value.strip()

    raise RuntimeError("No API key: set OMLET_API_KEY or OMLET_SECRET_ID")
