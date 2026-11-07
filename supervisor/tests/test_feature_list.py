"""The feature ledger feature-list.json (ADR-0203) and the progress journal PROGRESS.md (ADR-0204).

The ledger:
* is a JSON object with a description and a list of features, written as
  json.dumps(ledger, indent=2, ensure_ascii=False) plus a newline, so that a pass changes
  single lines;
* every feature has id, category, description, steps and passes, and may have verified_by,
  adr and notes, nothing else;
* id is <category>.<slug> and unique, and category is one of the areas of docs/adr;
* verified_by names files of the repository, and a passing feature names at least one test;
* every ADR id the ledger mentions has its file in docs/adr;
* compared with the ledger at the branch's base, a feature is dropped, or its description or
  steps change, only with an ADR that is new since the base (its number is not in the base's
  docs/adr) and names the feature's id; a changed feature also lists that ADR in adr. The
  base is FEATURE_LIST_BASE (a git revision, which must hold a ledger) or
  `git merge-base HEAD origin/main`; without a merge base, or without a ledger there, the
  comparison with the base is skipped. So it guards nothing until main has a ledger, and CI
  needs the history of origin/main in its checkout (ci.ledger-changes-checked). The rule
  itself runs on small fixture ledgers in every run (TestTheRequirementChangeRule), and the
  reading of a base on a throwaway repository (TestTheBase).

The journal has the sections Now, Dev device and Log, in this order, and Log entries are
dated, newest first.

Feature ids named in the text of either file exist in the ledger.

Both are English and name no scratch or personal paths.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Collection, Iterable, Mapping
from datetime import date
from pathlib import Path
from typing import NamedTuple

import pytest

REPO = Path(__file__).resolve().parents[2]
LEDGER = REPO / "feature-list.json"
JOURNAL = REPO / "PROGRESS.md"
ADR_DIR = REPO / "docs" / "adr"

# The areas of the ADRs (docs/adr/README.md), in their order.
AREAS = (
    "process", "architecture", "security", "auth", "storage", "shares", "apps", "backup",
    "files", "system", "webui", "deploy", "device", "testing", "ci",
)
REQUIRED = {"id": str, "category": str, "description": str, "steps": list, "passes": bool}
OPTIONAL = {"verified_by": list, "adr": list, "notes": str}

ID = re.compile(r"[a-z]+\.[a-z0-9]+(?:-[a-z0-9]+)*")
ADR_ID = re.compile(r"\bADR-(\d{4})\b")
ADR_FILE = re.compile(r"(\d{4})-[a-z0-9]+(?:-[a-z0-9]+)*\.md")
TEST_FILE = re.compile(r"supervisor/tests/test_[A-Za-z0-9_]+\.py|webui/tests/[A-Za-z0-9_.-]+\.spec\.ts")
CYRILLIC = re.compile(r"[Ѐ-ӿ]")
FOREIGN_PATH = re.compile(r"/private/tmp|scratchpad|/Users/|/home/[a-z]")
# A feature id in running text, but not a file name such as deploy.sh or auth.spec.ts.
NOT_A_SLUG = "sh|py|md|json|ts|js|mjs|cjs|yml|yaml|toml|ini|txt|svelte|conf|cfg|env|lock|example|html|css|spec|test"
FEATURE_ID = re.compile(
    r"(?<![\w/.-])((?:%s)\.(?!(?:%s)\b)[a-z0-9]+(?:-[a-z0-9]+)*)(?![\w/-])" % ("|".join(AREAS), NOT_A_SLUG)
)
LOG_ENTRY = re.compile(r"### (\d{4}-\d{2}-\d{2})(?: to (\d{4}-\d{2}-\d{2}))?: \S")


def _ledger() -> dict:
    return json.loads(LEDGER.read_text(encoding="utf-8"))


def _features() -> list[dict]:
    return _ledger()["features"]


def _adr_files() -> dict[str, Path]:
    """ADR id -> its file in docs/adr."""
    files = {}
    for path in sorted(ADR_DIR.glob("[0-9][0-9][0-9][0-9]-*.md")):
        files[f"ADR-{path.name[:4]}"] = path
    return files


def _adr_texts() -> dict[str, str]:
    """ADR id -> the text of its file in docs/adr."""
    return {adr: path.read_text(encoding="utf-8") for adr, path in _adr_files().items()}


class TestLedgerFormat:
    def test_is_an_object_with_a_description_and_features(self):
        ledger = _ledger()

        assert set(ledger) == {"description", "features"}
        assert isinstance(ledger["description"], str) and ADR_ID.search(ledger["description"])
        assert isinstance(ledger["features"], list) and ledger["features"]

    def test_keeps_its_format(self):
        text = LEDGER.read_text(encoding="utf-8")

        assert text == json.dumps(json.loads(text), indent=2, ensure_ascii=False) + "\n", (
            "write feature-list.json as json.dumps(ledger, indent=2, ensure_ascii=False) + '\\n'"
        )

    def test_features_have_the_known_fields(self):
        problems = []
        for i, feature in enumerate(_features()):
            name = feature.get("id", f"feature {i}") if isinstance(feature, dict) else f"feature {i}"
            if not isinstance(feature, dict):
                problems.append(f"{name}: not an object")
                continue
            for key in set(REQUIRED) - set(feature):
                problems.append(f"{name}: no {key}")
            for key in set(feature) - set(REQUIRED) - set(OPTIONAL):
                problems.append(f"{name}: unknown field {key}")
            for key, kind in {**REQUIRED, **OPTIONAL}.items():
                if key in feature and not isinstance(feature[key], kind):
                    problems.append(f"{name}: {key} is not a {kind.__name__}")
        assert problems == []

    def test_ids_are_unique_category_slugs(self):
        features = _features()
        ids = [f["id"] for f in features]

        assert [i for i in ids if not ID.fullmatch(i)] == []
        assert sorted({i for i in ids if ids.count(i) > 1}) == []
        assert [f["id"] for f in features if f["id"].split(".")[0] != f["category"]] == []

    def test_categories_are_adr_areas(self):
        assert sorted({f["category"] for f in _features()} - set(AREAS)) == []

    def test_descriptions_and_steps_are_filled_in(self):
        problems = []
        for feature in _features():
            text = feature["description"]
            if not text.strip() or "\n" in text:
                problems.append(f"{feature['id']}: the description must be one line")
            steps = feature["steps"]
            if not steps or not all(isinstance(s, str) and s.strip() and "\n" not in s for s in steps):
                problems.append(f"{feature['id']}: steps must be a non-empty list of one-line strings")
            if "notes" in feature and not feature["notes"].strip():
                problems.append(f"{feature['id']}: empty notes")
        assert problems == []


class TestLedgerReferences:
    def test_verified_by_names_files_of_the_repository(self):
        problems = []
        for feature in _features():
            for path in feature.get("verified_by", []):
                file = (REPO / path).resolve()
                if not isinstance(path, str) or Path(path).is_absolute() or REPO not in file.parents:
                    problems.append(f"{feature['id']}: {path!r} is not a path inside the repository")
                elif not file.is_file():
                    problems.append(f"{feature['id']}: {path} does not exist")
        assert problems == []

    def test_a_passing_feature_names_its_tests(self):
        """passes turns true only after the scenario passed: the tests that showed it are named."""
        unproven = [
            f["id"] for f in _features()
            if f["passes"] and not any(TEST_FILE.fullmatch(p) for p in f.get("verified_by", []))
        ]
        assert unproven == []

    def test_adr_lists_hold_adr_ids(self):
        problems = []
        for feature in _features():
            adr = feature.get("adr", [])
            if not all(isinstance(a, str) and re.fullmatch(r"ADR-\d{4}", a) for a in adr):
                problems.append(f"{feature['id']}: {adr}")
            elif len(set(adr)) != len(adr):
                problems.append(f"{feature['id']}: an ADR id twice in {adr}")
        assert problems == []

    def test_every_adr_it_names_exists(self):
        files = _adr_files()
        missing = sorted({f"ADR-{n}" for n in ADR_ID.findall(LEDGER.read_text(encoding="utf-8"))} - set(files))

        assert missing == [], f"no file docs/adr/NNNN-*.md for {', '.join(missing)}"

    def test_feature_ids_in_the_text_exist(self):
        ids = {f["id"] for f in _features()}
        named = set(FEATURE_ID.findall(LEDGER.read_text(encoding="utf-8")))

        assert sorted(named - ids) == []

    def test_english_and_no_foreign_paths(self):
        text = LEDGER.read_text(encoding="utf-8")

        assert CYRILLIC.findall(text) == [], "repository text is English (ADR-0200)"
        assert FOREIGN_PATH.findall(text) == [], "name repository paths only"


# --- the comparison with the base: pure functions -------------------------------------


class Base(NamedTuple):
    """The features of the ledger and the ids of the ADRs at the branch's base."""

    features: list[dict]
    adrs: frozenset[str]


