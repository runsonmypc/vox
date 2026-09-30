"""Durable recovery audio, migration and cleanup fault coverage."""

import io
import sqlite3
import stat
import wave
from pathlib import Path
from unittest.mock import patch

import pytest

from vox.history import HistoryDB
from vox.recovery import RecoveryError, RecoveryStore


def wav_bytes():
    out = io.BytesIO()
    with wave.open(out, 'wb') as wav:
        wav.setparams((1, 2, 16000, 0, 'NONE', 'not compressed'))
        wav.writeframes(b'\x01\x00' * 1600)
    return out.getvalue()


def metadata(text=''):
    return dict(text=text, original_mode='streaming', attempted_mode='batch',
                error_summary='Transcription failed. Check the selected provider and retry.',
                created_at='2026-01-02 03:04:05', duration_seconds=0.1, app_type='EDITOR')


@pytest.fixture
def store(tmp_path):
    db = HistoryDB(tmp_path / 'history.db')
    yield RecoveryStore(db)
    db.close()


def save(store, text=''):
    audio_id = store.stage(wav_bytes(), metadata(text))
    store.publish(audio_id)
    return audio_id, store.index(audio_id)


def test_original_wav_permissions_and_restart(store):
    audio_id, entry_id = save(store, 'part one')
    assert store.read(audio_id) == wav_bytes()
    assert stat.S_IMODE(store.root.stat().st_mode) == 0o700
    directory = store.root / audio_id
    assert stat.S_IMODE(directory.stat().st_mode) == 0o700
    assert stat.S_IMODE((directory / 'original.wav').stat().st_mode) == 0o600
    restarted = HistoryDB(store.db.path)
    try:
        again = RecoveryStore(restarted)
        assert again.reconcile() == []
        assert again.read(audio_id) == wav_bytes()
        rec = restarted.get(entry_id)
        assert rec.status == 'partial' and rec.text == 'part one'
        assert restarted.attempts(entry_id)[0][1:] == ('part one', 'batch')
    finally:
        restarted.close()


def test_index_failure_reconciles_once_without_rollback(store):
    audio_id = store.stage(wav_bytes(), metadata('prior'))
    store.publish(audio_id)
    with patch.object(store.db, 'insert_failure', side_effect=sqlite3.OperationalError('readonly')):
        with pytest.raises(sqlite3.Error):
            store.index(audio_id)
    assert store.read(audio_id) == wav_bytes()
    assert store.reconcile() == []
    rec = store.db.search()[0]
    assert store.db.update_recovery(rec.id, rec.revision, status='retrying')
    # A crash after INSERT but before removing a manifest must preserve the indexed state.
    import json
    (store.root / audio_id / 'manifest.json').write_text(json.dumps({'version': 1, 'audio_id': audio_id, **metadata('prior')}))
    assert store.reconcile() == []
    assert len(store.db.search()) == 1
    assert store.db.get(rec.id).status == 'retrying'


def test_disk_write_failure_leaves_no_audio(store):
    with patch('vox.recovery.os.fsync', side_effect=OSError('disk full')):
        with pytest.raises(OSError):
            store.stage(wav_bytes(), metadata())
    assert list(store.root.iterdir()) == []


@pytest.mark.parametrize('audio_id', ['../outside', '/tmp/file', 'x', 'a' * 33])
def test_unsafe_identifiers_rejected(store, audio_id):
    with pytest.raises(RecoveryError):
        store.read(audio_id)


def test_corrupt_and_symlink_audio_rejected(store, tmp_path):
    audio_id, _ = save(store)
    wav = store.root / audio_id / 'original.wav'
    wav.write_bytes(wav_bytes()[:-2])
    with pytest.raises(RecoveryError, match='corrupt'):
        store.read(audio_id)
    wav.unlink()
    outside = tmp_path / 'outside.wav'
    outside.write_bytes(wav_bytes())
    wav.symlink_to(outside)
    with pytest.raises(RecoveryError):
        store.read(audio_id)
    wav.unlink()
    (store.root / audio_id).rmdir()
    (store.root / audio_id).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(RecoveryError):
        store.read(audio_id)


def test_delete_failure_is_durable_and_cannot_resurrect(store):
    audio_id, entry_id = save(store, 'partial')
    unlink = Path.unlink

    def fail_wav(path, *args, **kwargs):
        if path.name == 'original.wav':
            raise PermissionError('blocked')
        return unlink(path, *args, **kwargs)

    with patch.object(Path, 'unlink', fail_wav):
        with pytest.raises(RecoveryError, match='Cleanup will retry'):
            store.db.delete(entry_id)
    assert store.db.get(entry_id) is None
    assert store.db.attempts(entry_id) == []
    assert store.reconcile() == []
    assert not (store.root / audio_id).exists()
    assert store.db.search() == []


def test_clear_includes_unindexed_staged_and_partial_recordings(store):
    save(store, 'partial')
    audio_id = store.stage(wav_bytes(), metadata())
    store.publish(audio_id)
    store.stage(wav_bytes(), metadata())
    store.db.clear()
    assert list(store.root.iterdir()) == []
    assert store.reconcile() == []
    assert store.db.search() == []


def test_full_commit_preserves_metadata_and_tracks_cleanup(store):
    audio_id, entry_id = save(store, 'first')
    first = store.db.get(entry_id)
    assert store.db.fail_retry(entry_id, first.revision, 'whisper_cpp', 'Retry failed.', 'second')
    second = store.db.get(entry_id)
    assert [a[1:] for a in store.db.attempts(entry_id)] == [('first', 'batch'), ('second', 'whisper_cpp')]
    assert store.db.finish_recovery(entry_id, second.revision, 'complete', 'whisper_cpp')
    rec = store.db.get(entry_id)
    assert rec.created_at == first.created_at and rec.duration_seconds == first.duration_seconds
    assert rec.status == 'completed' and rec.text == 'complete' and rec.audio_id is None
    assert store.db.attempts(entry_id) == []
    assert store.cleanup() == []
    assert not (store.root / audio_id).exists()


def test_publication_sync_failure_keeps_recoverable_manifest(store):
    audio_id = store.stage(wav_bytes(), metadata('part'))
    with patch('vox.recovery._sync_directory', side_effect=OSError('disk full')):
        with pytest.raises(RecoveryError, match='publication'):
            store.publish(audio_id)
    assert (store.root / audio_id / 'manifest.json').exists()
    assert store.reconcile() == []
    assert len(store.db.search()) == 1


def test_disabled_publication_discards_audio(store):
    audio_id = store.stage(wav_bytes(), metadata())
    assert store.publish(audio_id, lambda: False) is False
    assert list(store.root.iterdir()) == []
    assert store.db.search() == []


def test_reconcile_cannot_restore_completed_recording(store):
    audio_id = store.stage(wav_bytes(), metadata('part'))
    store.publish(audio_id)
    original = store.db.insert_failure(audio_id=audio_id, **metadata('part'))
    assert store.db.finish_recovery(original, 0, 'full', 'batch')
    with pytest.raises(sqlite3.IntegrityError, match='pending deletion'):
        store.db.insert_failure(audio_id=audio_id, **metadata('part'))
    assert store.reconcile() == []
    assert len(store.db.search()) == 1 and store.db.get(original).status == 'completed'
