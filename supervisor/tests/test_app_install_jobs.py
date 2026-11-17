"""An app's install runs as a background job whose progress the App Store shows
(apps.install-progress, ADR-0383).

POST /api/v1/apps/install answers 202 at once with the job; the checks, the pulls, the creates and
the starts run in a thread of their own, and the job reports each image's pull (its layers and
bytes, as Docker's pull stream says them) and each container's start, then its end: the install's
answer, or why it failed with the status that the request would have answered. The app is the
synthetic Photos of test_app_stacks.py: a server with the companions database (with a health
check), cache and machine-learning, on the strict fake Docker of docker_fake.py.
"""

from __future__ import annotations

import importlib
import threading
import time
from unittest.mock import ANY

import pytest

from src.apps.schemas import AppInstallJob

from . import test_app_stacks, test_apps
from .docker_fake import StackDocker
from .test_app_stacks import CACHE, DATABASE, LEARNING, NAMES, NETWORK, PHOTOS_INSTALL, SERVER, STARTING, _audit
from .test_apps import INSTALL

api = test_apps.api
user_api = test_apps.user_api
pools = test_apps.pools
soc = test_apps.soc
shares = test_apps.shares
photos = test_app_stacks.photos
docker = test_app_stacks.docker
clock = test_app_stacks.clock

apps_router = importlib.import_module("src.apps.router")
install_jobs = importlib.import_module("src.apps.install_jobs")
stacks_module = importlib.import_module("src.apps.stacks")

JOBS = "/api/v1/apps/install/jobs"
# Photos's containers as a new job lists them: in their start order, none made yet.
WAITING = [
    {"name": "photos-database", "companion": "database", "state": "waiting"},
    {"name": "photos-cache", "companion": "cache", "state": "waiting"},
    {"name": "photos-machine-learning", "companion": "machine-learning", "state": "waiting"},
    {"name": "photos", "companion": None, "state": "waiting"},
]
# Its images, each once, in the order of the containers that run them.
IMAGES = [DATABASE, CACHE, LEARNING, SERVER]


@pytest.fixture
def published(monkeypatch) -> list[AppInstallJob]:
    """Every state of a job that its thread publishes, in order, with every line of a pull
    published (no wait between two reports)."""
    seen: list[AppInstallJob] = []
    real = install_jobs._publish

    def record(job: AppInstallJob) -> None:
        seen.append(job)
        real(job)

    monkeypatch.setattr(install_jobs, "_publish", record)
    monkeypatch.setattr(install_jobs, "PROGRESS_INTERVAL_SECONDS", 0)
    return seen


@pytest.fixture
def held(monkeypatch, docker: StackDocker) -> threading.Event:
    """Docker held: the install's first request to Docker waits until the event is set."""
    release = threading.Event()

    def client() -> StackDocker:
        assert release.wait(10), "the test did not release Docker"
        return docker

    monkeypatch.setattr(apps_router, "get_client", client)
    yield release
    release.set()


def _started(api, body: dict = PHOTOS_INSTALL) -> dict:
    response = api.post(INSTALL, json=body)
    assert response.status_code == 202, response.text
    return response.json()


def _end(api, job: dict) -> dict:
    """The job once it ended, as GET answers it when it waits for the end."""
    response = api.get(f"{JOBS}/{job['id']}", params={"wait": 10})
    assert response.status_code == 200, response.text
    ended = response.json()
    assert ended["state"] != "running", ended
    return ended


def _ended(api, body: dict = PHOTOS_INSTALL) -> dict:
    return _end(api, _started(api, body))


def _distinct(values: list) -> list:
    """``values`` without the repeats of a value right after itself."""
    return [value for i, value in enumerate(values) if i == 0 or value != values[i - 1]]


