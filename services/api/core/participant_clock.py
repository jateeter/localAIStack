"""localAI as a Lamport clock participant (localAIStack#140, RealityEngine_CI#600).

The rule is RealityEngine_CI ``SURFACE_SPEC.md`` "The instance clock". localAI
is a participant of its own, beside each engine's RE and PE:

* **Identity.** ``LOCALAI_INSTANCE_UUID``, allocated by CI. With it the clock is
  persisted and locked: an exclusive ``fcntl`` lock on ``<uuid>.lock`` for the
  life of the process, and two high-water marks, ``<uuid>.lamport`` and
  ``<uuid>.commit``, each reserved 1024 ticks ahead, in ``INSTANCE_CLOCK_DIR``
  (default ``$LOCALAI_STATE_DIR/clock``). A boot resumes from the marks and
  reserves the next block before issuing a tick; a tick or a receive past a
  mark writes the new mark (temp file, then rename) **before** the value is
  used. A clock that cannot be locked, read or written refuses the boot.
  Without the variable the clock is minted for this process and not persisted
  (``persisted: false``), as a hand-run stack has no allocation.
* **Ticks.** ``lamport`` ticks on every receive, send and origin event; a
  receive is ``max(local, received) + 1``. ``commit`` counts origin events: an
  interaction localAI computes once and writes to every engine
  (``core/engine_fanout.py``).
* **The header**, ``RE-Clock: instance=<uuid>; lamport=<n>``, on every engine
  request localAI sends and every response it gives. A malformed header on an
  inbound request is refused with 400 ``{"error": "malformed RE-Clock header"}``
  and still counts as a receive; ``headers {received, missing, rejected}``
  counts inbound requests since boot.
"""

from __future__ import annotations

import contextvars
import fcntl
import json
import os
import re
import threading
import uuid as uuid_lib
from pathlib import Path
from typing import Any

import httpx
import structlog

log = structlog.get_logger()

HEADER_NAME = "RE-Clock"
MAX_LAMPORT = 2**53 - 1
RESERVE = 1024
ERROR_BODY = {"error": "malformed RE-Clock header"}
_HEADER = re.compile(
    r"^instance=([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})\s*;\s*lamport=(0|[1-9][0-9]*)$"
)


class ClockError(RuntimeError):
    """The persisted clock cannot be locked, read or written: the boot is refused."""


def parse_header(value: str) -> tuple[str, int] | None:
    """``(instance, lamport)`` for a well-formed header value, else ``None``."""
    m = _HEADER.match(value.strip())
    if not m:
        return None
    lamport = int(m.group(2))
    if lamport > MAX_LAMPORT:
        return None
    return m.group(1), lamport


def _mint_v7() -> str:
    return str(uuid_lib.uuid7()) if hasattr(uuid_lib, "uuid7") else str(uuid_lib.uuid4())


