"""NFS exports (ADR-0291, ADR-0292): the reader of exports(5), UNOS's own file on the host, exportfs
and the API.

shares/exports.py reads the export files as nfs-utils' exportfs reads them (its tokenizer, its
entries and its option list, ported): the first part checks every line form of exports(5).
shares/nfs.py keeps UNOS's exports in /etc/exports.d/unos.exports on the host, changes only its
own lines there, and applies them with exportfs: the second part runs the API against
test_pool_persistence's FakeHost, which knows the host's files and its exportfs.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from src import host as host_module
from src.audit import router as audit_router
from src.shares import exports, nfs
from src.storage import pools

from .audit_helpers import audit_entries
from .test_pool_persistence import admin_client, install_fake_host

REPO = Path(__file__).resolve().parents[2]
API = "/api/v1/shares"


def summary(parsed: exports.Parsed) -> list[tuple[str, list[tuple[str, list[str], str | None]]]]:
    return [
        (entry.path, [(client.name, list(client.options), client.access) for client in entry.clients])
        for entry in parsed.entries
    ]


def read(data: bytes):
    return summary(exports.parse(data))


# ---------------------------------------------------------------------------
# The reader of exports(5)
# ---------------------------------------------------------------------------


class TestLines:
    def test_comments_and_blank_lines_are_skipped_and_a_hash_inside_a_word_is_kept(self):
        parsed = exports.parse(
            b"# Exports of this host\n\n   \n/srv/a 10.0.0.1(rw) # the office\n/srv/b#c 10.0.0.2(ro)\n"
        )

        assert summary(parsed) == [
            ("/srv/a", [("10.0.0.1", ["rw"], "write")]),
            ("/srv/b#c", [("10.0.0.2", ["ro"], "read")]),
        ]
        assert [(entry.first_line, entry.last_line) for entry in parsed.entries] == [(4, 4), (5, 5)]
        assert parsed.problem is None

    def test_a_backslash_at_the_end_of_a_line_continues_it(self):
        parsed = exports.parse(
            b"/srv/a \\\n    10.0.0.1(rw) \\\n\t10.0.0.2(ro)\n/srv/b 10.0.0.3(ro)\n"
        )

        assert summary(parsed) == [
            ("/srv/a", [("10.0.0.1", ["rw"], "write"), ("10.0.0.2", ["ro"], "read")]),
            ("/srv/b", [("10.0.0.3", ["ro"], "read")]),
        ]
        assert [(entry.first_line, entry.last_line) for entry in parsed.entries] == [(1, 3), (4, 4)]

    def test_a_continuation_inside_a_word_ends_it(self):
        # exportfs reads the continuation as a space: "10.0.0.1(rw," has no closing parenthesis.
        parsed = exports.parse(b"/srv/a 10.0.0.1(rw,\\\nsync)\n/srv/b 10.0.0.2(ro)\n")

        assert summary(parsed) == []
        assert parsed.problem == exports.Problem(1, "syntax error: bad option list")

    def test_crlf_line_ends(self):
        assert read(b"/srv/a 10.0.0.1(rw)\r\n/srv/b\r\n") == [
            ("/srv/a", [("10.0.0.1", ["rw"], "write")]),
            ("/srv/b", [("*", [], "read")]),
        ]

    def test_a_carriage_return_inside_a_line_ends_the_entry(self):
        assert read(b"/srv/a 10.0.0.1(rw) \r /srv/b 10.0.0.2(ro)\n") == [
            ("/srv/a", [("10.0.0.1", ["rw"], "write")]),
            ("/srv/b", [("10.0.0.2", ["ro"], "read")]),
        ]


class TestPaths:
    @pytest.mark.parametrize(
        "line, path",
        [
            (b'"/srv/my docs" 10.0.0.1(ro)', "/srv/my docs"),
            (b'/srv/"a b"/c 10.0.0.1(ro)', "/srv/a b/c"),
            (b"/srv/my\\040docs 10.0.0.1(ro)", "/srv/my docs"),
            (b'"/srv/a\\040b" 10.0.0.1(ro)', "/srv/a b"),
            (b"/srv/caf\\303\\251 10.0.0.1(ro)", "/srv/café"),
            # Only a backslash with three octal digits below 256 is an escape.
            (b"/srv/a\\400 10.0.0.1(ro)", "/srv/a\\400"),
            (b"/srv/a\\x41 10.0.0.1(ro)", "/srv/a\\x41"),
            (b"/srv/a\\08 10.0.0.1(ro)", "/srv/a\\08"),
            # A decoded backslash starts the next escape: \134 then 040.
            (b"/srv/a\\134040 10.0.0.1(ro)", "/srv/a "),
            # A continuation inside quotes is a space; the blanks after it are skipped.
            (b'"/srv/my \\\n   docs" 10.0.0.1(ro)', "/srv/my  docs"),
            # Bytes that are not UTF-8 are shown as U+FFFD.
            (b"/srv/\xff 10.0.0.1(ro)", "/srv/�"),
        ],
    )
    def test_quotes_and_escapes(self, line, path):
        assert read(line + b"\n") == [(path, [("10.0.0.1", ["ro"], "read")])]

    def test_an_unclosed_quote_takes_the_rest_of_the_file(self):
        parsed = exports.parse(b'/srv/a 10.0.0.1(ro)\n"/srv/b 10.0.0.2(rw)\n/srv/c 10.0.0.3(rw)\n')

        assert summary(parsed) == [
            ("/srv/a", [("10.0.0.1", ["ro"], "read")]),
            ("/srv/b 10.0.0.2(rw)\n/srv/c 10.0.0.3(rw)\n", [("*", [], "read")]),
        ]
        assert parsed.problem is None

    def test_a_path_longer_than_1024_bytes_ends_the_reading(self):
        parsed = exports.parse(b"/srv/a 10.0.0.1(ro)\n/" + b"x" * 1024 + b" 10.0.0.2(ro)\n/srv/c 10.0.0.3(ro)\n")

        assert summary(parsed) == [("/srv/a", [("10.0.0.1", ["ro"], "read")])]
        assert parsed.problem == exports.Problem(2, "a path longer than 1024 bytes")

    def test_a_path_of_1024_bytes_is_read(self):
        path = "/" + "x" * 1023
        assert read(path.encode() + b" 10.0.0.1(ro)\n") == [(path, [("10.0.0.1", ["ro"], "read")])]


class TestClients:
    @pytest.mark.parametrize(
        "line, clients",
        [
            (
                b"/srv/a 10.0.0.1(rw) 192.168.1.0/24(ro,sync) *(ro)",
                [("10.0.0.1", ["rw"], "write"), ("192.168.1.0/24", ["ro", "sync"], "read"), ("*", ["ro"], "read")],
            ),
            # Default options apply to every later client of the line, before its own options.
            (
                b"/srv/a -rw,sync 10.0.0.1 10.0.0.2(ro)",
                [("10.0.0.1", ["rw", "sync"], "write"), ("10.0.0.2", ["rw", "sync", "ro"], "read")],
            ),
            # Default options at the end of a line export to everyone.
            (b"/srv/a 10.0.0.1(rw) -ro", [("10.0.0.1", ["rw"], "write"), ("*", ["ro"], "read")]),
            # A second word with a dash is a client.
            (b"/srv/a -ro -rw 10.0.0.1", [("-rw", ["ro"], "read"), ("10.0.0.1", ["ro"], "read")]),
            # No client, or options without one: everyone. exportfs's default is read-only.
            (b"/srv/a", [("*", [], "read")]),
            (b"/srv/a (rw)", [("*", ["rw"], "write")]),
            (b"/srv/a 10.0.0.1()", [("10.0.0.1", [], "read")]),
            (b"/srv/a 10.0.0.1", [("10.0.0.1", [], "read")]),
            # Blanks after the parenthesis and after a comma, and a trailing comma.
            (b'/srv/a "10.0.0.1( rw, sync)"', [("10.0.0.1", ["rw", "sync"], "write")]),
            (b"/srv/a 10.0.0.1(rw,)", [("10.0.0.1", ["rw"], "write")]),
            # Escapes count in a client's word too.
            (b"/srv/a 10.0.0.1(\\162w)", [("10.0.0.1", ["rw"], "write")]),
            # The last of ro and rw counts; with sec= each flavor has its own.
            (b"/srv/a 10.0.0.1(rw,ro)", [("10.0.0.1", ["rw", "ro"], "read")]),
            (b"/srv/a 10.0.0.1(sec=krb5p,rw,sec=sys,ro)",
             [("10.0.0.1", ["sec=krb5p", "rw", "sec=sys", "ro"], None)]),
        ],
    )
    def test_clients_and_options(self, line, clients):
        assert read(line + b"\n") == [("/srv/a", clients)]

    def test_default_options_hold_for_their_line_only(self):
        assert read(b"/srv/a -rw 10.0.0.1\n/srv/b 10.0.0.2\n") == [
            ("/srv/a", [("10.0.0.1", ["rw"], "write")]),
            ("/srv/b", [("10.0.0.2", [], "read")]),
        ]

    @pytest.mark.parametrize(
        "options",
        [
            "rw,sync,no_subtree_check,root_squash,secure,mp=/mnt/pools/data",
            "ro,async,insecure,nohide,hide,crossmnt,nocrossmnt,wdelay,no_wdelay,nordirplus,security_label",
            "no_root_squash,all_squash,no_all_squash,subtree_check,auth_nlm,no_auth_nlm",
            "secure_locks,insecure_locks,acl,no_acl,pnfs,no_pnfs",
            "anonuid=65534,anongid=-2",
            "fsid=root", "fsid=0", "fsid=0x1f", "fsid=+12", "fsid=0123", "fsid=01234567-89ab-cdef-0123-456789abcdef",
            "sec=krb5p:krb5i:krb5:sys:unix:null:none",
            "mountpoint", "mp", "mountpoint=/mnt", "mp=",
            "refer=/x@host", "replicas=/y@h1+h2",
            "squash_uids=0-15", "squash_gids=7", "squash_uids=", "squash_gids=-",
            "xprtsec=tls:mtls:none", "reexport=auto-fsidnum",
        ],
    )
    def test_options_exportfs_accepts(self, options):
        [(path, [(client, read_options, _)])] = read(f"/srv/a 10.0.0.1({options})\n".encode())
        assert (path, client, read_options) == ("/srv/a", "10.0.0.1", options.split(","))


class TestUnreadable:
    @pytest.mark.parametrize(
        "client, message",
        [
            (b"10.0.0.1(rw", "syntax error: bad option list"),
            (b"10.0.0.1(", "syntax error: bad option list"),
            (b"(", "syntax error: bad option list"),
            (b"10.0.0.1(rw))", "syntax error: bad option list"),
            (b"10.0.0.1(rw)(ro)", "syntax error: bad option list"),
            (b"10.0.0.1(rw)#x", "syntax error: bad option list"),
            (b"10.0.0.1(rw,,sync)", 'unknown keyword ""'),
            (b"10.0.0.1(,rw)", 'unknown keyword ""'),
            (b'"10.0.0.1(rw ,sync)"', 'unknown keyword "rw "'),
            (b"10.0.0.1(rw,bogus)", 'unknown keyword "bogus"'),
            (b"10.0.0.1(RW)", 'unknown keyword "RW"'),
            (b"10.0.0.1(ttl=5)", 'unknown keyword "ttl=5"'),
            (b"10.0.0.1(anonuid=x)", "bad anonuid"),
            (b"10.0.0.1(anonuid=)", "bad anonuid"),
            (b"10.0.0.1(anongid=1.5)", "bad anongid"),
            (b"10.0.0.1(fsid=abc)", "bad fsid"),
            (b"10.0.0.1(fsid=08)", "bad fsid"),
            (b"10.0.0.1(sec=krb5:bogus)", 'unknown flavor "bogus"'),
            (b"10.0.0.1(sec=)", 'unknown flavor ""'),
            (b"10.0.0.1(xprtsec=ssl)", 'unknown xprtsec mode "ssl"'),
            (b"10.0.0.1(reexport=always)", 'unknown reexport mode "always"'),
            (b"10.0.0.1(squash_uids=-1)", "uid/gid -1 not permitted"),
            (b"10.0.0.1(squash_gids=5--1)", "uid/gid -1 not permitted"),
            (b"10.0.0.1(squash_uids=1x)", "bad uid/gid list"),
            # The option list ends at the comma: the second id is a keyword of its own.
            (b"10.0.0.1(squash_uids=1,2)", 'unknown keyword "2"'),
            (b"-bogus 10.0.0.1(rw)", 'unknown keyword "bogus"'),
            (b"1" * 512, "a client longer than 511 bytes"),
        ],
    )
    def test_exportfs_reads_no_further_in_the_file(self, client, message):
        parsed = exports.parse(b"/srv/a 10.0.0.1(ro)\n/srv/b " + client + b"\n/srv/c 10.0.0.3(ro)\n")

        assert summary(parsed) == [("/srv/a", [("10.0.0.1", ["ro"], "read")])]
        assert parsed.problem == exports.Problem(2, message)

    def test_the_clients_before_a_problem_on_its_line_are_read(self):
        parsed = exports.parse(b"/srv/a 10.0.0.1(rw) \\\n  10.0.0.2(bogus) 10.0.0.3(ro)\n/srv/b 10.0.0.4(ro)\n")

        assert summary(parsed) == [("/srv/a", [("10.0.0.1", ["rw"], "write")])]
        assert parsed.entries[0].first_line == 1
        assert parsed.problem == exports.Problem(2, 'unknown keyword "bogus"')

    @pytest.mark.parametrize(
        "data",
        [
            # A blank line of a file with CRLF line ends: the carriage return is no path.
            b"/srv/a 10.0.0.1(rw)\r\n\r\n/srv/b 10.0.0.2(ro)\r\n",
            b'/srv/a 10.0.0.1(rw)\n"" 10.0.0.2(ro)\n/srv/b 10.0.0.2(ro)\n',
        ],
    )
    def test_no_path_where_exportfs_expects_one_ends_the_reading(self, data):
        # exportfs stops there without a message; UNOS names the line.
        parsed = exports.parse(data)

        assert summary(parsed) == [("/srv/a", [("10.0.0.1", ["rw"], "write")])]
        assert parsed.problem == exports.Problem(2, exports.NO_PATH)

    def test_a_client_of_511_bytes_is_read(self):
        client = "x" * 511
        assert read(f"/srv/a {client}\n".encode()) == [("/srv/a", [(client, [], "read")])]


# ---------------------------------------------------------------------------
# The host and the API
# ---------------------------------------------------------------------------

OWNER_EXPORTS = (
    "# /etc/exports: the access control list for filesystems which may be exported\n"
    "/srv/nfs/backup 10.0.0.5(rw,no_root_squash) 192.168.1.0/24(ro)\n"
    "/srv/nfs/iso -ro,sync \\\n    *\n"
)
UNOS_FILE = "/etc/exports.d/unos.exports"
EXPORTFS = ["timeout", "--kill-after=5", "60", "exportfs", "-ra"]


def unos_line(path: str, client: str, access: str, pool: str) -> str:
    return f"{path} {client}({access},sync,no_subtree_check,root_squash,secure,mp={pool})\n"


class Nfs:
    """The API with an administrator, the fake host and the test's pools folder."""

    def __init__(self, client, host, pools_dir: Path, audit: Path):
        self.client = client
        self.host = host
        self.pools = pools_dir
        self.data = pools_dir / "data"
        self.audit = audit

    def create(self, path, client="192.168.1.0/24", access="write", **extra):
        return self.client.post(f"{API}/nfs", json={"path": str(path), "client": client, "access": access, **extra})

    def delete(self, path):
        return self.client.delete(f"{API}/nfs", params={"path": str(path)})

    def listed(self):
        response = self.client.get(f"{API}/nfs")
        assert response.status_code == 200, response.text
        return response.json()

    @property
    def unos_file(self) -> str | None:
        return self.host.files.get(UNOS_FILE)

    @property
    def exportfs_runs(self) -> list[list[str]]:
        return [cmd for cmd in self.host.calls if "exportfs" in cmd]

    @property
    def writes(self) -> list[list[str]]:
        return [change for change in self.host.changes if change[0] in ("write", "mkdir")]