class TestTheRequest:
    def test_it_answers_202_at_once_and_the_install_runs_in_the_background(self, api, pools, docker, held):
        job = _started(api)

        assert job == {
            "id": ANY, "template_id": "photos", "name": "Photos", "state": "running", "step": "checking",
            "started_at": ANY, "finished_at": None, "images": [], "containers": WAITING, "result": None,
            "error": None, "error_status": None,
        }
        # Nothing was asked of Docker yet, and the job is listed while it runs.
        assert docker.calls == []
        assert api.get(JOBS).json() == [job]

        held.set()
        ended = _end(api, job)
        assert (ended["id"], ended["state"], ended["error"]) == (job["id"], "succeeded", None)
        assert ended["finished_at"] is not None
        assert docker.containers_.keys() == set(NAMES)

    @pytest.mark.parametrize(("body", "detail"), [
        ({**PHOTOS_INSTALL, "port_overrides": {2283: 70000}}, "Host port for 2283/tcp must be between 1 and 65535"),
        ({**PHOTOS_INSTALL, "env_overrides": {"DB_HOSTNAME": "db"}}, "DB_HOSTNAME is set by UNOS and cannot be changed"),
        ({**PHOTOS_INSTALL, "run_in_unos_smb": True},
         "Photos cannot run in the group unos-smb: it runs in several containers, and the group would reach only "
         "one of them"),
    ])
    def test_a_request_that_the_template_refuses_is_answered_at_once_without_a_job(
            self, api, pools, docker, soc, body, detail):
        response = api.post(INSTALL, json=body)

        assert (response.status_code, response.json()) == (400, {"detail": detail})
        assert install_jobs.current() == []
        assert docker.calls == [] and soc.commands == []

    def test_an_unknown_app_is_answered_404_at_once(self, api, pools, docker):
        response = api.post(INSTALL, json={"template_id": "nope"})

        assert (response.status_code, response.json()) == (404, {"detail": "App nope not found"})
        assert install_jobs.current() == []

    def test_one_install_runs_at_a_time(self, api, pools, docker, held):
        _started(api)

        same = api.post(INSTALL, json=PHOTOS_INSTALL)
        other = api.post(INSTALL, json={"template_id": "dozzle"})

        assert (same.status_code, same.json()) == (
            409, {"detail": "Photos is being installed already: wait for the end of its install"}
        )
        assert (other.status_code, other.json()) == (
            409, {"detail": "An install of Photos is under way: install Dozzle once it has ended"}
        )
        assert [job.template_id for job in install_jobs.current()] == ["photos"]
        held.set()
        assert _end(api, {"id": install_jobs.current()[0].id})["state"] == "succeeded"

    def test_the_job_holds_the_apps_lock_until_its_end(self, api, pools, docker, held):
        job = _started(api)

        with pytest.raises(Exception) as refused:
            with stacks_module.app_lock("photos", "Photos"):
                pass
        assert getattr(refused.value, "status_code", None) == 409

        held.set()
        _end(api, job)
        with stacks_module.app_lock("photos", "Photos"):
            pass

    def test_an_install_of_an_app_under_another_change_is_refused(self, api, pools, docker):
        with stacks_module.app_lock("photos", "Photos"):
            response = api.post(INSTALL, json=PHOTOS_INSTALL)

        assert (response.status_code, response.json()) == (
            409, {"detail": "Another change of Photos is under way; try again when it has finished"}
        )
        assert install_jobs.current() == []

    def test_only_an_administrator_installs_or_reads_or_dismisses_jobs(self, user_api, pools, docker):
        assert user_api.post(INSTALL, json=PHOTOS_INSTALL).status_code == 403
        assert user_api.get(JOBS).status_code == 403
        assert user_api.get(f"{JOBS}/x").status_code == 403
        assert user_api.delete(f"{JOBS}/x").status_code == 403
        assert install_jobs.current() == [] and docker.calls == []


