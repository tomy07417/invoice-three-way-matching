"""Private local storage never uses caller filenames as paths."""
from hashlib import sha256
import os
import re
import stat

import pytest

from app.application.dtos.invoice_documents import DocumentMetadata, ValidatedDocument
from app.application.ports.invoice_documents import DocumentStorage, DocumentStorageError
from app.infrastructure.storage.local_documents import LocalDocumentStorage


def document(content=b"synthetic private invoice"):
    return ValidatedDocument(content, DocumentMetadata(
        "../../sensitive.pdf", "application/pdf", len(content), sha256(content).hexdigest(), 1))


def test_roundtrip_private_modes_and_opaque_unique_keys(tmp_path):
    root = tmp_path / "documents"
    storage: DocumentStorage = LocalDocumentStorage(root)
    keys = [storage.put(document()) for _ in range(2)]
    assert keys[0] != keys[1]
    assert all(re.fullmatch(r"[0-9a-f]{64}", key) for key in keys)
    assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert {p.name for p in root.iterdir()} == set(keys)
    for key in keys:
        assert storage.read(key) == document().content
        assert stat.S_IMODE((root / key).stat().st_mode) == 0o600
    storage.delete(keys[0])
    storage.delete(keys[0])
    assert storage.read(keys[1]) == document().content
    with pytest.raises(DocumentStorageError):
        storage.read(keys[0])


@pytest.mark.parametrize("key", ["../secret", "/absolute", "", ".", "..", "a/b", "a\\b", "a"*63, "A"*64, "g"*64, "x\x00"])
def test_invalid_keys_leave_existing_data_unchanged(tmp_path, key):
    storage = LocalDocumentStorage(tmp_path / "documents")
    existing = storage.put(document())
    for operation in (storage.read, storage.delete):
        with pytest.raises(DocumentStorageError) as caught:
            operation(key)
        assert caught.value.__context__ is None
        assert str(tmp_path) not in str(caught.value)
        assert storage.read(existing) == document().content
    assert len(list((tmp_path / "documents").iterdir())) == 1


def test_symlink_file_cannot_be_read_or_deleted(tmp_path):
    root = tmp_path / "documents"
    storage = LocalDocumentStorage(root)
    target = tmp_path / "secret"
    target.write_bytes(b"private")
    key = "a" * 64
    (root / key).symlink_to(target)
    for operation in (storage.read, storage.delete):
        with pytest.raises(DocumentStorageError) as caught:
            operation(key)
        assert caught.value.__context__ is None
        assert "secret" not in str(caught.value)
        assert target.read_bytes() == b"private"
        assert (root / key).is_symlink()


def test_symlink_root_and_ancestor_are_rejected(tmp_path):
    target = tmp_path / "target"
    target.mkdir(mode=0o700)
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)
    for root in (link, link / "child"):
        with pytest.raises(DocumentStorageError) as caught:
            LocalDocumentStorage(root)
        assert caught.value.__context__ is None
        assert not list(target.iterdir())


def test_existing_insecure_root_is_not_modified(tmp_path):
    root = tmp_path / "public"
    root.mkdir(mode=0o755)
    with pytest.raises(DocumentStorageError):
        LocalDocumentStorage(root)
    assert stat.S_IMODE(root.stat().st_mode) == 0o755


def test_collision_does_not_overwrite_existing_file(tmp_path, monkeypatch):
    storage = LocalDocumentStorage(tmp_path / "documents")
    monkeypatch.setattr("app.infrastructure.storage.local_documents.secrets.token_hex", lambda size: "b"*64)
    key = storage.put(document(b"first"))
    with pytest.raises(DocumentStorageError) as caught:
        storage.put(document(b"second"))
    assert caught.value.__context__ is None
    assert storage.read(key) == b"first"


def test_root_replacement_is_not_followed(tmp_path):
    root = tmp_path / "documents"
    storage = LocalDocumentStorage(root)
    key = storage.put(document())
    root.rename(tmp_path / "original")
    root.symlink_to(tmp_path / "original", target_is_directory=True)
    for operation in (lambda: storage.put(document()), lambda: storage.read(key), lambda: storage.delete(key)):
        with pytest.raises(DocumentStorageError):
            operation()
    assert (tmp_path / "original" / key).read_bytes() == document().content


def test_failed_publication_leaves_no_files_or_exception_context(tmp_path, monkeypatch):
    root = tmp_path / "documents"
    storage = LocalDocumentStorage(root)
    existing = storage.put(document())

    def fail(*args, **kwargs):
        raise OSError("secret filesystem path and invoice content")

    monkeypatch.setattr("app.infrastructure.storage.local_documents.os.link", fail)
    with pytest.raises(DocumentStorageError) as caught:
        storage.put(document(b"other invoice"))
    assert caught.value.__context__ is None
    assert "secret" not in str(caught.value)
    assert {p.name for p in root.iterdir()} == {existing}
    assert storage.read(existing) == document().content


@pytest.mark.parametrize("rollback_sync_fails", [False, True])
def test_directory_sync_failure_rolls_back_only_new_publication(tmp_path, monkeypatch, rollback_sync_fails):
    root = tmp_path / "documents"
    storage = LocalDocumentStorage(root)
    existing = storage.put(document())
    before = {path.name: sha256(path.read_bytes()).hexdigest() for path in root.iterdir()}
    real_fsync = os.fsync
    calls = 0

    def fail_directory_sync_once(fd):
        nonlocal calls
        calls += 1
        if calls == 2 or (rollback_sync_fails and calls == 3):
            raise OSError("secret directory sync failure")
        return real_fsync(fd)

    monkeypatch.setattr("app.infrastructure.storage.local_documents.os.fsync", fail_directory_sync_once)
    with pytest.raises(DocumentStorageError) as caught:
        storage.put(document(b"new private invoice"))
    assert calls == 3
    assert str(caught.value) == "Private document storage operation failed"
    assert caught.value.__context__ is None
    assert caught.value.__cause__ is None
    assert {path.name: sha256(path.read_bytes()).hexdigest() for path in root.iterdir()} == before
    assert storage.read(existing) == document().content


def test_hardlinks_are_rejected_without_affecting_original(tmp_path):
    root = tmp_path / "documents"
    storage = LocalDocumentStorage(root)
    key = storage.put(document())
    alias = root / ("d" * 64)
    alias.hardlink_to(root / key)
    for operation in (storage.read, storage.delete):
        with pytest.raises(DocumentStorageError):
            operation(key)
    assert alias.read_bytes() == document().content
    assert (root / key).read_bytes() == document().content


def test_nonregular_entries_and_unsafe_file_modes_are_rejected(tmp_path):
    root = tmp_path / "documents"
    storage = LocalDocumentStorage(root)
    key = "c" * 64
    (root / key).mkdir()
    for operation in (storage.read, storage.delete):
        with pytest.raises(DocumentStorageError):
            operation(key)
    assert (root / key).is_dir()
    other = storage.put(document())
    (root / other).chmod(0o644)
    with pytest.raises(DocumentStorageError):
        storage.read(other)
