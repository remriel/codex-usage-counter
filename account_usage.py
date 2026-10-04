"""Read account allowances through the installed Codex app-server, without handling credentials."""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from pathlib import Path
from typing import Any


ACCOUNT_POLL_SECONDS = 15
REQUEST_TIMEOUT_SECONDS = 15


class AccountUsageClient:
    """One hidden stdio child and an independent polling thread; never starts an agent turn."""

    def __init__(self, codex_home: Path) -> None:
        self.codex_home = codex_home
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._thread = None
        self._process = None
        self._messages: queue.Queue = queue.Queue(maxsize=128)
        self._request_id = 0
        self._state: tuple[dict[str, Any] | None, float | None, str | None, int] = (
            None, None, "Connecting to account usage", 0
        )

    @staticmethod
    def executable() -> str | None:
        # Prefer an executable over npm's shell wrappers on Windows.
        command = shutil.which("codex.exe" if os.name == "nt" else "codex")
        if command:
            return command
        candidate = Path(os.environ.get("LOCALAPPDATA", "")) / "OpenAI/Codex/bin/codex.exe"
        return str(candidate) if os.name == "nt" and candidate.is_file() else None

    def snapshot(self):
        with self._lock:
            if self._thread is None and not self._stop.is_set():
                self._thread = threading.Thread(target=self._run, name="codex-account-usage", daemon=True)
                self._thread.start()
            return self._state

    def refresh(self) -> None:
        self._wake.set()

    def _publish(self, payload=None, timestamp=None, error=None) -> None:
        with self._lock:
            previous = self._state
            self._state = (
                payload if payload is not None else previous[0],
                timestamp if timestamp is not None else previous[1],
                error,
                previous[3] + 1,
            )

    @staticmethod
    def _receive(process, messages, stop) -> None:
        try:
            for line in process.stdout:
                if stop.is_set():
                    break
                if len(line) > 1024 * 1024:
                    continue
                try:
                    message = json.loads(line)
                except (ValueError, TypeError):
                    continue
                if isinstance(message, dict) and "id" in message:
                    try:
                        messages.put_nowait(message)
                    except queue.Full:
                        break
        except (OSError, ValueError):
            pass

    def _send(self, message) -> None:
        if self._stop.is_set() or self._process is None or self._process.poll() is not None:
            raise OSError("Account connection closed")
        self._process.stdin.write(json.dumps(message) + "\n")
        self._process.stdin.flush()

    def _request(self, method, params=None):
        self._request_id += 1
        request_id = self._request_id
        message = {"method": method, "id": request_id}
        if params is not None:
            message["params"] = params
        self._send(message)
        deadline = time.monotonic() + REQUEST_TIMEOUT_SECONDS
        while not self._stop.is_set() and time.monotonic() < deadline:
            if self._process is None or self._process.poll() is not None:
                raise OSError("Account connection closed")
            try:
                response = self._messages.get(timeout=min(0.25, max(0.001, deadline - time.monotonic())))
            except queue.Empty:
                continue
            if response.get("id") != request_id:
                continue
            if "error" in response:
                # Upstream errors can contain private data. Expose only fixed messages.
                detail = str(response["error"])
                if "401" in detail or "authentication required" in detail:
                    raise RuntimeError("Account sign-in required in Codex")
                if "403" in detail:
                    raise RuntimeError("Account usage access unavailable")
                raise RuntimeError("Account usage unavailable; retrying")
            result = response.get("result")
            if not isinstance(result, dict):
                raise RuntimeError("Account usage response unavailable")
            return result
        raise TimeoutError("Account usage request timed out")

    def _connect(self) -> None:
        executable = self.executable()
        if not executable:
            raise RuntimeError("Install Codex CLI for cloud usage")
        if not self.codex_home.is_dir():
            raise RuntimeError("Selected Codex profile is missing")
        environment = os.environ.copy()
        environment["CODEX_HOME"] = str(self.codex_home)
        environment.pop("OPENAI_API_KEY", None)
        self._messages = queue.Queue(maxsize=128)
        self._process = subprocess.Popen(
            [executable, "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL, text=True, encoding="utf-8", errors="replace",
            env=environment, cwd=str(self.codex_home),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        threading.Thread(target=self._receive, args=(self._process, self._messages, self._stop),
                         name="codex-account-stdio", daemon=True).start()
        self._request("initialize", {"clientInfo": {
            "name": "codex_usage_counter", "title": "Codex Usage Counter", "version": "1.2.0"
        }})
        self._send({"method": "initialized", "params": {}})

    def _disconnect(self) -> None:
        process = self._process
        if process is None:
            return
        self._process = None
        try:
            process.terminate()
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
        except OSError:
            pass
        finally:
            for stream in (process.stdin, process.stdout):
                if stream:
                    stream.close()

    def _run(self) -> None:
        failures = 0
        try:
            while not self._stop.is_set():
                self._wake.clear()
                try:
                    if self._process is None:
                        self._connect()
                    payload = self._request("account/rateLimits/read")
                    self._publish(payload, time.time())
                    failures = 0
                except (OSError, ValueError, RuntimeError, TimeoutError) as error:
                    message = str(error) if isinstance(error, (RuntimeError, TimeoutError)) else "Account connection unavailable; retrying"
                    self._publish(error=message)
                    self._disconnect()
                    failures += 1
                self._wake.wait(min(300, ACCOUNT_POLL_SECONDS * 2 ** min(failures, 4)))
        finally:
            self._disconnect()

    def close(self) -> None:
        self._stop.set()
        self._wake.set()
        if self._thread is not None:
            self._thread.join(timeout=3)
