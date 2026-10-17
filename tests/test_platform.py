import asyncio
import json
import sys
import threading
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Config
from app.execution import Policy, execute_process
from app.main import create_app
from app.model import ModelGateway


@pytest.fixture
def client(tmp_path):
    application = create_app(Config(tmp_path, "local-bootstrap"))
    with TestClient(application) as client:
        response = client.post(
            "/api/v1/auth/setup",
            json={
                "name": "主人",
                "password": "private-test-password",
                "setup_code": "local-bootstrap",
            },
        )
        client.headers["Authorization"] = "Bearer " + response.json()["token"]
        yield client


def test_initialization_is_private_and_single_owner(tmp_path):
    with TestClient(create_app(Config(tmp_path, "private-code"))) as client:
        assert client.get("/api/v1/auth/status").json() == {
            "configured": False,
            "registration": "disabled",
        }
        assert client.get("/api/v1/files").status_code == 401
        assert (
            client.post(
                "/api/v1/auth/setup",
                json={
                    "name": "me",
                    "password": "long-password-123",
                    "setup_code": "wrong",
                },
            ).status_code
            == 403
        )
        good = {
            "name": "me",
            "password": "long-password-123",
            "setup_code": "private-code",
        }
        assert client.post("/api/v1/auth/setup", json=good).status_code == 200
        assert client.post("/api/v1/auth/setup", json=good).status_code == 409
        assert client.post("/api/v1/auth/register", json=good).status_code == 404


def test_unicode_owner_login_and_password_revocation(client):
    first = client.headers["Authorization"]
    response = client.post(
        "/api/v1/auth/login", json={"name": "主人", "password": "private-test-password"}
    )
    assert response.status_code == 200
    response = client.put(
        "/api/v1/auth/password",
        json={"current": "private-test-password", "password": "new-private-password"},
    )
    assert response.status_code == 200
    assert (
        client.get("/api/v1/auth/me", headers={"Authorization": first}).status_code
        == 401
    )
    assert (
        client.get(
            "/api/v1/auth/me",
            headers={"Authorization": "Bearer " + response.json()["token"]},
        ).status_code
        == 200
    )


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/dashboard",
        "/api/v1/skills",
        "/api/v1/records?kind=article",
        "/api/v1/servers",
        "/api/v1/memories",
        "/api/v1/settings",
        "/api/v1/runs",
        "/api/v1/schedules",
        "/api/v1/audit",
    ],
)
def test_private_routes_require_auth(client, path):
    assert (
        client.get(path, headers={"Authorization": "Bearer invalid"}).status_code == 401
    )


@pytest.mark.parametrize(
    "kind,payload",
    [
        ("article", {"title": "私人文章", "content": "内容", "published": False}),
        ("collection", {"title": "我的项目", "description": "合集"}),
        ("photo", {"title": "日常", "category": "生活"}),
        ("poem", {"content": "山有木兮木有枝", "author": "佚名"}),
        (
            "resume",
            {
                "name": "主人",
                "experiences": [{"title": "项目", "image_file_id": "legacy-reference"}],
            },
        ),
    ],
)
def test_content_crud_preserves_payload(client, kind, payload):
    created = client.post("/api/v1/records", json={"kind": kind, "payload": payload})
    assert created.status_code == 200
    ident = created.json()["id"]
    assert (
        client.get("/api/v1/records", params={"kind": kind}).json()[0]["payload"]
        == payload
    )
    updated = {**payload, "note": "更新"}
    assert (
        client.put(
            "/api/v1/records/" + ident, json={"kind": kind, "payload": updated}
        ).json()["payload"]
        == updated
    )
    assert client.delete("/api/v1/records/" + ident).status_code == 200
    assert client.get("/api/v1/records", params={"kind": kind}).json() == []


def test_file_search_download_and_active_content(client):
    response = client.post(
        "/api/v1/files",
        files={"file": ("资料.md", "私人苹果项目的部署记录".encode(), "text/markdown")},
    )
    assert response.status_code == 200
    ident = response.json()["id"]
    assert client.get("/api/v1/files/search?q=苹果").json()[0]["file_id"] == ident
    assert (
        client.get(f"/api/v1/files/{ident}/download").content
        == "私人苹果项目的部署记录".encode()
    )
    assert (
        client.get(
            f"/api/v1/files/{ident}/download", headers={"Authorization": "Bearer bad"}
        ).status_code
        == 401
    )
    script = client.post(
        "/api/v1/files",
        files={"file": ("untrusted.html", b"<script>alert(1)</script>", "text/html")},
    ).json()
    download = client.get(f"/api/v1/files/{script['id']}/download?inline=true")
    assert "attachment" in download.headers["content-disposition"]
    assert download.headers["content-type"] == "application/octet-stream"
    assert "sandbox" in download.headers["content-security-policy"]
    assert client.delete(f"/api/v1/files/{ident}").status_code == 200
    assert client.get(f"/api/v1/files/{ident}/download").status_code == 400