class TestProgress:
    def test_each_image_reports_its_layers_and_bytes_as_dockers_pull_stream_says_them(
            self, api, pools, docker, published):
        job = _ended(api)

        assert [image["image"] for image in job["images"]] == IMAGES
        assert [(i["state"], i["layers"], i["layers_done"], i["bytes_total"], i["bytes_done"]) for i in job["images"]] == [
            ("pulled", 2, 2, 4000, 4000)
        ] * 4
        # The first image on its way: each layer named, the first downloaded in two steps of its
        # 3000 bytes and complete, then the second's 1000 bytes.
        first = [
            (image.layers, image.layers_done, image.bytes_total, image.bytes_done)
            for image in (j.images[0] for j in published if j.images and j.images[0].state == "pulling")
        ]
        assert _distinct(first) == [
            (0, 0, 0, 0), (1, 0, 0, 0), (2, 0, 0, 0), (2, 0, 3000, 1500), (2, 0, 3000, 3000), (2, 1, 3000, 3000),
            (2, 1, 4000, 3500), (2, 1, 4000, 4000), (2, 2, 4000, 4000),
        ]
        # The images are pulled one after the other: each waits until the one before it is pulled.
        states = _distinct([tuple(image.state for image in j.images) for j in published if j.images])
        assert states == [
            ("waiting",) * 4,
            ("pulling", "waiting", "waiting", "waiting"),
            ("pulled", "waiting", "waiting", "waiting"),
            ("pulled", "pulling", "waiting", "waiting"),
            ("pulled", "pulled", "waiting", "waiting"),
            ("pulled", "pulled", "pulling", "waiting"),
            ("pulled", "pulled", "pulled", "waiting"),
            ("pulled", "pulled", "pulled", "pulling"),
            ("pulled",) * 4,
        ]

    def test_an_image_that_docker_has_is_reported_present_and_not_pulled(self, api, pools, docker):
        docker.pulled(DATABASE)

        job = _ended(api)

        assert job["images"][0] == {
            "image": DATABASE, "state": "present", "layers": 0, "layers_done": 0, "bytes_total": 0, "bytes_done": 0
        }
        assert [subject for _, subject in docker.called("api.pull")] == [
            "example/valkey@" + CACHE.partition("@")[2],
            "example/photos-ml@" + LEARNING.partition("@")[2],
            "example/photos-server@" + SERVER.partition("@")[2],
        ]

    def test_each_container_is_reported_created_then_starting_then_running_in_its_order(
            self, api, pools, docker, published):
        job = _ended(api)

        seen: list[tuple[str, str]] = []
        for state in published:
            for container in state.containers:
                if container.state != "waiting" and (container.name, container.state) not in seen:
                    seen.append((container.name, container.state))
        assert seen == [(name, "created") for name in NAMES] + [
            (name, state) for name in NAMES for state in ("starting", "running")
        ]
        assert _distinct([state.step for state in published]) == ["checking", "pulling", "creating", "starting"]
        assert [c["state"] for c in job["containers"]] == ["running"] * 4

    def test_the_end_holds_the_installs_answer_and_its_one_audit_entry(self, api, pools, docker):
        job = _ended(api)

        result = job["result"]
        assert (result["name"], result["web_url"], result["pool"]) == ("photos", "http://localhost:2283", "alpha")
        assert [c["container"] for c in result["companions"]] == NAMES[:3]
        password = pools.alpha / "apps/photos/database/password"
        assert _audit() == [{
            "action": "app_installed", "resource_type": "app", "resource_id": "photos",
            "details": "Installed Photos 1.0 from the catalog as photos with the containers photos-database, "
            f"photos-cache, photos-machine-learning and photos, with its data on the pool alpha; generated its "
            f"password file {password}",
            "severity": "info", "success": True,
        }]


