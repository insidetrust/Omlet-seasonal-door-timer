"""Minimal Omlet SmartCoop API client.

Deliberately does *not* use `smartcoop-python-sdk` for writes. Its
`ConfigurationDoor.to_json()` emits a fixed 10 fields, but the live device also
carries `lightOffset` and `temperatureUnit` - a round-trip through the SDK
would silently drop both. This client preserves the raw JSON and mutates only
the two fields it owns.

It also PATCHes the `door` sub-object alone rather than the whole
configuration, so `general.datetime` is never echoed back (which could set the
device clock) and light/connectivity settings are untouched.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import requests

logger = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://x107.omlet.co.uk/api/v1"


class OmletError(RuntimeError):
    """An API call failed after retries."""


@dataclass
class DoorState:
    """The subset of device state this project cares about."""

    device_id: str
    name: str
    open_time: str
    close_time: str
    open_mode: str
    close_mode: str
    battery_level: int | None
    power_source: str | None
    connected: bool | None
    wifi_strength: int | None
    firmware: str | None
    door_state: str | None
    fault: str | None
    last_connected: datetime | None = None
    raw_door_config: dict[str, Any] | None = None


class OmletClient:
    """Thin authenticated wrapper over the SmartCoop REST API."""

    def __init__(
        self,
        api_key: str,
        base_url: str = DEFAULT_BASE_URL,
        timeout: int = 30,
        max_retries: int = 4,
    ) -> None:
        """Initialise the client.

        Args:
            api_key: Bearer token from the Omlet developer console.
            base_url: API root.
            timeout: Per-request timeout in seconds.
            max_retries: Attempts before raising `OmletError`.
        """
        self._key = api_key
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.max_retries = max_retries

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._key}",
            "Content-Type": "application/json",
        }

    def _redact(self, text: str) -> str:
        """Strip the API key from anything destined for a log (API-7)."""
        return text.replace(self._key, "<REDACTED>") if self._key else text

    def _request(self, method: str, endpoint: str, payload: dict | None = None) -> Any:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        last_error: Exception | None = None

        for attempt in range(1, self.max_retries + 1):
            try:
                response = requests.request(
                    method,
                    url,
                    headers=self._headers(),
                    json=payload,
                    timeout=self.timeout,
                )
                if response.status_code >= 500 or response.status_code == 429:
                    raise OmletError(
                        f"{method} {endpoint} -> HTTP {response.status_code}: "
                        f"{self._redact(response.text[:200])}"
                    )
                if response.status_code >= 400:
                    # 4xx (other than 429) will not improve on retry.
                    raise OmletError(
                        f"{method} {endpoint} -> HTTP {response.status_code}: "
                        f"{self._redact(response.text[:200])}"
                    )
                if response.status_code == 204 or not response.content:
                    return None
                return response.json()
            except OmletError as exc:
                last_error = exc
                if "HTTP 4" in str(exc) and "HTTP 429" not in str(exc):
                    raise
            except requests.RequestException as exc:
                last_error = OmletError(self._redact(str(exc)))

            if attempt < self.max_retries:
                backoff = 2**attempt
                logger.warning(
                    "%s %s failed (attempt %d/%d), retrying in %ds",
                    method,
                    endpoint,
                    attempt,
                    self.max_retries,
                    backoff,
                )
                time.sleep(backoff)

        raise OmletError(
            f"{method} {endpoint} failed after {self.max_retries} attempts: {last_error}"
        )

    def get_devices(self) -> list[dict[str, Any]]:
        """Return the raw device list."""
        return self._request("GET", "device") or []

    def get_device(self, device_id: str) -> dict[str, Any]:
        """Return one raw device object."""
        return self._request("GET", f"device/{device_id}")

    def read_state(self, device_id: str) -> DoorState:
        """Read the device and project it onto `DoorState`."""
        device = self.get_device(device_id)
        config = device.get("configuration") or {}
        state = device.get("state") or {}
        door_cfg = config.get("door") or {}
        general = state.get("general") or {}
        conn = state.get("connectivity") or {}
        door_state = state.get("door") or {}

        last_connected = None
        if raw_last := device.get("lastConnected"):
            try:
                last_connected = datetime.fromisoformat(raw_last).astimezone(timezone.utc)
            except ValueError:
                logger.warning("Unparseable lastConnected: %s", raw_last)

        return DoorState(
            device_id=device.get("deviceId", device_id),
            name=device.get("name", ""),
            open_time=door_cfg.get("openTime", ""),
            close_time=door_cfg.get("closeTime", ""),
            open_mode=door_cfg.get("openMode", ""),
            close_mode=door_cfg.get("closeMode", ""),
            battery_level=general.get("batteryLevel"),
            power_source=general.get("powerSource"),
            connected=conn.get("connected"),
            wifi_strength=conn.get("wifiStrength"),
            firmware=general.get("firmwareVersionCurrent"),
            door_state=door_state.get("state"),
            fault=door_state.get("fault"),
            last_connected=last_connected,
            raw_door_config=door_cfg,
        )

    def set_times(
        self,
        device_id: str,
        open_time: str,
        close_time: str,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """Write open/close times, preserving every other door field.

        Args:
            device_id: Target device.
            open_time: Local wall-clock "HH:MM".
            close_time: Local wall-clock "HH:MM".
            dry_run: Compute and log the payload without sending it.

        Returns:
            The payload that was (or would have been) sent.

        Raises:
            OmletError: If the device has no door configuration, or the write fails.
        """
        current = self.read_state(device_id)
        door = dict(current.raw_door_config or {})
        if not door:
            raise OmletError(f"Device {device_id} returned no door configuration")

        door["openTime"] = open_time
        door["closeTime"] = close_time

        # API-3: assert timer mode. The device already reports "time"; if it ever
        # reports otherwise our writes would be ignored by the light sensor.
        if current.open_mode != "time" or current.close_mode != "time":
            logger.warning(
                "Door not in time mode (open=%s close=%s) - forcing",
                current.open_mode,
                current.close_mode,
            )
            door["openMode"] = "time"
            door["closeMode"] = "time"

        payload = {"door": door}
        if dry_run:
            logger.info("DRY RUN - would PATCH %s", payload)
            return payload

        # Stamped before the call so a device check-in racing the PATCH cannot
        # be mistaken for acknowledgement of it.
        written_at = datetime.now(timezone.utc)
        self._request("PATCH", f"device/{device_id}/configuration", payload)
        return {**payload, "_written_at": written_at}

    def verify_times(
        self,
        device_id: str,
        open_time: str,
        close_time: str,
        written_at: datetime | None = None,
        timeout_sec: int = 780,
        interval_sec: int = 30,
    ) -> bool:
        """Poll until the *device* has collected the expected times.

        Two distinct layers are checked, and the difference matters:

        1. The configuration endpoint returns Omlet's **desired** state, which
           updates the instant a PATCH is accepted. Matching this only proves
           the cloud took the write.
        2. `lastConnected` advancing past `written_at` proves the **device**
           has since checked in and therefore collected the new configuration.

        Without (2) a run reports success in seconds while the door is still
        operating on yesterday's schedule for up to another `pollFreq` (600 s).

        Args:
            device_id: Target device.
            open_time: Expected local "HH:MM".
            close_time: Expected local "HH:MM".
            written_at: UTC instant the PATCH was accepted. If None, only the
                cloud-side check is performed.
            timeout_sec: Give up after this long.
            interval_sec: Delay between polls.

        Returns:
            True if the device collected the new times within the timeout.
        """
        deadline = time.monotonic() + timeout_sec
        while True:
            state = self.read_state(device_id)
            cloud_ok = state.open_time == open_time and state.close_time == close_time
            device_ok = (
                written_at is None
                or (state.last_connected is not None and state.last_connected >= written_at)
            )
            if cloud_ok and device_ok:
                return True
            if time.monotonic() >= deadline:
                logger.error(
                    "Verify timed out: cloud_ok=%s device_ok=%s "
                    "(reports %s/%s, expected %s/%s, lastConnected=%s, written=%s)",
                    cloud_ok,
                    device_ok,
                    state.open_time,
                    state.close_time,
                    open_time,
                    close_time,
                    state.last_connected,
                    written_at,
                )
                return False
            time.sleep(interval_sec)
