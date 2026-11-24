"""architecture.database-migrations (ADR-0444): the schema of unos.db comes from the Alembic
revisions of supervisor/src/migrations, which the supervisor applies at its start, before it
serves. A database that create_all made before the migrations, as the dev device's, is taken as
the baseline: its tables stay as they are, and only the revision is recorded."""

import argparse
import asyncio
import hashlib
import importlib
import logging
import re
import sqlite3
from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.ext.asyncio import async_sessionmaker

from src import database
from src.database import Base

SUPERVISOR = Path(__file__).resolve().parents[1]
REPO = SUPERVISOR.parent
# The SHA-256 of supervisor/src/migrations/versions/0001_baseline.py, as committed.
BASELINE_SHA256 = "951771e4d841b922e7eb8c6ac1a8dcdb6cb1cdfa1bf69c6fd882e36355f9114b"


def _script() -> ScriptDirectory:
    return ScriptDirectory.from_config(database.migration_config())


def _head() -> str:
    return _script().get_current_head()


def _revision(path: Path) -> str | None:
    with sqlite3.connect(path) as conn:
        revisions = database.stored_revisions(conn)
    assert len(revisions) <= 1, revisions
    return revisions[0] if revisions else None


def _layouts(path: Path) -> dict[str, frozenset[str]]:
    with sqlite3.connect(path) as conn:
        return database.layouts(conn.execute(database.LAYOUT_SQL).fetchall())


def _schema(path: Path) -> list[tuple]:
    """Every table and index of the database but alembic_version, with the SQL that made it."""
    with sqlite3.connect(path) as conn:
        return conn.execute(
            "SELECT type, name, tbl_name, sql FROM sqlite_master WHERE name NOT LIKE 'alembic_version%' "
            "AND name NOT LIKE 'sqlite_%' ORDER BY type, name"
        ).fetchall()


