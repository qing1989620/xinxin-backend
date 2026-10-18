import asyncio
import hashlib
import inspect
import shutil
import json
import os
import re
import shlex
import sys
import threading
from pathlib import Path

from .config import ROOT
from .execution import Policy, run_command, remote_command
from .model import ModelGateway
from .persona import build_system_prompt
from .store import encode, now, uid
from .creative import plot_csv, transform_image, gallery

sys.path.insert(0, str(ROOT / "vendor"))
from tina import Agent, Tools, AgentEvents
from tina.agent.core.executor import ToolsExecutor


class PlatformExecutor(ToolsExecutor):
    def __init__(self, sanitize=str):
        super().__init__(max_workers=1)
        self.unresolved = {}
        self.inflight = set()
        self.sanitize = sanitize

    def _raw_execute(self, tool, arguments):
        # Tina's global stdout redirection is unsuitable for concurrent HTTP jobs.
        result = tool(**arguments)
        return encode(result) if isinstance(result, (dict, list)) else str(result)

    async def aexecute(
        self, calls, tools, _mcp_client=None, timeout=60, events=None, **kwargs
    ):
        # Validate before Tina's generic logger, which may include exception input.
        for call in calls or []:
            name = call["function"]["name"]
            try:
                if (
                    not isinstance(json.loads(call["function"]["arguments"]), dict)
                    or tools.get_tool(name=name) is None
                ):
                    raise ValueError("工具或参数无效")
            except (ValueError, TypeError):
                self.unresolved[name] = "工具或参数无效"
                return [
                    self._tool_call_result(
                        encode({"ok": False, "error": "工具或参数无效，本批未执行"}),
                        item["id"],
                        item["function"]["name"],
                    )
                    for item in calls
                ]
        return await super().aexecute(
            calls, tools, _mcp_client, timeout, events, **kwargs
        )

    async def _async_dispatch(self, name, arguments, tool, timeout):
        if tool is None:
            self.unresolved[name] = "未知工具"
            return "未知工具：" + name
        try:
            if inspect.iscoroutinefunction(tool):
                result = await tool(**arguments)
            else:
                # Task deadline and the managed process own cancellation.
                pending = asyncio.create_task(
                    asyncio.to_thread(self._raw_execute, tool, arguments)
                )
                self.inflight.add(pending)
                pending.add_done_callback(self.inflight.discard)
                result = await asyncio.shield(pending)
            structured = result
            if isinstance(result, str):
                try:
                    structured = json.loads(result)
                except (ValueError, TypeError):
                    pass
            if isinstance(structured, dict) and structured.get("ok") is False:
                self.unresolved[name] = "工具执行失败，退出码 " + str(
                    structured.get("exit_code", "unknown")
                )
            else:
                self.unresolved.pop(name, None)
            return self.sanitize(
                encode(result) if isinstance(result, (dict, list)) else str(result)
            )
        except Exception as exc:
            self.unresolved[name] = self.sanitize(str(exc))
            return encode({"ok": False, "error": self.unresolved[name]})

    async def drain(self):
        if self.inflight:
            await asyncio.gather(*list(self.inflight), return_exceptions=True)


def safe_summary(value):
    text = str(value)[:12000]
    text = re.sub(
        r"(?i)(api[_-]?key|password|secret|authorization|token)([\s:=]+)[^\s,;]+",
        r"\1\2[redacted]",
        text,
    )
    text = re.sub(r"sk-[A-Za-z0-9_-]{12,}", "[redacted]", text)
    return text