class TestFailures:
    def test_a_check_that_refuses_ends_the_job_with_the_status_and_detail_of_the_refusal(
            self, api, pools, docker, soc):
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:2283   0.0.0.0:*   users:(("nginx",pid=9,fd=7))']

        job = _ended(api)

        assert (job["state"], job["step"], job["error_status"]) == ("failed", "checking", 409)
        assert job["error"] == (
            "Port 2283/tcp is already in use on the host by nginx: choose another host port for 2283/tcp"
        )
        assert (job["images"], job["result"]) == ([], None)
        assert docker.called("api.pull", "networks.create", "api.create_container") == []
        assert _audit() == []

    def test_an_error_in_a_pull_stream_fails_the_install_before_anything_is_made(self, api, pools, docker):
        docker.publish(CACHE, volumes=("/data",), pull_error="unexpected EOF")

        job = _ended(api)

        assert (job["state"], job["step"], job["error_status"]) == ("failed", "pulling", 500)
        assert job["error"] == f"Failed to pull image {CACHE}: unexpected EOF"
        assert [image["state"] for image in job["images"]] == ["pulled", "failed", "waiting", "waiting"]
        assert docker.called("networks.create", "api.create_container") == []
        assert not (pools.alpha / "apps").exists()

    def test_a_failed_start_reports_the_container_that_failed_and_those_removed(self, api, pools, docker):
        docker.script("photos-database", {"Status": "exited", "Running": False, "ExitCode": 3})

        job = _ended(api)

        # The clean-up is a step of its own (ADR-0383).
        assert (job["state"], job["step"], job["error_status"]) == ("failed", "cleaning", 500)
        assert job["error"].startswith("photos-database stopped (exit code 3); its containers were removed")
        assert [(c["name"], c["state"]) for c in job["containers"]] == [
            ("photos-database", "failed"), ("photos-cache", "removed"), ("photos-machine-learning", "removed"),
            ("photos", "removed"),
        ]
        assert docker.containers_ == {}


class TestTheJobs:
    def test_an_ended_job_is_listed_until_it_is_dismissed(self, api, pools, docker):
        job = _ended(api)

        assert api.get(JOBS).json() == [job]
        assert api.delete(f"{JOBS}/{job['id']}").status_code == 204
        assert api.get(JOBS).json() == []
        for response in (api.get(f"{JOBS}/{job['id']}"), api.delete(f"{JOBS}/{job['id']}")):
            assert (response.status_code, response.json()) == (404, {"detail": "No such install job"})

    def test_a_delete_of_a_running_job_cancels_it(self, api, pools, docker, held):
        job = _started(api)

        response = api.delete(f"{JOBS}/{job['id']}")

        # The cancel is asked: the job answers as it is, still running, until it reaches a step.
        assert (response.status_code, response.json()["id"], response.json()["state"]) == (202, job["id"], "running")
        held.set()
        ended = _end(api, job)
        assert (ended["state"], ended["error_status"]) == ("cancelled", None)
        assert ended["error"] == "Cancelled by admin before any container of Photos was made"
        assert docker.called("api.pull", "networks.create", "api.create_container") == []
        assert _audit() == []
        # A cancelled job is dismissed like any job that ended.
        assert api.delete(f"{JOBS}/{job['id']}").status_code == 204

    def test_waiting_gets_are_bounded(self, api, pools, docker, held, monkeypatch):
        # Each waiting GET holds a connection that counts against uvicorn's --limit-concurrency
        # (ADR-0333): a few at a time, then 429.
        monkeypatch.setattr(install_jobs, "MAX_WAITERS", 1)
        job = _started(api)
        first = threading.Thread(target=api.get, args=(f"{JOBS}/{job['id']}",), kwargs={"params": {"wait": 3}})
        first.start()
        deadline = time.monotonic() + 5
        while install_jobs._waiters < 1 and time.monotonic() < deadline:
            time.sleep(0.01)

        second = api.get(f"{JOBS}/{job['id']}", params={"wait": 3})
        unwaited = api.get(f"{JOBS}/{job['id']}")

        assert (second.status_code, second.json()) == (
            429, {"detail": "Too many requests wait for install jobs; try again in a moment"}
        )
        assert unwaited.status_code == 200
        held.set()
        first.join(10)
        assert _end(api, job)["state"] == "succeeded"

    def test_a_new_install_of_an_app_replaces_its_last_job(self, api, pools, docker, soc):
        soc.sockets = ['tcp   LISTEN 0      4096   0.0.0.0:2283   0.0.0.0:*   users:(("nginx",pid=9,fd=7))']
        failed = _ended(api)
        soc.sockets = []

        succeeded = _ended(api)

        assert failed["id"] != succeeded["id"]
        assert api.get(JOBS).json() == [succeeded]

    def test_a_wait_answers_the_running_job_once_it_has_passed(self, api, pools, docker, held):
        job = _started(api)

        began = time.monotonic()
        response = api.get(f"{JOBS}/{job['id']}", params={"wait": 1})

        assert response.status_code == 200
        assert response.json()["state"] == "running"
        assert time.monotonic() - began >= 1
        held.set()
        assert _end(api, job)["state"] == "succeeded"

    def test_a_wait_holds_no_database_session(self, api, pools, docker, held, monkeypatch):
        # The session that checked the credentials is closed before the wait (ADR-0306).
        from sqlalchemy.ext.asyncio import AsyncSession

        closed = []
        real_close = AsyncSession.close

        async def close(session):
            closed.append(time.monotonic())
            await real_close(session)

        monkeypatch.setattr(AsyncSession, "close", close)
        job = _started(api)
        closed.clear()

        began = time.monotonic()
        api.get(f"{JOBS}/{job['id']}", params={"wait": 1})

        assert closed and closed[0] - began < 0.5
        held.set()
        assert _end(api, job)["state"] == "succeeded"

    @pytest.mark.parametrize("wait", [-1, 3601])
    def test_a_wait_outside_0_to_3600_seconds_is_refused(self, api, pools, docker, wait):
        job = _ended(api)

        assert api.get(f"{JOBS}/{job['id']}", params={"wait": wait}).status_code == 422