@pytest.fixture
def nfs_api(monkeypatch, tmp_path):
    host = install_fake_host(monkeypatch)
    pools_dir = tmp_path / "pools"
    for folder in ("data/media", "data/shared/docs", "data/apps/jellyfin", "data/.snapshots/s1",
                   "data/.unos-trash", "other/films"):
        (pools_dir / folder).mkdir(parents=True)
    monkeypatch.setattr(pools.settings, "pools_base_path", str(pools_dir))
    monkeypatch.setattr(pools.settings, "nfs_exports_path", "/etc/exports")
    monkeypatch.setattr(pools, "managed_pool_names", lambda: {"data"})
    monkeypatch.setattr(audit_router, "AUDIT_FILE", str(tmp_path / "audit.jsonl"))
    host.files["/etc/exports"] = OWNER_EXPORTS
    with admin_client() as client:
        yield Nfs(client, host, pools_dir.resolve(), tmp_path / "audit.jsonl")


class TestList:
    def test_every_export_of_the_host_is_listed_with_its_file_and_line(self, nfs_api):
        # A line that UNOS writes names a folder below the pools base (the test's pools folder).
        media = str(nfs_api.data / "media")
        nfs_api.host.files[UNOS_FILE] = nfs.HEADER + unos_line(media, "192.168.1.0/24", "rw", str(nfs_api.data))
        nfs_api.host.files["/etc/exports.d/other.exports"] = "/srv/other fd00::/64(rw)\n"
        nfs_api.host.files["/etc/exports.d/.hidden.exports"] = "/srv/hidden *(rw)\n"
        nfs_api.host.files["/etc/exports.d/notes.txt"] = "/srv/notes *(rw)\n"

        listed = nfs_api.listed()

        assert [(e["path"], e["file"], e["line"], e["managed"]) for e in listed] == [
            ("/srv/nfs/backup", "/etc/exports", 2, False),
            ("/srv/nfs/iso", "/etc/exports", 3, False),
            ("/srv/other", "/etc/exports.d/other.exports", 1, False),
            (media, UNOS_FILE, 3, True),
        ]
        assert listed[0]["clients"] == [
            {"client": "10.0.0.5", "options": ["rw", "no_root_squash"], "access": "write"},
            {"client": "192.168.1.0/24", "options": ["ro"], "access": "read"},
        ]
        assert listed[1]["clients"] == [{"client": "*", "options": ["ro", "sync"], "access": "read"}]
        assert nfs_api.host.changes == []

    def test_no_export_files(self, nfs_api):
        del nfs_api.host.files["/etc/exports"]
        assert nfs_api.listed() == []

    def test_the_status_names_unreadable_lines_and_a_missing_nfs_server(self, nfs_api):
        nfs_api.host.files["/etc/exports"] = OWNER_EXPORTS + "/srv/broken 10.0.0.9(rw,bogus)\n/srv/after *(ro)\n"
        nfs_api.host.missing_programs.add("exportfs")

        response = nfs_api.client.get(f"{API}/nfs-status")

        assert response.status_code == 200, response.text
        assert response.json() == {
            "server_missing": True,
            "problems": [
                '/etc/exports, line 5: unknown keyword "bogus"; exportfs reads no further in this file, '
                "and UNOS adds and deletes no export until the line is corrected"
            ],
        }
        # The exports before the line are listed, those after it are not: exportfs never reads them.
        assert [e["path"] for e in nfs_api.listed()] == ["/srv/nfs/backup", "/srv/nfs/iso"]

    def test_the_status_says_when_the_export_files_cannot_be_read(self, nfs_api):
        nfs_api.host.fail[("/bin/sh", "-c", nfs._FILES_SCRIPT)] = (1, "od: /etc/exports: Permission denied")
        nfs_api.host.missing_programs.add("exportfs")

        response = nfs_api.client.get(f"{API}/nfs-status")

        assert response.status_code == 200, response.text
        status = response.json()
        assert status["server_missing"] is True
        [problem] = status["problems"]
        assert problem.startswith("The export files of the host could not be read: ")
        assert "Permission denied" in problem

    def test_the_status_of_a_host_with_an_nfs_server(self, nfs_api):
        response = nfs_api.client.get(f"{API}/nfs-status")
        assert response.json() == {"server_missing": False, "problems": []}

    @pytest.mark.parametrize(
        "line",
        [
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} 10.0.0.7(ro,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} fd00::/64(ro,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            # The last line of the file, without its newline.
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})",
        ],
    )
    def test_a_line_as_unos_writes_it_is_an_export_of_unos(self, nfs_api, line):
        media, data = nfs_api.data / "media", nfs_api.data
        nfs_api.host.files[UNOS_FILE] = nfs.HEADER + "# by hand\n" + line.format(media=media, data=data)

        [entry] = [e for e in nfs_api.listed() if e["file"] == UNOS_FILE]

        assert (entry["path"], entry["line"], entry["managed"]) == (str(media), 4, True)

    @pytest.mark.parametrize(
        "line",
        [
            # As UNOS writes a line, with something more, less or else: written by hand.
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data}) # mine\n",
            "{media}  192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            " {media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\r\n",
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,no_root_squash,secure,mp={data})\n",
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data},insecure)\n",
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure)\n",
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp=/mnt)\n",
            "{media} 192.168.1.0/24(sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} 192.168.1.0/255.255.255.0(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} *(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} nas.lan(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} 8.8.8.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data}) 10.0.0.1(ro)\n",
            "{media} -rw 192.168.1.0/24(sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{media} \\\n  192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            '"{media}" 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n',
            "{data}/./media 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{data}//media 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "{data} 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
            "/srv/media 192.168.1.0/24(rw,sync,no_subtree_check,root_squash,secure,mp={data})\n",
        ],
    )
    def test_a_line_of_unos_s_file_that_unos_did_not_write_is_left_to_the_owner(self, nfs_api, line):
        # The file's first lines promise that UNOS keeps every line it did not write: it lists such a
        # line with its file and line, and does not delete it.
        media, data = nfs_api.data / "media", nfs_api.data
        before = nfs.HEADER + "# by hand\n" + line.format(media=media, data=data)
        nfs_api.host.files[UNOS_FILE] = before

        [entry] = [e for e in nfs_api.listed() if e["file"] == UNOS_FILE]
        response = nfs_api.delete(entry["path"])

        assert (entry["line"], entry["managed"]) == (4, False)
        assert (response.status_code, response.json()["detail"]) == (
            409, nfs.foreign_export(entry["path"], UNOS_FILE, 4))
        assert nfs_api.unos_file == before and nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_the_status_names_exports_of_unos_below_folders_that_other_accounts_can_change(self, nfs_api):
        # exportfs follows symbolic links at every run and at every start of the NFS server: whoever
        # can change a folder above an export can put a link in its place, and exportfs would export
        # the link's target (ADR-0292). The rule is checked when the export is added, and again here.
        docs, shared = nfs_api.data / "shared" / "docs", nfs_api.data / "shared"
        assert nfs_api.create(docs).status_code == 200
        # Exports that UNOS did not write are the owner's, as are their folders.
        nfs_api.host.files["/etc/exports"] = OWNER_EXPORTS + f"{docs} 10.9.0.0/16(ro)\n"
        nfs_api.host.files[UNOS_FILE] += f"{docs}/x 10.8.0.0/16(ro)\n"

        shared.chmod(0o777)
        try:
            response = nfs_api.client.get(f"{API}/nfs-status")
        finally:
            shared.chmod(0o755)

        assert response.status_code == 200, response.text
        assert response.json()["problems"] == [
            nfs.unsafe_export(str(docs), str(shared), "mode 0777", f"'chmod 755 {shared}'")
        ]
        assert nfs_api.client.get(f"{API}/nfs-status").json()["problems"] == []

    def test_the_status_says_when_the_folders_above_an_export_of_unos_cannot_be_checked(self, nfs_api, tmp_path):
        docs = nfs_api.data / "shared" / "docs"
        assert nfs_api.create(docs).status_code == 200
        # The folder above the export is a symbolic link now.
        moved = tmp_path / "moved"
        (nfs_api.data / "shared").rename(moved)
        os.symlink(moved, nfs_api.data / "shared")

        [problem] = nfs_api.client.get(f"{API}/nfs-status").json()["problems"]

        assert problem.startswith(f"NFS export {docs}: the folders above it could not be checked: ")


