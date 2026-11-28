"""SASK Self-Evolving UI/UX Engine (local only).

Reads local interaction telemetry and evolves a small, bounded set of visual
"genes" (CSS custom properties) toward two goals:

1. Higher visual attractiveness and polish.
2. Better comprehension of how SASK works.

Every cycle is gated by CSS, JavaScript and DOM integrity checks. Successful
cycles are versioned into ``ui_backups/`` and logged in ``ui_evolver_log.json``;
any cycle can be reverted with one call. No kernel module, state ledger or
repository file is ever touched.
"""

from __future__ import annotations

import html.parser
import json
import random
import re
import shutil
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent
UI_DIR = ROOT / "ui"
BACKUP_DIR = ROOT / "ui_backups"
TELEMETRY_PATH = ROOT / "ui_telemetry.json"
LOG_PATH = ROOT / "ui_evolver_log.json"

INDEX_PATH = UI_DIR / "index.html"
STYLES_PATH = UI_DIR / "styles.css"
APP_PATH = UI_DIR / "app.js"
EVOLVED_PATH = UI_DIR / "evolved.css"

GENE_BOUNDS: Dict[str, Tuple[float, float]] = {
    "accent_h": (150.0, 230.0),
    "accent2_h": (250.0, 320.0),
    "blur_px": (8.0, 28.0),
    "radius_px": (12.0, 26.0),
    "pad_px": (12.0, 26.0),
    "motion_ms": (160.0, 520.0),
    "font_scale": (0.94, 1.12),
    "border_alpha": (0.08, 0.30),
    "chart_height_px": (180.0, 280.0),
    "explain_glow": (0.20, 0.80),
    "glow_strength": (0.25, 0.85),
}

DEFAULT_GENES: Dict[str, float] = {
    "accent_h": 188.0,
    "accent2_h": 268.0,
    "blur_px": 18.0,
    "radius_px": 18.0,
    "pad_px": 18.0,
    "motion_ms": 380.0,
    "font_scale": 1.0,
    "border_alpha": 0.14,
    "chart_height_px": 220.0,
    "explain_glow": 0.38,
    "glow_strength": 0.55,
}

_REQUIRED_IDS = {
    # shell + topbar
    "app-shell",
    "topbar",
    "status-pill",
    "connection-dot",
    "status-text",
    "clock",
    "fps-meter",
    "explain-toggle",
    "telemetry-toggle",
    "help-toggle",
    "evolve-btn",
    "revert-btn",
    # open world
    "world-shell",
    "world-canvas",
    "mission-strip",
    "mission-line",
    "copilot-state",
    "demo-btn",
    "copilot-toggle",
    "copilot-panel",
    "copilot-mode",
    "copilot-log",
    "minimap-wrap",
    "minimap-canvas",
    "minimap-caption",
    "world-layout-pill",
    "waypoint-dock",
    "waypoint-grid",
    "station-hint",
    "station-hint-name",
    "world-badges",
    "sandbox-badge",
    "sandbox-reset",
    "world-controls",
    "demo-banner",
    "demo-step",
    "demo-cancel",
    "telemetry-dock",
    "isolation-pill",
    "telemetry-close",
    "telemetry-stats",
    "versions-list",
    # station order console
    "station-console",
    "station-script",
    "station-name",
    "station-close",
    "station-desc",
    "station-live",
    "station-metrics",
    "station-takeover",
    "station-pause",
    "station-intensity",
    "station-intensity-value",
    "station-explain",
    "order-list",
    "order-form",
    "order-input",
    "order-send",
    "station-progress-label",
    "station-progress",
    "order-queue",
    "station-game-btn",
    # sandbox games
    "game-shell",
    "game-backdrop",
    "panel-sandbox",
    "sandbox-badge-games",
    "sandbox-reset-games",
    "game-close",
    "sandbox",
    "game-tabs",
    "game-mutator",
    "game-egraph",
    "game-axioms",
    "game-science",
    "mut-score",
    "mut-risk",
    "mut-budget",
    "mut-regressions",
    "mut-risk-bar",
    "mut-verdict",
    "mut-log",
    "expr-grid",
    "eg-classes",
    "eg-merges",
    "eg-goals",
    "eg-verdict",
    "eg-log",
    "axiom-grid",
    "axiom-graph",
    "ax-verdict",
    "ax-log",
    "sci-budget",
    "sci-accuracy",
    "sci-time",
    "sci-target",
    "sci-target-slider",
    "sci-target-value",
    "sci-verdict",
    "sci-log",
    # explainer + shell artifacts
    "explainer-drawer",
    "explainer-title",
    "explainer-body",
    "explainer-close",
    "toast-host",
    "evolved-styles",
    # reliability + active fixes
    "reliability-toggle",
    "reliability-drawer",
    "reliability-close",
    "reliability-provenance",
    "rel-refresh",
    "rel-fix-all",
    "rel-queue-all",
    "rel-cloud-status",
    "rel-cloud-meta",
    "rel-cloud-bar",
    "rel-cloud-window",
    "rel-cycle-status",
    "rel-cycle-meta",
    "rel-pass",
    "rel-cpu",
    "rel-rss",
    "rel-discipline",
    "rel-limiters",
    "rel-bottleneck-list",
    "rel-steps-chart",
    "rel-trend-chart",
    "rel-trend-legend",
    "rel-rank-chart",
    "rel-cloud-count",
    "rel-cloud-runs",
    "rel-fix-counts",
    "rel-fix-queue",
    "rel-directive-wrap",
    "rel-directive",
    "rel-copy-directive",
}