class TestAStopOfTheSupervisor:
    def test_a_stop_before_any_container_ends_the_job_with_nothing_made(self, api, pools, docker, held):
        job = _started(api)

        install_jobs.stop_for_shutdown(timeout=0.1)  # the job is held in Docker: the wait ends first
        held.set()
        ended = _end(api, job)

        assert (ended["state"], ended["error_status"]) == ("failed", 503)
        assert ended["error"] == (
            "A stop of the supervisor stopped the install of Photos before it made any container"
        )
        assert docker.called("api.pull", "networks.create", "api.create_container") == []
        assert _audit() == []

    def test_a_stop_during_a_pull_ends_the_job_before_any_container(self, api, pools, docker):
        def stop_at_the_first_download(ref: str, line: dict) -> None:
            if line.get("status") == "Downloading":
                install_jobs._running.stop.set()

        docker.pull_hook = stop_at_the_first_download

        job = _ended(api)

        assert (job["state"], job["step"], job["error_status"]) == ("failed", "pulling", 503)
        assert docker.called("networks.create", "api.create_container") == []
        assert not (pools.alpha / "apps").exists()

    def test_a_stop_after_a_container_was_made_leaves_what_it_made_and_audits_it(
            self, api, pools, docker, clock, monkeypatch):
        # The database never becomes healthy; the stop comes while the install waits for it.
        docker.script("photos-database", STARTING)
        sleep = clock.sleep

        def stop_while_waiting(seconds: float) -> None:
            install_jobs._running.stop.set()
            sleep(seconds)

        monkeypatch.setattr(clock, "sleep", stop_while_waiting)

        job = _ended(api)

        made = "photos-database, photos-cache, photos-machine-learning, photos and the network unos-app-photos"
        data = pools.alpha / "apps/photos/database/data"
        assert (job["state"], job["step"], job["error_status"]) == ("failed", "starting", 500)
        assert job["error"] == (
            f"A stop of the supervisor stopped the install of Photos; it left {made}: start or uninstall Photos on "
            f"the Installed tab. {data} was empty before this install and may hold data that photos-database did "
            "not finish setting up: if you uninstall Photos, move it away before you install Photos again."
        )
        # Nothing is stopped or removed: a database halfway through its first start is not killed.
        assert docker.called("container.stop", "container.remove") == []
        assert set(docker.containers_) == set(NAMES) and NETWORK in docker.networks_
        assert docker.containers_["photos-database"].status == "running"
        assert _audit() == [{
            "action": "app_installed", "resource_type": "app", "resource_id": "photos",
            "details": f"Created {made}; then: stopped by a stop of the supervisor; left behind: {made}",
            "severity": "info", "success": False,
        }]