class TestCreate:
    def test_an_export_is_written_to_unos_s_own_file_and_applied(self, nfs_api):
        folder = nfs_api.data / "media"

        response = nfs_api.create(folder)

        assert response.status_code == 200, response.text
        line = unos_line(str(folder), "192.168.1.0/24", "rw", str(nfs_api.data))
        assert nfs_api.unos_file == nfs.HEADER + line
        assert response.json() == {
            "path": str(folder),
            "clients": [{"client": "192.168.1.0/24", "options": line.split("(")[1].rstrip(")\n").split(","),
                         "access": "write"}],
            "managed": True,
            "file": UNOS_FILE,
            "line": 3,
            "warnings": [],
        }
        # The owner's file is never written; the directory is made before UNOS's file.
        assert nfs_api.host.files["/etc/exports"] == OWNER_EXPORTS
        assert nfs_api.writes == [["mkdir", "-p", "--", "/etc/exports.d"], ["write", UNOS_FILE]]
        assert ["sync", "/etc"] in nfs_api.host.calls
        assert nfs_api.exportfs_runs == [EXPORTFS]
        [entry] = audit_entries(nfs_api.audit)
        assert (entry["action"], entry["username"], entry["resource_type"], entry["resource_id"]) == (
            "share_created", "admin", "nfs_export", str(folder))
        assert entry["details"] == f"Exported {folder} to 192.168.1.0/24, read and write"

    def test_a_second_export_is_appended_and_the_lines_before_stay(self, nfs_api):
        hand_made = nfs.HEADER + "# kept by hand\n/srv/hand 10.1.0.0/16(ro) \\\n  10.2.0.0/16(rw)"
        nfs_api.host.files[UNOS_FILE] = hand_made

        response = nfs_api.create(nfs_api.data / "shared" / "docs", "fd00::/64", "read")

        assert response.status_code == 200, response.text
        assert nfs_api.unos_file == hand_made + "\n" + unos_line(
            str(nfs_api.data / "shared" / "docs"), "fd00::/64", "ro", str(nfs_api.data))
        assert response.json()["line"] == 6
        assert nfs_api.writes == [["write", UNOS_FILE]]
        # The line written by hand is listed, but it is not one of UNOS's.
        assert [(e["path"], e["managed"]) for e in nfs_api.listed() if e["file"] == UNOS_FILE] == [
            ("/srv/hand", False), (str(nfs_api.data / "shared" / "docs"), True)]

    @pytest.mark.parametrize(
        "client, written",
        [
            ("192.168.1.0/24", "192.168.1.0/24"),
            ("192.168.1.0/255.255.255.0", "192.168.1.0/24"),
            ("192.168.1.77/24", "192.168.1.0/24"),
            ("10.0.0.7", "10.0.0.7"),
            ("10.0.0.7/32", "10.0.0.7"),
            ("172.16.0.0/12", "172.16.0.0/12"),
            ("100.64.0.0/10", "100.64.0.0/10"),
            ("169.254.0.0/16", "169.254.0.0/16"),
            ("fd12:3456:789a::/48", "fd12:3456:789a::/48"),
            ("FD00::1", "fd00::1"),
            ("fe80::/64", "fe80::/64"),
        ],
    )
    def test_clients_of_the_local_network(self, nfs_api, client, written):
        response = nfs_api.create(nfs_api.data / "media", client, "read")

        assert response.status_code == 200, response.text
        assert response.json()["clients"][0]["client"] == written
        assert f" {written}(ro," in nfs_api.unos_file

    @pytest.mark.parametrize(
        "client, detail",
        [
            ("*", nfs.CLIENT_SYNTAX_PROBLEM),
            ("nas.local", nfs.CLIENT_SYNTAX_PROBLEM),
            ("*.example.com", nfs.CLIENT_SYNTAX_PROBLEM),
            ("@backup", nfs.CLIENT_SYNTAX_PROBLEM),
            ("", nfs.CLIENT_SYNTAX_PROBLEM),
            ("192.168.1.0/24(rw,no_root_squash)", nfs.CLIENT_SYNTAX_PROBLEM),
            ("10.0.0.1 *", nfs.CLIENT_SYNTAX_PROBLEM),
            ("10.0.0.0/33", nfs.CLIENT_SYNTAX_PROBLEM),
            ("fe80::1%eth0", nfs.CLIENT_SYNTAX_PROBLEM),
            ("8.8.8.8", nfs.client_not_local_problem("8.8.8.8")),
            ("0.0.0.0/0", nfs.client_not_local_problem("0.0.0.0/0")),
            ("10.0.0.0/7", nfs.client_not_local_problem("10.0.0.0/7")),
            ("2001:db8::/32", nfs.client_not_local_problem("2001:db8::/32")),
            ("::/0", nfs.client_not_local_problem("::/0")),
        ],
    )
    def test_clients_that_are_refused(self, nfs_api, client, detail):
        response = nfs_api.create(nfs_api.data / "media", client)

        assert (response.status_code, response.json()["detail"]) == (400, detail)
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_read_only(self, nfs_api):
        response = nfs_api.create(nfs_api.data / "media", access="read")

        assert response.json()["clients"][0]["access"] == "read"
        assert nfs_api.unos_file.endswith(
            unos_line(str(nfs_api.data / "media"), "192.168.1.0/24", "ro", str(nfs_api.data)))

    @pytest.mark.parametrize(
        "relative, detail",
        [
            ("data", nfs.POOL_ROOT_PROBLEM),
            ("data/apps", nfs.apps_problem("data")),
            ("data/apps/jellyfin", nfs.apps_problem("data")),
            ("data/.snapshots/s1", nfs.SNAPSHOTS_PROBLEM),
            ("data/.unos-trash", nfs.TRASH_PROBLEM),
            ("other/films", nfs.unmanaged_pool_problem("other")),
            ("data/missing", None),
            ("data/a b", nfs.PATH_CHARACTERS_PROBLEM),
            ("data/a#b", nfs.PATH_CHARACTERS_PROBLEM),
            ("data/x\n/ *", None),
        ],
    )
    def test_folders_that_are_refused(self, nfs_api, relative, detail):
        folder = nfs_api.pools / relative

        response = nfs_api.create(folder)

        assert response.status_code == 400, response.text
        if relative == "data/missing":
            detail = nfs.missing_folder_problem(str(folder))
        if detail is not None:
            assert response.json()["detail"] == detail
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    @pytest.mark.parametrize("path", ["/etc", "/mnt/pools/../etc", "relative/path", ""])
    def test_paths_outside_the_pools_are_refused(self, nfs_api, path):
        response = nfs_api.client.post(f"{API}/nfs", json={"path": path, "client": "10.0.0.1", "access": "read"})

        assert response.status_code == 400, response.text
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_a_folder_reached_through_a_symlink_is_refused(self, nfs_api, tmp_path):
        outside = tmp_path / "outside"
        outside.mkdir()
        os.symlink(outside, nfs_api.data / "link")

        for folder in (nfs_api.data / "link", nfs_api.data / "link" / "sub"):
            response = nfs_api.create(folder)
            assert response.status_code == 400, response.text
            assert "symbolic link" in response.json()["detail"]
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_a_folder_below_one_that_other_accounts_can_change_is_refused(self, nfs_api):
        shared = nfs_api.data / "shared"
        shared.chmod(0o777)
        try:
            response = nfs_api.create(shared / "docs")
        finally:
            shared.chmod(0o755)

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == (
            f"Export folders must be inside folders that only root can change: {shared} can be changed by "
            "other accounts (mode 0777), and exportfs would export whatever is put in the folder's place. "
            f"Run 'chmod 755 {shared}' on the NAS, then try again."
        )
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_the_pool_list_cannot_be_read(self, nfs_api, monkeypatch):
        def broken():
            raise OSError("fstab is gone")

        monkeypatch.setattr(pools, "managed_pool_names", broken)

        response = nfs_api.create(nfs_api.data / "media")

        assert response.status_code == 503, response.text
        assert response.json()["detail"] == "The pool list could not be read: fstab is gone"
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_a_folder_that_unos_exports_already(self, nfs_api):
        assert nfs_api.create(nfs_api.data / "media").status_code == 200

        response = nfs_api.create(nfs_api.data / "media", "10.0.0.0/8", "read")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == nfs.exported_already(str(nfs_api.data / "media"))
        assert nfs_api.unos_file.count("\n/") == 1

    @pytest.mark.parametrize(
        "written",
        # The same network written otherwise, a larger one, an address in it, a host name (which
        # mountd matches before networks), everyone, another network.
        ["192.168.1.0/255.255.255.0", "192.168.0.0/16", "192.168.1.20", "client.lan", "*", "172.16.5.0/24"],
    )
    def test_a_folder_that_another_export_file_exports(self, nfs_api, written):
        # mountd answers a client from one entry, chosen by the kind of client and by the order
        # of the files: with two files, UNOS could not tell which one applies.
        folder = nfs_api.data / "media"
        nfs_api.host.files["/etc/exports"] = OWNER_EXPORTS + f"{folder} {written}(ro)\n"

        response = nfs_api.create(folder, "192.168.1.0/24")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == nfs.exported_elsewhere("/etc/exports", 5, str(folder))
        assert nfs_api.unos_file is None and nfs_api.exportfs_runs == []

        # Another folder can be exported.
        assert nfs_api.create(nfs_api.data / "shared" / "docs", "192.168.1.0/24").status_code == 200

    def test_a_folder_that_a_line_written_by_hand_into_unos_s_file_exports(self, nfs_api):
        folder = nfs_api.data / "media"
        before = nfs.HEADER + f"{folder} 192.168.1.0/24(rw)\n"
        nfs_api.host.files[UNOS_FILE] = before

        response = nfs_api.create(folder)

        assert (response.status_code, response.json()["detail"]) == (
            409, nfs.exported_elsewhere(UNOS_FILE, 3, str(folder)))
        assert nfs_api.unos_file == before and nfs_api.exportfs_runs == []

    @pytest.mark.parametrize(
        "file, number",
        [("/etc/exports", 5), ("/etc/exports.d/backup.exports", 2), (UNOS_FILE, 3)],
    )
    def test_no_export_is_added_while_an_export_file_has_a_line_that_exportfs_cannot_read(
        self, nfs_api, file, number
    ):
        # exportfs -ra, which applies a change, reads no further in that file: the exports after the
        # line, live until then, would end, and a new line of UNOS's file would never be read.
        start = {"/etc/exports": OWNER_EXPORTS, UNOS_FILE: nfs.HEADER}.get(file, "# backup\n")
        nfs_api.host.files[file] = start + "/srv/broken 10.0.0.9(rw,bogus)\n/srv/after 10.0.0.10(ro)\n"
        before = dict(nfs_api.host.files)

        response = nfs_api.create(nfs_api.data / "media")

        assert (response.status_code, response.json()["detail"]) == (409, nfs.file_unreadable(file, number))
        assert nfs_api.host.files == before and nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_a_folder_of_a_pool_that_is_not_mounted(self, nfs_api, monkeypatch):
        # Its mountpoint is a folder of the system disk (tests/conftest.py replaces the check:
        # the test's pools are folders).
        from src.shares import service

        problem = f"Pool 'data' is not mounted at {nfs_api.data}. Mount it on the Storage page first."
        monkeypatch.setattr(service, "_pool_root_mount_problem", lambda fd, pool: problem)

        response = nfs_api.create(nfs_api.data / "media")

        assert (response.status_code, response.json()["detail"]) == (409, problem)
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_a_path_longer_than_exportfs_reads(self, nfs_api):
        folder = nfs_api.data.joinpath(*["x" * 200] * 5)

        response = nfs_api.create(folder)

        assert (response.status_code, response.json()["detail"]) == (400, nfs.PATH_TOO_LONG)
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    @pytest.mark.parametrize(
        "ending",
        [
            # A continuation at the end of the last line would make the new line a part of it.
            "/srv/hand 10.1.0.0/16(ro) \\\n",
            "/srv/hand 10.1.0.0/16(ro) \\",
            # An open quote takes in the rest of the file.
            '/srv/hand 10.1.0.0/16(ro)\n"/srv/open\n',
        ],
    )
    def test_no_export_is_added_where_exportfs_would_not_read_it_as_one(self, nfs_api, ending):
        before = nfs.HEADER + ending
        nfs_api.host.files[UNOS_FILE] = before

        response = nfs_api.create(nfs_api.data / "media")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == nfs.unfinished_file(UNOS_FILE)
        assert nfs_api.unos_file == before and nfs_api.exportfs_runs == []

    def test_bytes_that_are_not_utf8_in_unos_s_file_are_kept(self, nfs_api):
        hand_made = (nfs.HEADER + "/srv/caf\xe9 10.1.0.0/16(ro)\r\n").encode("latin-1")
        nfs_api.host.files[UNOS_FILE] = hand_made.decode("utf-8", "surrogateescape")

        response = nfs_api.create(nfs_api.data / "media")

        assert response.status_code == 200, response.text
        assert nfs_api.unos_file.encode("utf-8", "surrogateescape") == hand_made + unos_line(
            str(nfs_api.data / "media"), "192.168.1.0/24", "rw", str(nfs_api.data)).encode()
        assert nfs_api.delete(nfs_api.data / "media").status_code == 200
        assert nfs_api.unos_file.encode("utf-8", "surrogateescape") == hand_made

    def test_the_export_is_written_under_the_pool_lock(self, nfs_api, monkeypatch):
        # The volume check of pools.py holds it: no volume is deleted between the folder check
        # and the write.
        locked = []
        write = host_module.write_host_file_atomic

        def recording(path, content, **kwargs):
            locked.append(pools._lock._is_owned())
            return write(path, content, **kwargs)

        monkeypatch.setattr(host_module, "write_host_file_atomic", recording)

        assert nfs_api.create(nfs_api.data / "media").status_code == 200
        assert locked == [True]

    def test_no_export_is_added_after_a_line_that_exportfs_cannot_read(self, nfs_api):
        broken = nfs.HEADER + "/srv/hand 10.1.0.0/16(rw,bogus)\n"
        nfs_api.host.files[UNOS_FILE] = broken

        response = nfs_api.create(nfs_api.data / "media")

        assert response.status_code == 409, response.text
        assert response.json()["detail"] == nfs.file_unreadable(UNOS_FILE, 3)
        assert nfs_api.unos_file == broken and nfs_api.exportfs_runs == []

    def test_old_fields_are_refused(self, nfs_api):
        response = nfs_api.create(nfs_api.data / "media", options="rw,no_root_squash")
        assert response.status_code == 422
        response = nfs_api.client.post(f"{API}/nfs", json={"path": str(nfs_api.data / "media"), "clients": "*"})
        assert response.status_code == 422
        assert nfs_api.writes == []

    @pytest.mark.parametrize(
        "result, warning",
        [
            ((127, "timeout: failed to run command 'exportfs': No such file or directory"), nfs.SERVER_MISSING),
            ((124, ""), nfs.EXPORTFS_SLOW),
            # Killed after --kill-after.
            ((137, ""), nfs.EXPORTFS_SLOW),
            ((1, "exportfs: /etc/exports:5: unknown keyword \"bogus\"\n"),
             'exportfs reported a problem (exit status 1): exportfs: /etc/exports:5: unknown keyword "bogus"'),
        ],
    )
    def test_what_exportfs_reports_is_a_warning_of_the_change(self, nfs_api, result, warning):
        nfs_api.host.fail[tuple(EXPORTFS)] = result

        response = nfs_api.create(nfs_api.data / "media")

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [warning]
        assert nfs_api.unos_file is not None

    def test_a_host_without_exportfs(self, nfs_api):
        nfs_api.host.missing_programs.add("exportfs")

        response = nfs_api.create(nfs_api.data / "media")

        assert response.status_code == 200, response.text
        assert response.json()["warnings"] == [nfs.SERVER_MISSING]


