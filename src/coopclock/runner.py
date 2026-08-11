"""The daily run: compute, compare, write, verify."""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any

from .config import Config
from .omlet import OmletClient, OmletError
from .schedule import Schedule, compute, local_today

logger = logging.getLogger(__name__)


@dataclass
class RunResult:
    """Outcome of one daily run, suitable for logging and alerting."""

    day: str
    target_open: str
    target_close: str
    device_open: str
    device_close: str
    changed: bool
    written: bool
    verified: bool | None
    dry_run: bool
    clamped: list[str]
    battery_level: int | None
    power_source: str | None
    door_fault: str | None
    alerts: list[str]

    @property
    def ok(self) -> bool:
        """True if the run completed without anything needing attention."""
        return not self.alerts


def build_schedule(config: Config, day: date | None = None) -> Schedule:
    """Compute the schedule for `day` (default: today, in the coop's timezone)."""
    day = day or local_today(config.location.timezone)
    return compute(
        day=day,
        latitude=config.location.latitude,
        longitude=config.location.longitude,
        timezone=config.location.timezone,
        offsets=config.offsets,
        bounds=config.bounds,
        rounding_min=config.rounding_min,
    )


def run(
    config: Config,
    api_key: str,
    day: date | None = None,
    dry_run: bool = False,
    verify: bool = True,
) -> RunResult:
    """Execute one daily update.

    Writes only when the target differs from what the device already reports
    (API-5), which is the case on roughly a third of days.

    Args:
        config: Loaded configuration.
        api_key: Omlet API key.
        day: Date to compute for; defaults to today in the coop's timezone.
        dry_run: Compute and log without writing.
        verify: Poll for read-back confirmation after a write.

    Returns:
        A `RunResult` describing what happened.
    """
    schedule = build_schedule(config, day)
    client = OmletClient(api_key, base_url=config.api.base_url)

    alerts: list[str] = []
    if schedule.is_clamped:
        # A clamp firing means the astronomical result left the expected
        # envelope - that is a signal, not routine (SAF-6).
        alerts.extend(f"CLAMP: {c}" for c in schedule.clamped)

    state = client.read_state(config.device_id)

    if state.power_source == "internal" and (state.battery_level or 100) < 45:
        alerts.append(f"BATTERY LOW: {state.battery_level}% on internal power")
    if state.fault and state.fault != "none":
        alerts.append(f"DOOR FAULT: {state.fault}")

    changed = (state.open_time, state.close_time) != (schedule.open_time, schedule.close_time)
    written = False
    verified: bool | None = None

    if not changed:
        logger.info(
            "No change needed: device already at %s/%s", state.open_time, state.close_time
        )
    else:
        logger.info(
            "Updating %s -> %s (open), %s -> %s (close)",
            state.open_time,
            schedule.open_time,
            state.close_time,
            schedule.close_time,
        )
        written_at = None
        try:
            result = client.set_times(
                config.device_id, schedule.open_time, schedule.close_time, dry_run=dry_run
            )
            written = not dry_run
            written_at = result.get("_written_at")
        except OmletError as exc:
            alerts.append(f"WRITE FAILED: {exc}")

        if written and verify:
            # written_at makes this wait for the *device* to check in, not just
            # for Omlet's cloud to echo the desired state back.
            verified = client.verify_times(
                config.device_id,
                schedule.open_time,
                schedule.close_time,
                written_at=written_at,
                timeout_sec=config.api.verify_timeout_sec,
                interval_sec=config.api.verify_interval_sec,
            )
            if not verified:
                alerts.append("VERIFY FAILED: device did not collect new times")

    return RunResult(
        day=schedule.day.isoformat(),
        target_open=schedule.open_time,
        target_close=schedule.close_time,
        device_open=state.open_time,
        device_close=state.close_time,
        changed=changed,
        written=written,
        verified=verified,
        dry_run=dry_run,
        clamped=schedule.clamped,
        battery_level=state.battery_level,
        power_source=state.power_source,
        door_fault=state.fault,
        alerts=alerts,
    )


def to_dict(result: RunResult) -> dict[str, Any]:
    """Serialise a `RunResult` for structured logging."""
    return asdict(result)
