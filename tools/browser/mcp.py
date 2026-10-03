"""Playwright MCP provider: a real MCP client over stdio.

Mamba speaks the Model Context Protocol directly to the browser-control server
(``@playwright/mcp``) as a subprocess. There is no MCP framework dependency: the
transport is JSON-RPC 2.0 over newline-delimited stdin/stdout, which is what the
MCP stdio transport specifies and what the server implements.

Design notes:

* **Chrome channel by default.** The provider launches Chrome (``--browser
  chrome``); ``connect`` mode attaches to an already-running Chrome over CDP so
  Mamba can drive an existing session instead of always starting its own.
* **Session-scoped, lazy.** The server process starts on first use, is reused
  for the session, and is terminated explicitly. No background polling.
* **Structured, not raw.** Server text output is parsed into
  :class:`BrowserPage` (URL, title, visible text, interactive elements, refs) so
  Mamba's planner and verifier see structure instead of MCP prose.
"""

from __future__ import annotations

import json
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from typing import Any

from .snapshot import extract_visible_text, parse_snapshot
from .types import (
    BrowserElement,
    BrowserOutcome,
    BrowserPage,
    BrowserTarget,
    BrowserTargetError,
)

DEFAULT_PACKAGE = "@playwright/mcp@0.0.83"
_PROTOCOL_VERSION = "2025-06-18"

_URL_LINE = re.compile(r"^-\s*Page URL:\s*(\S+)\s*$", re.MULTILINE)
_TITLE_LINE = re.compile(r"^-\s*Page Title:\s*(.+?)\s*$", re.MULTILINE)
_TAB_LINE = re.compile(
    r"^-\s*(?P<index>\d+):\s*(?P<current>\(current\)\s*)?\[(?P<title>[^\]]*)\]\((?P<url>[^)]*)\)\s*$",
    re.MULTILINE,
)


def extract_page(text: str) -> tuple[str, str, str]:
    """Extract (url, title, snapshot_yaml) from one MCP tool response."""
    url_match = _URL_LINE.search(text)
    title_match = _TITLE_LINE.search(text)
    url = url_match.group(1).strip() if url_match else ""
    title = title_match.group(1).strip() if title_match else ""

    snapshot = ""
    fence = re.search(r"```(?:yaml)?\s*\n(?P<body>.*?)\n```", text, re.DOTALL)
    if fence:
        body = fence.group("body")
        # Only a YAML tree is a snapshot; skip ```js code blocks.
        if re.search(r"^\s*-\s+\S+.*\[ref=", body, re.MULTILINE):
            snapshot = body.strip()
    return url, title, snapshot


# Sentinel pushed onto a request's queue when the connection closes while it waits.
_CLOSED = object()