class TestDelete:
    def test_only_the_lines_of_the_export_are_removed(self, nfs_api):
        media, docs = nfs_api.data / "media", nfs_api.data / "shared" / "docs"
        before = (
            nfs.HEADER
            + "# a note by hand\n"
            + unos_line(str(media), "192.168.1.0/24", "rw", str(nfs_api.data))
            + "/srv/hand 10.1.0.0/16(ro) \\\n  10.2.0.0/16(rw)\n"
            + unos_line(str(docs), "fd00::/64", "ro", str(nfs_api.data))
        )
        nfs_api.host.files[UNOS_FILE] = before

        response = nfs_api.delete(media)

        assert (response.status_code, response.json()) == (200, {"status": "deleted", "warnings": []})
        assert nfs_api.unos_file == before.replace(
            unos_line(str(media), "192.168.1.0/24", "rw", str(nfs_api.data)), "")
        assert nfs_api.host.files["/etc/exports"] == OWNER_EXPORTS
        assert nfs_api.exportfs_runs == [EXPORTFS]
        [entry] = audit_entries(nfs_api.audit)
        assert (entry["action"], entry["severity"], entry["resource_type"], entry["resource_id"], entry["details"]) == (
            "share_deleted", "warning", "nfs_export", str(media), f"Removed the NFS export of {media}")

        # A line written by hand into UNOS's file is the owner's: UNOS does not delete it.
        after = nfs_api.unos_file
        response = nfs_api.delete("/srv/hand")
        assert (response.status_code, response.json()["detail"]) == (
            409, nfs.foreign_export("/srv/hand", UNOS_FILE, 4))
        assert nfs_api.unos_file == after and len(nfs_api.exportfs_runs) == 1

    def test_an_export_that_shares_its_line_with_another_is_not_deleted(self, nfs_api):
        # A blank and a carriage return inside a line end an entry, so exportfs reads two exports
        # from line 3. UNOS writes one export per line: this line is not one of its own, and
        # removing it for either export would remove the other one too.
        media = nfs_api.data / "media"
        before = (
            nfs.HEADER
            + unos_line(str(media), "192.168.1.0/24", "rw", str(nfs_api.data)).rstrip("\n")
            + " \r /srv/b 10.0.0.2(ro)\n"
        )
        nfs_api.host.files[UNOS_FILE] = before

        listed = [(e["path"], e["line"], e["managed"]) for e in nfs_api.listed() if e["file"] == UNOS_FILE]

        assert listed == [(str(media), 3, False), ("/srv/b", 3, False)]
        for path in (str(media), "/srv/b"):
            response = nfs_api.delete(path)
            assert (response.status_code, response.json()["detail"]) == (409, nfs.foreign_export(path, UNOS_FILE, 3))
        assert nfs_api.unos_file == before and nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_the_line_before_a_last_line_without_a_newline_keeps_its_newline(self, nfs_api):
        media = nfs_api.data / "media"
        nfs_api.host.files[UNOS_FILE] = nfs.HEADER + "# kept by hand\n" + unos_line(
            str(media), "192.168.1.0/24", "rw", str(nfs_api.data)).rstrip("\n")

        response = nfs_api.delete(media)

        assert response.status_code == 200, response.text
        assert nfs_api.unos_file == nfs.HEADER + "# kept by hand\n"

    @pytest.mark.parametrize("file, number", [("/etc/exports", 5), ("/etc/exports.d/backup.exports", 2)])
    def test_no_export_is_deleted_while_an_export_file_has_a_line_that_exportfs_cannot_read(
        self, nfs_api, file, number
    ):
        # exportfs -ra would read no further in that file: the exports after the line would end.
        assert nfs_api.create(nfs_api.data / "media").status_code == 200
        start = OWNER_EXPORTS if file == "/etc/exports" else "# backup\n"
        nfs_api.host.files[file] = start + "/srv/broken 10.0.0.9(rw,bogus)\n/srv/after 10.0.0.10(ro)\n"
        before = dict(nfs_api.host.files)

        response = nfs_api.delete(nfs_api.data / "media")

        assert (response.status_code, response.json()["detail"]) == (409, nfs.file_unreadable(file, number))
        assert nfs_api.host.files == before and len(nfs_api.exportfs_runs) == 1

    def test_add_then_delete_leaves_the_header(self, nfs_api):
        assert nfs_api.create(nfs_api.data / "media").status_code == 200
        response = nfs_api.delete(nfs_api.data / "media")
        assert response.status_code == 200, response.text
        assert nfs_api.unos_file == nfs.HEADER
        assert nfs_api.listed() == [e for e in nfs_api.listed() if not e["managed"]]
        assert len(nfs_api.exportfs_runs) == 2

    def test_the_path_is_matched_as_a_path(self, nfs_api):
        assert nfs_api.create(nfs_api.data / "media").status_code == 200

        response = nfs_api.delete(f"{nfs_api.data}//media/")

        assert response.status_code == 200, response.text
        assert nfs_api.unos_file == nfs.HEADER

    def test_an_export_that_unos_does_not_have(self, nfs_api):
        response = nfs_api.delete(nfs_api.data / "media")

        assert (response.status_code, response.json()["detail"]) == (
            404, nfs.not_exported(str(nfs_api.data / "media")))
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_an_export_of_another_file_is_not_changed(self, nfs_api):
        response = nfs_api.delete("/srv/nfs/backup")

        assert (response.status_code, response.json()["detail"]) == (
            409, nfs.foreign_export("/srv/nfs/backup", "/etc/exports", 2))
        assert nfs_api.host.files["/etc/exports"] == OWNER_EXPORTS
        assert nfs_api.writes == [] and nfs_api.exportfs_runs == []

    def test_an_export_whose_lines_exportfs_cannot_read_is_left_to_the_owner(self, nfs_api):
        media = nfs_api.data / "media"
        broken = (
            nfs.HEADER
            + unos_line(str(media), "192.168.1.0/24", "rw", str(nfs_api.data))
            + "/srv/hand 10.1.0.0/16(rw) 10.2.0.0/16(bogus)\n"
        )
        nfs_api.host.files[UNOS_FILE] = broken

        hand = nfs_api.delete("/srv/hand")
        mine = nfs_api.delete(media)

        assert (hand.status_code, hand.json()["detail"]) == (409, nfs.foreign_export("/srv/hand", UNOS_FILE, 4))
        assert (mine.status_code, mine.json()["detail"]) == (409, nfs.file_unreadable(UNOS_FILE, 4))
        assert nfs_api.unos_file == broken and nfs_api.exportfs_runs == []

    def test_exportfs_problems_are_warnings(self, nfs_api):
        assert nfs_api.create(nfs_api.data / "media").status_code == 200
        nfs_api.host.missing_programs.add("exportfs")

        response = nfs_api.delete(nfs_api.data / "media")

        assert (response.status_code, response.json()) == (200, {"status": "deleted", "warnings": [nfs.SERVER_MISSING]})