class TaskManager:
    def __init__(self, store, auth, config, library, router):
        self.store, self.auth, self.config = store, auth, config
        self.library, self.router = library, router
        self.tasks, self.cancellations = {}, {}
        self.semaphore = asyncio.Semaphore(config.task_concurrency)
        self.model_transport = None

    def model_config(self):
        return (
            self.store.setting("model_url", self.config.model_url),
            self.store.setting("model", self.config.model),
            self.auth.secret("model_api_key", self.config.api_key),
        )

    def redact(self, value):
        text = str(value)
        keys = [self.config.api_key]
        vault_key = self.store.directory / "vault.key"
        if vault_key.is_file():
            keys.append(vault_key.read_text(encoding="ascii").strip())
        for row in self.store.all("SELECT key FROM settings WHERE key LIKE 'secret:%'"):
            keys.append(self.auth.secret(row["key"][7:]))
        forms = {
            form
            for key in keys
            if key
            for form in (
                key,
                json.dumps(key, ensure_ascii=False)[1:-1],
                json.dumps(key, ensure_ascii=True)[1:-1],
            )
        }

        def redact_text(text):
            for form in forms:
                text = text.replace(form, "[redacted]")
            text = re.sub(
                r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
                "[redacted private key]",
                text,
                flags=re.S,
            )
            return safe_summary(text)

        def redact_data(data):
            if isinstance(data, str):
                return redact_text(data)
            if isinstance(data, list):
                return [redact_data(item) for item in data]
            if isinstance(data, dict):
                secret_fields = {
                    "apikey",
                    "password",
                    "secret",
                    "authorization",
                    "token",
                    "privatekey",
                    "modelapikey",
                    "smtppassword",
                }
                cleaned = {}
                for key, item in data.items():
                    clean_key = redact_text(key)
                    suffix = 1
                    while clean_key in cleaned:
                        clean_key = f"{redact_text(key)} [{suffix}]"
                        suffix += 1
                    cleaned[clean_key] = (
                        "[redacted]"
                        if key.lower().replace("_", "").replace("-", "")
                        in secret_fields
                        else redact_data(item)
                    )
                return cleaned
            return data

        try:
            structured = json.loads(text)
        except (ValueError, TypeError):
            structured = None
        if isinstance(structured, (dict, list)):
            # Redact values before serialization so escaping/cursors stay valid.
            result = encode(redact_data(structured))
            return (
                result
                if len(result) <= 16000
                else encode({"truncated": True, "preview": result[:6000]})
            )
        return redact_text(text)

    def conversation(self, title="新对话"):
        ident = uid()
        self.store.execute(
            "INSERT INTO conversations(id,title,created,updated) VALUES(?,?,?,?)",
            (ident, title[:80], now(), now()),
        )
        return ident

    async def submit(
        self,
        task,
        conversation_id=None,
        permission="read",
        server_id=None,
        max_calls=8,
        max_seconds=300,
        request_key=None,
        operation=None,
        operation_input=None,
        allow_memory=False,
    ):
        signature = hashlib.sha256(
            encode(
                [
                    task,
                    conversation_id,
                    permission,
                    server_id,
                    max_calls,
                    max_seconds,
                    operation_input,
                    bool(allow_memory),
                ]
            ).encode()
        ).hexdigest()
        if request_key:
            existing = self.store.one(
                "SELECT * FROM runs WHERE request_key=?", (request_key,)
            )
            if existing:
                if (
                    existing.get("request_signature")
                    and existing["request_signature"] != signature
                ):
                    raise ValueError("相同请求标识不能用于不同任务")
                return existing
        if not operation and not self.model_config()[2]:
            raise ValueError("请先在设置中配置 DeepSeek API 密钥")
        if permission not in {"read", "workspace", "full"}:
            raise ValueError("未知权限等级")
        if allow_memory and permission == "read":
            raise ValueError("允许保存记忆还需要工作区权限")
        if server_id and not self.store.one(
            "SELECT id FROM records WHERE id=? AND kind='server'", (server_id,)
        ):
            raise ValueError("目标服务器不存在")
        cid = conversation_id or self.conversation(task[:40])
        if not self.store.one("SELECT id FROM conversations WHERE id=?", (cid,)):
            raise ValueError("对话不存在")
        if self.store.one("SELECT archived FROM conversations WHERE id=?", (cid,))[
            "archived"
        ]:
            raise ValueError("此对话已归档，请先恢复或开启新对话")
        active = self.store.one(
            "SELECT id FROM runs WHERE conversation_id=? AND status IN ('queued','running','cancelling')",
            (cid,),
        )
        if active:
            raise ValueError("此对话已有运行中的任务，请等待或取消")
        ident = uid()
        self.store.execute(
            "INSERT INTO runs(id,conversation_id,task,status,permission,server_id,max_calls,max_seconds,created,updated,request_key,request_signature,allow_memory) VALUES(?,?,?,'queued',?,?,?,?,?,?,?,?,?)",
            (
                ident,
                cid,
                task,
                permission,
                server_id,
                max_calls,
                max_seconds,
                now(),
                now(),
                request_key,
                signature,
                int(allow_memory),
            ),
        )
        self.store.message(cid, "user", task)
        self.store.event(
            ident, "queued", {"permission": permission, "server_id": server_id}
        )
        flag = threading.Event()
        self.cancellations[ident] = flag
        self.tasks[ident] = asyncio.create_task(self._run(ident, flag, operation))

        def completed(_):
            self.tasks.pop(ident, None)
            self.cancellations.pop(ident, None)

        self.tasks[ident].add_done_callback(completed)
        return self.store.one("SELECT * FROM runs WHERE id=?", (ident,))

    def cancel(self, ident):
        run = self.store.one("SELECT * FROM runs WHERE id=?", (ident,))
        if not run:
            raise ValueError("任务不存在")
        if run["status"] not in {"queued", "running", "cancelling"}:
            return run
        if ident in self.cancellations:
            self.cancellations[ident].set()
        self.store.execute(
            "UPDATE runs SET status=?,updated=? WHERE id=?",
            ("cancelled" if run["status"] == "queued" else "cancelling", now(), ident),
        )
        self.store.event(ident, "cancelling", {})
        # Wake suspended model I/O; process tools observe the cancellation flag.
        if ident in self.tasks:
            self.tasks[ident].cancel()
        if run["status"] == "queued":
            self.store.event(
                ident, "finished", {"status": "cancelled", "error": "排队任务已取消"}
            )
        return self.store.one("SELECT * FROM runs WHERE id=?", (ident,))

    def _tools(self, run, flag):
        policy = Policy(
            run["permission"],
            self.store.directory / "workspaces" / run["conversation_id"],
            flag,
        )
        tools = Tools(tools_executor=PlatformExecutor(self.redact))

        @tools.register()
        def search_skills(query: str):
            """Search available skill summaries for a task."""
            return self.router.search(query)

        @tools.register()
        def load_skill(name: str, offset: int = 0):
            """Load a skill instruction page after discovery; follow next_offset until null."""
            return self.router.load(name, offset)

        @tools.register()
        def read_skill_resource(name: str, path: str, offset: int = 0):
            """Read a listed skill reference/script as data, never execute it. Follow next_offset."""
            return self.router.packages.read(name, path, offset)

        @tools.register()
        def search_documents(query: str):
            """Search the owner's private documents; return excerpts and file references."""
            return self.library.search(query)

        @tools.register()
        def read_document(file_id: str, offset: int = 0, limit: int = 12000):
            """Read one page of private document text. Follow next_offset until null to read the full document."""
            return self.library.read(file_id, offset, min(limit, 6000))

        @tools.register()
        def list_files(path: str = "."):
            """List files in the current task workspace only."""
            policy.require("read")
            directory = policy.path(path)
            return [
                {"name": p.name, "directory": p.is_dir()}
                for p in list(directory.iterdir())[:150]
            ]

        @tools.register()
        def read_file(path: str):
            """Read a UTF-8 file inside the current task workspace."""
            policy.require("read")
            target = policy.path(path)
            if target.stat().st_size > 200000:
                raise ValueError("文件过大，请分块处理")
            return target.read_text(encoding="utf-8", errors="replace")

        @tools.register()
        def write_file(path: str, content: str):
            """Write a UTF-8 file inside the task workspace; requires workspace permission."""
            policy.require("workspace")
            if len(content.encode()) > 500000:
                raise ValueError("写入内容过大")
            target = policy.path(path)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
            saved = self.library.save(target.name, content.encode(), run_id=run["id"])
            return {"written": path, "artifact": saved}

        @tools.register()
        def copy_document_to_workspace(file_id: str, path: str):
            """Copy a registered private file into this task workspace for code processing; requires workspace permission."""
            policy.require("workspace")
            target = policy.path(path)
            if target.exists():
                raise ValueError("目标文件已存在，请换一个名称")
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(self.library.path(file_id), target)
            return {"path": path, "source_file_id": file_id}

        @tools.register()
        def publish_file(path: str):
            """Save a generated workspace file as an authenticated downloadable artifact; requires workspace permission."""
            policy.require("workspace")
            target = policy.path(path)
            if not target.is_file():
                raise ValueError("文件不存在")
            with target.open("rb") as source:
                return self.library.save_stream(target.name, source, run_id=run["id"])

        @tools.register()
        def execute_command(command: str):
            """Run a shell command on this backend host. Requires full task permission."""
            return run_command(policy, command)

        @tools.register()
        def list_servers():
            """List registered servers without credentials."""
            return [
                {"id": row["id"], **json.loads(row["payload"])}
                for row in self.store.all("SELECT * FROM records WHERE kind='server'")
            ]

        @tools.register()
        def run_remote_command(command: str):
            """Execute on this task's selected SSH server. Requires full permission."""
            policy.require("full")
            if not run["server_id"]:
                raise ValueError("当前任务未选择目标服务器")
            server = json.loads(
                self.store.one(
                    "SELECT payload FROM records WHERE id=? AND kind='server'",
                    (run["server_id"],),
                )["payload"]
            )
            key = self.auth.secret("ssh:" + run["server_id"])
            if not key or not server.get("host_key"):
                raise ValueError("请先配置服务器私钥及已验证的 SSH 主机公钥")
            secrets_dir = self.store.directory / "ssh-runtime"
            secrets_dir.mkdir(exist_ok=True)
            key_path, hosts = (
                secrets_dir / (run["id"] + ".key"),
                secrets_dir / (run["id"] + ".hosts"),
            )
            key_path.write_text(key.rstrip() + "\n", encoding="utf-8")
            hosts.write_text(server["host_key"] + "\n", encoding="utf-8")
            try:
                key_path.chmod(0o600)
                return remote_command(policy, server, key_path, command, hosts)
            finally:
                key_path.unlink(missing_ok=True)
                hosts.unlink(missing_ok=True)

        @tools.register()
        def deploy_repository(repository: str, app_name: str):
            """Deploy a GitHub repository containing compose.yaml/docker-compose.yml with Docker Compose on the selected server; health checks must pass."""
            if not re.fullmatch(
                r"https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?",
                repository,
            ) or not re.fullmatch(r"[a-z][a-z0-9-]{1,40}", app_name):
                raise ValueError("仓库地址或应用名称无效")
            if not run["server_id"]:
                raise ValueError("请为当前任务选择服务器")
            server = json.loads(
                self.store.one(
                    "SELECT payload FROM records WHERE id=?", (run["server_id"],)
                )["payload"]
            )
            target = server.get("workdir", "/srv/xinxin") + "/" + app_name
            command = (
                "set -eu; target="
                + shlex.quote(target)
                + "; if [ -e \"$target\" ]; then echo '目标目录已存在，请先检查，未覆盖'; exit 2; fi; git clone --depth 1 -- "
                + shlex.quote(repository)
                + ' "$target"; cd "$target"; docker compose config --quiet; docker compose up -d --build --wait --wait-timeout 90; docker compose ps'
            )
            return run_remote_command(command)

        @tools.register()
        def plot_csv_tool(
            file_id: str, x: str, y: str, kind: str = "line", title: str = "Data study"
        ):
            """Render a private CSV to PNG/PDF and reproducible code; requires workspace permission."""
            policy.require("workspace")
            return plot_csv(
                self.library,
                file_id,
                x,
                y,
                kind,
                title,
                run_id=run["id"],
                cancelled=flag,
            )

        @tools.register()
        def list_gallery():
            """List reference chart styles with provenance retained."""
            return gallery()

        @tools.register()
        def read_plot_resource(path: str):
            """Read a bundled plotting stage, gallery style card or Python reference. Paths are relative to the plotting skill root."""
            root = (ROOT / "resources/plot").resolve()
            target = (root / path).resolve()
            if (
                not target.is_relative_to(root)
                or target.suffix not in {".md", ".py", ".json"}
                or not target.is_file()
            ):
                raise ValueError("只可读取绘图库中的说明、样式卡和参考代码")
            if target.stat().st_size > 100000:
                raise ValueError("参考内容过大")
            return target.read_text(encoding="utf-8")

        @tools.register()
        async def transform_image_tool(file_id: str, style: str, instruction: str = ""):
            """Transform a supplied photo using collage or distillation and the configured image service; requires workspace permission."""
            policy.require("workspace")
            return await transform_image(
                self.library, self.auth, file_id, style, instruction, run["id"]
            )

        @tools.register()
        def list_memories():
            """Read the owner's editable long-term memories."""
            return self.store.all(
                "SELECT * FROM memories ORDER BY created DESC LIMIT 50"
            )

        @tools.register()
        def remember(content: str, category: str = "preference"):
            """Save an explicitly requested owner preference or fact; requires workspace permission."""
            policy.require("workspace")
            if not run.get("allow_memory"):
                raise PermissionError("主人没有为本轮开启保存记忆")
            if not content.strip() or len(content) > 1000:
                raise ValueError("记忆内容为空或过长")
            self.store.execute(
                "INSERT INTO memories VALUES(?,?,?,?)",
                (uid(), content, category[:30], now()),
            )
            return "已保存，可在记忆页编辑或删除"

        @tools.register()
        def search_content(query: str):
            """Search private articles, collections, photos, poems and resume records."""
            return [
                {
                    "id": row["id"],
                    "kind": row["kind"],
                    "payload": json.loads(row["payload"]),
                }
                for row in self.store.all(
                    "SELECT * FROM records WHERE kind!='server' AND payload LIKE ? LIMIT 20",
                    ("%" + query + "%",),
                )
            ]

        @tools.register()
        def create_content_draft(kind: str, title: str, content: str):
            """Create a private article or poem draft requested by the owner; requires workspace permission."""
            policy.require("workspace")
            if (
                kind not in {"article", "poem"}
                or not title.strip()
                or not content.strip()
                or len(content) > 100000
            ):
                raise ValueError(
                    "只可创建有标题和正文的私人文章或诗词草稿，正文上限 10 万字"
                )
            ident = uid()
            self.store.execute(
                "INSERT INTO records VALUES(?,?,?,?,?)",
                (
                    ident,
                    kind,
                    encode(
                        {"title": title[:200], "content": content, "published": False}
                    ),
                    now(),
                    now(),
                ),
            )
            return {"id": ident, "kind": kind, "published": False}

        return tools

    async def _run(self, ident, flag, operation):
        text, status, error = "", "failed", ""
        tools, pending_operation = None, None
        try:
            async with self.semaphore:
                run = self.store.one("SELECT * FROM runs WHERE id=?", (ident,))
                if flag.is_set():
                    raise asyncio.CancelledError()
                self.store.execute(
                    "UPDATE runs SET status='running',updated=? WHERE id=?",
                    (now(), ident),
                )
                self.store.event(ident, "started", {})
                async with asyncio.timeout(run["max_seconds"]):
                    if operation:
                        pending_operation = asyncio.create_task(operation(ident, flag))
                        output = await asyncio.shield(pending_operation)
                        text = encode(output)
                        self.store.event(ident, "artifacts", output)
                    else:
                        url, model, key = self.model_config()

                        def usage(value):
                            self.store.execute(
                                "UPDATE runs SET usage=? WHERE id=?",
                                (encode(value), ident),
                            )
                            self.store.event(ident, "usage", value)

                        gateway = ModelGateway(
                            url,
                            model,
                            key,
                            usage,
                            flag,
                            run["max_calls"],
                            transport=self.model_transport,
                        )
                        tools, events = self._tools(run, flag), AgentEvents()

                        def before(name, arguments):
                            if flag.is_set():
                                raise RuntimeError("任务已取消")
                            self.store.event(
                                ident,
                                "tool.started",
                                {
                                    "name": name,
                                    "arguments": self.redact(encode(arguments)),
                                },
                            )
                            return name, arguments

                        def after(name, arguments, result):
                            self.store.event(
                                ident,
                                "tool.completed",
                                {
                                    "name": name,
                                    "result": self.redact(result),
                                    "ok": name not in tools.tools_executor.unresolved,
                                },
                            )
                            return name, arguments, result

                        events.add_before_tool_call_handler(before)
                        events.add_after_tool_call_handler(after)
                        matches = self.router.search(run["task"])
                        self.store.event(ident, "skills", matches)
                        memories = self.store.all(
                            "SELECT content FROM memories ORDER BY created DESC LIMIT 20"
                        )
                        prompt = build_system_prompt(
                            run,
                            matches,
                            memories,
                            self.store.directory
                            / "workspaces"
                            / run["conversation_id"],
                        )
                        agent = Agent(
                            llm=gateway,
                            tools=tools,
                            events=events,
                            system_prompt=prompt,
                            max_tool_loop=run["max_calls"],
                            max_context_length=30000,
                            max_tool_result_length=16000,
                        )
                        history = self.store.all(
                            "SELECT role,content FROM messages WHERE conversation_id=? ORDER BY created DESC LIMIT 20",
                            (run["conversation_id"],),
                        )
                        for message in list(reversed(history))[0:-1]:
                            agent.add_message(
                                role=message["role"], content=message["content"][:10000]
                            )
                        answered = False
                        async for chunk in agent.apredict(instruction=run["task"]):
                            if chunk.get("tool_calls"):
                                if text:
                                    self.store.event(
                                        ident, "assistant.progress", {"text": text}
                                    )
                                text = ""
                                answered = False
                                continue
                            if chunk.get("role") == "assistant" and chunk.get(
                                "content"
                            ):
                                delta = chunk["content"]
                                text += delta
                                answered = True
                                self.store.event(ident, "delta", {"text": delta})
                        if not answered:
                            raise RuntimeError(
                                "任务未形成最终答复，可能已达到工具调用预算"
                            )
                        if tools.tools_executor.unresolved:
                            raise RuntimeError(
                                "仍有未完成的工具操作："
                                + safe_summary(tools.tools_executor.unresolved)
                            )
                    status = "completed"
        except asyncio.CancelledError:
            flag.set()
            status, error = "cancelled", "任务已取消；已发送的外部模型请求无法撤回。"
        except TimeoutError:
            flag.set()
            status, error = "failed", "任务超过时间预算，已停止本地受管执行。"
        except Exception as exc:
            error = self.redact(str(exc))
        finally:
            if flag.is_set():
                if tools:
                    await tools.tools_executor.drain()
                if pending_operation:
                    pending_operation.cancel()
                    await asyncio.gather(pending_operation, return_exceptions=True)
            # A queued cancellation is persisted before the coroutine wakes.
            # Do not emit its terminal event twice if it was waiting for a slot.
            current = self.store.one("SELECT status FROM runs WHERE id=?", (ident,))
            if current and current["status"] == "cancelled":
                self.tasks.pop(ident, None)
                self.cancellations.pop(ident, None)
                return
            if text:
                self.store.message(
                    self.store.one(
                        "SELECT conversation_id FROM runs WHERE id=?", (ident,)
                    )["conversation_id"],
                    "assistant",
                    text,
                )
            self.store.execute(
                "UPDATE runs SET status=?,result=?,error=?,updated=? WHERE id=?",
                (status, text, error, now(), ident),
            )
            self.store.event(ident, "finished", {"status": status, "error": error})
            self.tasks.pop(ident, None)
            self.cancellations.pop(ident, None)

    async def close(self):
        tasks = list(self.tasks.values())
        for flag in self.cancellations.values():
            flag.set()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