def test_the_e2e_fakes_have_the_fields_of_the_servers_jobs():
    """apps-fakes.ts answers and sends install jobs with the fields of the server's AppInstallJob,
    ImagePull and ContainerStart (ADR-0189), so that the App Store's specs see what the server
    sends."""
    fakes = (test_app_stacks.WEBUI_TESTS / "apps-fakes.ts").read_text(encoding="utf-8")
    schemas = importlib.import_module("src.apps.schemas")

    for fake, model in (
        ("FakeInstallJob", schemas.AppInstallJob),
        ("FakeImagePull", schemas.ImagePull),
        ("FakeContainerStart", schemas.ContainerStart),
    ):
        assert test_app_stacks._interface_fields(fakes, fake) == list(model.model_fields), fake


class TestThePullStream:
    """Progress.pull_line reads the lines of Docker's pull stream as docker-py decodes them."""

    @staticmethod
    def _progress(monkeypatch) -> install_jobs.Progress:
        monkeypatch.setattr(install_jobs, "_publish", lambda job: None)
        job = AppInstallJob(id="j", template_id="photos", name="Photos", started_at="2026-11-17T10:00:00Z")
        progress = install_jobs.Progress(job, install_jobs._Running(job_id="j", name="Photos"))
        progress.pulling([CACHE])
        return progress

    def test_layers_already_there_count_as_done_without_bytes(self, monkeypatch):
        progress = self._progress(monkeypatch)
        for line in (
            {"status": "Pulling from example/valkey", "id": "9"},
            {"status": "Already exists", "progressDetail": {}, "id": "aaa"},
            {"status": "Pulling fs layer", "progressDetail": {}, "id": "bbb"},
            {"status": "Waiting", "progressDetail": {}, "id": "bbb"},
            {"status": "Downloading", "progressDetail": {"current": 100, "total": 1000}, "id": "bbb"},
            {"status": "Retrying in 5 seconds", "progressDetail": {}, "id": "bbb"},
            {"status": "Downloading", "progressDetail": {"current": 400, "total": 1000}, "id": "bbb"},
            {"status": "Verifying Checksum", "progressDetail": {}, "id": "bbb"},
            {"status": "Digest: sha256:" + "3" * 64},
        ):
            progress.pull_line(CACHE, line)
        progress.pulled(CACHE, "pulling")

        image = progress.job.images[0]
        assert (image.layers, image.layers_done, image.bytes_total, image.bytes_done) == (2, 1, 1000, 1000)

    def test_an_error_line_fails_the_pull_with_dockers_message(self, monkeypatch):
        progress = self._progress(monkeypatch)

        with pytest.raises(Exception) as failed:
            progress.pull_line(CACHE, {"errorDetail": {"message": "toomanyrequests: rate limit"}, "error": "x"})

        assert str(failed.value) == "toomanyrequests: rate limit"
        assert isinstance(failed.value, apps_router._DOCKER_ERRORS)