class TestRealCommands:
    """The reading and writing of export files through real commands on this machine
    (UNOS_HOST_EXEC=local): text mode must change no byte, CR and bytes that are not UTF-8
    included."""

    @pytest.fixture
    def local(self, monkeypatch, tmp_path):
        monkeypatch.setenv("UNOS_HOST_EXEC", "local")
        monkeypatch.setattr(pools.settings, "nfs_exports_path", str(tmp_path / "exports"))
        (tmp_path / "exports.d").mkdir()
        return tmp_path

    def test_every_file_is_read_byte_for_byte_with_one_command(self, local, monkeypatch):
        main = b"/srv/a 10.0.0.1(rw)\r\n\r\n/srv/b 10.0.0.2(ro)\r\n"
        own = b"# UNOS\n/srv/caf\xe9 10.0.0.3(ro)\n"
        (local / "exports").write_bytes(main)
        (local / "exports.d" / "unos.exports").write_bytes(own)
        (local / "exports.d" / "skipped.txt").write_bytes(b"/srv/x *(rw)\n")
        calls = []
        run = subprocess.run

        def counting(cmd, *args, **kwargs):
            calls.append(cmd)
            return run(cmd, *args, **kwargs)

        monkeypatch.setattr(subprocess, "run", counting)

        files = nfs._read_files()

        assert [(f.path, f.data) for f in files] == [
            (str(local / "exports"), main), (str(local / "exports.d" / "unos.exports"), own)]
        assert len(calls) == 1
        # The blank CRLF line ends the reading of the main file, as exportfs reads it.
        assert files[0].parsed.problem == exports.Problem(2, exports.NO_PATH)
        assert [e.path for e in files[1].parsed.entries] == ["/srv/caf\ufffd"]

    def test_a_file_is_written_byte_for_byte(self, local):
        content = b"# UNOS\r\n/srv/caf\xe9 10.0.0.3(ro)\n"
        target = local / "exports.d" / "unos.exports"

        host_module.write_host_file_atomic(
            str(target), content.decode("utf-8", "surrogateescape"), mode="0644", errors="surrogateescape")

        assert target.read_bytes() == content


