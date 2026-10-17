from contextlib import closing
import sqlite3
import tarfile

from app.store import Store
from scripts.backup_private_data import backup


def test_backup_contains_database_vault_and_resources_and_rotates_only_own_archives(tmp_path):
    source, output = tmp_path / "data", tmp_path / "backups"
    store = Store(source)
    store.execute("INSERT INTO owner VALUES(1,'fixture','hashed-password')")
    (source / "vault.key").write_bytes(b"private-fixture-key")
    files = source / "files"
    files.mkdir()
    (files / "fixture.txt").write_bytes(b"immutable resource")
    output.mkdir()
    unrelated = output / "owner-notes.txt"
    unrelated.write_text("keep")
    first = backup(source, output, keep=1)
    second = backup(source, output, keep=1)
    assert not first.exists() and second.exists() and unrelated.exists()
    with tarfile.open(second) as archive:
        assert archive.extractfile("data/vault.key").read() == b"private-fixture-key"
        assert archive.extractfile("data/files/fixture.txt").read() == b"immutable resource"
        restored = tmp_path / "restored.sqlite3"
        restored.write_bytes(archive.extractfile("data/xinxin.sqlite3").read())
    with closing(sqlite3.connect(restored)) as db:
        assert db.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert db.execute("SELECT name FROM owner").fetchone()[0] == "fixture"
    assert not list(output.glob("*.partial"))