def adr_ids(names: Iterable[str]) -> frozenset[str]:
    """The ADR ids of file names in docs/adr (NNNN-slug.md); README.md and template.md are none."""
    return frozenset(f"ADR-{m.group(1)}" for name in names if (m := ADR_FILE.fullmatch(Path(name).name)))


def names_feature(text: str, feature_id: str) -> bool:
    """Whether ``text`` names ``feature_id`` itself, not a longer id or a path that contains it."""
    return re.search(r"(?<![\w/.-])%s(?![\w/-])" % re.escape(feature_id), text) is not None


def new_adrs_naming(feature_id: str, base_adrs: Collection[str], adr_texts: Mapping[str, str]) -> set[str]:
    """The ADRs added since the base that name ``feature_id``: only these may drop or change it."""
    return {adr for adr, text in adr_texts.items() if adr not in base_adrs and names_feature(text, feature_id)}


def dropped_without_a_new_adr(
    base: list[dict], now: list[dict], base_adrs: Collection[str], adr_texts: Mapping[str, str]
) -> list[str]:
    """The ids of the base's features that are gone, unless an ADR added since the base names them."""
    kept = {feature["id"] for feature in now}
    return [
        old["id"] for old in base
        if old["id"] not in kept and not new_adrs_naming(old["id"], base_adrs, adr_texts)
    ]


