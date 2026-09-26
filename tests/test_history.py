"""Unit tests for the SQLite dictation history."""

import sqlite3
import threading

import pytest

from vox.history import HistoryDB


@pytest.fixture
def db(tmp_path):
    history = HistoryDB(tmp_path / "nested" / "history.db")
    yield history
    history.close()


def test_creates_parent_dirs_and_schema(tmp_path):
    path = tmp_path / "a" / "b" / "history.db"
    HistoryDB(path).close()
    conn = sqlite3.connect(path)
    cols = [row[1] for row in conn.execute("PRAGMA table_info(history)")]
    indexes = [row[1] for row in conn.execute("PRAGMA index_list(history)")]
    conn.close()
    assert cols == ["id", "created_at", "text", "app_type", "duration_seconds", "transcription_mode"]
    assert "idx_history_created_at" in indexes


def test_default_path_is_used(tmp_path):
    history = HistoryDB()
    try:
        assert history.path == tmp_path / "history.db"  # redirected by conftest
    finally:
        history.close()


def test_insert_and_read_back(db):
    row_id = db.insert("hello world", app_type="TERMINAL", duration_seconds=1.5, transcription_mode="batch")
    assert row_id is not None
    [rec] = db.search()
    assert rec.id == row_id
    assert rec.text == "hello world"
    assert rec.app_type == "TERMINAL"
    assert rec.duration_seconds == 1.5
    assert rec.transcription_mode == "batch"
    assert rec.created_at  # filled by DEFAULT CURRENT_TIMESTAMP


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_empty_text_is_not_stored(db, text):
    assert db.insert(text) is None
    assert db.search() == []


def test_search_newest_first_and_case_insensitive(db):
    db.insert("Deploy the cluster")
    db.insert("unrelated note")
    db.insert("deploy again")
    assert [r.text for r in db.search("DEPLOY")] == ["deploy again", "Deploy the cluster"]
    assert [r.text for r in db.search("")] == ["deploy again", "unrelated note", "Deploy the cluster"]


def test_search_escapes_like_wildcards(db):
    db.insert("100% done")
    db.insert("1000 done")
    db.insert("snake_case")
    db.insert("snakeXcase")
    assert [r.text for r in db.search("0%")] == ["100% done"]
    assert [r.text for r in db.search("e_c")] == ["snake_case"]


def test_recent_limits_results(db):
    for i in range(5):
        db.insert(f"entry {i}")
    assert [r.text for r in db.recent(3)] == ["entry 4", "entry 3", "entry 2"]


def test_concurrent_inserts_from_threads(db):
    def worker(n):
        for i in range(20):
            db.insert(f"t{n}-{i}")

    threads = [threading.Thread(target=worker, args=(n,)) for n in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(db.search(limit=1000)) == 80


def test_persists_across_instances(tmp_path):
    path = tmp_path / "history.db"
    first = HistoryDB(path)
    first.insert("survives restart")
    first.close()
    second = HistoryDB(path)
    try:
        assert [r.text for r in second.search()] == ["survives restart"]
    finally:
        second.close()