def test_secrets_never_return_to_frontend(client):
    response = client.put(
        "/api/v1/settings",
        json={"model_api_key": "test-secret-do-not-return", "model": "deepseek-flash"},
    )
    assert response.status_code == 200
    assert "test-secret" not in response.text
    assert response.json()["model_configured"] is True
    stored = client.app.state.store.setting("secret:model_api_key")
    assert "test-secret" not in stored
    server = client.post(
        "/api/v1/servers",
        json={
            "name": "测试机",
            "host": "example.org",
            "private_key": "SECRET-PRIVATE-KEY",
        },
    ).json()
    assert server["key_configured"] and "SECRET-PRIVATE-KEY" not in json.dumps(server)
    assert "SECRET-PRIVATE-KEY" not in client.get("/api/v1/servers").text


@pytest.mark.parametrize(
    "url",
    [
        "http://remote.example/v1",
        "https://user:password@example.com/api",
        "file:///tmp/key",
        "https://example.com/?key=secret",
    ],
)
def test_model_urls_reject_credential_leak_routes(client, url):
    assert client.put("/api/v1/settings", json={"model_url": url}).status_code == 400


def test_task_permission_rejects_write_and_traversal(client, tmp_path):
    manager = client.app.state.manager
    run = {
        "id": "local-test",
        "conversation_id": "workspace",
        "permission": "read",
        "server_id": None,
    }
    tools = manager._tools(run, threading.Event())
    with pytest.raises(PermissionError):
        tools.get_tool("write_file")("notes.txt", "content")
    with pytest.raises(PermissionError):
        tools.get_tool("execute_command")("echo no")
    with pytest.raises(PermissionError):
        Policy("workspace", tmp_path).path("../outside.txt")
    run["permission"] = "workspace"
    tools = manager._tools(run, threading.Event())
    assert (
        tools.get_tool("write_file")("nested/notes.txt", "内容")["written"]
        == "nested/notes.txt"
    )
    with pytest.raises(PermissionError):
        tools.get_tool("execute_command")("echo no")
    with pytest.raises(PermissionError):
        tools.get_tool("run_remote_command")("echo no")


def frames(content=None, tool=None):
    chunks = []
    if content:
        chunks.append(
            {"choices": [{"delta": {"content": content}, "finish_reason": None}]}
        )
    if tool:
        args = json.dumps(tool["arguments"], ensure_ascii=False)
        chunks.extend(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call-1",
                                        "type": "function",
                                        "function": {
                                            "name": tool["name"],
                                            "arguments": args[:5],
                                        },
                                    }
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {"index": 0, "function": {"arguments": args[5:]}}
                                ]
                            },
                            "finish_reason": None,
                        }
                    ]
                },
            ]
        )
    chunks.append(
        {
            "choices": [
                {"delta": {}, "finish_reason": "tool_calls" if tool else "stop"}
            ],
            "usage": {"total_tokens": 12, "prompt_tokens": 8, "completion_tokens": 4},
        }
    )
    return (
        "\n\n".join(
            "data: " + json.dumps(chunk, ensure_ascii=False) for chunk in chunks
        )
        + "\n\ndata: [DONE]\n\n"
    )


def wait_run(client, ident):
    for _ in range(1500):
        run = client.get("/api/v1/runs/" + ident).json()
        if run["status"] not in {"queued", "running", "cancelling"}:
            return run
        time.sleep(0.02)
    pytest.fail("local mock run did not complete")