class McpStdioClient:
    """Minimal, synchronous MCP client over a child process' stdio."""

    def __init__(
        self,
        command: list[str],
        *,
        timeout: float = 180.0,
        env: dict[str, str] | None = None,
    ) -> None:
        self._command = command
        self._timeout = timeout
        self._env = env
        self._process: subprocess.Popen[str] | None = None
        # Lifecycle lock guards start()/stop() and the process handle. It is
        # never held while awaiting a response, so a hung request can always be
        # stopped and shutdown stays bounded.
        self._proc_lock = threading.RLock()
        # Serialises writes to the child's stdin (single writer).
        self._write_lock = threading.Lock()
        # Guards the request-id -> response-queue map and the id counter.
        self._pending_lock = threading.Lock()
        self._pending: dict[int, queue.Queue[object]] = {}
        self._counter = 0
        self._stderr_lines: list[str] = []
        self._initialized = False
        self._closed = threading.Event()
        self._reader_thread: threading.Thread | None = None

    # ── lifecycle ──

    def start(self) -> None:
        with self._proc_lock:
            if self._process is not None and self._process.poll() is None:
                return
            env = dict(os.environ)
            if self._env:
                env.update(self._env)
            creation_flags = 0
            if sys.platform == "win32" and hasattr(subprocess, "CREATE_NO_WINDOW"):
                creation_flags |= subprocess.CREATE_NO_WINDOW
            try:
                self._process = subprocess.Popen(
                    self._command,
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,
                    env=env,
                    creationflags=creation_flags,
                    shell=(sys.platform == "win32"),
                )
            except Exception as exc:
                raise BrowserTargetError(
                    f"could not start the browser provider process: {exc}"
                ) from exc

            self._stderr_lines = []
            with self._pending_lock:
                self._pending = {}
            self._closed.clear()
            threading.Thread(target=self._drain_stderr, daemon=True).start()
            self._reader_thread = threading.Thread(
                target=self._read_loop, daemon=True, name="mcp-stdout-reader"
            )
            self._reader_thread.start()
            self._initialize()

    def _drain_stderr(self) -> None:
        process = self._process
        if process is None or process.stderr is None:
            return
        try:
            for line in process.stderr:
                text = line.rstrip()
                if text:
                    self._stderr_lines.append(text)
                    del self._stderr_lines[:-40]
        except Exception:
            pass

    def _read_loop(self) -> None:
        """Continuously read stdout, dispatching responses by id.

        Runs on a dedicated daemon thread so no request path blocks on
        ``readline``. On EOF (process exit) it wakes every pending request so
        they fail promptly instead of waiting out their timeout.
        """
        process = self._process
        if process is None or process.stdout is None:
            return
        try:
            for line in process.stdout:
                text = line.strip()
                if not text:
                    continue
                try:
                    message = json.loads(text)
                except json.JSONDecodeError:
                    continue
                self._dispatch(message)
        except Exception:
            pass
        finally:
            self._wake_pending()

    def _dispatch(self, message: dict[str, Any]) -> None:
        """Route a parsed JSON-RPC message to its waiting request, if any.

        Messages whose id has no pending request (notifications, or responses
        that arrive after a request timed out/cancelled) are dropped so a late
        reply can never be delivered to a subsequent request.
        """
        message_id = message.get("id")
        if not isinstance(message_id, int):
            return
        with self._pending_lock:
            response_queue = self._pending.get(message_id)
        if response_queue is None:
            return
        try:
            response_queue.put_nowait(message)
        except queue.Full:
            pass

    def _wake_pending(self) -> None:
        """Unblock every pending request (the connection is going away)."""
        with self._pending_lock:
            waiters = list(self._pending.values())
            self._pending = {}
        for response_queue in waiters:
            try:
                response_queue.put_nowait(_CLOSED)
            except queue.Full:
                pass

    def _ambient_cancel_event(self) -> threading.Event | None:
        """Cancellation event for the in-flight request, if one is bound.

        Imported lazily so the tools layer does not take a hard dependency on
        core at import time (keeps the CORE-above-TOOLS layering intact).
        """
        try:
            from core.cancellation import current_cancel_event
        except Exception:
            return None
        try:
            return current_cancel_event()
        except Exception:
            return None

    def stop(self) -> None:
        with self._proc_lock:
            self._closed.set()
            self._wake_pending()
            process = self._process
            self._process = None
            self._initialized = False
            if process is None:
                return
            try:
                if process.stdin is not None:
                    process.stdin.close()
            except Exception:
                pass
            self._terminate_tree(process)

    def _terminate_tree(self, process: subprocess.Popen[str]) -> None:
        """Terminate the child and, on Windows, its whole process tree.

        ``npx`` spawns ``node`` as a grandchild; terminating only the direct
        child leaks the browser server. psutil lets us reach the descendants.
        Every step is bounded so shutdown can never hang.
        """
        children: list[Any] = []
        try:
            import psutil

            parent = psutil.Process(process.pid)
            children = parent.children(recursive=True)
        except Exception:
            children = []

        for child in children:
            try:
                child.terminate()
            except Exception:
                pass
        if children:
            try:
                import psutil

                _, alive = psutil.wait_procs(children, timeout=5)
                for child in alive:
                    try:
                        child.kill()
                    except Exception:
                        pass
            except Exception:
                pass

        try:
            process.terminate()
        except Exception:
            pass
        try:
            process.wait(timeout=5)
            return
        except Exception:
            pass
        try:
            process.kill()
        except Exception:
            pass
        if sys.platform == "win32":
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception:
                pass

    @property
    def running(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def recent_stderr(self) -> str:
        return "\n".join(self._stderr_lines[-8:])

    # ── protocol ──

    def _initialize(self) -> None:
        response = self.request(
            "initialize",
            {
                "protocolVersion": _PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "mamba", "version": "1.0"},
            },
        )
        if "error" in response:
            raise BrowserTargetError(
                f"browser provider refused initialization: {response['error']}"
            )
        self.notify("notifications/initialized", {})
        self._initialized = True

    def notify(self, method: str, params: dict[str, Any] | None = None) -> None:
        self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})

    def _write(self, message: dict[str, Any]) -> None:
        with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise BrowserTargetError("the browser provider process is not running")
            try:
                process.stdin.write(json.dumps(message) + "\n")
                process.stdin.flush()
            except Exception as exc:
                raise BrowserTargetError(
                    f"lost the browser provider connection: {exc}"
                ) from exc

    def request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a request and wait for its matching response, bounded and cancellable.

        The wait never holds the lifecycle lock, so ``stop()`` can always make
        progress. The wait ends on: the matching response, the timeout, ambient
        cancellation, connection close, or unexpected process exit. A per-request
        queue keyed by the request id means a late response to a timed-out or
        cancelled request is dropped rather than corrupting a later one.
        """
        process = self._process
        if process is None:
            raise BrowserTargetError("the browser provider process is not running")

        limit = self._timeout if timeout is None else timeout
        cancel_event = self._ambient_cancel_event()

        with self._pending_lock:
            self._counter += 1
            request_id = self._counter
            response_queue: queue.Queue[object] = queue.Queue(maxsize=1)
            self._pending[request_id] = response_queue

        try:
            self._write(
                {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params or {}}
            )

            deadline = time.monotonic() + limit
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise BrowserTargetError(
                        f"the browser provider did not answer '{method}' within {limit:.0f}s"
                    )
                if cancel_event is not None and cancel_event.is_set():
                    raise BrowserTargetError(f"cancelled while awaiting '{method}'")
                if self._closed.is_set():
                    raise BrowserTargetError(
                        f"the browser provider connection closed while awaiting '{method}'"
                    )
                if process.poll() is not None:
                    detail = self.recent_stderr()
                    raise BrowserTargetError(
                        "the browser provider exited unexpectedly"
                        + (f": {detail}" if detail else "")
                    )
                try:
                    item = response_queue.get(timeout=min(remaining, 0.1))
                except queue.Empty:
                    continue
                if item is _CLOSED:
                    raise BrowserTargetError(
                        f"the browser provider connection closed while awaiting '{method}'"
                    )
                return item  # type: ignore[return-value]
        finally:
            with self._pending_lock:
                self._pending.pop(request_id, None)

    def call_tool(self, name: str, arguments: dict[str, Any]) -> tuple[str, bool]:
        """Invoke an MCP tool. Returns (text, is_error)."""
        response = self.request("tools/call", {"name": name, "arguments": arguments})
        if "error" in response:
            error = response["error"]
            return str(error.get("message") or error), True
        result = response.get("result", {})
        content = result.get("content", []) or []
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text":
                parts.append(str(part.get("text", "")))
        text = "\n".join(parts)
        return text, bool(result.get("isError"))


class PlaywrightMcpProvider:
    """Browser provider backed by the Playwright MCP server (Chrome by default)."""

    provider_name = "playwright-mcp"

    def __init__(
        self,
        *,
        browser: str = "chrome",
        headless: bool = True,
        package: str = DEFAULT_PACKAGE,
        cdp_endpoint: str = "",
        user_data_dir: str = "",
        isolated: bool = False,
        viewport: str = "",
        timeout: float = 180.0,
        extra_args: tuple[str, ...] = (),
        command: list[str] | None = None,
    ) -> None:
        self._browser = browser
        self._headless = headless
        self._package = package
        self._cdp_endpoint = cdp_endpoint
        self._user_data_dir = user_data_dir
        self._isolated = isolated
        self._viewport = viewport
        self._timeout = timeout
        self._extra_args = tuple(extra_args)
        self._command_override = command
        self._client: McpStdioClient | None = None
        self._last_page = BrowserPage()

    # ── configuration ──

    def _resolved_command(self) -> list[str]:
        if self._command_override:
            return list(self._command_override)
        if shutil.which("npx") is None:
            raise BrowserTargetError(
                "npx is not available, so the Playwright MCP browser provider cannot be "
                "started. Install Node.js or configure another browser provider."
            )
        command = ["npx", "-y", self._package]
        if self._cdp_endpoint:
            command += ["--cdp-endpoint", self._cdp_endpoint]
        else:
            command += ["--browser", self._browser]
        if self._headless and not self._cdp_endpoint:
            command.append("--headless")
        if self._isolated and not self._user_data_dir:
            command.append("--isolated")
        if self._user_data_dir and not self._cdp_endpoint:
            command += ["--user-data-dir", self._user_data_dir]
        if self._viewport:
            command += ["--viewport-size", self._viewport]
        command += list(self._extra_args)
        return command

    def is_available(self) -> tuple[bool, str]:
        if shutil.which("npx") is None:
            return False, "npx (Node.js) is not installed, so Playwright MCP is unavailable."
        return True, f"Playwright MCP is available ({self._browser} channel)."

    @property
    def mode(self) -> str:
        return "connect" if self._cdp_endpoint else "launch"

    # ── lifecycle ──

    def start(self) -> None:
        if self._client is None:
            self._client = McpStdioClient(self._resolved_command(), timeout=self._timeout)
        self._client.start()

    def stop(self) -> None:
        if self._client is not None:
            self._client.stop()

    @property
    def running(self) -> bool:
        return self._client is not None and self._client.running

    # ── observation ──

    def current_page(self) -> tuple[str, str]:
        outcome = self.snapshot()
        if not outcome.success:
            return "", ""
        return outcome.page.url, outcome.page.title

    def list_targets(self) -> list[BrowserTarget]:
        self.start()
        assert self._client is not None
        text, is_error = self._client.call_tool("browser_tabs", {"action": "list"})
        if is_error:
            raise BrowserTargetError(f"could not list browser tabs: {text.strip()}")
        targets: list[BrowserTarget] = []
        for match in _TAB_LINE.finditer(text):
            targets.append(
                BrowserTarget(
                    session_id="default",
                    tab_index=int(match.group("index")),
                    url=match.group("url").strip(),
                    title=match.group("title").strip(),
                    current=bool(match.group("current")),
                )
            )
        if not targets:
            url = _URL_LINE.search(text)
            title = _TITLE_LINE.search(text)
            targets.append(
                BrowserTarget(
                    session_id="default",
                    tab_index=0,
                    url=url.group(1) if url else "",
                    title=title.group(1).strip() if title else "",
                    current=True,
                )
            )
        return targets

    def _page_from(self, text: str) -> BrowserPage:
        url, title, snapshot = extract_page(text)
        elements: tuple[BrowserElement, ...] = ()
        refs: dict[str, str] = {}
        visible = ""
        if snapshot:
            parsed, refs = parse_snapshot(snapshot)
            elements = tuple(parsed)
            visible = extract_visible_text(snapshot)
        else:
            visible = _strip_scaffolding(text)
        if not url:
            url = self._last_page.url
        if not title:
            title = self._last_page.title
        page = BrowserPage(url=url, title=title, text=visible, elements=elements, refs=refs)
        if url or title:
            self._last_page = page
        return page

    def _call(self, tool: str, arguments: dict[str, Any]) -> BrowserOutcome:
        self.start()
        assert self._client is not None
        text, is_error = self._client.call_tool(tool, arguments)
        if is_error:
            return BrowserOutcome.failed(
                _clean_error(text) or f"{tool} failed",
                tool=tool,
            )
        page = self._page_from(text)
        return BrowserOutcome.ok(page, message=_first_line(text), tool=tool)

    # ── navigation ──

    def navigate(self, url: str) -> BrowserOutcome:
        return self._call("browser_navigate", {"url": url})

    def history(self, direction: str) -> BrowserOutcome:
        if direction == "back":
            return self._call("browser_navigate_back", {})
        if direction == "forward":
            # The MCP surface exposes no forward primitive, but its snapshot can
            # report the next history entry � navigate to it explicitly.
            return self._call("browser_navigate_forward", {})
        return BrowserOutcome.failed(f"unsupported history direction '{direction}'")

    def reload(self) -> BrowserOutcome:
        # The MCP surface has no dedicated reload; re-entering the current URL is
        # the equivalent observable operation.
        url, _ = self.current_page()
        if not url:
            return BrowserOutcome.failed("there is no current page to reload")
        return self.navigate(url)

    # ── inspection ──

    def snapshot(self) -> BrowserOutcome:
        return self._call("browser_snapshot", {})

    def page_text(self) -> BrowserOutcome:
        return self._call("browser_snapshot", {})

    def find(self, *, text: str = "", regex: str = "") -> BrowserOutcome:
        arguments: dict[str, Any] = {}
        if text:
            arguments["text"] = text
        if regex:
            arguments["regex"] = regex
        if not arguments:
            return BrowserOutcome.failed("a search text or pattern is required")
        return self._call("browser_find", arguments)

    def wait_for(self, *, text: str = "", time: float | None = None) -> BrowserOutcome:
        arguments: dict[str, Any] = {}
        if text:
            arguments["text"] = text
        if time is not None:
            arguments["time"] = time
        return self._call("browser_wait_for", arguments)

    def scroll(self, direction: str) -> BrowserOutcome:
        key = {
            "down": "PageDown",
            "up": "PageUp",
            "top": "Home",
            "bottom": "End",
            "left": "ArrowLeft",
            "right": "ArrowRight",
        }.get(direction)
        if key is None:
            return BrowserOutcome.failed(f"unsupported scroll direction '{direction}'")
        outer = self._call("browser_press_key", {"key": key})
        if not outer.success:
            return outer
        # Keyboard scrolling moves focus; keep the observation from a fresh snapshot.
        return self.snapshot()

    # ── interaction ──

    def act(self, action: str, params: dict[str, Any]) -> BrowserOutcome:
        if action == "click":
            return self._call(
                "browser_click",
                _target_arguments(params),
            )
        if action == "type":
            arguments = _target_arguments(params)
            arguments["text"] = str(params.get("text") or "")
            if params.get("submit"):
                arguments["submit"] = True
            return self._call("browser_type", arguments)
        if action == "clear":
            # MCP has no clear primitive: select-all then delete.
            outer = self._call("browser_click", _target_arguments(params))
            if not outer.success:
                return outer
            self._call("browser_press_key", {"key": "Control+a"})
            return self._call("browser_press_key", {"key": "Delete"})
        if action == "press_key":
            key = str(params.get("key") or "")
            if not key:
                return BrowserOutcome.failed("a key name is required")
            return self._call("browser_press_key", {"key": key})
        if action == "select_option":
            return self._call(
                "browser_select_option",
                _target_arguments(params) | {"values": list(params.get("values") or [])},
            )
        if action == "hover":
            return self._call("browser_hover", _target_arguments(params))
        if action == "fill_form":
            return self._call("browser_fill_form", {"fields": params.get("fields") or []})
        return BrowserOutcome.failed(f"unsupported browser action '{action}'")

    def close_page(self) -> BrowserOutcome:
        return self._call("browser_close", {})


def _target_arguments(params: dict[str, Any]) -> dict[str, Any]:
    """Build MCP targeting arguments from Mamba's element parameters."""
    arguments: dict[str, Any] = {}
    target = str(params.get("ref") or params.get("target") or "").strip()
    if target:
        arguments["target"] = target
    element = str(params.get("element") or params.get("description") or "").strip()
    if not element:
        element = str(params.get("name") or params.get("text") or "").strip()
    if element:
        arguments["element"] = element
    return arguments


def _clean_error(text: str) -> str:
    """Reduce an MCP error payload to a readable sentence."""
    value = (text or "").strip()
    if not value:
        return ""
    value = re.sub(r"^###\s*Error\s*", "", value).strip()
    value = re.sub(r"```.*?```", "", value, flags=re.DOTALL).strip()
    return value.splitlines()[0][:400] if value else ""


def _first_line(text: str) -> str:
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("```"):
            return stripped[:200]
    return ""


def _strip_scaffolding(text: str) -> str:
    """Remove MCP prose/header scaffolding, keeping human-visible content."""
    kept: list[str] = []
    in_code = False
    for line in (text or "").splitlines():
        stripped = line.strip()
        if stripped.startswith("```"):
            in_code = not in_code
            continue
        if in_code:
            continue
        if stripped.startswith("###"):
            continue
        if re.match(r"^-\s*(Page URL|Page Title|Snapshot)\b", stripped):
            continue
        if stripped:
            kept.append(stripped)
    return "\n".join(kept)


__all__ = [
    "DEFAULT_PACKAGE",
    "McpStdioClient",
    "PlaywrightMcpProvider",
    "extract_page",
]
