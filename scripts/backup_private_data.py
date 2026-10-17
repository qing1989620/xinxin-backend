"""Back up a consistent SQLite snapshot plus immutable resources privately."""

from __future__ import annotations

import argparse
from contextlib import closing
from datetime import datetime, timezone
import os
from pathlib import Path
import sqlite3
import tarfile
import tempfile


def backup(source: Path, output: Path, keep: int = 7) -> Path:
    source, output = source.resolve(strict=True), output.resolve()
    if output.is_relative_to(source) or keep < 1:
        raise ValueError("Backup output must be outside the data directory; keep >= 1")
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    output.chmod(0o700)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    target = output / f"xinxin-{stamp}.tar.gz"
    incomplete = target.with_suffix(target.suffix + ".partial")
    # The temporary directory and all rotation candidates remain confined to
    # the explicitly supplied backup directory, on Windows as well as Linux.
    with tempfile.TemporaryDirectory(prefix=".snapshot-", dir=output) as temporary:
        temporary_path = Path(temporary).resolve()
        if not temporary_path.is_relative_to(output):
            raise ValueError("Temporary backup directory escaped the output")
        snapshot = temporary_path / "xinxin.sqlite3"
        with closing(sqlite3.connect((source / "xinxin.sqlite3").as_uri() + "?mode=ro", uri=True)) as db:
            with closing(sqlite3.connect(snapshot)) as destination:
                db.backup(destination)
                if destination.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise ValueError("Database backup integrity check failed")
        descriptor = os.open(incomplete, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            with os.fdopen(descriptor, "wb") as raw, tarfile.open(fileobj=raw, mode="w:gz", dereference=False) as archive:
                archive.add(snapshot, arcname="data/xinxin.sqlite3")
                for name in ("vault.key", "bootstrap.code", "files", "skills", "workspaces"):
                    path = source / name
                    if path.exists():
                        archive.add(path, arcname="data/" + name)
            incomplete.rename(target)
        finally:
            incomplete.unlink(missing_ok=True)
    candidates = sorted(output.glob("xinxin-*.tar.gz"), reverse=True)
    for previous in candidates[keep:]:
        if previous.resolve().parent != output or previous.is_symlink():
            raise ValueError("Backup rotation candidate escaped the output")
        previous.unlink()
    return target


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--keep", type=int, default=7)
    args = parser.parse_args()
    result = backup(args.source, args.output, args.keep)
    print("Private backup completed:", result.name, result.stat().st_size, "bytes")
