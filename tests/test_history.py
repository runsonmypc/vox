"""Unit tests for the SQLite dictation history."""

import os
import sqlite3
import stat
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


def test_search_folds_non_ascii_case(db):
    db.insert("Über das Café")
    db.insert("Élan vital")
    db.insert("Straße")
    assert [r.text for r in db.search("über")] == ["Über das Café"]
    assert [r.text for r in db.search("CAFÉ")] == ["Über das Café"]
    assert [r.text for r in db.search("élan")] == ["Élan vital"]
    assert [r.text for r in db.search("STRASSE")] == ["Straße"]


def test_search_treats_backslashes_literally(db):
    db.insert(r"C:\Users\me")
    db.insert("C:Usersme")
    assert [r.text for r in db.search("\\users")] == [r"C:\Users\me"]


def test_database_and_new_directory_are_owner_only(tmp_path):
    path = tmp_path / "vox" / "history.db"
    history = HistoryDB(path)
    history.insert("private words")
    history.close()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_database_is_made_owner_only(tmp_path):
    path = tmp_path / "history.db"
    HistoryDB(path).close()
    os.chmod(path, 0o644)
    HistoryDB(path).close()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_existing_vox_data_directory_is_made_owner_only(tmp_path, monkeypatch):
    path = tmp_path / "share" / "vox" / "history.db"
    monkeypatch.setattr("vox.history.DEFAULT_HISTORY_PATH", path)
    path.parent.mkdir(parents=True)
    for directory in (path.parent, path.parent.parent):
        os.chmod(directory, 0o755)  # as older versions left them, whatever the umask
    HistoryDB().close()
    assert stat.S_IMODE(path.parent.stat().st_mode) == 0o700
    assert stat.S_IMODE(path.parent.parent.stat().st_mode) == 0o755  # never ~/.local/share itself


def test_a_directory_given_by_path_keeps_its_mode(tmp_path):
    shared = tmp_path / "shared"
    shared.mkdir()
    os.chmod(shared, 0o755)
    HistoryDB(shared / "history.db").close()
    assert stat.S_IMODE(shared.stat().st_mode) == 0o755


def test_history_opens_when_its_directory_cannot_be_made_private(tmp_path, monkeypatch, caplog):
    path = tmp_path / "vox" / "history.db"
    monkeypatch.setattr("vox.history.DEFAULT_HISTORY_PATH", path)
    chmod = os.chmod

    def refuse_directory(target, mode):
        if target == path.parent:
            raise PermissionError(1, "Operation not permitted")
        chmod(target, mode)

    monkeypatch.setattr("vox.history.os.chmod", refuse_directory)
    history = HistoryDB()
    history.insert("still saved")
    assert [r.text for r in history.search()] == ["still saved"]
    history.close()
    assert "Couldn't make" in caplog.text
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_delete_and_clear_remove_rows(db):
    first = db.insert("first")
    db.insert("second")
    db.insert("third")
    db.delete(first)
    assert [r.text for r in db.search()] == ["third", "second"]
    db.clear()
    assert db.search() == []
    assert db.stats() == (0, None)


@pytest.mark.parametrize("erase", ["delete", "clear"])
def test_deleted_text_is_erased_from_the_file(tmp_path, erase):
    path = tmp_path / "history.db"
    history = HistoryDB(path)
    history.insert("keep this one")
    secret_id = history.insert("my password is hunter2-zebra")
    if erase == "delete":
        history.delete(secret_id)
    else:
        history.clear()
    history.close()
    assert b"hunter2-zebra" not in path.read_bytes()