def reworded_without_a_new_adr(
    base: list[dict], now: list[dict], base_adrs: Collection[str], adr_texts: Mapping[str, str]
) -> list[str]:
    """The ids of features whose description or steps changed since the base, unless the feature's
    adr list names an ADR that was added since the base and names the feature's id."""
    current = {feature["id"]: feature for feature in now}
    reworded = []
    for old in base:
        new = current.get(old["id"])
        if new is None or (new["description"], new["steps"]) == (old["description"], old["steps"]):
            continue
        if not new_adrs_naming(old["id"], base_adrs, adr_texts) & set(new.get("adr", [])):
            reworded.append(old["id"])
    return reworded


# --- the comparison with the base: reading the base from git -------------------------


def _git(repo: Path, *args: str, **env: str) -> subprocess.CompletedProcess | None:
    """git in ``repo``, without the caller's GIT_* variables: a git hook's GIT_DIR or
    GIT_INDEX_FILE names another repository or index. ``env`` adds variables."""
    environ = {name: value for name, value in os.environ.items() if not name.startswith("GIT_")}
    try:
        return subprocess.run(
            ["git", "-C", str(repo), *args], capture_output=True, encoding="utf-8", timeout=60, env={**environ, **env}
        )
    except (OSError, subprocess.TimeoutExpired):
        return None


def read_base(revision: str, repo: Path = REPO) -> Base | None:
    """The ledger's features and the ADR ids at ``revision`` of ``repo``; None without a ledger there."""
    shown = _git(repo, "show", f"{revision}:feature-list.json")
    if shown is None or shown.returncode != 0:
        return None
    listed = _git(repo, "ls-tree", "-z", "--name-only", revision, "--", "docs/adr/")
    assert listed is not None and listed.returncode == 0, f"git ls-tree {revision} docs/adr/ failed"
    return Base(json.loads(shown.stdout)["features"], adr_ids(listed.stdout.split("\0")))


@pytest.fixture(scope="module")
def base() -> Base:
    """The ledger and the ADR ids at the branch's base (see the module docstring)."""
    revision = os.environ.get("FEATURE_LIST_BASE")
    if revision:
        found = read_base(revision)
        assert found is not None, f"FEATURE_LIST_BASE={revision} has no feature-list.json"
        return found
    merge_base = _git(REPO, "merge-base", "HEAD", "origin/main")
    if merge_base is None or merge_base.returncode != 0:
        pytest.skip("no merge base with origin/main to compare the ledger with")
    revision = merge_base.stdout.strip()
    found = read_base(revision)
    if found is None:
        pytest.skip(f"no feature-list.json at the base {revision[:12]}")
    return found


class TestRequirementsChangeOnlyWithANewAdr:
    """Entries are not dropped or reworded to make them pass (ADR-0203): this ledger against its base."""

    def test_no_feature_is_dropped_without_a_new_adr(self, base):
        dropped = dropped_without_a_new_adr(base.features, _features(), base.adrs, _adr_texts())

        assert dropped == [], "dropping a feature needs an ADR, added since the branch's base, that names its id"

    def test_no_description_or_steps_change_without_a_new_adr(self, base):
        reworded = reworded_without_a_new_adr(base.features, _features(), base.adrs, _adr_texts())

        assert reworded == [], (
            "a changed description or changed steps are a requirements change: record it in an ADR, "
            "added since the branch's base, that names the feature's id, and add that ADR's id to the "
            "feature's adr list"
        )