class ParticipantClock:
    def __init__(self, instance: str | None = None, clock_dir: Path | None = None):
        self._lock = threading.Lock()
        self.counts = {"received": 0, "missing": 0, "rejected": 0}
        self.persisted = bool(instance)
        self.instance = (instance or _mint_v7()).lower()
        self.lamport = 0
        self.commit = 0
        self._marks = {"lamport": 0, "commit": 0}
        self._dir = clock_dir
        self._lock_fd: int | None = None
        if self.persisted:
            if parse_header(f"instance={self.instance}; lamport=0") is None:
                raise ClockError(f"LOCALAI_INSTANCE_UUID {instance!r} is not a canonical UUID")
            self._boot()

    # -- persistence -------------------------------------------------------
    def _path(self, kind: str) -> Path:
        assert self._dir is not None
        return self._dir / f"{self.instance}.{kind}"

    def _boot(self) -> None:
        assert self._dir is not None
        try:
            self._dir.mkdir(parents=True, exist_ok=True)
            fd = os.open(self._path("lock"), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError as exc:
                os.close(fd)
                raise ClockError(f"{self._path('lock')} is held by another live process") from exc
            self._lock_fd = fd
            for kind in ("lamport", "commit"):
                path = self._path(kind)
                if path.exists():
                    self._marks[kind] = int(path.read_text().strip() or "0")
            # A pre-#600 directory holds only <uuid>.lamport: it seeds both.
            if self._path("lamport").exists() and not self._path("commit").exists():
                self._marks["commit"] = self._marks["lamport"]
            self.lamport, self.commit = self._marks["lamport"], self._marks["commit"]
            self._write("lamport", self.lamport + RESERVE)
            self._write("commit", self.commit + RESERVE)
        except (OSError, ValueError) as exc:
            raise ClockError(f"the clock in {self._dir} cannot be used: {exc}") from exc

    def _write(self, kind: str, mark: int) -> None:
        path = self._path(kind)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with open(tmp, "w", encoding="ascii") as fh:
            fh.write(f"{mark}\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        self._marks[kind] = mark

    def _advance(self, kind: str, value: int) -> None:
        """Persist the mark past ``value`` before it is used (called under the lock)."""
        if self.persisted and value > self._marks[kind]:
            self._write(kind, value + RESERVE)

    # -- ticks -------------------------------------------------------------
    def _tick_to(self, value: int) -> int:
        self._advance("lamport", value)
        self.lamport = value
        return value

    def send(self) -> int:
        with self._lock:
            return self._tick_to(self.lamport + 1)

    def receive(self, received: int | None) -> int:
        with self._lock:
            return self._tick_to(max(self.lamport, received or 0) + 1)

    def origin(self) -> dict[str, Any]:
        """An origin event: an interaction computed once. Ticks commit and lamport."""
        with self._lock:
            self._advance("commit", self.commit + 1)
            self.commit += 1
            return {"instance": self.instance, "lamport": self._tick_to(self.lamport + 1)}

    def header(self, lamport: int) -> str:
        return f"instance={self.instance}; lamport={lamport}"

    def count(self, kind: str) -> None:
        with self._lock:
            self.counts[kind] += 1

    def json(self) -> dict[str, Any]:
        with self._lock:
            return {
                "instance": self.instance,
                "lamport": self.lamport,
                "commit": self.commit,
                "persisted": self.persisted,
                "headers": dict(self.counts),
            }


_clock: ParticipantClock | None = None
_clock_guard = threading.Lock()


def clock() -> ParticipantClock:
    """The process's clock, booted on first use from the environment."""
    global _clock
    with _clock_guard:
        if _clock is None:
            instance = os.environ.get("LOCALAI_INSTANCE_UUID", "").strip() or None
            base = os.environ.get("INSTANCE_CLOCK_DIR") or str(
                Path(os.environ.get("LOCALAI_STATE_DIR", "/app/state")) / "clock"
            )
            _clock = ParticipantClock(instance, Path(base))
            log.info(
                "participant_clock.boot",
                **{k: v for k, v in _clock.json().items() if k != "headers"},
            )
        return _clock


def reset_for_tests(c: ParticipantClock | None = None) -> None:
    global _clock
    with _clock_guard:
        _clock = c


# -- inbound: every request is a receive, every response a send ---------------

# The RE-Clock an inbound request presented, for the drift ledger.
presented: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "re_clock_presented", default=None
)


class ClockMiddleware:
    """ASGI middleware: the receive rule on every inbound request, a stamped response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        c = clock()
        values = [
            v.decode("latin-1")
            for k, v in scope.get("headers", [])
            if k.decode("latin-1").lower() == "re-clock"
        ]
        parsed = parse_header(values[0]) if len(values) == 1 else None

        async def stamped(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                headers.append((b"re-clock", c.header(c.send()).encode()))
                message = {**message, "headers": headers}
            await send(message)

        if values and parsed is None:
            c.count("rejected")
            c.receive(None)
            body = json.dumps(ERROR_BODY).encode()
            await stamped(
                {
                    "type": "http.response.start",
                    "status": 400,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode()),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        c.count("received" if parsed else "missing")
        at = c.receive(parsed[1] if parsed else None)
        token = presented.set(
            {
                "request": {"instance": parsed[0], "lamport": parsed[1]} if parsed else None,
                "localai": {"instance": c.instance, "lamport": at},
            }
        )
        try:
            await self.app(scope, receive, stamped)
        finally:
            presented.reset(token)


# -- outbound: every engine call is a send, its response a receive ------------

# The fan-out interaction in progress (core/engine_fanout.py): its origin and
# each engine's send and reply lamports.
interaction: contextvars.ContextVar[dict | None] = contextvars.ContextVar(
    "re_clock_interaction", default=None
)


def _on_request(request: httpx.Request) -> None:
    c = clock()
    lamport = c.send()
    request.headers[HEADER_NAME] = c.header(lamport)
    request.extensions["re_clock_sent"] = lamport


def _on_response(response: httpx.Response) -> None:
    c = clock()
    parsed = parse_header(response.headers.get(HEADER_NAME, ""))
    c.receive(parsed[1] if parsed else None)
    current = interaction.get()
    if current is not None:
        current["sends"].append(
            {
                "url": f"{response.request.url.scheme}://{response.request.url.netloc.decode()}",
                "sent": response.request.extensions.get("re_clock_sent"),
                "reply": {"instance": parsed[0], "lamport": parsed[1]} if parsed else None,
            }
        )


async def _on_request_async(request: httpx.Request) -> None:
    _on_request(request)


async def _on_response_async(response: httpx.Response) -> None:
    _on_response(response)


def engine_client(**kwargs: Any) -> httpx.Client:
    """An ``httpx.Client`` for calls to the engines: each request stamped, each reply received."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).append(_on_request)
    hooks.setdefault("response", []).append(_on_response)
    return httpx.Client(event_hooks=hooks, **kwargs)


def async_engine_client(**kwargs: Any) -> httpx.AsyncClient:
    """The async counterpart of ``engine_client``."""
    hooks = kwargs.pop("event_hooks", {}) or {}
    hooks.setdefault("request", []).append(_on_request_async)
    hooks.setdefault("response", []).append(_on_response_async)
    return httpx.AsyncClient(event_hooks=hooks, **kwargs)
