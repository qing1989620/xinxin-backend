import base64
from contextlib import contextmanager
import threading
from types import SimpleNamespace

from app import remote_runner


def test_stop_confirmation_waits_for_a_launch_already_inside_the_critical_section(tmp_path, monkeypatch):
    lock = threading.Lock()
    launching, allow_launch, stop_returned = threading.Event(), threading.Event(), threading.Event()
    launched = []

    @contextmanager
    def command_lock(directory):
        with lock:
            yield

    class Child:
        pid, returncode = 123, 0
        def poll(self):
            return 0
        def wait(self, timeout):
            return 0

    def popen(*args, **kwargs):
        launching.set()
        assert allow_launch.wait(3)
        launched.append(True)
        return Child()

    # No real process, stdin, signals or /tmp access in this portable race fixture.
    monkeypatch.setattr(remote_runner, "job_directory", lambda nonce: tmp_path)
    monkeypatch.setattr(remote_runner, "command_lock", command_lock)
    monkeypatch.setattr(remote_runner, "process_identity", lambda pid: "same-process")
    monkeypatch.setattr(remote_runner, "stop_group", lambda record: True)
    monkeypatch.setattr(remote_runner.subprocess, "Popen", popen)
    monkeypatch.setattr(remote_runner, "sys", SimpleNamespace(stdout=None, stderr=None, stdin=SimpleNamespace(buffer=SimpleNamespace(read=lambda n: allow_launch.wait(3)))))
    result = {}
    run = threading.Thread(target=lambda: result.update(code=remote_runner.run("fixture", base64.b64encode(b"synthetic").decode(), 30)))
    stop = threading.Thread(target=lambda: (result.update(stop=remote_runner.cancel("fixture")), stop_returned.set()))
    run.start()
    assert launching.wait(2)
    stop.start()
    assert not stop_returned.wait(.1)
    allow_launch.set()
    run.join(3)
    stop.join(3)
    assert not run.is_alive() and not stop.is_alive()
    assert launched == [True] and result["stop"]["confirmed"]
    assert remote_runner.run("fixture", base64.b64encode(b"synthetic").decode(), 30) == 125
    assert launched == [True]  # A late starter observes the durable cancellation marker.