def test_the_e2e_fakes_answer_with_the_servers_texts():
    """webui/tests/shares-fakes.ts answers the NFS requests with these texts word for word (ADR-0189)."""
    fakes = (REPO / "webui" / "tests" / "shares-fakes.ts").read_text(encoding="utf-8")
    for text in (nfs.SERVER_MISSING, nfs.CLIENT_SYNTAX_PROBLEM, nfs.POOL_ROOT_PROBLEM):
        assert json.dumps(text).strip('"') in fakes, text
    template = "${client}"
    assert f"`{nfs.client_not_local_problem(template)}`" in fakes
    assert f"`{nfs.exported_already('${path}')}`" in fakes
    assert f"`{nfs.not_exported('${path}')}`" in fakes
    assert f"`{nfs.foreign_export('${path}', '${file}', '${line}')}`" in fakes
    assert f"`{nfs.file_unreadable('${file}', '${line}')}`" in fakes
    assert f"`{nfs.file_problem('${file}', exports.Problem('${line}', '${message}'))}`" in fakes
    assert f"`{nfs.unsafe_export('${path}', '${folder}', '${detail}', '${commands}')}`" in fakes


def test_exportfs_is_an_optional_host_tool_of_its_package():
    # deploy/install.sh does not install the NFS server (ADR-0291): the startup check of the host's
    # programs leaves exportfs out, and the NFS tab says when the host lacks it.
    from src import host

    assert host.OPTIONAL_HOST_TOOLS["exportfs"] == "nfs-kernel-server"
    assert "exportfs" not in host.HOST_TOOLS
