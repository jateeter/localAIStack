"""localAI's Lamport clock (localAIStack#140, RealityEngine_CI#600)."""

from __future__ import annotations

import uuid

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core import engine_fanout, participant_clock
from core.participant_clock import ClockError, ParticipantClock, parse_header

UUID = "01a2151c-7a87-7c06-9491-54dc94e61ad7"


@pytest.fixture
def fresh(tmp_path):
    c = ParticipantClock(UUID, tmp_path)
    participant_clock.reset_for_tests(c)
    yield c
    participant_clock.reset_for_tests(None)


def test_parse_header():
    assert parse_header(f"instance={UUID}; lamport=5") == (UUID, 5)
    assert parse_header(f"instance={UUID};lamport=0") == (UUID, 0)
    for bad in (
        f"instance={UUID}",
        f"instance={UUID.upper()}; lamport=1",
        f"instance={UUID}; lamport=-1",
        f"instance={UUID}; lamport=01",
        f"lamport=1; instance={UUID}",
        "instance=x; lamport=1",
        f"instance={UUID}; lamport={2**53}",
        f"instance={UUID}; lamport=1; extra=2",
    ):
        assert parse_header(bad) is None, bad


def test_receive_send_and_origin(fresh):
    assert fresh.send() == 1
    assert fresh.receive(1_000_000) == 1_000_001
    assert fresh.receive(5) == 1_000_002
    origin = fresh.origin()
    assert origin == {"instance": UUID, "lamport": 1_000_003}
    assert fresh.commit == 1 and fresh.lamport >= fresh.commit


def test_marks_reserved_ahead_and_resumed(tmp_path):
    c = ParticipantClock(UUID, tmp_path)
    assert int((tmp_path / f"{UUID}.lamport").read_text()) == 1024
    c.receive(5_000)
    # A jump past the mark is persisted before use: received + 1 + 1024.
    assert int((tmp_path / f"{UUID}.lamport").read_text()) == 5_001 + 1024
    c.origin()
    del c  # releases nothing explicitly; a new process would hold its own lock
    participant_clock.reset_for_tests(None)


def test_relaunch_resumes_past_every_issued_tick(tmp_path):
    import subprocess
    import sys

    script = (
        "import sys; from pathlib import Path; sys.path.insert(0, '.');"
        "from core.participant_clock import ParticipantClock;"
        f"c = ParticipantClock('{UUID}', Path(sys.argv[1])); c.receive(int(sys.argv[2])); print(c.lamport)"
    )
    first = int(
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), "777"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    second = int(
        subprocess.run(
            [sys.executable, "-c", script, str(tmp_path), "0"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout
    )
    assert second > first, "a relaunch never reissues a tick"


def test_second_live_holder_is_refused(tmp_path):
    import subprocess
    import sys

    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            "import sys, time; from pathlib import Path; sys.path.insert(0, '.');"
            "from core.participant_clock import ParticipantClock;"
            f"c = ParticipantClock('{UUID}', Path(sys.argv[1])); print('held', flush=True); time.sleep(30)",
            str(tmp_path),
        ],
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline().strip() == "held"
        with pytest.raises(ClockError):
            ParticipantClock(UUID, tmp_path)
    finally:
        holder.kill()
        holder.wait()


def test_pre_600_lamport_seeds_both_marks(tmp_path):
    (tmp_path / f"{UUID}.lamport").write_text("42\n")
    c = ParticipantClock(UUID, tmp_path)
    assert (c.lamport, c.commit) == (42, 42)


def test_unallocated_clock_is_minted_and_unpersisted(tmp_path):
    c = ParticipantClock(None, tmp_path)
    assert not c.persisted and parse_header(c.header(0)) is not None
    c.receive(10)
    assert list(tmp_path.iterdir()) == []


def _app() -> FastAPI:
    app = FastAPI()
    app.add_middleware(participant_clock.ClockMiddleware)

    @app.get("/chat")
    def chat():
        return {"presented": participant_clock.presented.get()}

    return app


def test_middleware_receive_stamp_and_refusal(fresh):
    client = TestClient(_app())
    r = client.get("/chat")
    assert r.status_code == 200 and parse_header(r.headers["re-clock"])[0] == UUID
    assert r.json()["presented"]["request"] is None

    engine = str(uuid.uuid4())
    r = client.get("/chat", headers={"RE-Clock": f"instance={engine}; lamport=9000000"})
    assert r.json()["presented"]["request"] == {"instance": engine, "lamport": 9_000_000}
    assert parse_header(r.headers["re-clock"])[1] > 9_000_000

    before = fresh.lamport
    r = client.get("/chat", headers={"RE-Clock": "instance=nope; lamport=1"})
    assert r.status_code == 400 and r.json() == {"error": "malformed RE-Clock header"}
    assert parse_header(r.headers["re-clock"])[1] >= before + 2, (
        "a refused request is still a receive"
    )
    assert fresh.json()["headers"] == {"received": 1, "missing": 1, "rejected": 1}


class _Engine:
    """A conforming engine: the receive rule on the request, a stamped reply."""

    def __init__(self):
        self.instance = str(uuid.uuid4())
        self.lamport = 0
        self.received: list[int] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        presented = parse_header(request.headers.get("RE-Clock", ""))
        assert presented is not None, "localAI stamps every engine call"
        self.received.append(presented[1])
        self.lamport = max(self.lamport, presented[1]) + 1
        self.lamport += 1
        return httpx.Response(
            200, json={}, headers={"RE-Clock": f"instance={self.instance}; lamport={self.lamport}"}
        )


def test_fan_out_one_origin_and_received_plus_one(fresh, monkeypatch):
    engines = {f"e{i}": _Engine() for i in range(3)}
    targets = [
        {"instance": k, "re_url": f"https://{k}.invalid", "pe_url": f"https://{k}.invalid"}
        for k in engines
    ]
    monkeypatch.setattr(engine_fanout, "interaction_targets", lambda: targets)
    engine_fanout.reset_parity_report()

    def write(target):
        transport = httpx.MockTransport(engines[target["instance"]])
        with participant_clock.engine_client(transport=transport) as c:
            c.post(f"{target['pe_url']}/api/sensors/x", json={"values": [1]})

    engine_fanout.fan_out(write)
    record = engine_fanout.causal_report()[-1]
    assert record["origin"]["instance"] == UUID
    assert [s["engine"] for s in record["sends"]] == ["e0", "e1", "e2"]
    for send in record["sends"]:
        assert send["sent"] > record["origin"]["lamport"], "every write follows its one origin"
        # The engine received the send and answered past it: received + 1, then its send.
        assert send["reply"]["lamport"] >= send["sent"] + 2
    assert fresh.lamport > max(s["reply"]["lamport"] for s in record["sends"])