_ALLOWED_VARS = {
    "--accent-h",
    "--accent2-h",
    "--glass-blur",
    "--radius",
    "--panel-pad",
    "--motion",
    "--font-scale",
    "--border-alpha",
    "--chart-height",
    "--explain-glow",
    "--glow-strength",
}

_UI_WRITE_ALLOWLIST = (
    UI_DIR / "index.html",
    UI_DIR / "styles.css",
    UI_DIR / "app.js",
    UI_DIR / "evolved.css",
)


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------


def _safe_load(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, TypeError):
        return default


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def _utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _assert_ui_path(path: Path) -> None:
    resolved = path.resolve()
    allowed = [item.resolve() for item in _UI_WRITE_ALLOWLIST]
    if resolved not in allowed:
        raise ValueError(f"Refusing to write outside the UI allowlist: {path}")


# ---------------------------------------------------------------------------
# Telemetry + signal analysis
# ---------------------------------------------------------------------------


def load_telemetry() -> List[Dict[str, Any]]:
    payload = _safe_load(TELEMETRY_PATH, [])
    if isinstance(payload, dict):
        payload = payload.get("events", [])
    if not isinstance(payload, list):
        return []
    return [event for event in payload if isinstance(event, dict)]


def analyze(events: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Convert raw UX events into bounded improvement signals."""
    counts: Dict[str, int] = {}
    panel_dwell: Dict[str, float] = {}
    panel_opens: Dict[str, int] = {}
    game_actions: Dict[str, int] = {}
    stuck = 0
    explain_on = 0
    explain_off = 0
    explain_opened = 0
    fps_samples: List[float] = []
    mobile = 0
    viewport_samples = 0

    for event in events:
        event_type = str(event.get("type", ""))
        counts[event_type] = counts.get(event_type, 0) + 1
        panel = str(event.get("panel", "") or "")
        game = str(event.get("game", "") or "")
        if event_type == "panel_dwell" and panel:
            panel_dwell[panel] = panel_dwell.get(panel, 0.0) + float(
                event.get("dwell_ms", 0) or 0
            )
        elif event_type == "panel_open" and panel:
            panel_opens[panel] = panel_opens.get(panel, 0) + 1
        elif event_type == "game_action" and game:
            game_actions[game] = game_actions.get(game, 0) + 1
        elif event_type == "game_stuck":
            stuck += 1
        elif event_type == "explain_toggle":
            if str(event.get("mode", "")) == "on":
                explain_on += 1
            else:
                explain_off += 1
        elif event_type == "explain_open":
            explain_opened += 1
        elif event_type == "fps_sample":
            try:
                fps_samples.append(float(event.get("fps", 0) or 0))
            except (TypeError, ValueError):
                continue
        elif event_type == "viewport":
            viewport_samples += 1
            try:
                if float(event.get("viewport", 0) or 0) < 900:
                    mobile += 1
            except (TypeError, ValueError):
                continue

    toggles = explain_on + explain_off
    fps_avg = sum(fps_samples) / len(fps_samples) if fps_samples else 60.0
    total_dwell = sum(panel_dwell.values())
    top_panels = sorted(
        (
            {"panel": panel, "dwell_ms": round(value, 1)}
            for panel, value in panel_dwell.items()
        ),
        key=lambda item: item["dwell_ms"],
        reverse=True,
    )[:4]

    confusion_panels = [
        item["panel"]
        for item in top_panels
        if item["dwell_ms"] > 12000 and item["panel"] not in panel_opens
    ]

    return {
        "events": len(events),
        "has_data": bool(events),
        "event_counts": dict(sorted(counts.items())),
        "explain_rate": round(explain_on / max(1, toggles), 4) if toggles else 0.0,
        "explain_opened": explain_opened,
        "game_stuck": stuck,
        "game_actions": game_actions,
        "fps_avg": round(fps_avg, 2),
        "mobile_ratio": round(mobile / max(1, viewport_samples), 4),
        "panel_dwell": {key: round(value, 1) for key, value in panel_dwell.items()},
        "mean_dwell_ms": round(total_dwell / max(1, len(panel_dwell)), 1),
        "top_panels": top_panels,
        "confusion_panels": confusion_panels,
    }


def _clamp(name: str, value: float) -> float:
    low, high = GENE_BOUNDS[name]
    return round(max(low, min(high, value)), 4)


def _seed(signals: Dict[str, Any]) -> int:
    material = json.dumps(signals, sort_keys=True, default=str).encode("utf-8")
    return int.from_bytes(material[:8] or b"\x00", "big")


def propose(
    genes: Dict[str, float],
    signals: Dict[str, Any],
    version: int,
) -> Tuple[Dict[str, float], List[str]]:
    """Deterministically adjust genes toward attractiveness + comprehension."""
    updated = dict(genes)
    changes: List[str] = []
    fps = float(signals.get("fps_avg", 60.0))
    explain_rate = float(signals.get("explain_rate", 0.0))
    stuck = int(signals.get("game_stuck", 0))
    mobile_ratio = float(signals.get("mobile_ratio", 0.0))
    mean_dwell = float(signals.get("mean_dwell_ms", 0.0))

    if not signals.get("has_data"):
        updated["accent_h"] = _clamp("accent_h", updated["accent_h"] + 7)
        updated["chart_height_px"] = _clamp(
            "chart_height_px", updated["chart_height_px"] + 8
        )
        updated["border_alpha"] = _clamp(
            "border_alpha", updated["border_alpha"] + 0.02
        )
        changes.append("Bootstrap polish: cooler accent, taller charts, crisper panel edges.")
    else:
        if fps < 48:
            before = updated["glass_blur"] if "glass_blur" in updated else updated["blur_px"]
            updated["blur_px"] = _clamp("blur_px", updated["blur_px"] - 2.0)
            updated["motion_ms"] = _clamp("motion_ms", updated["motion_ms"] - 30.0)
            updated["glow_strength"] = _clamp(
                "glow_strength", updated["glow_strength"] - 0.05
            )
            changes.append(
                f"Performance first: glass blur {before:.0f}->{updated['blur_px']:.0f}px "
                f"and motion {updated['motion_ms']:.0f}ms to protect smoothness "
                f"(measured {fps:.0f} fps)."
            )
        if explain_rate > 0.20 or stuck > 0 or mobile_ratio > 0.5:
            updated["explain_glow"] = _clamp(
                "explain_glow", updated["explain_glow"] + 0.08
            )
            updated["font_scale"] = _clamp(
                "font_scale", updated["font_scale"] + 0.02
            )
            if stuck:
                changes.append(
                    f"Comprehension aid: stronger explainer glow because {stuck} sandbox "
                    "pause(s) were detected."
                )
            else:
                changes.append(
                    "Comprehension aid: explainer emphasis raised based on usage patterns."
                )
        if mobile_ratio > 0.35:
            updated["radius_px"] = _clamp("radius_px", updated["radius_px"] - 2)
            updated["pad_px"] = _clamp("pad_px", updated["pad_px"] - 2)
            changes.append("Responsive polish: tighter radii and padding for narrow viewports.")
        if 0 < mean_dwell < 1800:
            updated["chart_height_px"] = _clamp(
                "chart_height_px", updated["chart_height_px"] + 10
            )
            updated["border_alpha"] = _clamp(
                "border_alpha", updated["border_alpha"] + 0.03
            )
            changes.append(
                "Legibility: taller charts and stronger panel edges to slow fast scanning."
            )
        if max(signals.get("game_actions", {}).values() or [0]) >= 8:
            updated["glow_strength"] = _clamp(
                "glow_strength", updated["glow_strength"] + 0.04
            )
            changes.append("Sandbox focus: extra glow to keep active game feedback visible.")

    rng = random.Random(_seed(signals) + version)
    updated["accent_h"] = _clamp("accent_h", updated["accent_h"] + rng.randint(-3, 3))
    updated["accent2_h"] = _clamp(
        "accent2_h", updated["accent2_h"] + rng.randint(-2, 2)
    )
    if not changes:
        changes.append(
            "Subtle hue refinement and polish; no bottlenecks detected in the last window."
        )
    return updated, changes


# ---------------------------------------------------------------------------
# CSS rendering
# ---------------------------------------------------------------------------


def render_css(genes: Dict[str, float], version: int) -> str:
    return f"""/* SASK UI evolution v{version:03d} — generated locally by ui_evolver.py.
   Safe to delete; the interface falls back to ui/styles.css defaults. */
:root {{
  --accent-h: {genes['accent_h']:.1f};
  --accent2-h: {genes['accent2_h']:.1f};
  --glass-blur: {genes['blur_px']:.1f}px;
  --radius: {genes['radius_px']:.1f}px;
  --panel-pad: {genes['pad_px']:.1f}px;
  --motion: {genes['motion_ms']:.0f}ms;
  --font-scale: {genes['font_scale']:.3f};
  --border-alpha: {genes['border_alpha']:.3f};
  --chart-height: {genes['chart_height_px']:.1f}px;
  --explain-glow: {genes['explain_glow']:.3f};
  --glow-strength: {genes['glow_strength']:.3f};
}}

body.explain-mode .panel {{
  box-shadow:
    0 0 0 1px hsla(var(--accent-h), 90%, 72%, var(--explain-glow)),
    0 18px 60px hsl(var(--accent2-h) 80% 8% / 0.55);
}}

.panel.panel-hot {{
  border-color: hsla(var(--accent-h), 90%, 70%, calc(var(--border-alpha) + 0.18));
}}
"""


# ---------------------------------------------------------------------------
# Validation gates
# ---------------------------------------------------------------------------


def _strip_css_comments(text: str) -> str:
    return re.sub(r"/\*.*?\*/", "", text, flags=re.DOTALL)


def validate_css(text: str, mode: str = "genes") -> Dict[str, Any]:
    """Validate generated gene overrides (default) or a full stylesheet.

    ``mode="genes"`` enforces the bounded evolution contract: size limit,
    ``:root`` block, and only the allow-listed custom properties.
    ``mode="stylesheet"`` checks generic syntax for the hand-authored base
    stylesheet, which legitimately declares many other tokens.
    """
    errors: List[str] = []
    body = _strip_css_comments(text)
    if mode == "genes" and len(text) > 20000:
        errors.append("css: evolved stylesheet exceeds 20000 characters")
    if body.count("{") != body.count("}"):
        errors.append("css: unbalanced braces")
    if body.count("(") != body.count(")"):
        errors.append("css: unbalanced parentheses")
    lowered = body.lower()
    for forbidden in ("@import", "javascript:", "expression(", "</style"):
        if forbidden in lowered:
            errors.append(f"css: forbidden token {forbidden!r}")
    if mode == "genes" and body.strip():
        if ":root" not in body:
            errors.append("css: missing :root block")
        declared = set(re.findall(r"(--[a-z0-9-]+)\s*:", body))
        unknown = declared - _ALLOWED_VARS
        if unknown:
            errors.append(f"css: unexpected custom properties {sorted(unknown)}")
        if len(declared) < 6:
            errors.append("css: fewer than 6 custom properties declared")
        root_match = re.search(r":root\s*\{(.*?)\}", body, re.DOTALL)
        if root_match is None:
            errors.append("css: :root rule is not closed")
        else:
            for declaration in root_match.group(1).split(";"):
                if declaration.strip() and ":" not in declaration:
                    errors.append("css: malformed declaration in :root")
                    break
    return {"ok": not errors, "errors": errors, "check": "css-syntax"}


def validate_js(path: Optional[Path] = None) -> Dict[str, Any]:
    target = Path(path or APP_PATH)
    if not target.is_file():
        return {"ok": False, "errors": [f"js: missing {target.name}"], "check": "js-syntax"}
    node = shutil.which("node")
    if node is None:
        return {
            "ok": True,
            "errors": [],
            "check": "js-syntax",
            "note": "node unavailable; skipped --check",
        }
    try:
        completed = subprocess.run(
            [node, "--check", str(target)],
            capture_output=True,
            text=True,
            timeout=20,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"ok": False, "errors": [f"js: check failed to run: {exc}"], "check": "js-syntax"}
    errors = []
    if completed.returncode != 0:
        message = (completed.stderr or completed.stdout or "syntax error").strip()
        errors.append(f"js: {message[:400]}")
    return {"ok": not errors, "errors": errors, "check": "js-syntax"}


class _IdCollector(html.parser.HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.ids: List[str] = []

    def handle_starttag(self, tag: str, attrs: List[Tuple[str, Optional[str]]]) -> None:
        for name, value in attrs:
            if name == "id" and value:
                self.ids.append(value)


def validate_dom() -> Dict[str, Any]:
    errors: List[str] = []
    if not INDEX_PATH.is_file():
        return {"ok": False, "errors": ["dom: ui/index.html missing"], "check": "dom-integrity"}
    source = INDEX_PATH.read_text(encoding="utf-8")
    collector = _IdCollector()
    collector.feed(source)
    ids = collector.ids
    duplicates = sorted({item for item in ids if ids.count(item) > 1})
    if duplicates:
        errors.append(f"dom: duplicate ids {duplicates}")
    missing = sorted(_REQUIRED_IDS - set(ids))
    if missing:
        errors.append(f"dom: missing required ids {missing}")

    if 'id="evolved-styles"' not in source:
        errors.append("dom: evolved stylesheet link missing")
    for asset in ("/ui/styles.css", "/ui/app.js"):
        if asset not in source:
            errors.append(f"dom: asset reference missing {asset}")

    if not STYLES_PATH.is_file():
        errors.append("dom: ui/styles.css missing")
    else:
        styles = STYLES_PATH.read_text(encoding="utf-8")
        if ".panel" not in styles:
            errors.append("dom: styles.css does not style .panel")
        if "--accent-h" not in styles:
            errors.append("dom: styles.css is missing the --accent-h token")

    if APP_PATH.is_file():
        js = APP_PATH.read_text(encoding="utf-8")
        referenced = set(re.findall(r"\$\(\s*[\"']#([A-Za-z0-9_-]+)[\"']\s*\)", js))
        referenced.update(
            re.findall(r"getElementById\(\s*[\"']([A-Za-z0-9_-]+)[\"']\s*\)", js)
        )
        unknown = sorted(item for item in referenced if item not in set(ids))
        if unknown:
            errors.append(f"dom: app.js references missing ids {unknown}")
    else:
        errors.append("dom: ui/app.js missing")

    return {"ok": not errors, "errors": errors, "check": "dom-integrity", "ids": len(ids)}


# ---------------------------------------------------------------------------
# Log / backup management
# ---------------------------------------------------------------------------


def _load_log() -> Dict[str, Any]:
    payload = _safe_load(LOG_PATH, {})
    if isinstance(payload, list):
        return {"active_version": 0, "entries": payload}
    if not isinstance(payload, dict):
        return {"active_version": 0, "entries": []}
    entries = payload.get("entries")
    if not isinstance(entries, list):
        entries = []
    return {
        "active_version": int(payload.get("active_version", 0) or 0),
        "entries": [entry for entry in entries if isinstance(entry, dict)],
    }


def _save_log(log: Dict[str, Any]) -> None:
    _atomic_write(LOG_PATH, json.dumps(log, indent=2))


def _latest_genes(log: Dict[str, Any]) -> Dict[str, float]:
    for entry in reversed(log.get("entries", [])):
        genes = entry.get("genes")
        if isinstance(genes, dict) and entry.get("promoted") and entry.get("type") == "evolve":
            merged = dict(DEFAULT_GENES)
            for key in DEFAULT_GENES:
                try:
                    merged[key] = float(genes.get(key, DEFAULT_GENES[key]))
                except (TypeError, ValueError):
                    merged[key] = DEFAULT_GENES[key]
            return merged
    return dict(DEFAULT_GENES)


def _snapshot(version: int, genes: Dict[str, float], changes: List[str]) -> Path:
    target = BACKUP_DIR / f"v{version:03d}-{_utc_stamp()}"
    target.mkdir(parents=True, exist_ok=False)
    for source in (INDEX_PATH, STYLES_PATH, APP_PATH, EVOLVED_PATH):
        if source.is_file():
            shutil.copy2(source, target / source.name)
    manifest = {
        "version": version,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "genes": genes,
        "changes": changes,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return target


# ---------------------------------------------------------------------------
# Public engine
# ---------------------------------------------------------------------------


class UIEvolver:
    """Telemetry-driven UI/UX evolution with validation and rollback."""

    def __init__(self, root: Optional[Path] = None) -> None:
        self.root = Path(root) if root else ROOT

    def evolve(self) -> Dict[str, Any]:
        events = load_telemetry()
        signals = analyze(events)
        log = _load_log()
        version = max(
            [int(entry.get("version", 0) or 0) for entry in log["entries"]] or [0]
        ) + 1
        genes = _latest_genes(log)
        proposed, changes = propose(genes, signals, version)
        css_text = render_css(proposed, version)

        css_check = validate_css(css_text)
        js_check = validate_js()
        dom_check = validate_dom()
        validation = {
            "css": css_check,
            "js": js_check,
            "dom": dom_check,
            "ok": all(check["ok"] for check in (css_check, js_check, dom_check)),
        }

        base_entry: Dict[str, Any] = {
            "version": version,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "type": "evolve",
            "genes": proposed,
            "changes": changes,
            "signals": {
                key: signals.get(key)
                for key in (
                    "events",
                    "explain_rate",
                    "game_stuck",
                    "fps_avg",
                    "mobile_ratio",
                    "mean_dwell_ms",
                    "top_panels",
                    "confusion_panels",
                )
            },
            "validation": validation,
        }

        if not validation["ok"]:
            base_entry["promoted"] = False
            base_entry["summary"] = "Rejected: validation gate failed; UI left unchanged."
            log["entries"].append(base_entry)
            _save_log(log)
            return {
                "promoted": False,
                "version": version,
                "changes": changes,
                "validation": validation,
                "signals": base_entry["signals"],
            }

        backup = _snapshot(version, genes, changes)
        _assert_ui_path(EVOLVED_PATH)
        _atomic_write(EVOLVED_PATH, css_text)

        base_entry["promoted"] = True
        base_entry["backup"] = str(backup.relative_to(self.root))
        base_entry["summary"] = " ".join(changes)[:280]
        log["entries"].append(base_entry)
        log["active_version"] = version
        log["entries"] = log["entries"][-200:]
        _save_log(log)
        return {
            "promoted": True,
            "version": version,
            "genes": proposed,
            "changes": changes,
            "validation": validation,
            "backup": str(backup.relative_to(self.root)),
            "signals": base_entry["signals"],
        }

    def revert(self, version: Optional[int] = None) -> Dict[str, Any]:
        log = _load_log()
        entries = log.get("entries", [])
        if not entries:
            return {"reverted": False, "error": "no UI evolution history"}

        target_version = (
            int(version)
            if version is not None
            else int(log.get("active_version", 0) or 0)
        )
        target_entry = next(
            (
                entry
                for entry in reversed(entries)
                if entry.get("type") == "evolve"
                and int(entry.get("version", 0) or 0) == target_version
                and entry.get("backup")
            ),
            None,
        )
        if target_entry is None:
            return {
                "reverted": False,
                "error": f"no snapshot found for version {target_version}",
            }

        backup = (self.root / str(target_entry["backup"])).resolve()
        if not backup.is_dir() or BACKUP_DIR.resolve() not in backup.parents:
            return {"reverted": False, "error": "backup path is not a valid UI snapshot"}

        for name in ("index.html", "styles.css", "app.js", "evolved.css"):
            source = backup / name
            if not source.is_file():
                continue
            destination = (UI_DIR / name).resolve()
            _assert_ui_path(destination)
            shutil.copy2(source, destination)

        css_text = EVOLVED_PATH.read_text(encoding="utf-8") if EVOLVED_PATH.is_file() else ""
        validation = {
            "css": validate_css(css_text),
            "js": validate_js(),
            "dom": validate_dom(),
        }
        validation["ok"] = all(check["ok"] for check in validation.values() if isinstance(check, dict))

        next_version = max(int(entry.get("version", 0) or 0) for entry in entries) + 1
        for entry in entries:
            if (
                entry.get("type") == "evolve"
                and int(entry.get("version", 0) or 0) > target_version
            ):
                entry["reverted"] = True
        log["entries"].append(
            {
                "version": next_version,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "type": "revert",
                "target_version": target_version,
                "promoted": bool(validation["ok"]),
                "validation": validation,
                "summary": f"Reverted UI evolution to version {target_version}.",
            }
        )
        log["active_version"] = target_version
        _save_log(log)
        return {
            "reverted": True,
            "version": target_version,
            "restored_from": target_entry["backup"],
            "validation": validation,
        }

    def status(self) -> Dict[str, Any]:
        log = _load_log()
        entries = log.get("entries", [])
        compact = []
        for entry in entries[-15:]:
            compact.append(
                {
                    key: entry.get(key)
                    for key in (
                        "version",
                        "timestamp",
                        "type",
                        "promoted",
                        "summary",
                        "changes",
                        "target_version",
                        "reverted",
                    )
                }
            )
        return {
            "available": True,
            "active_version": log.get("active_version", 0),
            "evolutions": sum(1 for entry in entries if entry.get("type") == "evolve"),
            "entries": compact,
            "backups": sorted(
                path.name for path in BACKUP_DIR.glob("v*") if path.is_dir()
            )[-10:],
            "current_genes": _latest_genes(log),
            "telemetry_events": len(load_telemetry()),
        }


def _main() -> int:
    import argparse

    parser = argparse.ArgumentParser(description="SASK self-evolving UI engine")
    parser.add_argument("--evolve", action="store_true", help="run one evolution cycle")
    parser.add_argument("--revert", type=int, default=None, metavar="VERSION")
    parser.add_argument("--status", action="store_true")
    args = parser.parse_args()

    engine = UIEvolver()
    if args.revert is not None:
        print(json.dumps(engine.revert(args.revert), indent=2, default=str))
        return 0
    if args.evolve:
        print(json.dumps(engine.evolve(), indent=2, default=str))
        return 0
    print(json.dumps(engine.status(), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