def test_tina_tool_loop_persists_events_artifacts_and_usage(client):
    client.put("/api/v1/settings", json={"model_api_key": "offline-test-key"})
    requests = []

    def provider(request):
        body = json.loads(request.content)
        requests.append(body)
        assert (
            body["model"] == "deepseek-flash" and body["thinking"]["type"] == "disabled"
        )
        payload = (
            frames(
                tool={
                    "name": "write_file",
                    "arguments": {"path": "hello.txt", "content": "hello"},
                }
            )
            if len(requests) == 1
            else frames(content="已创建文件，并记录了交付物。")
        )
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"}, content=payload.encode()
        )

    client.app.state.manager.model_transport = httpx.MockTransport(provider)
    body = {
        "task": "创建一个文本文件",
        "permission": "workspace",
        "request_key": "stable-request-1",
    }
    response = client.post("/api/v1/runs", json=body)
    assert response.status_code == 200
    ident = response.json()["id"]
    run = wait_run(client, ident)
    assert run["status"] == "completed", run
    assert run["files"][0]["name"] == "hello.txt"
    assert json.loads(run["usage"])["total_tokens"] == 24
    assert len(requests) == 2
    assert any(message["role"] == "tool" for message in requests[1]["messages"])
    replay = client.get(f"/api/v1/runs/{ident}/events?after=1")
    assert "tool.completed" in replay.text and "event: end" in replay.text
    assert "id: 1\n" not in replay.text
    assert client.post("/api/v1/runs", json=body).json()["id"] == ident
    assert (
        client.post(
            "/api/v1/skills/coding-workspace/feedback",
            json={"run_id": ident, "reward": 1},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/v1/skills/coding-workspace/feedback",
            json={"run_id": ident, "reward": 1},
        ).status_code
        == 400
    )
    from app.skills import SkillRouter

    restored = SkillRouter(client.app.state.store)
    assert restored.bandit.n_updates == 1


def test_missing_model_key_is_actionable_not_fake_success(client):
    response = client.post(
        "/api/v1/runs", json={"task": "部署网站", "request_key": "no-key-request"}
    )
    assert response.status_code == 400 and "密钥" in response.json()["detail"]
    assert client.get("/api/v1/runs").json() == []


def test_managed_process_cancel_prevents_late_side_effect(tmp_path):
    flag = threading.Event()
    target = tmp_path / "late.txt"
    command = [
        sys.executable,
        "-c",
        "import time;from pathlib import Path;time.sleep(3);Path('late.txt').write_text('bad')",
    ]
    result = []
    worker = threading.Thread(
        target=lambda: result.append(execute_process(command, tmp_path, flag, 10))
    )
    worker.start()
    time.sleep(0.15)
    flag.set()
    worker.join(timeout=8)
    assert not worker.is_alive()
    assert result[0]["stopped"] == "cancelled" and not result[0]["ok"]
    assert not target.exists()


def test_restart_preserves_history_and_marks_inflight_interrupted(client):
    store = client.app.state.store
    store.execute("INSERT INTO conversations VALUES('c','test',1,1)")
    store.execute(
        "INSERT INTO runs(id,conversation_id,task,status,permission,max_calls,max_seconds,created,updated) VALUES('r','c','task','running','read',2,30,1,1)"
    )
    from app.store import Store

    restarted = Store(store.directory)
    assert (
        restarted.one("SELECT status FROM runs WHERE id='r'")["status"] == "interrupted"
    )
    assert (
        restarted.one("SELECT title FROM conversations WHERE id='c'")["title"] == "test"
    )


def test_model_stream_disconnect_cannot_be_success():
    async def exercise():
        transport = httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                content=b'data: {"choices":[{"delta":{"content":"partial"},"finish_reason":null}]}\n\n',
            )
        )
        gateway = ModelGateway(
            "https://example.com/chat",
            "test",
            "test",
            lambda value: None,
            threading.Event(),
            transport=transport,
        )
        stream = await gateway.apredict(
            messages=[{"role": "user", "content": "hello"}], stream=True
        )
        with pytest.raises(RuntimeError):
            async for _ in stream:
                pass

    asyncio.run(exercise())


def test_plot_job_produces_three_real_artifacts(client):
    file = client.post(
        "/api/v1/files",
        files={"file": ("series.csv", b"x,y\n1,2\n2,5\n3,4", "text/csv")},
    ).json()
    assert client.get(f"/api/v1/files/{file['id']}/columns").json()["columns"] == [
        "x",
        "y",
    ]
    response = client.post(
        "/api/v1/tools/plot",
        json={
            "file_id": file["id"],
            "x": "x",
            "y": "y",
            "title": "Local verification",
            "request_key": "plot-verification",
        },
    )
    run = wait_run(client, response.json()["id"])
    assert run["status"] == "completed", run
    names = {file["name"] for file in run["files"]}
    assert names == {"xinxin-figure.png", "xinxin-figure.pdf", "xinxin-figure.py"}
    png = next(file for file in run["files"] if file["name"].endswith(".png"))
    assert client.get(f"/api/v1/files/{png['id']}/download").content.startswith(
        b"\x89PNG"
    )