# --- the comparison with the base: the rule on fixture ledgers ------------------------


def _entry(feature_id: str, adr: tuple[str, ...] = ()) -> dict:
    """An entry of a fixture ledger."""
    return {
        "id": feature_id,
        "category": feature_id.split(".")[0],
        "description": f"The feature {feature_id}",
        "steps": ["The user sees it"],
        "passes": False,
        "adr": list(adr),
    }


# The fixture's ADR ids exist in docs/adr, but the texts are made up. At the base there were
# ADR-0001 (it names both entries) and the ledger BASE_LEDGER; ADR-0002 (it names files.trash)
# and ADR-0003 (it names only longer ids and paths that contain files.trash) were added since.
BASE_ADRS = frozenset({"ADR-0001"})
ADR_TEXTS = {
    "ADR-0001": "Decides files.trash and auth.two-step.",
    "ADR-0002": "Changes `files.trash`: deleted files stay in the trash for 30 days.",
    "ADR-0003": "Adds files.trash-limits, xfiles.trash, files.trashcan and docs/files.trash/notes.",
}
BASE_LEDGER = [_entry("files.trash"), _entry("auth.two-step", adr=("ADR-0001",))]


def _without(feature_id: str) -> list[dict]:
    """BASE_LEDGER without one entry."""
    return [dict(feature) for feature in BASE_LEDGER if feature["id"] != feature_id]


def _changed(feature_id: str, **fields) -> list[dict]:
    """BASE_LEDGER with ``fields`` of one entry replaced."""
    return [{**feature, **fields} if feature["id"] == feature_id else dict(feature) for feature in BASE_LEDGER]


def _refused(now: list[dict], adr_texts: Mapping[str, str] = ADR_TEXTS) -> tuple[list[str], list[str]]:
    """What the two comparisons refuse in ``now``: (dropped ids, reworded ids)."""
    return (
        dropped_without_a_new_adr(BASE_LEDGER, now, BASE_ADRS, adr_texts),
        reworded_without_a_new_adr(BASE_LEDGER, now, BASE_ADRS, adr_texts),
    )


NEW_STEPS = ["The user sees it", "The user empties the trash"]


class TestTheRequirementChangeRule:
    """The comparison with the base on fixture ledgers: it runs in every make test-fast, whatever
    main holds."""

    def test_an_unchanged_ledger_passes(self):
        assert _refused([dict(feature) for feature in BASE_LEDGER]) == ([], [])

    def test_passes_verified_by_notes_and_adr_are_not_compared(self):
        now = _changed(
            "files.trash", passes=True, verified_by=["supervisor/tests/test_files.py"], notes="Checked.",
            adr=["ADR-0003"],
        )

        assert _refused(now) == ([], [])

    def test_a_new_feature_needs_no_adr(self):
        assert _refused([*BASE_LEDGER, _entry("files.share-links")]) == ([], [])

    def test_a_drop_needs_an_adr(self):
        assert _refused(_without("files.trash"), {}) == (["files.trash"], [])

    def test_a_drop_named_only_by_an_adr_of_the_base_is_refused(self):
        assert _refused(_without("auth.two-step")) == (["auth.two-step"], [])

    def test_a_drop_named_by_a_new_adr_passes(self):
        assert _refused(_without("files.trash")) == ([], [])

    def test_a_new_adr_must_name_the_dropped_id_itself(self):
        texts = {adr: text for adr, text in ADR_TEXTS.items() if adr != "ADR-0002"}

        assert _refused(_without("files.trash"), texts) == (["files.trash"], [])

    def test_a_reworded_description_or_changed_steps_are_refused(self):
        texts = {"ADR-0001": ADR_TEXTS["ADR-0001"]}  # no ADR added since the base

        assert _refused(_changed("files.trash", description="The trash keeps deleted files"), texts) == (
            [], ["files.trash"]
        )
        assert _refused(_changed("files.trash", steps=NEW_STEPS), texts) == ([], ["files.trash"])

    def test_a_reword_with_an_adr_of_the_base_is_refused(self):
        """ADR-0001 names the entry, but it was there at the base: it decided the old steps."""
        assert _refused(_changed("files.trash", steps=NEW_STEPS, adr=["ADR-0001"])) == ([], ["files.trash"])

    def test_a_reword_with_a_new_adr_that_does_not_name_the_id_is_refused(self):
        assert _refused(_changed("files.trash", steps=NEW_STEPS, adr=["ADR-0003"])) == ([], ["files.trash"])

    def test_a_reword_with_a_new_adr_that_names_the_id_passes(self):
        now = _changed("files.trash", description="The trash keeps deleted files", steps=NEW_STEPS, adr=["ADR-0002"])

        assert _refused(now) == ([], [])

    def test_the_new_adr_must_be_on_the_reworded_entry(self):
        """ADR-0002 names files.trash, but the entry does not list it."""
        assert _refused(_changed("files.trash", steps=NEW_STEPS)) == ([], ["files.trash"])

    def test_a_feature_is_named_as_a_whole_id(self):
        assert names_feature("see `files.trash`.", "files.trash")
        assert names_feature("(files.trash, auth.two-step)", "files.trash")
        assert not any(names_feature(text, "files.trash") for text in ADR_TEXTS["ADR-0003"].split())


