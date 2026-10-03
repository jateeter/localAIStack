"""Removals are right; forgetting them is not (RealityEngine_CI#518, K-line support)."""

from __future__ import annotations

import json

import pytest

from core import pe_sources, source_observations


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAI_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(source_observations, "_instance_for", lambda url: "cpp-1")
    return tmp_path


def test_a_removal_is_persisted_with_what_existed(state_dir):
    src = {
        "id": "test-x",
        "type": "test",
        "name": "localai/personal_health_baseline / 5 sequences",
        "region": {"offset": 7574, "length": 4},
    }
    source_observations.record_removal("source", "http://pe", src, "window_claimed")
    lines = (state_dir / "source-observations.jsonl").read_text().splitlines()
    assert len(lines) == 1
    row = json.loads(lines[0])
    assert row["observed"] == src
    assert row["reason"] == "window_claimed"
    assert row["instance"] == "cpp-1"
    assert source_observations.removals()[-1]["observed"]["name"] == src["name"]


def test_the_record_is_append_only_and_filterable(state_dir, monkeypatch):
    source_observations.record_removal("source", "http://pe", {"id": "a"}, "slot_left_scope")
    monkeypatch.setattr(source_observations, "_instance_for", lambda url: "lsp-1")
    source_observations.record_removal("machine", "http://re", {"id": "b"}, "machine_replaced")
    assert [r["observed"]["id"] for r in source_observations.removals()] == ["a", "b"]
    assert [r["observed"]["id"] for r in source_observations.removals(instance="lsp-1")] == ["b"]


class _PE:
    def __init__(self):
        self.deleted = []

    def delete(self, url):
        self.deleted.append(url)

        class R:
            status_code = 200

        return R()


def test_claim_window_records_the_replay_it_removes(state_dir):
    replay = {
        "id": "test-m",
        "type": "test",
        "name": "localai/personal_health_baseline / 5 sequences",
        "region": {"offset": 7574, "length": 4},
    }
    pe = _PE()
    assert pe_sources.claim_window(pe, "http://pe", {"offset": 7574, "length": 4}, [replay]) == 1
    assert source_observations.removals()[-1]["observed"] == replay


def test_an_unwritable_state_dir_never_fails_the_removal(monkeypatch, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    monkeypatch.setenv("LOCALAI_STATE_DIR", str(blocker))
    monkeypatch.setattr(source_observations, "_instance_for", lambda url: None)
    row = source_observations.record_removal("source", "http://pe", {"id": "z"}, "slot_moved")
    assert row["observed"] == {"id": "z"}