def test_unresolved_tool_denial_cannot_be_completed(client):
    client.put("/api/v1/settings", json={"model_api_key": "offline-test"})
    calls = []

    def provider(request):
        calls.append(request)
        data = (
            frames(
                tool={
                    "name": "write_file",
                    "arguments": {"path": "denied.txt", "content": "no"},
                }
            )
            if len(calls) == 1
            else frames(content="已完成写入。")
        )
        return httpx.Response(200, content=data.encode())

    client.app.state.manager.model_transport = httpx.MockTransport(provider)
    result = client.post(
        "/api/v1/runs",
        json={"task": "写文件", "permission": "read", "request_key": "denial-request"},
    ).json()
    run = wait_run(client, result["id"])
    assert run["status"] == "failed" and "工具" in run["error"]
    assert run["files"] == []


def test_conflicting_idempotency_key_rejects_different_operation(client):
    file = client.post(
        "/api/v1/files", files={"file": ("a.csv", b"x,y\n1,2\n2,3", "text/csv")}
    ).json()
    payload = {
        "file_id": file["id"],
        "x": "x",
        "y": "y",
        "title": "Same title",
        "request_key": "plot-stable-id",
    }
    original = client.post("/api/v1/tools/plot", json=payload).json()
    wait_run(client, original["id"])
    assert (
        client.post("/api/v1/tools/plot", json=payload).json()["id"] == original["id"]
    )
    assert (
        client.post("/api/v1/tools/plot", json={**payload, "kind": "bar"}).status_code
        == 400
    )


def test_server_edit_preserves_encrypted_key_and_schedule_scope(client):
    server = client.post(
        "/api/v1/servers",
        json={"name": "node", "host": "example.org", "private_key": "PRIVATE"},
    ).json()
    body = {"name": "renamed", "host": "new.example.org", "private_key": ""}
    assert client.put("/api/v1/servers/" + server["id"], json=body).json()[
        "key_configured"
    ]
    assert client.app.state.auth.secret("ssh:" + server["id"]) == "PRIVATE"
    task = {
        "title": "Review server",
        "task": "检查状态",
        "interval_minutes": 60,
        "permission": "full",
        "server_id": server["id"],
    }
    schedule = client.post("/api/v1/schedules", json=task).json()
    assert client.get("/api/v1/schedules").json()[0]["permission"] == "full"
    assert (
        client.put(
            "/api/v1/schedules/" + schedule["id"], json={**task, "enabled": False}
        ).status_code
        == 200
    )
    assert not client.get("/api/v1/schedules").json()[0]["enabled"]


def test_password_whitespace_is_not_silently_changed(tmp_path):
    with TestClient(create_app(Config(tmp_path, "code"))) as client:
        body = {"name": "owner", "password": " long password ", "setup_code": "code"}
        assert client.post("/api/v1/auth/setup", json=body).status_code == 200
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"name": "owner", "password": "long password"},
            ).status_code
            == 401
        )
        assert (
            client.post(
                "/api/v1/auth/login",
                json={"name": "owner", "password": " long password "},
            ).status_code
            == 200
        )


def test_myself_migration_is_readonly_idempotent_and_skips_credentials(tmp_path):
    import hashlib
    import sqlite3
    from scripts.migrate_myself import migrate
    from app.store import Store

    source = tmp_path / "source"
    source.mkdir()
    (source / "public").mkdir()
    original = source / "dev.db"
    with sqlite3.connect(original) as db:
        db.executescript(
            "CREATE TABLE Article(id INTEGER,title TEXT,content TEXT,collectionId INTEGER,coverImage TEXT); CREATE TABLE User(id INTEGER,password TEXT); INSERT INTO User VALUES(1,'must-not-import');"
        )
        db.execute(
            "INSERT INTO Article VALUES(1,?,?,NULL,NULL)", ("Imported", "plain content")
        )
    before = hashlib.sha256(original.read_bytes()).hexdigest()
    store = Store(tmp_path / "destination")
    assert migrate(source, store)["source_counts"]["Article"] == 1
    assert migrate(source, store, apply=True)["records_added"] == 1
    assert migrate(source, store, apply=True)["records_added"] == 0
    assert store.all("SELECT * FROM owner") == []
    assert "must-not-import" not in encode_for_test(store.all("SELECT * FROM records"))
    assert before == hashlib.sha256(original.read_bytes()).hexdigest()


def encode_for_test(value):
    return json.dumps(value, ensure_ascii=False)