class TestTheBase:
    """How the base is read from git, on a throwaway repository."""

    ISOLATED = {"GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}

    def _commit(self, repo: Path) -> str:
        for args in (
            ("add", "-A"),
            ("-c", "user.name=Ledger test", "-c", "user.email=ledger@example.invalid", "commit", "-q", "-m", "x"),
        ):
            done = _git(repo, *args, **self.ISOLATED)
            assert done is not None and done.returncode == 0, done and done.stderr
        return _git(repo, "rev-parse", "HEAD", **self.ISOLATED).stdout.strip()

    def test_adr_ids_come_from_the_adr_file_names(self):
        names = ["docs/adr/0001-first.md", "0012-a-b.md", "docs/adr/README.md", "template.md", "0003.md", ""]

        assert adr_ids(names) == {"ADR-0001", "ADR-0012"}

    def test_the_base_holds_the_ledger_and_the_adr_ids_of_its_revision(self, tmp_path):
        repo = tmp_path / "repo"
        adr = repo / "docs" / "adr"
        adr.mkdir(parents=True)
        initialized = _git(repo, "init", "-q", **self.ISOLATED)
        if initialized is None:
            pytest.skip("git is not installed")
        assert initialized.returncode == 0, initialized.stderr
        (adr / "0001-first.md").write_text("The first.\n", encoding="utf-8")
        (adr / "README.md").write_text("The index.\n", encoding="utf-8")
        without_a_ledger = self._commit(repo)
        (repo / "feature-list.json").write_text(json.dumps({"description": "x", "features": BASE_LEDGER}))
        (adr / "0002-second.md").write_text("The second.\n", encoding="utf-8")
        with_a_ledger = self._commit(repo)
        (adr / "0003-later.md").write_text("Not committed.\n", encoding="utf-8")

        assert read_base(without_a_ledger, repo) is None
        assert read_base(with_a_ledger, repo) == Base(BASE_LEDGER, frozenset({"ADR-0001", "ADR-0002"}))
        assert read_base("no-such-revision", repo) is None


class TestJournal:
    def test_has_now_dev_device_and_log(self):
        text = JOURNAL.read_text(encoding="utf-8")
        sections = re.findall(r"^## (.+?)\s*$", text, flags=re.M)

        assert text.startswith("# ")
        assert sections == ["Now", "Dev device", "Log"]

    def test_log_entries_are_dated_newest_first(self):
        log = JOURNAL.read_text(encoding="utf-8").split("\n## Log", 1)[1]
        headings = re.findall(r"^###.*$", log, flags=re.M)

        assert headings, "the Log has no entries"
        bad = [h for h in headings if not LOG_ENTRY.match(h)]
        assert bad == [], "write Log entries as '### YYYY-MM-DD: title' or '### YYYY-MM-DD to YYYY-MM-DD: title'"
        spans = []
        for heading in headings:
            first, last = LOG_ENTRY.match(heading).groups()
            start = date.fromisoformat(first)
            end = date.fromisoformat(last) if last else start
            assert start <= end, heading
            spans.append((start, end, heading))
        for (start, _, newer), (_, end, older) in zip(spans, spans[1:], strict=False):
            assert end <= start, f"newest first: {older!r} is listed after {newer!r}"

    def test_feature_ids_it_names_exist(self):
        ids = {f["id"] for f in _features()}
        named = set(FEATURE_ID.findall(JOURNAL.read_text(encoding="utf-8")))

        assert sorted(named - ids) == []

    def test_english_and_no_foreign_paths(self):
        text = JOURNAL.read_text(encoding="utf-8")

        assert CYRILLIC.findall(text) == [], "repository text is English (ADR-0200)"
        assert FOREIGN_PATH.findall(text) == [], "name repository paths only"