def _tables(path: Path) -> set[str]:
    with sqlite3.connect(path) as conn:
        return {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")}


def _start(path: Path, monkeypatch) -> None:
    """init_db on the database file ``path`` with the app's engine, as at a start of the supervisor."""
    engine = database.create_app_engine(str(path))
    monkeypatch.setattr(database, "engine", engine)
    try:
        asyncio.run(database.init_db())
    finally:
        asyncio.run(engine.dispose())


def _upgrade(path: Path, target: str) -> None:
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            database.upgrade(conn, target)
    finally:
        engine.dispose()


# The tables that revisions after the baseline added: create_all never made them on a database from before
# the migrations, such as the dev device's.
AFTER_THE_BASELINE = ("standby_apps",)


def _before_the_revisions(schema: list[tuple]) -> list[tuple]:
    """The schema without the tables, and their indexes, of the revisions after the baseline."""
    return [entry for entry in schema if entry[2] not in AFTER_THE_BASELINE]


def _create_all(path: Path, *, without: tuple[str, ...] = ()) -> None:
    """The database as create_all made it before the migrations, with one administrator."""
    database.import_models()
    engine = create_engine(f"sqlite:///{path}")
    left_out = (*without, *AFTER_THE_BASELINE)
    try:
        Base.metadata.create_all(engine, tables=[t for name, t in Base.metadata.tables.items() if name not in left_out])
        with engine.begin() as conn:
            conn.exec_driver_sql(
                "INSERT INTO users (username, hashed_password, is_admin) VALUES ('owner', 'hash', 1)")
    finally:
        engine.dispose()


def _users(path: Path) -> list[tuple]:
    with sqlite3.connect(path) as conn:
        return conn.execute("SELECT username, hashed_password, is_admin FROM users ORDER BY id").fetchall()


def _diff(path: Path) -> list:
    """What alembic revision --autogenerate would write for the database ``path``: the models'
    difference from it."""
    database.import_models()
    engine = create_engine(f"sqlite:///{path}")
    try:
        with engine.connect() as conn:
            context = MigrationContext.configure(conn, opts=database.AUTOGENERATE)
            return compare_metadata(context, Base.metadata)
    finally:
        engine.dispose()


class TestTheSchema:
    def test_an_empty_database_gets_the_whole_schema_at_the_head(self, tmp_path, monkeypatch):
        """Step 1: alembic upgrade head on an empty database creates the whole schema."""
        path = tmp_path / "unos.db"

        _start(path, monkeypatch)

        database.import_models()
        assert _revision(path) == _head()
        assert _tables(path) == set(Base.metadata.tables) | {"alembic_version"}

    def test_the_models_and_the_head_of_the_migrations_agree(self, tmp_path):
        """Step 2: a change of a model comes with its revision. The models' autogenerate diff from a
        database at the head is empty; a model changed without a revision fails here, with the
        operations that its revision needs."""
        path = tmp_path / "unos.db"
        _upgrade(path, "head")

        assert _diff(path) == []

    def test_the_tables_at_the_head_are_those_of_the_models(self, tmp_path):
        """What autogenerate leaves out: CHECK constraints, collations, AUTOINCREMENT, WITHOUT ROWID.
        Every table and index at the head has the clauses that create_all of the models gives it."""
        head, models = tmp_path / "head.db", tmp_path / "models.db"
        _upgrade(head, "head")
        engine = create_engine(f"sqlite:///{models}")
        try:
            database.import_models()
            Base.metadata.create_all(engine)
        finally:
            engine.dispose()

        assert _layouts(head) == _layouts(models)
        assert "CONSTRAINT ck_smb_state_singleton CHECK (id = 1)" in _layouts(head)["smb_state"]

    def test_the_baseline_is_never_edited(self):
        """A database at 0001 is the dev device's and a backup's: a change of the schema is a new
        revision, never an edit of one that a database may already record."""
        baseline = next((database.MIGRATIONS / "versions").glob(f"{database.BASELINE}_*.py"))

        assert hashlib.sha256(baseline.read_bytes()).hexdigest() == BASELINE_SHA256

    def test_the_history_is_one_line_from_the_baseline(self):
        script = _script()

        assert script.get_heads() == [_head()]
        assert script.get_bases() == [database.BASELINE]
        for revision in script.walk_revisions():
            assert revision.down_revision is None or isinstance(revision.down_revision, str), revision.revision
            assert re.fullmatch(r"\d{4}", revision.revision), revision.revision


class TestUpgrades:
    def test_every_revision_runs_on_a_database_made_by_the_one_before(self, tmp_path):
        """Step 3: from the baseline, one revision at a time, on one database with a row of the
        baseline, to the head."""
        path = tmp_path / "unos.db"
        order = [revision.revision for revision in reversed(list(_script().walk_revisions()))]
        assert order[0] == database.BASELINE

        _upgrade(path, database.BASELINE)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('owner', 'hash', 1)")
        for revision in order:
            _upgrade(path, revision)
            assert _revision(path) == revision

        assert _users(path) == [("owner", "hash", 1)]
        assert _diff(path) == []

    def test_a_database_at_the_baseline_upgrades_to_the_head_at_the_start(self, tmp_path, monkeypatch):
        path = tmp_path / "unos.db"
        _upgrade(path, database.BASELINE)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('owner', 'hash', 1)")

        _start(path, monkeypatch)

        assert _revision(path) == _head()
        assert _users(path) == [("owner", "hash", 1)]

    def test_the_start_applies_a_pending_revision_to_an_existing_table(self, tmp_path, monkeypatch):
        """Step 2: a change to an existing table ships as a revision, and the next start applies it,
        keeping the rows. A copy of the migrations with one revision more, which adds a column to users."""
        path = tmp_path / "unos.db"
        _start(path, monkeypatch)
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('owner', 'hash', 1)")
        before = _head()
        migrations = tmp_path / "migrations"
        (migrations / "versions").mkdir(parents=True)
        for name in ("env.py", "script.py.mako"):
            (migrations / name).write_text((database.MIGRATIONS / name).read_text())
        for source in (database.MIGRATIONS / "versions").glob("*.py"):
            (migrations / "versions" / source.name).write_text(source.read_text())
        (migrations / "versions" / "9999_a_note_for_each_user.py").write_text(
            '"""A note for each user."""\n'
            "import sqlalchemy as sa\nfrom alembic import op\n\n"
            f'revision = "9999"\ndown_revision = "{before}"\nbranch_labels = None\ndepends_on = None\n\n\n'
            "def upgrade() -> None:\n"
            '    with op.batch_alter_table("users") as batch:\n'
            '        batch.add_column(sa.Column("note", sa.String(), nullable=True))\n'
        )
        monkeypatch.setattr(database, "MIGRATIONS", migrations)

        _start(path, monkeypatch)

        assert _revision(path) == "9999"
        with sqlite3.connect(path) as conn:
            assert "note" in {row[1] for row in conn.execute("PRAGMA table_info(users)")}
        assert _users(path) == [("owner", "hash", 1)]

    def test_a_failed_upgrade_leaves_the_database_as_it_was(self, tmp_path, monkeypatch):
        """Every revision of a start runs in one SQLite transaction, its DDL included: a failure
        part way, such as a full disk, leaves no table and no revision behind."""
        path = tmp_path / "unos.db"
        create_table = Operations.create_table
        made: list[str] = []

        def fail_at_the_third(self, name, *columns, **kw):
            if len(made) == 2:
                raise OSError("database or disk is full")
            made.append(name)
            return create_table(self, name, *columns, **kw)

        monkeypatch.setattr(Operations, "create_table", fail_at_the_third)

        with pytest.raises(OSError, match="disk is full"):
            _start(path, monkeypatch)

        assert len(made) == 2
        assert _tables(path) == set()

    def test_a_start_at_the_head_writes_nothing(self, tmp_path, monkeypatch):
        """ADR-0214: unos.db is on the system disk. A start with nothing to apply only reads."""
        path = tmp_path / "unos.db"
        _start(path, monkeypatch)
        before = hashlib.sha256(path.read_bytes()).hexdigest()

        engine = database.create_app_engine(str(path))
        monkeypatch.setattr(database, "engine", engine)
        try:
            asyncio.run(database.init_db())
            wal = path.with_name("unos.db-wal")
            assert not wal.exists() or wal.stat().st_size == 0
        finally:
            asyncio.run(engine.dispose())
        assert hashlib.sha256(path.read_bytes()).hexdigest() == before

    def test_a_database_of_an_unknown_revision_stops_the_start(self, tmp_path, monkeypatch):
        """A database of a newer UNOS, as after a rollback of the code: the start fails and changes
        nothing, instead of serving with a schema it does not know."""
        path = tmp_path / "unos.db"
        _start(path, monkeypatch)
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE alembic_version SET version_num = 'ffff'")
        schema = _schema(path)

        with pytest.raises(Exception, match="ffff"):
            _start(path, monkeypatch)

        assert _revision(path) == "ffff"
        assert _schema(path) == schema


class TestTheDevDevicesDatabase:
    def test_a_database_made_by_create_all_is_taken_as_the_baseline(self, tmp_path, monkeypatch, caplog):
        """The dev device's unos.db: today's schema, made by create_all, without a revision. The start
        records the baseline and recreates or changes no table, and the rows stay."""
        path = tmp_path / "unos.db"
        _create_all(path)
        schema = _schema(path)

        with caplog.at_level(logging.INFO, logger="src.database"):
            _start(path, monkeypatch)

        assert _revision(path) == _head()
        # The revisions after the baseline add their tables; the baseline's stay as they were.
        assert _before_the_revisions(_schema(path)) == schema
        assert {entry[2] for entry in _schema(path)} >= set(AFTER_THE_BASELINE)
        assert _users(path) == [("owner", "hash", 1)]
        assert _diff(path) == []
        assert [(r.levelname, r.getMessage()) for r in caplog.records if r.name == "src.database"] == [
            ("INFO", f"unos.db was brought from the revision none (no migrations yet) to {_head()}")]

    def test_a_table_of_an_older_layout_stays_and_a_warning_names_it(self, tmp_path, monkeypatch, caplog):
        """smb_state as create_all made it before ADR-0319, with pending_import_conf and the wider
        CHECK, which create_all kept: the baseline keeps it too, and says so."""
        path = tmp_path / "unos.db"
        _create_all(path)
        with sqlite3.connect(path) as conn:
            conn.executescript(
                "DROP TABLE smb_state;"
                "CREATE TABLE smb_state (id INTEGER NOT NULL, next_uid INTEGER DEFAULT '70000' NOT NULL, "
                "shares_imported_at DATETIME, import_report TEXT, pending_import_source VARCHAR(8), "
                "pending_import_conf TEXT, PRIMARY KEY (id), CONSTRAINT ck_smb_state_singleton CHECK (id = 1), "
                "CONSTRAINT ck_smb_state_pending_import_source CHECK (pending_import_source IS NULL OR "
                "pending_import_source IN ('backup', 'live')));"
                "INSERT INTO smb_state (id, next_uid) VALUES (1, 70003);")
        schema = _schema(path)

        with caplog.at_level(logging.INFO, logger="src.database"):
            _start(path, monkeypatch)

        assert _revision(path) == _head()
        # The revisions after the baseline add their tables; the baseline's stay as they were.
        assert _before_the_revisions(_schema(path)) == schema
        assert {entry[2] for entry in _schema(path)} >= set(AFTER_THE_BASELINE)
        with sqlite3.connect(path) as conn:
            assert conn.execute("SELECT id, next_uid FROM smb_state").fetchall() == [(1, 70003)]
        warnings = [r.getMessage() for r in caplog.records if r.name == "src.database" and r.levelname == "WARNING"]
        assert warnings == [f"unos.db was taken as the baseline {database.BASELINE}, but these tables and indexes "
                            "of it differ from the baseline's and stay as they are: smb_state"]

    def test_a_database_made_before_a_table_existed_gets_it(self, tmp_path, monkeypatch):
        """A backup made by create_all before a table of the baseline existed, restored: the start
        adds the table, as create_all did, and leaves the others as they are."""
        path = tmp_path / "unos.db"
        _create_all(path, without=("app_resource_limits", "smb_bind_interfaces"))
        schema = _schema(path)

        _start(path, monkeypatch)

        assert _revision(path) == _head()
        assert {"app_resource_limits", "smb_bind_interfaces"} <= _tables(path)
        assert set(schema) <= set(_schema(path))
        assert _diff(path) == []


class TestTheSupervisor:
    def test_the_supervisor_upgrades_the_database_before_it_serves(self, tmp_path, monkeypatch):
        """Step 2: the lifespan brings unos.db to the head before the first request."""
        from src.auth import router as auth_router
        from src.main import app

        path = tmp_path / "unos.db"  # a new installation
        engine = database.create_app_engine(str(path))
        monkeypatch.setattr(database, "engine", engine)
        monkeypatch.setattr(database, "async_session", async_sessionmaker(engine, expire_on_commit=False))
        monkeypatch.setenv("UNOS_DISABLE_BACKGROUND_TASKS", "1")
        monkeypatch.setattr("src.audit.router.AUDIT_FILE", str(tmp_path / "audit.jsonl"))
        seen: list[str | None] = []
        real_init_db = database.init_db

        async def init_db():
            await real_init_db()
            seen.append(_revision(path))

        monkeypatch.setattr("src.main.init_db", init_db)
        auth_router.limiter.reset()
        try:
            with TestClient(app) as client:
                assert seen == [_head()]
                status = client.get("/api/v1/auth/status")
        finally:
            auth_router.limiter.reset()
            asyncio.run(engine.dispose())

        assert status.json() == {"setup_required": True}

    def test_the_lifespan_calls_init_db_first(self):
        source = (SUPERVISOR / "src" / "main.py").read_text()
        lifespan = source[source.index("async def lifespan"):]

        assert lifespan.split("\n")[1].strip() == "await init_db()"
        assert not [path for path in (SUPERVISOR / "src").rglob("*.py") if ".create_all(" in path.read_text()]

    def test_a_restore_refuses_a_backup_of_an_unknown_revision(self, tmp_path):
        """A restore upgrades a backup's database before the swap: one of a revision this UNOS does
        not know, or of several, is refused first, as a start on it would fail."""
        backup_router = importlib.import_module("src.backup.router")

        path = tmp_path / "unos.db"
        _upgrade(path, "head")
        with sqlite3.connect(path) as conn:
            conn.execute("INSERT INTO users (username, hashed_password, is_admin) VALUES ('owner', 'hash', 1)")
        backup_router._verify_database(path)
        with sqlite3.connect(path) as conn:
            conn.execute("UPDATE alembic_version SET version_num = 'ffff'")

        with pytest.raises(backup_router.BackupValidationError, match="revision ffff"):
            backup_router._verify_database(path)
        with sqlite3.connect(path) as conn:
            conn.execute(f"UPDATE alembic_version SET version_num = '{_head()}'")
            conn.execute(f"INSERT INTO alembic_version VALUES ('{database.BASELINE}x')")
        with pytest.raises(backup_router.BackupValidationError, match="several database revisions"):
            backup_router._verify_database(path)


class TestShipping:
    def test_the_images_ship_the_migrations_and_alembic(self):
        """The migrations are below src/, which both images copy and the dev deploy sends."""
        assert database.MIGRATIONS == SUPERVISOR / "src" / "migrations"
        assert (database.MIGRATIONS / "env.py").is_file()
        assert (database.MIGRATIONS / "script.py.mako").is_file()
        assert sorted((database.MIGRATIONS / "versions").glob(f"{database.BASELINE}_*.py"))
        for dockerfile in ("Dockerfile", "Dockerfile.test"):
            assert "COPY src/ ./src/" in (SUPERVISOR / dockerfile).read_text().splitlines(), dockerfile
        ignored = [line.strip() for line in (SUPERVISOR / ".dockerignore").read_text().splitlines()
                   if line.strip() and not line.startswith("#")]
        assert not [pattern for pattern in ignored if "migration" in pattern or "versions" in pattern or ".mako" in pattern]
        assert '"supervisor/src/*",' in (REPO / "scripts" / "dev" / "tree.py").read_text()
        assert re.search(r"^alembic==\d+\.\d+\.\d+$", (SUPERVISOR / "requirements.txt").read_text(), re.M)

    def test_the_command_line_of_alembic_uses_the_same_migrations(self, tmp_path):
        """supervisor/alembic.ini, for `alembic revision --autogenerate` (supervisor/AGENTS.md)."""
        path = tmp_path / "unos.db"
        config = Config(str(SUPERVISOR / "alembic.ini"))
        config.cmd_opts = argparse.Namespace(x=[f"db={path}"])

        assert Path(ScriptDirectory.from_config(config).dir) == database.MIGRATIONS
        command.upgrade(config, "head")
        assert _revision(path) == _head()
        assert _diff(path) == []
