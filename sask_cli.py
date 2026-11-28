#!/usr/bin/env python3
"""SASK local command center.

Two live surfaces over the read-only kernel telemetry:

* a Textual + Rich terminal HUD with sparklines, drive gauges, active
  e-graph equivalence classes and prover diagnostics;
* a FastAPI + WebSocket web command deck served at 127.0.0.1:8420.

``SASK`` / ``sask`` launch both. The kernel is never written to: the only
persisted artifacts are local UX files (telemetry, evolution log, backups,
evolved.css), all gitignored.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import socket
import sys
import threading
import time
import traceback
import urllib.request
import webbrowser
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

SPARK_RAMP = "▁▂▃▄▅▆▇█"
CONSOLE_WIDTH = 100
DEFAULT_PORT = 8420
PORT_ADVANCE_LIMIT = 10
VERBOSE = False


def _status(message: str) -> None:
    """Always-visible launcher status line (flushed for CMD pipes)."""
    print(f"[SASK] {message}", flush=True)


def _detail(message: str) -> None:
    if VERBOSE:
        _status(message)


# ---------------------------------------------------------------------------
# Small rendering helpers (works with or without Rich)
# ---------------------------------------------------------------------------
def sparkline(values: Iterable[Any], width: int = 34) -> str:
    nums: List[float] = []
    for value in values:
        try:
            nums.append(float(value))
        except (TypeError, ValueError):
            continue
    nums = nums[-width:]
    if not nums:
        return "·" * width
    lo, hi = min(nums), max(nums)
    if hi - lo < 1e-9:
        char = SPARK_RAMP[-1] if hi > 0 else SPARK_RAMP[0]
        return char * len(nums)
    scale = (len(SPARK_RAMP) - 1) / (hi - lo)
    return "".join(SPARK_RAMP[int((v - lo) * scale)] for v in nums)


def gauge(value: float, floor: Optional[float] = None, width: int = 14) -> str:
    try:
        value = max(0.0, min(1.0, float(value)))
    except (TypeError, ValueError):
        value = 0.0
    filled = int(round(value * width))
    bar = "█" * filled + "░" * (width - filled)
    tail = f" {value:.2f}"
    if floor is not None:
        tail += f" / floor {floor:.2f}"
    return bar + tail


def _fmt(value: Any, digits: int = 3) -> str:
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}" if isinstance(value, float) else str(value)
    if value is None:
        return "—"
    return str(value)


def _trim_list(values: Iterable[Any], limit: int = 5) -> str:
    items = [str(item) for item in values][:limit]
    return ", ".join(items) if items else "—"


# ---------------------------------------------------------------------------
# Web server handle
# ---------------------------------------------------------------------------
class WebDeck:
    """Lazily-started local web deck (daemon thread + uvicorn server).

    Visible from CMD: every step prints a flushed ``[SASK]`` line, a busy
    port is reused when it already serves SASK (or auto-advances to the next
    free port), and a failed bind surfaces the real error instead of a dead
    background thread.
    """

    def __init__(
        self,
        port: int = DEFAULT_PORT,
        auto_open: bool = True,
        verbose: bool = False,
    ) -> None:
        self.port = port
        self.requested_port = port
        self.auto_open = auto_open
        self.verbose = verbose
        self.thread: Optional[threading.Thread] = None
        self.server: Any = None
        self.reused = False
        self.error: Optional[str] = None
        self.browser_opened: Optional[bool] = None
        self._started = False
        self._lock = threading.Lock()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.port}/"

    # -- probes ---------------------------------------------------------
    @staticmethod
    def _probe_sask(port: int, timeout: float = 0.7) -> Tuple[bool, Optional[Dict[str, Any]]]:
        url = f"http://127.0.0.1:{port}/api/health"
        try:
            with urllib.request.urlopen(url, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except Exception:
            return False, None
        if isinstance(payload, dict) and payload.get("service") == "sask-command-deck":
            return True, payload
        return False, payload if isinstance(payload, dict) else None

    @staticmethod
    def _port_free(port: int) -> bool:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            try:
                sock.bind(("127.0.0.1", port))
            except OSError:
                return False
        return True

    def _resolve_port(self) -> Optional[int]:
        already, _ = self._probe_sask(self.requested_port)
        if already:
            self.reused = True
            self.port = self.requested_port
            _status(f"SASK deck already running on {self.url} - reusing it.")
            return self.port
        if self._port_free(self.requested_port):
            self.port = self.requested_port
            _detail(f"port {self.port} is free.")
            return self.port
        _status(f"port {self.requested_port} is busy; looking for a free port...")
        upper = min(self.requested_port + PORT_ADVANCE_LIMIT, 65535)
        for candidate in range(self.requested_port + 1, upper + 1):
            if self._port_free(candidate):
                self.port = candidate
                _status(f"using port {candidate} instead of {self.requested_port}.")
                return candidate
        self.error = (
            f"no free port between {self.requested_port} and {upper}; "
            f"free one or pass --port"
        )
        _status(f"error: {self.error}")
        return None

    # -- lifecycle ------------------------------------------------------
    def start(self) -> bool:
        with self._lock:
            if self._started:
                return self.error is None
            self._started = True
            try:
                import sask_ui_server
            except Exception as exc:
                self.error = f"web deck unavailable: {type(exc).__name__}: {exc}"
                _status(self.error)
                if self.verbose:
                    traceback.print_exc()
                return False

            port = self._resolve_port()
            if port is None:
                return False
            if self.reused:
                self._open_browser_when_ready()
                return True

            _status(f"starting web deck on {self.url} ...")
            try:
                self.thread, self.server = sask_ui_server.start_server_thread(
                    port, log_level="debug" if self.verbose else "warning"
                )
            except Exception as exc:
                self.error = f"web deck failed to start: {type(exc).__name__}: {exc}"
                _status(self.error)
                if self.verbose:
                    traceback.print_exc()
                return False

            if not sask_ui_server.wait_until_ready(port, timeout=15.0):
                alive = bool(self.thread and self.thread.is_alive())
                self.error = (
                    f"deck did not answer /api/health on port {port} "
                    f"({'server thread running' if alive else 'server thread exited'})"
                )
                _status(f"error: {self.error}")
                _status("run 'SASK --verbose' or 'SASK --doctor' for details.")
                return False

            _status(f"web deck ready at {self.url}")
            self._open_browser_when_ready()
            return True

    def _open_browser_when_ready(self) -> None:
        if not self.auto_open:
            _status("browser auto-open disabled (--no-browser).")
            return

        def worker() -> None:
            import sask_ui_server

            ready = sask_ui_server.wait_until_ready(self.port, timeout=8.0)
            try:
                opened = webbrowser.open(self.url)
            except Exception:
                opened = False
            self.browser_opened = bool(opened)
            if ready and opened:
                _status(f"browser launched: {self.url}")
            elif ready:
                _status(f"could not launch a browser; open {self.url} manually.")

        threading.Thread(target=worker, name="sask-open-deck", daemon=True).start()

    def open(self, anchor: str = "") -> None:
        if not self.start():
            return
        import sask_ui_server

        sask_ui_server.open_deck(self.port, anchor)

    def wait(self) -> int:
        """Block until Ctrl+C or unexpected server exit."""
        if not self.start():
            return 1
        _status("serving - press Ctrl+C to stop.")
        try:
            while True:
                time.sleep(0.5)
                if (
                    not self.reused
                    and self.thread is not None
                    and not self.thread.is_alive()
                ):
                    _status("error: server thread exited unexpectedly.")
                    return 1
        except KeyboardInterrupt:
            _status("stopping web deck ...")
            self.stop()
            return 0

    def stop(self) -> None:
        if self.server is not None:
            try:
                self.server.should_exit = True
            except Exception:  # pragma: no cover - shutdown guard
                pass
        if self.thread is not None and self.thread.is_alive():
            self.thread.join(timeout=3.0)


# ---------------------------------------------------------------------------
# Rich snapshot rendering (used by --once and the HUD)
# ---------------------------------------------------------------------------
def _drive_rows(snapshot: Dict[str, Any]):
    from rich.table import Table

    drives = snapshot.get("drives") or {}
    floor = float(drives.get("floor", 0.9) or 0.9)
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    rows = [
        ("Discipline", drives.get("discipline", 0.0), floor),
        ("Obsession", drives.get("obsession", 0.0), None),
        ("Motivation", drives.get("motivation", 0.0), None),
        ("Overthinking", drives.get("overthinking", 0.0), None),
        ("Mutation", drives.get("mutation_pressure", 0.0), None),
    ]
    for label, value, row_floor in rows:
        table.add_row(label, gauge(value, row_floor))
    return table


def _egraph_renderable(snapshot: Dict[str, Any]):
    from rich.table import Table

    egraph = snapshot.get("egraph") or {}
    sat = egraph.get("saturation") or {}
    table = Table(
        title="E-GRAPH EQUIVALENCE CLASSES",
        title_style="bold magenta",
        box=None,
        padding=(0, 1),
        show_header=False,
    )
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="white")
    table.add_row("classes", _fmt(egraph.get("classes")))
    table.add_row("merged", _fmt(egraph.get("merged_classes")))
    table.add_row("rewrites", _fmt(sat.get("rewrites")))
    table.add_row("goal", _fmt(egraph.get("goal_expr")))
    merged = egraph.get("merged") or []
    if merged:
        pairs = " | ".join(
            f"{m.get('class')}×{len(m.get('members') or [])}" for m in merged[:4]
        )
        table.add_row("top classes", pairs)
    return table


def _series_renderable(snapshot: Dict[str, Any]):
    from rich.table import Table

    series = snapshot.get("series") or {}
    table = Table(
        title="LIVE TELEMETRY",
        title_style="bold green",
        box=None,
        padding=(0, 1),
        show_header=False,
    )
    table.add_column(style="cyan", no_wrap=True)
    table.add_column(style="green", no_wrap=True)
    for key in ("rewrite_rate", "search_rate", "proof_velocity", "cache_ratio"):
        row = series.get(key) or []
        latest = row[-1] if row else 0.0
        table.add_row(key.replace("_", " "), f"{sparkline(row)} {_fmt(latest)}")
    return table


def _prover_renderable(snapshot: Dict[str, Any]):
    from rich.table import Table

    prover = snapshot.get("prover") or {}
    table = Table(
        title="BIDIRECTIONAL PROOF SEARCH",
        title_style="bold yellow",
        box=None,
        padding=(0, 1),
        show_header=False,
    )
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("solved", _fmt(prover.get("solved")))
    table.add_row("depth", _fmt(prover.get("depth")))
    table.add_row("expanded", f"{_fmt(prover.get('forward_expanded'))} fwd / {_fmt(prover.get('backward_expanded'))} bwd")
    table.add_row("cache", f"{_fmt(prover.get('cache_hits'))} hits / {_fmt(prover.get('cache_misses'))} misses")
    path = prover.get("path") or []
    if path:
        rendered = " → ".join(str(step) for step in path[:8])
        table.add_row("path", rendered)
    return table


def _kernel_renderable(snapshot: Dict[str, Any]):
    from rich.table import Table

    kernel = snapshot.get("kernel") or {}
    isolation = snapshot.get("isolation") or {}
    table = Table(
        title="KERNEL — READ-ONLY SNAPSHOT",
        title_style="bold white",
        box=None,
        padding=(0, 1),
        show_header=False,
    )
    table.add_column(style="cyan", no_wrap=True)
    table.add_column()
    table.add_row("state", _fmt(kernel.get("state")))
    table.add_row("mastery", _fmt(kernel.get("system_mastery")))
    table.add_row("cycles", _fmt(kernel.get("cycles")))
    table.add_row("fingerprint", str(isolation.get("kernel_fingerprint", "—")))
    table.add_row("unchanged", _fmt(isolation.get("state_unchanged")))
    return table


def _science_renderable(snapshot: Dict[str, Any]):
    from rich.panel import Panel
    from rich.text import Text

    science = snapshot.get("science") or {}
    routers = science.get("routers") or []
    names = []
    for router in routers:
        if isinstance(router, dict):
            names.append(str(router.get("name") or router.get("router") or "?"))
        else:
            names.append(str(router))
    text = Text()
    text.append(f"{len(names)} router domains\n", style="bold cyan")
    for index in range(0, len(names), 5):
        text.append("  ".join(names[index : index + 5]) + "\n", style="white")
    text.append("\nISOLATED SANDBOX — games never touch the kernel or repo.", style="bold yellow")
    return Panel(text, title="SCIENCE MATRIX", border_style="blue")


def render_snapshot(snapshot: Dict[str, Any]):
    """Compose the full HUD renderable group."""
    from rich.console import Group

    return Group(
        _kernel_renderable(snapshot),
        _series_renderable(snapshot),
        _drive_rows(snapshot),
        _egraph_renderable(snapshot),
        _prover_renderable(snapshot),
        _science_renderable(snapshot),
    )


def print_once(as_json: bool = False) -> int:
    import sask_ui_server

    snapshot = sask_ui_server.build_snapshot(record_series=True)
    if as_json:
        print(json.dumps(snapshot, indent=2, default=str))
        return 0
    try:
        from rich.console import Console

        console = Console()
        console.print(
            "[bold cyan]SASK[/] command center — one-shot telemetry "
            f"[dim]{snapshot.get('generated_at')}[/]"
        )
        console.print(render_snapshot(snapshot))
        console.print(
            "[dim]Press [bold]SASK[/bold] without flags for the live HUD + web deck.[/]"
        )
    except Exception:
        print(json.dumps(snapshot, indent=2, default=str))
    return 0


# ---------------------------------------------------------------------------
# Textual HUD
# ---------------------------------------------------------------------------
class _TelemetryPoller:
    """Background snapshot poller shared by the Textual HUD."""

    def __init__(self, interval: float = 2.5) -> None:
        self.interval = interval
        self.snapshot: Optional[Dict[str, Any]] = None
        self.error: Optional[str] = None
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self, on_snapshot) -> None:
        def loop() -> None:
            import sask_ui_server

            while not self._stop.is_set():
                snapshot = None
                try:
                    snapshot = sask_ui_server.build_snapshot(record_series=True)
                    self.snapshot = snapshot
                    self.error = None
                except Exception as exc:  # pragma: no cover - boundary guard
                    self.error = f"{type(exc).__name__}: {exc}"
                if snapshot is not None:
                    # A render failure must never stop live updates; the HUD
                    # shows it in the status panel on the next tick instead.
                    try:
                        on_snapshot(snapshot)
                    except Exception as exc:  # pragma: no cover - app shutdown race
                        self.error = f"render: {type(exc).__name__}: {exc}"
                self._stop.wait(self.interval)

        self._thread = threading.Thread(target=loop, name="sask-hud-poll", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


def _build_textual_app(web: WebDeck):
    from textual.app import App, ComposeResult
    from textual.containers import Horizontal, VerticalScroll
    from textual.widgets import Footer, Header, Static

    class SaskHUD(App):
        TITLE = "SASK — Autopoietic Command Center"
        SUB_TITLE = "read-only telemetry · isolated sandbox · [W] web [G] games [E] evolve [Q] quit"
        CSS = """
        Screen { background: #05070d; }
        Header { background: #0b1220; color: #6ee7ff; }
        Footer { background: #0b1220; color: #8b98a9; }
        #body { height: 1fr; }
        #left, #right { width: 1fr; height: 1fr; padding: 0 1; }
        .panel {
            background: #0b1220;
            border: round #1f2a3a;
            padding: 0 1;
            margin: 0 0 1 0;
            height: auto;
        }
        #status { border: round #38bdf8; }
        #sandbox-note {
            border: round #f59e0b;
            color: #fbbf24;
        }
        """
        BINDINGS = [
            ("w", "open_web", "Web Deck"),
            ("g", "open_games", "Sandbox Games"),
            ("e", "evolve_ui", "Evolve UI"),
            ("r", "refresh_now", "Refresh"),
            ("q", "quit_hud", "Quit"),
        ]

        def __init__(self) -> None:
            super().__init__()
            self.poller = _TelemetryPoller()
            self._started_at = time.monotonic()

        def compose(self) -> ComposeResult:
            yield Header(show_clock=True)
            with Horizontal(id="body"):
                with VerticalScroll(id="left"):
                    yield Static("Warming up…", id="status", classes="panel")
                    yield Static("", id="drives", classes="panel")
                    yield Static("", id="egraph", classes="panel")
                with VerticalScroll(id="right"):
                    yield Static("", id="series", classes="panel")
                    yield Static("", id="prover", classes="panel")
                    yield Static("", id="science", classes="panel")
                    yield Static(
                        "ISOLATED SANDBOX — games run client-side only; "
                        "zero effect on the live kernel or repo. Press G.",
                        id="sandbox-note",
                        classes="panel",
                        markup=False,
                    )
            yield Footer()

        def on_mount(self) -> None:
            self._started_at = time.monotonic()
            self.set_interval(0.5, self._tick_status)
            self.poller.start(self._apply_from_thread)

        def on_unmount(self) -> None:
            self.poller.stop()

        def _tick_status(self) -> None:
            if self.poller.snapshot is not None:
                return
            elapsed = time.monotonic() - self._started_at
            lines = [f"Warming up… building first snapshot ({elapsed:.1f}s)"]
            if self.poller.error:
                lines.append(f"snapshot error: {self.poller.error}")
            lines.append("Press W for the web deck, G for games, Q to quit.")
            self.query_one("#status", Static).update("\n".join(lines))

        def _apply_from_thread(self, snapshot: Dict[str, Any]) -> None:
            self.call_from_thread(self._apply, snapshot)

        def _apply(self, snapshot: Dict[str, Any]) -> None:
            self.query_one("#status", Static).update(
                render_snapshot_header(snapshot)
            )
            self.query_one("#drives", Static).update(_drives_panel(snapshot))
            self.query_one("#egraph", Static).update(_egraph_renderable(snapshot))
            self.query_one("#series", Static).update(_series_renderable(snapshot))
            self.query_one("#prover", Static).update(_prover_renderable(snapshot))
            self.query_one("#science", Static).update(_science_renderable(snapshot))

        # -- actions ----------------------------------------------------
        def action_open_web(self) -> None:
            if not web.start():
                self.notify(
                    f"web deck unavailable: {web.error or 'unknown error'}",
                    severity="error",
                    timeout=8,
                )
                return
            web.open()
            self.notify(f"Web command deck: {web.url}", timeout=6)

        def action_open_games(self) -> None:
            if not web.start():
                self.notify(
                    f"web deck unavailable: {web.error or 'unknown error'}",
                    severity="error",
                    timeout=8,
                )
                return
            web.open("sandbox")
            self.notify("Sandbox games opened in the web deck.", timeout=6)

        def action_evolve_ui(self) -> None:
            self.notify("UI self-evolution running…", timeout=4)

            def work() -> None:
                try:
                    from ui_evolver import UIEvolver

                    result = UIEvolver().evolve()
                    message = result.get("summary") or (
                        "evolved to v%s" % result.get("version")
                    )
                    if not result.get("ok", True):
                        message = "evolution rejected: %s" % (
                            result.get("reason") or "validation failed"
                        )
                except Exception as exc:  # pragma: no cover - boundary guard
                    message = f"evolution failed: {type(exc).__name__}: {exc}"
                try:
                    self.call_from_thread(self.notify, message, timeout=8)
                except Exception:  # pragma: no cover - app shutdown race
                    pass

            threading.Thread(target=work, name="sask-ui-evolve", daemon=True).start()

        def action_refresh_now(self) -> None:
            snapshot = self.poller.snapshot
            if snapshot is not None:
                self._apply(snapshot)
            if self.poller.error:
                self.notify(f"snapshot error: {self.poller.error}", timeout=6)

        def action_quit_hud(self) -> None:
            self.poller.stop()
            web.stop()
            self.exit()

    return SaskHUD


def render_snapshot_header(snapshot: Dict[str, Any]):
    from rich.table import Table

    kernel = snapshot.get("kernel") or {}
    isolation = snapshot.get("isolation") or {}
    unchanged = isolation.get("state_unchanged")
    badge = "[green]KERNEL UNCHANGED[/]" if unchanged else "[red]STATE DRIFT — inspect[/]"
    table = Table.grid(padding=(0, 2))
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    table.add_row("STATUS", f"{_fmt(kernel.get('state'))} · mastery {_fmt(kernel.get('system_mastery'))} · {badge}")
    table.add_row("SNAPSHOT", _fmt(snapshot.get("generated_at")))
    table.add_row("FINGERPRINT", str(isolation.get("kernel_fingerprint", "—")))
    return table


def _drives_panel(snapshot: Dict[str, Any]):
    from rich.table import Table

    drives = snapshot.get("drives") or {}
    floor = float(drives.get("floor", 0.9) or 0.9)
    table = Table(
        title="DRIVES — FLOOR DISCIPLINE %.2f" % floor,
        title_style="bold white",
        box=None,
        padding=(0, 1),
        show_header=False,
    )
    table.add_column(style="bold cyan", no_wrap=True)
    table.add_column()
    for label, key, row_floor in (
        ("Discipline", "discipline", floor),
        ("Obsession", "obsession", None),
        ("Motivation", "motivation", None),
        ("Overthinking", "overthinking", None),
        ("Mutation", "mutation_pressure", None),
    ):
        table.add_row(label, gauge(drives.get(key, 0.0), row_floor))
    return table


def run_hud(web: WebDeck, autostart: bool = True) -> int:
    if autostart:
        if not web.start():
            _status("web deck failed; continuing with the terminal HUD only.")
    try:
        app_cls = _build_textual_app(web)
    except Exception as exc:
        _status(f"Textual unavailable ({type(exc).__name__}: {exc}); rich fallback.")
        if VERBOSE:
            traceback.print_exc()
        return run_rich_fallback(web, autostart=False)
    _status("starting terminal HUD (W web, G games, E evolve, Q quit) ...")
    try:
        app_cls().run()
    except KeyboardInterrupt:  # pragma: no cover - terminal interrupt
        pass
    except Exception as exc:
        _status(f"HUD crashed: {type(exc).__name__}: {exc}")
        if VERBOSE:
            traceback.print_exc()
        _status("falling back to the rich HUD (Ctrl+C to quit).")
        return run_rich_fallback(web, autostart=False)
    finally:
        web.stop()
    return 0


def run_rich_fallback(web: WebDeck, autostart: bool = True) -> int:
    import sask_ui_server
    from rich.console import Console
    from rich.live import Live

    console = Console()
    if autostart and not web.start():
        _status("web deck failed; rich HUD will run without it.")
    console.print(
        "[bold cyan]SASK[/] — rich fallback HUD. Press Ctrl+C to quit.",
        highlight=False,
    )
    try:
        with Live(console=console, refresh_per_second=1, screen=True) as live:
            while True:
                try:
                    snapshot = sask_ui_server.build_snapshot(record_series=True)
                    live.update(render_snapshot(snapshot))
                except Exception as exc:  # pragma: no cover - boundary guard
                    live.update(f"[red]snapshot error: {type(exc).__name__}: {exc}")
                time.sleep(2.5)
    except KeyboardInterrupt:
        pass
    finally:
        web.stop()
    return 0


# ---------------------------------------------------------------------------
# Launcher banner + doctor
# ---------------------------------------------------------------------------
def print_banner(mode: str, web: Optional[WebDeck] = None) -> None:
    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    _status("SASK local command center")
    _status(f"  python : {sys.executable}")
    _status(f"  root   : {ROOT}")
    _status(f"  mode   : {mode}")
    if web is not None:
        _status(f"  deck   : {web.url}")
    _status(
        "  console: interactive"
        if interactive
        else "  console: non-interactive (stdin/stdout are not a TTY)"
    )
    _status(f"  stream : stdout={sys.stdout.encoding or '?'} cwd={Path.cwd()}")


def _persisted_user_path() -> Optional[str]:
    if os.name != "nt":
        return None
    try:
        import winreg

        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as key:
            value, _ = winreg.QueryValueEx(key, "Path")
            return str(value)
    except Exception:
        return None


def _norm_path(value: str) -> str:
    return os.path.normcase(os.path.normpath(value.strip().strip('"')))


def doctor() -> int:
    from importlib import metadata

    rows: List[Tuple[str, Optional[bool], str]] = []

    def add(name: str, ok: Optional[bool], detail: str = "") -> None:
        rows.append((name, ok, detail))

    add("python", True, f"{sys.version.split()[0]}  {sys.executable}")
    add("platform", True, platform.platform())
    add("cwd", True, str(Path.cwd()))
    add("repo root", ROOT.is_dir(), str(ROOT))
    add("venv entry", (ROOT / ".venv" / "Scripts" / "sask.exe").is_file(), str(ROOT / ".venv" / "Scripts" / "sask.exe"))

    modules = [
        "core",
        "drives",
        "telos_v2",
        "egraph_engine",
        "meta_prover",
        "manifold_bridge",
        "devil_advocate",
        "prover",
        "mutate",
        "science_mcp",
        "scale_orchestrator",
        "verify",
        "cycle_telemetry",
        "sask_ui_server",
        "ui_evolver",
    ]
    missing = [name for name in modules if importlib.util.find_spec(name) is None]
    add("kernel modules", not missing, ", ".join(missing) if missing else f"{len(modules)} importable")

    for dependency in ("fastapi", "uvicorn", "websockets", "textual", "rich"):
        try:
            add(f"dep {dependency}", True, metadata.version(dependency))
        except Exception:
            add(f"dep {dependency}", False, "not installed")

    ui_dir = ROOT / "ui"
    missing_assets = [
        name
        for name in ("index.html", "styles.css", "app.js", "evolved.css")
        if not (ui_dir / name).is_file()
    ]
    add("ui assets", not missing_assets, ", ".join(missing_assets) if missing_assets else "all present")

    try:
        import ui_evolver

        dom = ui_evolver.validate_dom()
        js = ui_evolver.validate_js(ui_dir / "app.js")
        css = ui_evolver.validate_css(
            (ui_dir / "styles.css").read_text(encoding="utf-8"), mode="stylesheet"
        )
        add("DOM gate", bool(dom.get("ok")), str(dom.get("reason") or ""))
        add("JS gate", bool(js.get("ok")), str(js.get("stderr") or js.get("reason") or ""))
        add("CSS gate", bool(css.get("ok")), str(css.get("errors") or ""))
    except Exception as exc:
        add("validators", False, f"{type(exc).__name__}: {exc}")

    for command in ("SASK", "sask"):
        found = shutil.which(command)
        add(f"command {command}", found is not None, found or "not on PATH")

    home = Path.home()
    shim_dirs = [home / "bin", home / ".local" / "bin"]
    persisted = _persisted_user_path()
    if persisted is None:
        add("persisted PATH", None, "could not read HKCU\\Environment (non-Windows?)")
    else:
        persisted_norm = {
            _norm_path(entry) for entry in persisted.split(";") if entry.strip()
        }
        add("persisted PATH", True, f"{len(persisted_norm)} entries")
        fresh_shim = False
        for shim_dir in shim_dirs:
            shim = shim_dir / "SASK.cmd"
            on_disk = shim.is_file()
            key = _norm_path(str(shim_dir))
            persisted_ok = key in persisted_norm
            if on_disk and persisted_ok:
                state = "installed and on the persisted User PATH"
                fresh_shim = True
            elif on_disk:
                state = "installed, but only visible via this process PATH"
            elif persisted_ok:
                state = "directory on persisted PATH, shim missing"
            else:
                state = "not installed"
            add(f"shim {shim_dir}", None, state)
        add("fresh-shell shim", fresh_shim, "SASK/sask resolve in a brand-new cmd.exe")

    try:
        free = WebDeck._port_free(DEFAULT_PORT)
        sask_running, _ = WebDeck._probe_sask(DEFAULT_PORT)
        state = "SASK deck responding" if sask_running else ("free" if free else "in use by another process")
        add(f"port {DEFAULT_PORT}", sask_running or free, state)
    except Exception as exc:
        add(f"port {DEFAULT_PORT}", False, f"{type(exc).__name__}: {exc}")

    try:
        import sask_ui_server

        started = time.monotonic()
        snapshot = sask_ui_server.build_snapshot(record_series=False)
        add(
            "snapshot",
            bool(snapshot.get("generated_at")),
            f"{time.monotonic() - started:.2f}s "
            f"({'unchanged' if (snapshot.get('isolation') or {}).get('state_unchanged') else 'drift'})",
        )
    except Exception as exc:
        add("snapshot", False, f"{type(exc).__name__}: {exc}")

    add("stdin tty", True, str(sys.stdin.isatty()))
    add("stdout tty", True, str(sys.stdout.isatty()))
    add("PYTHONUNBUFFERED", True, os.environ.get("PYTHONUNBUFFERED") or "unset")
    add("PYTHONUTF8", True, os.environ.get("PYTHONUTF8") or "unset")
    add("stdout encoding", True, str(sys.stdout.encoding))

    try:
        from rich.console import Console
        from rich.table import Table

        table = Table(title="SASK DOCTOR", title_style="bold cyan")
        table.add_column("check", no_wrap=True)
        table.add_column("result", no_wrap=True)
        table.add_column("detail", overflow="fold")
        for name, ok, detail in rows:
            if ok is None:
                mark = "[yellow]INFO[/]"
            else:
                mark = "[green]PASS[/]" if ok else "[red]FAIL[/]"
            table.add_row(name, mark, detail)
        Console().print(table)
    except Exception:
        for name, ok, detail in rows:
            mark = "INFO" if ok is None else ("PASS" if ok else "FAIL")
            print(f"{mark} {name}: {detail}")

    failures = [name for name, ok, _ in rows if ok is False]
    if failures:
        print(f"\n{len(failures)} problem(s): {', '.join(failures)}")
        return 1
    print(f"\nAll {len(rows)} checks passed.")
    return 0


# ---------------------------------------------------------------------------
# Self test
# ---------------------------------------------------------------------------
def selftest() -> int:
    results: List[tuple[str, bool, str]] = []

    def check(name: str, condition: bool, detail: str = "") -> None:
        results.append((name, bool(condition), detail))

    source_files = [
        "core.py",
        "drives.py",
        "telos_v2.py",
        "egraph_engine.py",
        "meta_prover.py",
        "manifold_bridge.py",
        "devil_advocate.py",
        "prover.py",
        "mutate.py",
        "science_mcp.py",
        "scale_orchestrator.py",
        "verify.py",
        "novelty_vault.py",
        "cycle_telemetry.py",
        "sask_ui_server.py",
        "sask_cli.py",
        "ui_evolver.py",
    ]
    for name in source_files:
        path = ROOT / name
        try:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
            check(f"syntax: {name}", True)
        except FileNotFoundError:
            check(f"syntax: {name}", False, "missing file")
        except SyntaxError as exc:
            check(f"syntax: {name}", False, f"{exc.msg} line {exc.lineno}")

    ui_dir = ROOT / "ui"
    for name in ("index.html", "styles.css", "app.js", "evolved.css"):
        check(f"ui asset: {name}", (ui_dir / name).exists())

    try:
        import sask_ui_server

        snapshot = sask_ui_server.build_snapshot(record_series=True)
        isolation = snapshot.get("isolation") or {}
        check("snapshot builds", bool(snapshot.get("generated_at")))
        check("isolation fingerprint present", bool(isolation.get("kernel_fingerprint")))
        game_keys = {"game_sandbox_state", "sandbox_state", "gameState"}
        leaked = game_keys & set(sask_ui_server._UX_ALLOWED_KEYS)
        check("sandbox state not ingestible", not leaked, ",".join(sorted(leaked)))
        check(
            "series populated",
            bool((snapshot.get("series") or {}).get("cache_ratio") is not None),
        )
    except Exception as exc:
        check("snapshot builds", False, f"{type(exc).__name__}: {exc}")

    try:
        import io
        import unittest

        import verify as verify_module

        integrity_ok, integrity = verify_module.IntegrityGuard.verify()
        check(
            "verifier integrity seal",
            integrity_ok,
            str(integrity.get("failures") or integrity.get("reason_code")),
        )
        zt_suite = unittest.TestLoader().loadTestsFromTestCase(
            verify_module.ZeroTrustPipelineTests
        )
        zt_result = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=0
        ).run(zt_suite)
        check(
            "zero-trust pipeline",
            zt_result.wasSuccessful(),
            f"{zt_result.testsRun} tests, {len(zt_result.failures)} failed, {len(zt_result.errors)} errors",
        )
        scale_suite = unittest.TestLoader().loadTestsFromTestCase(
            verify_module.ScaleOrchestratorTests
        )
        scale_result = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=0
        ).run(scale_suite)
        check(
            "elastic scale orchestrator",
            scale_result.wasSuccessful(),
            f"{scale_result.testsRun} tests, {len(scale_result.failures)} failed, {len(scale_result.errors)} errors",
        )
        vault_suite = unittest.TestLoader().loadTestsFromTestCase(
            verify_module.NoveltyVaultTests
        )
        vault_result = unittest.TextTestRunner(
            stream=io.StringIO(), verbosity=0
        ).run(vault_suite)
        check(
            "novelty vault",
            vault_result.wasSuccessful(),
            f"{vault_result.testsRun} tests, {len(vault_result.failures)} failed, {len(vault_result.errors)} errors",
        )
    except Exception as exc:
        check("zero-trust pipeline", False, f"{type(exc).__name__}: {exc}")

    try:
        import ui_evolver

        dom = ui_evolver.validate_dom()
        check("DOM validation", bool(dom.get("ok")), str(dom.get("reason") or ""))
        js = ui_evolver.validate_js(ui_dir / "app.js")
        check("JS validation", bool(js.get("ok")), str(js.get("stderr") or js.get("reason") or ""))
        css = ui_evolver.validate_css(
            (ui_dir / "styles.css").read_text(encoding="utf-8"), mode="stylesheet"
        )
        check("CSS validation", bool(css.get("ok")), str(css.get("errors") or ""))
        evolved = ui_evolver.validate_css(
            (ui_dir / "evolved.css").read_text(encoding="utf-8")
        )
        check("evolved CSS gate", bool(evolved.get("ok")), str(evolved.get("errors") or ""))
    except FileNotFoundError as exc:
        check("evolver validation", False, f"missing {exc.filename}")
    except Exception as exc:
        check("evolver validation", False, f"{type(exc).__name__}: {exc}")

    try:
        import shutil

        check("global command SASK", shutil.which("SASK") is not None, shutil.which("SASK") or "not on PATH")
        check("global command sask", shutil.which("sask") is not None, shutil.which("sask") or "not on PATH")
    except Exception as exc:
        check("global command check", False, str(exc))

    try:
        from rich.console import Console
        from rich.table import Table

        console = Console()
        table = Table(title="SASK SELFTEST", title_style="bold cyan")
        table.add_column("check")
        table.add_column("result")
        table.add_column("detail")
        for name, ok, detail in results:
            table.add_row(name, "[green]PASS[/]" if ok else "[red]FAIL[/]", detail[:80])
        console.print(table)
    except Exception:
        for name, ok, detail in results:
            print(("PASS " if ok else "FAIL ") + name + (" - " + detail if detail else ""))

    failures = [name for name, ok, _ in results if not ok]
    if failures:
        print(f"\n{len(failures)} check(s) failed: {', '.join(failures)}")
        return 1
    print(f"\nAll {len(results)} checks passed.")
    return 0


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------
def _ensure_utf8_streams() -> None:
    """Avoid UnicodeEncodeError on legacy Windows code pages."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # pragma: no cover - non-reconfigurable stream
            pass


def main(argv: Optional[List[str]] = None) -> int:
    global VERBOSE

    _ensure_utf8_streams()
    parser = argparse.ArgumentParser(
        prog="SASK",
        description="SASK local command center: terminal HUD + web command deck.",
    )
    parser.add_argument("--web-only", action="store_true", help="serve only the web deck")
    parser.add_argument("--hud-only", action="store_true", help="terminal HUD only (no web server)")
    parser.add_argument("--no-browser", action="store_true", help="never auto-open a browser")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"web deck port (default {DEFAULT_PORT})")
    parser.add_argument("--once", action="store_true", help="print one snapshot and exit")
    parser.add_argument("--json", action="store_true", help="with --once, emit JSON")
    parser.add_argument("--selftest", action="store_true", help="run local checks and exit")
    parser.add_argument("--doctor", action="store_true", help="diagnose launcher, PATH, port, and deps")
    parser.add_argument("--verbose", action="store_true", help="show debug detail and tracebacks")
    args = parser.parse_args(argv)
    VERBOSE = args.verbose

    if args.selftest:
        return selftest()
    if args.doctor:
        return doctor()
    if args.once:
        return print_once(as_json=args.json)
    if args.web_only and args.hud_only:
        _status("error: --web-only and --hud-only are mutually exclusive.")
        return 2
    if not 1 <= args.port <= 65535:
        _status(f"error: --port must be 1-65535 (got {args.port}).")
        return 2

    interactive = sys.stdin.isatty() and sys.stdout.isatty()
    web = WebDeck(port=args.port, auto_open=not args.no_browser, verbose=VERBOSE)

    if args.web_only:
        print_banner("web deck only", web)
        return web.wait()

    if args.hud_only:
        if not interactive:
            _status("error: --hud-only needs an interactive console; stdin/stdout are not a TTY.")
            _status("use --web-only (or plain SASK) instead.")
            return 2
        print_banner("terminal HUD only", None)
        return run_hud(web, autostart=False)

    if not interactive:
        print_banner("web deck (no interactive console detected)", web)
        _status("terminal HUD skipped because stdin/stdout are not a TTY.")
        _status(f"open {web.url} in a browser; Ctrl+C stops the deck.")
        return web.wait()

    print_banner("terminal HUD + web deck", web)
    return run_hud(web, autostart=True)


if __name__ == "__main__":
    raise SystemExit(main())
