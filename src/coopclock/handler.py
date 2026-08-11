"""AWS Lambda entry point."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from .config import load, load_api_key
from .runner import run, to_dict

logging.getLogger().setLevel(logging.INFO)
logger = logging.getLogger(__name__)

CONFIG_PATH = os.environ.get("COOPCLOCK_CONFIG", str(Path(__file__).parent / "swindon.yaml"))


# Read-only probe used to establish whether the API exposes any historical
# data. Fixed allow-list rather than a caller-supplied path, so this cannot be
# turned into an arbitrary-request primitive.
PROBE_PATHS = (
    "device/{id}/history",
    "device/{id}/events",
    "device/{id}/event",
    "device/{id}/state/history",
    "device/{id}/statistics",
    "device/{id}/stats",
    "device/{id}/log",
    "device/{id}/logs",
    "device/{id}/battery",
    "device/{id}/telemetry",
    "device/{id}/measurements",
    "device/{id}/action",
    "events",
    "history",
    "webhook",
)


def _probe(config, api_key: str, paths: tuple[str, ...] | None = None) -> dict[str, Any]:
    """GET candidate endpoints and report status codes.

    Always GET, always under the configured API base URL, so a supplied path
    cannot be turned into an arbitrary-request primitive.
    """
    import requests

    findings: dict[str, Any] = {}
    for template in paths or PROBE_PATHS:
        path = template.format(id=config.device_id).lstrip("/")
        try:
            response = requests.get(
                f"{config.api.base_url}/{path}",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=20,
            )
            entry: dict[str, Any] = {"status": response.status_code}
            if response.status_code == 200:
                body = response.text[:600]
                entry["body"] = body.replace(api_key, "<REDACTED>")
            findings[path] = entry
        except Exception as exc:  # noqa: BLE001 - diagnostic breadth is intended
            findings[path] = {"error": str(exc).replace(api_key, "<REDACTED>")}
    return findings


def _emit_metrics(result) -> None:
    """Publish battery level and run health to CloudWatch (namespace `coopclock`)."""
    try:
        import boto3

        metrics = [
            {
                "MetricName": "RunSucceeded",
                "Value": 0.0 if result.alerts else 1.0,
                "Unit": "Count",
            }
        ]
        if result.battery_level is not None:
            metrics.append(
                {
                    "MetricName": "BatteryLevel",
                    "Value": float(result.battery_level),
                    "Unit": "Percent",
                }
            )
        boto3.client("cloudwatch").put_metric_data(Namespace="coopclock", MetricData=metrics)
    except Exception as exc:  # noqa: BLE001 - metrics must never fail the run
        logger.warning("Metric publish failed: %s", exc)


def lambda_handler(event: dict[str, Any] | None, context: Any = None) -> dict[str, Any]:
    """Run one daily schedule update.

    Event keys (all optional):
        dry_run: bool - compute and log without writing.
        verify: bool - poll for read-back confirmation (default True).

    Returns:
        A structured result dict, also emitted to CloudWatch Logs.
    """
    event = event or {}
    config = load(CONFIG_PATH)
    api_key = load_api_key()

    if event.get("probe"):
        supplied = event.get("paths")
        return _probe(config, api_key, tuple(supplied) if supplied else None)

    if event.get("events"):
        import requests

        response = requests.get(
            f"{config.api.base_url}/events",
            headers={"Authorization": f"Bearer {api_key}"},
            params=event.get("params") or {},
            timeout=30,
        )
        return {"status": response.status_code, "events": response.json()}

    result = run(
        config=config,
        api_key=api_key,
        dry_run=bool(event.get("dry_run", False)),
        verify=bool(event.get("verify", True)),
    )

    payload = to_dict(result)
    logger.info("coopclock result: %s", json.dumps(payload))

    # The Omlet API exposes only point-in-time battery level - /events carries
    # door, light, connectivity and firmware changes but never batteryLevel,
    # and no history endpoint exists. Record it ourselves so a trend accrues.
    _emit_metrics(result)

    if result.alerts:
        # Non-zero-ish signal for CloudWatch metric filters / alarms (REL-3).
        logger.error("coopclock alerts: %s", json.dumps(result.alerts))

    return payload
