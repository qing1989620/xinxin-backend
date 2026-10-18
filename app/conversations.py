"""Explicit conversation erasure; shared library resources remain independent."""

import os
from pathlib import Path
import re
import shutil
import sqlite3

from .store import now, uid


def private_directory(store, name):
    root = store.directory.resolve()
    path = store.directory / name
    if path.is_symlink() or path.is_junction() or not path.resolve().is_relative_to(root):
        raise ValueError("私有数据子目录路径异常，未删除")
    return path.resolve()


def workspace_path(store, ident):
    if not re.fullmatch(r"[a-f0-9]{32}", ident):
        raise ValueError("对话标识无效")
    root = private_directory(store, "workspaces")
    target = root / ident
    if target.is_symlink() or target.is_junction() or not target.resolve().is_relative_to(root):
        raise ValueError("对话工作区路径异常，未删除")
    return root, target


def directory_size(target):
    if not target.exists():
        return 0
    total = 0
    for folder, directories, files in os.walk(target, followlinks=False):
        directories[:] = [name for name in directories if not (Path(folder) / name).is_symlink() and not (Path(folder) / name).is_junction()]
        for name in files:
            item = Path(folder) / name
            if not item.is_symlink():
                total += item.stat().st_size
    return total


def deletion_preview(store, ident):
    _, workspace = workspace_path(store, ident)
    counts = {}
    for key, sql in {
        "messages": "SELECT COUNT(*) n FROM messages WHERE conversation_id=?",
        "runs": "SELECT COUNT(*) n FROM runs WHERE conversation_id=?",
        "events": "SELECT COUNT(*) n FROM events WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=?)",
    }.items():
        counts[key] = store.one(sql, (ident,))["n"]
    counts["workspace_bytes"] = directory_size(workspace)
    files = store.all(
        "SELECT id,size FROM files WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=?)", (ident,),
    )
    counts["generated_files"] = len(files)
    counts["generated_bytes"] = sum(row["size"] for row in files)
    return counts


def shared_file(db, ident, conversation):
    pattern = "%" + ident + "%"
    return any((
        db.execute("SELECT 1 FROM records WHERE payload LIKE ? LIMIT 1", (pattern,)).fetchone(),
        db.execute("SELECT 1 FROM messages WHERE conversation_id<>? AND content LIKE ? LIMIT 1", (conversation, pattern)).fetchone(),
        db.execute("SELECT 1 FROM run_context WHERE conversation_id<>? AND messages LIKE ? LIMIT 1", (conversation, pattern)).fetchone(),
        db.execute("SELECT 1 FROM events e JOIN runs r ON r.id=e.run_id WHERE r.conversation_id<>? AND e.data LIKE ? LIMIT 1", (conversation, pattern)).fetchone(),
    ))


def purge_conversation(store, library, ident):
    root, workspace = workspace_path(store, ident)
    preview = deletion_preview(store, ident)
    trash_root = private_directory(store, ".deletion-trash")
    files_root = private_directory(store, "files")
    if library.root.resolve() != files_root:
        raise ValueError("文件库路径异常，未删除")
    trash_root.mkdir(mode=0o700, exist_ok=True)
    trash = trash_root / (ident + "-" + uid())
    trash.mkdir(mode=0o700)
    moved = []
    removed_bytes = preview["workspace_bytes"]
    retained = 0
    committed = False
    try:
        with store.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM runs WHERE status IN ('queued','running','cancelling') LIMIT 1").fetchone():
                raise ValueError("请先停止运行任务，再永久删除对话")
            if workspace.exists():
                destination = trash / "workspace"
                workspace.rename(destination)
                moved.append((destination, workspace))
            files = db.execute(
                "SELECT id,size FROM files WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=?)", (ident,),
            ).fetchall()
            for file in files:
                if shared_file(db, file["id"], ident):
                    retained += 1
                    continue
                raw_path = Path(db.execute("SELECT path FROM files WHERE id=?", (file["id"],)).fetchone()["path"])
                if raw_path.is_symlink() or raw_path.is_junction():
                    raise ValueError("生成文件路径异常，未删除")
                source = library.path(file["id"])
                if not source.is_relative_to(files_root):
                    raise ValueError("生成文件路径越界，未删除")
                destination = trash / (file["id"] + source.suffix)
                source.rename(destination)
                moved.append((destination, source))
                removed_bytes += file["size"]
                db.execute("DELETE FROM uploads WHERE file_id=?", (file["id"],))
                db.execute("DELETE FROM files WHERE id=?", (file["id"],))
            for table in ("events", "message_runs"):
                db.execute(f"DELETE FROM {table} WHERE run_id IN (SELECT id FROM runs WHERE conversation_id=?)", (ident,))
            for table in ("run_context", "run_plan", "messages", "runs"):
                db.execute(f"DELETE FROM {table} WHERE conversation_id=?", (ident,))
            db.execute("DELETE FROM conversations WHERE id=?", (ident,))
            db.execute("DELETE FROM conversation_options WHERE conversation_id=?", (ident,))
        committed = True
    except BaseException:
        for source, destination in reversed(moved):
            if source.exists():
                source.rename(destination)
        raise
    finally:
        cleanup_pending = False
        if (committed or not any(source.exists() for source, _ in moved)) and trash.exists() and not trash.is_symlink() and not trash.is_junction() and trash.resolve().parent == private_directory(store, ".deletion-trash"):
            try:
                shutil.rmtree(trash)
            except OSError:
                cleanup_pending = True
    if not committed:
        raise RuntimeError("对话删除未提交")
    compacted = False
    try:
        with store.connect() as db:
            db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            db.execute("VACUUM")
        compacted = True
    except sqlite3.Error:
        pass
    store.audit("conversation.deleted", {"conversation_id": ident, "removed_file_bytes": removed_bytes})
    return {"deleted": True, "removed_file_bytes": 0 if cleanup_pending else removed_bytes, "shared_generated_files_retained": retained, "database_compacted": compacted, "cleanup_pending": cleanup_pending}
