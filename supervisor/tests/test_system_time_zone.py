"""The time zone of System routes is the host's: timedatectl runs on the host through src/host.py
(ADR-0020, ADR-0331), never in the supervisor container, where it has no systemd to ask."""
from __future__ import annotations

import subprocess

import pytest

import src.host
from src.host import HostResult

from .test_pool_persistence import admin_client

API = "/api/v1/system"
ZONES = "Etc/GMT-3\nEtc/UTC\nEurope/Berlin\nEurope/Moscow\n"


@pytest.fixture
def client():
    with admin_client() as c:
        yield c


@pytest.fixture
def host(monkeypatch):
    """timedatectl on a fake host whose zone is Etc/UTC; every other host command fails the test."""
    state = {"zone": "Etc/UTC", "fail_set": None, "answer": None}
    calls: list[list[str]] = []

    def run_on_host(cmd, *, timeout=60, check=False, input=None, errors=None):
        # timedatectl runs under the host's timeout (ADR-0258); the fake answers what it ran.
        assert cmd[:2] == ["timeout", "10"] and timeout > 10, cmd
        cmd = cmd[2:]
        calls.append(list(cmd))
        if state["answer"] is not None:
            return state["answer"]
        if cmd == ["timedatectl", "show", "--property=Timezone", "--value"]:
            return HostResult(0, state["zone"] + "\n", "")
        if cmd == ["timedatectl", "list-timezones"]:
            return HostResult(0, ZONES, "")
        if cmd[:2] == ["timedatectl", "set-timezone"] and len(cmd) == 3:
            if state["fail_set"]:
                return HostResult(1, "", state["fail_set"])
            state["zone"] = cmd[2]
            return HostResult(0, "", "")
        raise AssertionError(f"unexpected host command {cmd}")

    def no_container_command(cmd, *args, **kwargs):
        raise AssertionError(f"ran in the container: {cmd}")

    monkeypatch.setattr(src.host, "run_on_host", run_on_host)
    monkeypatch.setattr(subprocess, "run", no_container_command)
    state["calls"] = calls
    return state


def test_the_time_zone_is_the_hosts(client, host):
    response = client.get(f"{API}/timezone")

    assert response.status_code == 200, response.text
    assert response.json() == {"timezone": "Etc/UTC"}


def test_the_zones_are_those_of_the_host(client, host):
    response = client.get(f"{API}/timezones")

    assert response.json() == {"timezones": ["Etc/GMT-3", "Etc/UTC", "Europe/Berlin", "Europe/Moscow"]}


def test_setting_the_time_zone_changes_the_hosts(client, host):
    response = client.put(f"{API}/timezone", json={"timezone": "Europe/Berlin"})

    assert response.status_code == 200, response.text
    assert response.json() == {"status": "ok", "timezone": "Europe/Berlin"}
    assert ["timedatectl", "set-timezone", "Europe/Berlin"] in host["calls"]
    assert client.get(f"{API}/timezone").json() == {"timezone": "Europe/Berlin"}


def test_every_zone_the_host_lists_can_be_set(client, host):
    """The names of tzdata carry digits, '+' and '-' (Etc/GMT-3, America/Port-au-Prince)."""
    response = client.put(f"{API}/timezone", json={"timezone": "Etc/GMT-3"})

    assert response.status_code == 200, response.text
    assert ["timedatectl", "set-timezone", "Etc/GMT-3"] in host["calls"]


@pytest.mark.parametrize("value", ["-h", "--host=x", "/etc/passwd", "../x", "a//b"])
def test_an_option_or_a_path_is_never_passed(client, host, value):
    host["answer"] = HostResult(1, "", "timedatectl gave no list")  # also without the host's list

    response = client.put(f"{API}/timezone", json={"timezone": value})

    assert response.status_code == 400
    assert host["calls"] == [["timedatectl", "list-timezones"]]


def test_a_zone_the_host_does_not_know_is_refused(client, host):
    response = client.put(f"{API}/timezone", json={"timezone": "Mars/Olympus"})

    assert response.status_code == 400
    assert not [call for call in host["calls"] if call[:2] == ["timedatectl", "set-timezone"]]


def test_a_failed_change_says_why(client, host):
    host["fail_set"] = "Failed to set time zone: Access denied"

    response = client.put(f"{API}/timezone", json={"timezone": "Europe/Berlin"})

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to set the time zone: Failed to set time zone: Access denied"


def test_a_host_without_timedatectl_has_an_unknown_zone(client, host):
    # nsenter's answer when the host lacks the program.
    host["answer"] = HostResult(127, "", "nsenter: failed to execute timedatectl: No such file or directory")

    assert client.get(f"{API}/timezone").json() == {"timezone": "Unknown"}
    assert client.get(f"{API}/timezones").json() == {"timezones": []}
    assert client.put(f"{API}/timezone", json={"timezone": "Europe/Berlin"}).status_code == 500


def test_a_timedatectl_without_an_answer_in_time_says_so(client, host):
    host["answer"] = HostResult(124, "", "")  # the host's timeout ended it

    assert client.get(f"{API}/timezone").json() == {"timezone": "Unknown"}
    response = client.put(f"{API}/timezone", json={"timezone": "Europe/Berlin"})
    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to set the time zone: no answer in time"


def test_the_hostname_is_set_on_the_network_page_alone(client, host):
    """PUT /system/hostname ran hostnamectl in the container; PUT /network/hostname sets it on the host."""
    response = client.put(f"{API}/hostname", json={"hostname": "nas"})

    assert response.status_code in (404, 405)