class TestAStalledPull:
    def test_a_pull_waits_at_most_ten_minutes_for_each_line(self, api, pools, docker):
        job = _ended(api)

        assert job["state"] == "succeeded"
        assert {call.kwargs["timeout"] for call in docker.calls if call.name == "api.pull"} == {(60, 600)}

    def test_a_pull_that_sends_nothing_for_ten_minutes_fails_the_install(self, api, pools, docker, caplog):
        docker.stall("example/valkey@" + CACHE.partition("@")[2], after=3)

        job = _ended(api)

        assert (job["state"], job["step"], job["error_status"]) == ("failed", "pulling", 500)
        assert job["error"] == (
            f"Failed to pull image {CACHE}: Docker sent nothing about it for 10 minutes, so the pull was stopped; "
            "install the app again to try once more"
        )
        assert [image["state"] for image in job["images"]] == ["pulled", "failed", "waiting", "waiting"]
        assert docker.called("networks.create", "api.create_container") == []
        # A job that failed says so in the supervisor's log, once.
        assert [r.getMessage() for r in caplog.records if r.name == "src.apps.install_jobs"] == [
            f"The install of Photos failed (500): {job['error']}"
        ]


class TestCancel:
    @staticmethod
    def _blocked(docker: StackDocker) -> threading.Event:
        """The pull of the cache's image blocks after its second line, until its socket is shut
        down; the event is set once it blocks."""
        ref = "example/valkey@" + CACHE.partition("@")[2]
        docker.stall(ref, after=2, block=True)
        blocked = threading.Event()
        lines = []

        def hook(pulled: str, line: dict) -> None:
            if pulled == ref:
                lines.append(line)
                if len(lines) == 2:
                    blocked.set()

        docker.pull_hook = hook
        return blocked

    def test_a_cancel_ends_a_pull_that_blocks(self, api, pools, docker):
        blocked = self._blocked(docker)
        job = _started(api)
        assert blocked.wait(10)

        response = api.delete(f"{JOBS}/{job['id']}")

        assert response.status_code == 202
        ended = _end(api, job)
        assert (ended["state"], ended["step"]) == ("cancelled", "pulling")
        assert ended["error"] == "Cancelled by admin before any container of Photos was made"
        assert [image["state"] for image in ended["images"]] == ["pulled", "failed", "waiting", "waiting"]
        assert docker.called("networks.create", "api.create_container") == []

    def test_a_stop_of_the_supervisor_ends_a_pull_that_blocks(self, api, pools, docker):
        blocked = self._blocked(docker)
        job = _started(api)
        assert blocked.wait(10)

        began = time.monotonic()
        install_jobs.stop_for_shutdown(timeout=5)

        assert time.monotonic() - began < 2
        ended = _end(api, job)
        assert (ended["state"], ended["error_status"]) == ("failed", 503)

    def test_a_cancel_after_a_container_was_made_removes_what_the_install_made(
            self, api, pools, docker, clock, monkeypatch):
        # The database never becomes healthy; the cancel comes while the install waits for it.
        docker.script("photos-database", STARTING)
        sleep = clock.sleep

        def cancel_while_waiting(seconds: float) -> None:
            install_jobs.cancel(install_jobs._running.job_id, "admin")
            sleep(seconds)

        monkeypatch.setattr(clock, "sleep", cancel_while_waiting)

        job = _ended(api)

        assert (job["state"], job["step"], job["error_status"]) == ("cancelled", "cleaning", None)
        assert job["error"].startswith("Cancelled by admin; its containers were removed and its data folders kept")
        assert [c["state"] for c in job["containers"]] == ["removed"] * 4
        # As after a failure (ADR-0381): the database it started is stopped with its stop timeout.
        assert ("container.stop", "photos-database") in docker.called("container.stop")
        assert docker.containers_ == {} and NETWORK not in docker.networks_
        assert _audit() == []

    def test_a_stop_after_the_network_alone_says_so(self, api, pools, docker, monkeypatch):
        create = docker.networks.create

        def create_then_stop(*args, **kwargs):
            network = create(*args, **kwargs)
            install_jobs._running.stop.set()
            return network

        monkeypatch.setattr(docker.networks, "create", create_then_stop)

        job = _ended(api)

        assert (job["state"], job["step"], job["error_status"]) == ("failed", "creating", 500)
        assert job["error"] == (
            "A stop of the supervisor stopped the install of Photos after it made the network unos-app-photos and "
            "before any container: install Photos again, which uses that network again"
        )
        assert _audit()[0]["details"] == (
            "Created the network unos-app-photos; then: stopped by a stop of the supervisor; left behind: the "
            "network unos-app-photos"
        )


