"""Tests for the Omlet API client, with the network mocked out."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from coopclock.omlet import OmletClient, OmletError

# Mirrors the real device response captured on 09/08/2026, including the
# lightOffset/temperatureUnit fields the official SDK would drop.
DEVICE = {
    "deviceId": "EXAMPLEDEVICE01",
    "name": "Example autodoor",
    "state": {
        "general": {
            "batteryLevel": 63,
            "powerSource": "internal",
            "firmwareVersionCurrent": "1.0.55-1cc08340",
        },
        "connectivity": {"wifiStrength": -43, "connected": False},
        "door": {"state": "open", "fault": "none"},
    },
    "configuration": {
        "door": {
            "openMode": "time",
            "openDelay": 0,
            "openTime": "05:40",
            "closeMode": "time",
            "closeDelay": 0,
            "closeLightLevel": 6,
            "closeTime": "21:35",
            "openLightLevel": 27,
            "doorType": "sliding",
            "colour": "green",
            "lightOffset": 3,
            "temperatureUnit": "C",
        },
        "general": {"datetime": "2026-08-09T19:28:50+01:00", "timezone": "Europe/London"},
    },
}


class FakeClient(OmletClient):
    """OmletClient with `_request` replaced by an in-memory device."""

    def __init__(self, device=None):
        super().__init__(api_key="test-key")
        # Deep copy: a shallow one shares `configuration` with the module-level
        # fixture and lets writes in one test leak into the next.
        self.device = copy.deepcopy(device if device is not None else DEVICE)
        self.patches: list[dict] = []

    def _request(self, method, endpoint, payload=None):
        if method == "GET":
            return self.device
        if method == "PATCH":
            self.patches.append(payload)
            self.device["configuration"]["door"].update(payload["door"])
            return None
        raise AssertionError(f"unexpected {method}")


class TestReadState:
    def test_projects_fields(self):
        state = FakeClient().read_state("d")
        assert state.open_time == "05:40"
        assert state.close_time == "21:35"
        assert state.battery_level == 63
        assert state.power_source == "internal"
        assert state.wifi_strength == -43
        assert state.fault == "none"


class TestSetTimes:
    def test_preserves_fields_the_sdk_would_drop(self):
        client = FakeClient()
        payload = client.set_times("d", "06:45", "20:10")
        door = payload["door"]
        assert door["lightOffset"] == 3
        assert door["temperatureUnit"] == "C"
        assert door["openLightLevel"] == 27
        assert door["colour"] == "green"

    def test_writes_only_the_two_time_fields(self):
        client = FakeClient()
        before = dict(DEVICE["configuration"]["door"])
        payload = client.set_times("d", "06:45", "20:10")
        changed = {k for k in payload["door"] if payload["door"][k] != before.get(k)}
        assert changed == {"openTime", "closeTime"}

    def test_patches_door_subtree_only(self):
        # general.datetime must never be echoed back - it could set the clock.
        client = FakeClient()
        client.set_times("d", "06:45", "20:10")
        assert set(client.patches[0]) == {"door"}

    def test_dry_run_sends_nothing(self):
        client = FakeClient()
        client.set_times("d", "06:45", "20:10", dry_run=True)
        assert client.patches == []

    def test_forces_time_mode_if_device_drifted_to_light(self):
        device = {**DEVICE}
        device["configuration"] = {
            **DEVICE["configuration"],
            "door": {**DEVICE["configuration"]["door"], "openMode": "light", "closeMode": "light"},
        }
        client = FakeClient(device)
        payload = client.set_times("d", "06:45", "20:10")
        assert payload["door"]["openMode"] == "time"
        assert payload["door"]["closeMode"] == "time"

    def test_raises_when_no_door_configuration(self):
        client = FakeClient({"deviceId": "d", "name": "x", "configuration": {}, "state": {}})
        with pytest.raises(OmletError, match="no door configuration"):
            client.set_times("d", "06:45", "20:10")


class TestVerify:
    def test_returns_true_once_cloud_agrees_when_no_written_at(self):
        client = FakeClient()
        client.set_times("d", "06:45", "20:10")
        assert client.verify_times("d", "06:45", "20:10", timeout_sec=0) is True

    def test_returns_false_on_mismatch(self):
        client = FakeClient()
        assert client.verify_times("d", "06:45", "20:10", timeout_sec=0) is False

    def test_cloud_echo_alone_is_not_acknowledgement(self):
        # The config endpoint returns *desired* state and updates instantly, so
        # a matching read proves nothing about the device. If lastConnected has
        # not advanced past the write, verification must fail.
        client = FakeClient()
        result = client.set_times("d", "06:45", "20:10")
        stale = datetime(2020, 1, 1, tzinfo=timezone.utc)
        client.device["lastConnected"] = stale.isoformat()
        assert client.verify_times(
            "d", "06:45", "20:10", written_at=result["_written_at"], timeout_sec=0
        ) is False

    def test_true_once_device_checks_in_after_the_write(self):
        client = FakeClient()
        result = client.set_times("d", "06:45", "20:10")
        after = result["_written_at"] + timedelta(seconds=1)
        client.device["lastConnected"] = after.isoformat()
        assert client.verify_times(
            "d", "06:45", "20:10", written_at=result["_written_at"], timeout_sec=0
        ) is True


class TestLastConnected:
    def test_parsed_to_utc(self):
        client = FakeClient()
        client.device["lastConnected"] = "2026-08-09T18:28:52+00:00"
        assert client.read_state("d").last_connected == datetime(
            2026, 8, 9, 18, 28, 52, tzinfo=timezone.utc
        )

    def test_unparseable_value_is_tolerated(self):
        client = FakeClient()
        client.device["lastConnected"] = "not-a-timestamp"
        assert client.read_state("d").last_connected is None


class TestRedaction:
    def test_key_is_stripped_from_text(self):
        client = OmletClient(api_key="supersecret")
        assert "supersecret" not in client._redact("boom supersecret boom")
        assert "<REDACTED>" in client._redact("boom supersecret boom")
