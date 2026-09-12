"""The machine-import guard must not fail open.

`import_machines_everywhere` skips a machine the engine already holds, which it
learns from `GET /api/machines`. That read used to return `set()` on any
exception, so a read that timed out meant "this engine holds nothing" and the
bridge re-imported the entire localai set.

On 2026-09-11 that produced six machines resident twice on one engine of three
— 1344 against 1338 — and every fold they produced was emitted twice. It was
first reported as a Scala loader defect (RealityEngine_Scala#114) because Scala
was the engine that lost the race. It was this.

The read is not comfortable: `GET /api/machines` is ~16.4 MB, and cpp answers
in ~1.0s against what was a 2.0s client timeout, so losing is ordinary.

These pin both halves: the read reports "unknown" distinctly from "empty", and
the caller does nothing rather than everything when it does not know.
"""

from __future__ import annotations

import httpx
import pytest

from core import reality_bridge as rb


class _Resp:
    def __init__(self, payload, status=200):
        self._payload, self.status_code = payload, status

    def json(self):
        return self._payload

    def raise_for_status(self):
        if self.status_code >= 400:
            raise httpx.HTTPStatusError("boom", request=None, response=None)


class _Client:
    """Records POSTs; `get_raises` makes the inventory read fail."""

    def __init__(self, names=(), get_raises=None):
        self._names, self._get_raises = list(names), get_raises
        self.posts = []

    def get(self, url, **kw):
        if self._get_raises is not None:
            raise self._get_raises
        return _Resp({"machines": [{"name": n} for n in self._names]})

    def post(self, url, json=None, **kw):
        self.posts.append((url, json))
        return _Resp({"success": True})

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── the read ────────────────────────────────────────────────────────────────


def test_unreadable_inventory_is_none_not_empty():
    """`None` and `set()` are different answers and must stay different."""
    assert (
        rb._get_existing_machine_names(
            _Client(get_raises=httpx.ReadTimeout("timed out")), "http://re"
        )
        is None
    )


def test_genuinely_empty_inventory_is_an_empty_set():
    """An engine that holds nothing is not the same as an unreadable one."""
    assert rb._get_existing_machine_names(_Client(names=[]), "http://re") == set()


def test_readable_inventory_returns_the_names():
    assert rb._get_existing_machine_names(
        _Client(names=["localai/a", "localai/b"]), "http://re"
    ) == {"localai/a", "localai/b"}


# ── the caller ──────────────────────────────────────────────────────────────


@pytest.fixture
def one_target(monkeypatch):
    monkeypatch.setattr(rb, "_re_targets", lambda: [{"re_url": "http://re", "instance": "scala-1"}])


def _run(monkeypatch, client, machines):
    monkeypatch.setattr(rb.httpx, "Client", lambda *a, **k: client)
    return rb.import_machines_everywhere(machines, "test")


MACHINES = [
    ("localai/a", {"machine": {"name": "localai/a"}}),
    ("localai/b", {"machine": {"name": "localai/b"}}),
]


def test_unreadable_inventory_imports_nothing(monkeypatch, one_target):
    """The regression. A failed read must not mean "everything is new"."""
    c = _Client(get_raises=httpx.ReadTimeout("timed out"))
    ok = _run(monkeypatch, c, MACHINES)
    assert c.posts == [], "refused to import blind"
    assert ok is False, "a blind skip must not report success"


def test_already_present_machines_are_skipped(monkeypatch, one_target):
    c = _Client(names=["localai/a", "localai/b"])
    assert _run(monkeypatch, c, MACHINES) is True
    assert c.posts == []


def test_absent_machines_are_imported(monkeypatch, one_target):
    c = _Client(names=["localai/a"])
    assert _run(monkeypatch, c, MACHINES) is True
    assert [j["machine"]["name"] for _, j in c.posts] == ["localai/b"]


def test_inventory_read_has_its_own_timeout():
    """A 16.4 MB corpus listing must not inherit a push's 2s deadline."""
    assert rb._INVENTORY_TIMEOUT.read > rb._PUSH_TIMEOUT.read