class TestTheJobsOwnBookkeeping:
    def test_a_job_that_cannot_be_made_frees_the_app(self, photos, monkeypatch):
        def broken(**kwargs):
            raise ValueError("broken")

        monkeypatch.setattr(install_jobs, "AppInstallJob", broken)

        with pytest.raises(ValueError):
            install_jobs.start(photos, lambda progress: None)

        assert install_jobs._running is None
        with stacks_module.app_lock("photos", "Photos"):
            pass

    def test_the_slot_is_free_even_when_the_end_cannot_be_published(self, photos, monkeypatch):
        real = install_jobs._publish

        def publish(job):
            if job.state != "running":
                raise RuntimeError("not published")
            real(job)

        monkeypatch.setattr(install_jobs, "_publish", publish)
        install_jobs.start(photos, lambda progress: None)
        install_jobs._running.thread.join(5)

        assert install_jobs._running is None


class TestAStopOfUvicorn:
    """A stop of the supervisor's uvicorn reaches the lifespan's shutdown also while a GET waits for
    a job's end, within Docker's 10 s: uvicorn ends the requests that are still open after
    --timeout-graceful-shutdown (supervisor/Dockerfile), and the shutdown then stops the install."""

    def test_a_waiting_get_does_not_hold_back_the_shutdown_that_stops_the_install(
            self, api, photos, tmp_path, monkeypatch):
        import socket
        import urllib.request

        import uvicorn
        from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
        from sqlalchemy.pool import NullPool

        from src import database
        from src.main import app

        from .test_request_limits import _uvicorn_options

        engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'unos.db'}", poolclass=NullPool)
        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "async_session", async_sessionmaker(engine, expire_on_commit=False))
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
        graceful = _uvicorn_options("Dockerfile")["timeout_graceful_shutdown"]

        def work(progress):
            while True:  # an install that runs until it is stopped
                progress.check()
                time.sleep(0.02)

        sock = socket.socket()
        sock.bind(("127.0.0.1", 0))
        server = uvicorn.Server(uvicorn.Config(app, log_config=None, lifespan="on", timeout_graceful_shutdown=graceful))
        thread = threading.Thread(target=server.run, kwargs={"sockets": [sock]}, daemon=True)
        thread.start()
        try:
            deadline = time.monotonic() + 30
            while not server.started:
                assert thread.is_alive() and time.monotonic() < deadline, "uvicorn did not start"
                time.sleep(0.05)
            job = install_jobs.start(photos, work)
            url = f"http://127.0.0.1:{sock.getsockname()[1]}{JOBS}/{job.id}?wait=60"
            opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
            waiting = threading.Thread(target=lambda: opener.open(url, timeout=70).read(), daemon=True)
            waiting.start()
            while install_jobs._waiters < 1 and time.monotonic() < deadline:
                time.sleep(0.02)
            assert install_jobs._waiters == 1

            began = time.monotonic()
            server.should_exit = True
            thread.join(9)

            assert not thread.is_alive(), "the shutdown waited for the GET"
            assert time.monotonic() - began < 9
            ended = install_jobs.get(job.id)
            assert (ended.state, ended.error_status) == ("failed", 503)
        finally:
            server.should_exit = True
            thread.join(10)
            sock.close()
