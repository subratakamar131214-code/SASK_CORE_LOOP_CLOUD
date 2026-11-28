"""Dynamic compute meta-control for SASK v2.0.

``PortfolioAllocator`` splits a slot budget across exploitation, cross-domain
bridging, and high-variance exploration using an upper-confidence-bound score
seeded from observed yields, and prunes branches whose measured yield stays
below a floor after enough pulls.

``ProviderMesh`` rotates across zero-cost endpoints (local Ollama/llama.cpp
first, then free-tier cloud APIs) with health checks, exponential backoff, and
permanent retirement of deprecated endpoints. When every remote candidate is
unavailable it falls back to an offline distilled-rule provider, so execution
never hard-stops. Successful cloud answers are distilled into the encrypted
knowledge store for later offline reuse. API keys are read from the
environment at call time and never written or logged.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from .knowledge import KnowledgeStore

DEFAULT_LANES: Tuple[str, ...] = ("exploit", "bridge", "explore")


class ProviderError(RuntimeError):
    """Categorized provider failure used for rotation decisions."""

    def __init__(self, category: str, detail: str = "") -> None:
        self.category = category
        self.detail = detail
        super().__init__(f"{category}: {detail}"[:280])


class Provider:
    name = "provider"
    kind = "local"  # local | cloud | offline

    def available(self) -> bool:
        return True

    def complete(self, prompt: str, timeout: float = 30.0) -> str:  # pragma: no cover - interface
        raise NotImplementedError


class OfflineRulesProvider(Provider):
    """Deterministic last-resort provider backed by distilled rules."""

    name = "offline-rules"
    kind = "offline"

    def __init__(self, store: Optional[KnowledgeStore] = None) -> None:
        self.store = store

    def complete(self, prompt: str, timeout: float = 30.0) -> str:
        if self.store is not None:
            recalled = self.store.recall_distilled(prompt)
            if recalled is not None:
                return recalled
        digest = hashlib.sha256(str(prompt).encode("utf-8")).hexdigest()
        return f"[offline:{digest[:16]}]"


class OllamaProvider(Provider):
    name = "ollama"
    kind = "local"

    def __init__(
        self,
        model: str = "llama3.2",
        base_url: str = "http://127.0.0.1:11434",
        transport: Optional[Callable[[str, bytes, float], bytes]] = None,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.transport = transport

    def complete(self, prompt: str, timeout: float = 60.0) -> str:
        payload = json.dumps({"model": self.model, "prompt": prompt, "stream": False}).encode("utf-8")
        body = self._post(f"{self.base_url}/api/generate", payload, {}, timeout)
        try:
            return str(json.loads(body)["response"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ProviderError("schema", "ollama response missing 'response'") from exc

    def _post(self, url: str, payload: bytes, headers: Dict[str, str], timeout: float) -> bytes:
        if self.transport is not None:
            return self.transport(url, payload, timeout)
        request = urllib.request.Request(url, data=payload, headers={"Content-Type": "application/json", **headers})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed localhost
                return response.read()
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            raise ProviderError("connection", type(exc).__name__) from exc


class OpenAICompatProvider(Provider):
    """Adapter for OpenAI-compatible free-tier endpoints (Groq, OpenRouter, ...)."""

    kind = "cloud"

    def __init__(
        self,
        name: str,
        base_url: str,
        env_key: str,
        model: str,
        transport: Optional[Callable[[str, bytes, Dict[str, str], float], bytes]] = None,
    ) -> None:
        self.name = name
        self.base_url = base_url.rstrip("/")
        self.env_key = env_key
        self.model = model
        self.transport = transport

    def available(self) -> bool:
        return bool(os.environ.get(self.env_key))

    def complete(self, prompt: str, timeout: float = 60.0) -> str:
        key = os.environ.get(self.env_key)
        if not key:
            raise ProviderError("unavailable", f"missing {self.env_key}")
        payload = json.dumps(
            {"model": self.model, "messages": [{"role": "user", "content": str(prompt)}]}
        ).encode("utf-8")
        headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
        if self.transport is not None:
            body = self.transport(f"{self.base_url}/chat/completions", payload, headers, timeout)
        else:
            request = urllib.request.Request(f"{self.base_url}/chat/completions", data=payload, headers=headers)
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed https base
                    body = response.read()
            except urllib.error.HTTPError as exc:
                if exc.code == 429:
                    raise ProviderError("rate_limit", "429") from exc
                if exc.code in (404, 410):
                    raise ProviderError("deprecated", str(exc.code)) from exc
                raise ProviderError("http", str(exc.code)) from exc
            except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
                raise ProviderError("connection", type(exc).__name__) from exc
        try:
            parsed = json.loads(body)
            return str(parsed["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            raise ProviderError("schema", "chat completion shape changed") from exc


class ProviderMesh:
    """Health-checked rotation with backoff, retirement, and offline fallback."""

    def __init__(
        self,
        providers: Sequence[Provider],
        store: Optional[KnowledgeStore] = None,
        state_path: Optional[Path | str] = None,
        base_backoff: float = 2.0,
        max_backoff: float = 300.0,
        clock: Callable[[], float] = time.monotonic,
        distill: bool = True,
    ) -> None:
        self.providers = list(providers)
        self.offline = next((item for item in self.providers if item.kind == "offline"), None)
        if self.offline is None:
            self.offline = OfflineRulesProvider(store)
        self.store = store
        self.state_path = Path(state_path) if state_path is not None else None
        self.base_backoff = max(0.01, float(base_backoff))
        self.max_backoff = max(self.base_backoff, float(max_backoff))
        self.clock = clock
        self.distill = bool(distill)
        self._health: Dict[str, Dict[str, Any]] = {
            provider.name: {"fails": 0, "cooldown_until": 0.0, "deprecated": False, "last_error": ""}
            for provider in self.providers
        }
        self._load()

    # -- persistence -------------------------------------------------------

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            payload = json.loads(self.state_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        for name, record in (payload.get("providers") or {}).items():
            if name in self._health and isinstance(record, dict):
                self._health[name].update(
                    {
                        "fails": int(record.get("fails", 0) or 0),
                        "cooldown_until": float(record.get("cooldown_until", 0.0) or 0.0),
                        "deprecated": bool(record.get("deprecated")),
                        "last_error": str(record.get("last_error") or ""),
                    }
                )

    def _save(self) -> None:
        if self.state_path is None:
            return
        try:
            self.state_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_path.with_name(f".{self.state_path.name}.{os.getpid()}.tmp")
            tmp.write_text(json.dumps({"providers": self._health}, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    # -- completion --------------------------------------------------------

    def complete(self, prompt: str, timeout: float = 60.0) -> Dict[str, Any]:
        now = self.clock()
        attempts: List[Dict[str, Any]] = []
        for provider in self.providers:
            if provider.kind == "offline":
                continue
            health = self._health.setdefault(
                provider.name,
                {"fails": 0, "cooldown_until": 0.0, "deprecated": False, "last_error": ""},
            )
            if health["deprecated"]:
                attempts.append({"provider": provider.name, "status": "retired"})
                continue
            if float(health["cooldown_until"]) > now:
                attempts.append({"provider": provider.name, "status": "cooldown"})
                continue
            if not provider.available():
                attempts.append({"provider": provider.name, "status": "unavailable"})
                continue
            try:
                text = provider.complete(prompt, timeout)
            except ProviderError as exc:
                health["fails"] = int(health["fails"]) + 1
                health["last_error"] = exc.category
                if exc.category == "deprecated":
                    health["deprecated"] = True
                else:
                    backoff = min(self.max_backoff, self.base_backoff * (2 ** (health["fails"] - 1)))
                    health["cooldown_until"] = now + backoff
                attempts.append({"provider": provider.name, "status": "error", "category": exc.category})
                continue
            health.update({"fails": 0, "cooldown_until": 0.0, "last_error": ""})
            if self.distill and self.store is not None and provider.kind == "cloud":
                try:
                    self.store.distill(prompt, text)
                except Exception:
                    pass
            self._save()
            return {"ok": True, "text": text, "provider": provider.name, "fallback": False, "attempts": attempts}
        try:
            text = self.offline.complete(prompt, timeout)
            ok = True
            error = ""
        except Exception as exc:  # pragma: no cover - offline provider should not raise
            text = ""
            ok = False
            error = type(exc).__name__
        self._save()
        return {
            "ok": ok,
            "text": text,
            "provider": self.offline.name,
            "fallback": True,
            "attempts": attempts,
            "error": error,
        }

    def health(self) -> Dict[str, Any]:
        now = self.clock()
        rows: Dict[str, Any] = {}
        for provider in self.providers:
            state = self._health.get(provider.name, {})
            if state.get("deprecated"):
                score = 0.0
            elif float(state.get("cooldown_until", 0.0) or 0.0) > now:
                score = 0.2
            else:
                score = round(1.0 / (1.0 + int(state.get("fails", 0) or 0)), 4)
            rows[provider.name] = {
                "kind": provider.kind,
                "available": bool(provider.available()),
                "score": score,
                "fails": int(state.get("fails", 0) or 0),
                "deprecated": bool(state.get("deprecated")),
                "cooldown_remaining": max(0.0, round(float(state.get("cooldown_until", 0.0) or 0.0) - now, 3)),
                "last_error": str(state.get("last_error") or ""),
            }
        return rows


class PortfolioAllocator:
    """UCB1 slot allocation with predictive pruning of low-yield lanes."""

    def __init__(
        self,
        lanes: Sequence[str] = DEFAULT_LANES,
        min_yield: float = 0.05,
        prune_after: int = 5,
    ) -> None:
        self.lanes = list(lanes)
        self.min_yield = float(min_yield)
        self.prune_after = max(1, int(prune_after))
        self.stats: Dict[str, Dict[str, float]] = {
            lane: {"pulls": 0.0, "reward": 0.0, "cost": 0.0} for lane in self.lanes
        }

    def record(self, lane: str, reward: float, cost: float = 1.0) -> Dict[str, Any]:
        if lane not in self.stats:
            self.lanes.append(lane)
            self.stats[lane] = {"pulls": 0.0, "reward": 0.0, "cost": 0.0}
        stats = self.stats[lane]
        stats["pulls"] += 1.0
        stats["reward"] += max(0.0, float(reward))
        stats["cost"] += max(1e-6, float(cost))
        return dict(stats)

    def mean_yield(self, lane: str) -> float:
        stats = self.stats.get(lane)
        if not stats or stats["pulls"] <= 0:
            return 0.0
        return stats["reward"] / stats["cost"]

    def is_pruned(self, lane: str) -> bool:
        stats = self.stats[lane]
        return stats["pulls"] >= self.prune_after and self.mean_yield(lane) < self.min_yield

    def _ucb(self, lane: str) -> float:
        stats = self.stats[lane]
        total = sum(item["pulls"] for item in self.stats.values()) or 1.0
        if stats["pulls"] <= 0:
            return float("inf")
        mean = self.mean_yield(lane)
        bonus = math.sqrt(2.0 * math.log(total + 1.0) / stats["pulls"])
        return mean + bonus

    def allocate(self, slots: int) -> Dict[str, int]:
        slots = max(0, int(slots))
        allocation = {lane: 0 for lane in self.lanes}
        active = [lane for lane in self.lanes if not self.is_pruned(lane)] or list(self.lanes)
        if slots == 0 or not active:
            return allocation
        for lane in active:
            if slots <= 0:
                break
            allocation[lane] += 1
            slots -= 1
        while slots > 0:
            lane = max(active, key=lambda name: (self._ucb(name), -self.lanes.index(name)))
            allocation[lane] += 1
            slots -= 1
        return allocation

    def weights(self) -> Dict[str, float]:
        scores = {lane: (0.0 if self.is_pruned(lane) else self.mean_yield(lane)) for lane in self.lanes}
        total = sum(scores.values())
        if total <= 0:
            return {lane: round(1.0 / len(self.lanes), 4) for lane in self.lanes}
        return {lane: round(score / total, 4) for lane, score in scores.items()}

    def status(self) -> Dict[str, Any]:
        return {
            "lanes": {
                lane: {
                    "pulls": int(self.stats[lane]["pulls"]),
                    "mean_yield": round(self.mean_yield(lane), 4),
                    "pruned": self.is_pruned(lane),
                    "weight": self.weights()[lane],
                }
                for lane in self.lanes
            },
            "min_yield": self.min_yield,
            "prune_after": self.prune_after,
        }
