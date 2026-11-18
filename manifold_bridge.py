"""Cross-Domain Analogical Manifold Bridge.

Embeds concrete structures from compiler theory, abstract algebra, distributed
network topologies, and physical simulations into a shared topological space.
Structures are canonicalized with Weisfeiler-Lehman refinement, summarized by a
bounded spectral/invariant embedding, and compared for structural isomorphism.
When an isomorphism is found, a solution registered on one structure can be
transferred to its analogue in another domain.

The bridge is deterministic and dependency-free: no graph library is required,
and spectral coordinates are approximated with deflated power iteration.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Tuple

logger = logging.getLogger("SASK.ManifoldBridge")

_EPSILON = 1e-9

DOMAINS: Tuple[str, ...] = (
    "compiler_theory",
    "abstract_algebra",
    "distributed_networks",
    "physical_simulation",
)

DEFAULT_SOLUTIONS: List[Dict[str, Any]] = [
    {
        "name": "cycle_decomposition",
        "approach": "Decompose the structure into independent cycles and process each cycle with a single linear sweep, reusing the invariant state at the wrap point.",
        "invariants_used": ["cycle_rank", "degree_regularity"],
    },
    {
        "name": "lattice_sweep",
        "approximation": "lattice",
        "approach": "Order nodes along rows and columns, then apply alternating forward/backward sweeps so every dependency is satisfied in at most two passes.",
        "invariants_used": ["bipartite_balance", "degree_distribution"],
    },
    {
        "name": "dominator_collapse",
        "approach": "Collapse strongly dominated regions into single super-nodes before analysis; the reduced structure preserves all reachability invariants.",
        "invariants_used": ["reachability_depth", "component_count"],
    },
]

_MAX_STRUCTURES = 256
_MAX_TRANSFERS = 128


def _hash_unit(*parts: Any) -> float:
    digest = hashlib.blake2b(
        "\x1f".join(str(part) for part in parts).encode("utf-8"),
        digest_size=8,
    ).digest()
    return int.from_bytes(digest, "big") / float(1 << 64)


def _label_hash(values: Sequence[str]) -> str:
    payload = "|".join(sorted(values)).encode("utf-8")
    return hashlib.blake2b(payload, digest_size=8).hexdigest()


@dataclass
class DomainStructure:
    """A labeled graph drawn from one scientific domain."""

    id: str
    domain: str
    name: str
    nodes: List[str]
    edges: List[Tuple[str, str]]
    node_labels: Dict[str, str] = field(default_factory=dict)
    metadata: Dict[str, Any] = field(default_factory=dict)
    created_at: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def adjacency(self) -> Dict[str, List[str]]:
        adj: Dict[str, List[str]] = {node: [] for node in self.nodes}
        for source, target in self.edges:
            if source not in adj or target not in adj:
                continue
            if target not in adj[source]:
                adj[source].append(target)
            if source not in adj[target]:
                adj[target].append(source)
        for values in adj.values():
            values.sort()
        return adj

    def degrees(self) -> List[int]:
        return [len(values) for values in self.adjacency().values()]

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "domain": self.domain,
            "name": self.name,
            "nodes": list(self.nodes),
            "edges": [list(edge) for edge in self.edges],
            "node_labels": dict(self.node_labels),
            "metadata": dict(self.metadata),
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "DomainStructure":
        edges = []
        for edge in data.get("edges") or []:
            if isinstance(edge, (list, tuple)) and len(edge) == 2:
                edges.append((str(edge[0]), str(edge[1])))
        return cls(
            id=str(data.get("id", "")),
            domain=str(data.get("domain", "compiler_theory")),
            name=str(data.get("name", "")),
            nodes=[str(node) for node in data.get("nodes") or []],
            edges=edges,
            node_labels={
                str(key): str(value)
                for key, value in (data.get("node_labels") or {}).items()
            },
            metadata=dict(data.get("metadata") or {}),
            created_at=str(
                data.get("created_at", datetime.now(timezone.utc).isoformat())
            ),
        )


class AnalogicalManifoldBridge:
    """Shared topological embedding space with isomorphism-aware transfer."""

    def __init__(
        self,
        state_path: Optional[Path] = None,
        wl_rounds: int = 3,
        similarity_threshold: float = 0.92,
    ) -> None:
        self.state_path = Path(state_path) if state_path else None
        self.wl_rounds = max(1, int(wl_rounds))
        self.similarity_threshold = max(0.0, min(1.0, float(similarity_threshold)))
        self._lock = threading.RLock()
        self._structures: Dict[str, DomainStructure] = {}
        self._solutions: Dict[str, List[Dict[str, Any]]] = {}
        self._transfers: List[Dict[str, Any]] = []
        self._comparisons = 0
        self._isomorphisms = 0
        self._seed_exemplars()
        self._load()

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def _seed_exemplars(self) -> None:
        ring = [f"z{i}" for i in range(6)]
        ring_edges = [(ring[i], ring[(i + 1) % 6]) for i in range(6)]
        self.register_structure(
            DomainStructure(
                id="alg-cayley-z6",
                domain="abstract_algebra",
                name="Cayley graph of Z6",
                nodes=list(ring),
                edges=ring_edges,
                metadata={"group": "Z6", "generator": 1},
            )
        )

        net_ring = [f"n{i}" for i in range(6)]
        self.register_structure(
            DomainStructure(
                id="net-ring-6",
                domain="distributed_networks",
                name="Six-node ring topology",
                nodes=list(net_ring),
                edges=[(net_ring[i], net_ring[(i + 1) % 6]) for i in range(6)],
                metadata={"topology": "ring", "diameter": 3},
            )
        )

        lattice = [f"p{r}{c}" for r in range(2) for c in range(3)]
        lattice_edges: List[Tuple[str, str]] = []
        for row in range(2):
            for col in range(3):
                node = f"p{row}{col}"
                if col + 1 < 3:
                    lattice_edges.append((node, f"p{row}{col + 1}"))
                if row + 1 < 2:
                    lattice_edges.append((node, f"p{row + 1}{col}"))
        self.register_structure(
            DomainStructure(
                id="phys-lattice-2x3",
                domain="physical_simulation",
                name="2x3 periodic lattice patch",
                nodes=list(lattice),
                edges=lattice_edges,
                metadata={"lattice": "square", "shape": [2, 3]},
            )
        )

        mesh = [f"m{r}{c}" for r in range(2) for c in range(3)]
        mesh_edges: List[Tuple[str, str]] = []
        for row in range(2):
            for col in range(3):
                node = f"m{row}{col}"
                if col + 1 < 3:
                    mesh_edges.append((node, f"m{row}{col + 1}"))
                if row + 1 < 2:
                    mesh_edges.append((node, f"m{row + 1}{col}"))
        self.register_structure(
            DomainStructure(
                id="net-mesh-2x3",
                domain="distributed_networks",
                name="2x3 mesh network",
                nodes=list(mesh),
                edges=mesh_edges,
                metadata={"topology": "mesh", "shape": [2, 3]},
            )
        )

        cfg_nodes = ["entry", "branch", "then", "else", "merge", "exit"]
        cfg_edges = [
            ("entry", "branch"),
            ("branch", "then"),
            ("branch", "else"),
            ("then", "merge"),
            ("else", "merge"),
            ("merge", "exit"),
        ]
        self.register_structure(
            DomainStructure(
                id="comp-cfg-diamond",
                domain="compiler_theory",
                name="Diamond control-flow graph",
                nodes=cfg_nodes,
                edges=cfg_edges,
                metadata={"form": "diamond", "join": "merge"},
            )
        )

        # Seed a canonical solution on each exemplar family.
        self.register_solution("alg-cayley-z6", dict(DEFAULT_SOLUTIONS[0]))
        self.register_solution("net-ring-6", dict(DEFAULT_SOLUTIONS[0]))
        self.register_solution("phys-lattice-2x3", dict(DEFAULT_SOLUTIONS[1]))
        self.register_solution("net-mesh-2x3", dict(DEFAULT_SOLUTIONS[1]))
        self.register_solution("comp-cfg-diamond", dict(DEFAULT_SOLUTIONS[2]))

    def register_structure(
        self,
        structure: DomainStructure,
        overwrite: bool = False,
    ) -> Dict[str, Any]:
        """Register a structure; recomputes its canonical signature and embedding."""
        with self._lock:
            if structure.id in self._structures and not overwrite:
                return {
                    "registered": False,
                    "reason": "exists",
                    "id": structure.id,
                }
            self._structures[structure.id] = structure
            if len(self._structures) > _MAX_STRUCTURES:
                oldest = min(
                    self._structures.values(),
                    key=lambda item: item.created_at,
                )
                self._structures.pop(oldest.id, None)
            profile = self._profile(structure)
            return {
                "registered": True,
                "id": structure.id,
                "domain": structure.domain,
                "wl_signature": profile["wl_signature"],
                "embedding": profile["embedding"],
            }

    def build_structure(
        self,
        structure_id: str,
        domain: str,
        name: str,
        nodes: Iterable[str],
        edges: Iterable[Sequence[str]],
        **metadata: Any,
    ) -> DomainStructure:
        """Convenience constructor that registers a structure immediately."""
        structure = DomainStructure(
            id=structure_id,
            domain=domain if domain in DOMAINS else "compiler_theory",
            name=name,
            nodes=list(dict.fromkeys(str(node) for node in nodes)),
            edges=[
                (str(edge[0]), str(edge[1]))
                for edge in edges
                if len(edge) >= 2
            ],
            metadata=dict(metadata),
        )
        self.register_structure(structure, overwrite=True)
        return structure

    def register_solution(self, structure_id: str, solution: Dict[str, Any]) -> bool:
        """Attach a transferable solution to a structure."""
        with self._lock:
            if structure_id not in self._structures:
                return False
            record = dict(solution)
            record.setdefault(
                "registered_at", datetime.now(timezone.utc).isoformat()
            )
            self._solutions.setdefault(structure_id, []).append(record)
            return True

    # ------------------------------------------------------------------
    # Topological profile
    # ------------------------------------------------------------------

    def _wl_signature(self, structure: DomainStructure) -> str:
        adjacency = structure.adjacency()
        labels = {
            node: _label_hash([structure.node_labels.get(node, "node")])
            for node in adjacency
        }
        counts = [1]
        for _ in range(self.wl_rounds):
            updated = {}
            for node, neighbors in adjacency.items():
                updated[node] = _label_hash(
                    [labels[node]] + [labels[other] for other in neighbors]
                )
            labels = updated
            counts.append(len(set(labels.values())))
        ordered = sorted(labels.values())
        return _label_hash(
            [str(len(adjacency))] + [str(count) for count in counts] + ordered
        )

    def _components(self, adjacency: Dict[str, List[str]]) -> int:
        seen: set = set()
        components = 0
        for start in sorted(adjacency):
            if start in seen:
                continue
            components += 1
            stack = [start]
            while stack:
                node = stack.pop()
                if node in seen:
                    continue
                seen.add(node)
                stack.extend(adjacency.get(node, []))
        return components

    def _triangles(self, adjacency: Dict[str, List[str]]) -> int:
        count = 0
        for node, neighbors in adjacency.items():
            neighbor_set = set(neighbors)
            for other in neighbors:
                count += len(neighbor_set & set(adjacency.get(other, [])))
        return count // 6

    def _diameter(self, adjacency: Dict[str, List[str]]) -> int:
        if not adjacency:
            return 0
        best = 0
        for start in adjacency:
            distances = {start: 0}
            frontier = [start]
            while frontier:
                nxt: List[str] = []
                for node in frontier:
                    for neighbor in adjacency.get(node, []):
                        if neighbor not in distances:
                            distances[neighbor] = distances[node] + 1
                            nxt.append(neighbor)
                frontier = nxt
            if distances:
                best = max(best, max(distances.values()))
        return best

    @staticmethod
    def _normalize(vector: List[float]) -> List[float]:
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [value / norm for value in vector]

    @staticmethod
    def _matvec(
        adjacency: Dict[str, List[str]],
        order: List[str],
        vector: List[float],
    ) -> List[float]:
        result = [0.0] * len(order)
        for index, node in enumerate(order):
            total = 0.0
            for neighbor in adjacency.get(node, []):
                total += vector[order.index(neighbor)]
            degree = max(1, len(adjacency.get(node, [])))
            result[index] = total / degree
        return result

    def _spectral_summary(
        self,
        adjacency: Dict[str, List[str]],
        order: List[str],
    ) -> List[float]:
        size = len(order)
        if size == 0:
            return [0.0, 0.0, 0.0]
        eigenvalues: List[float] = []
        previous: List[List[float]] = []
        for index in range(3):
            vector = self._normalize(
                [0.5 + _hash_unit(order[k], index) for k in range(size)]
            )
            for _ in range(40):
                transformed = self._matvec(adjacency, order, vector)
                for basis in previous:
                    dot = sum(a * b for a, b in zip(transformed, basis))
                    transformed = [
                        value - dot * basis_value
                        for value, basis_value in zip(transformed, basis)
                    ]
                vector = self._normalize(transformed)
            transformed = self._matvec(adjacency, order, vector)
            rayleigh = sum(a * b for a, b in zip(vector, transformed))
            eigenvalues.append(round(rayleigh, 6))
            previous.append(vector)
        return eigenvalues

    def _embedding(self, structure: DomainStructure) -> List[float]:
        adjacency = structure.adjacency()
        degrees = [len(neighbors) for neighbors in adjacency.values()] or [0]
        size = max(1, len(adjacency))
        edge_count = sum(degrees) / 2.0
        mean_degree = sum(degrees) / float(size)
        variance = sum((degree - mean_degree) ** 2 for degree in degrees) / float(size)
        density = (2.0 * edge_count) / (size * (size - 1)) if size > 1 else 0.0
        components = self._components(adjacency)
        triangles = self._triangles(adjacency)
        max_triangles = max(1.0, size * (size - 1) * (size - 2) / 6.0)
        clustering = triangles / max_triangles
        diameter = self._diameter(adjacency)
        spectral = self._spectral_summary(adjacency, sorted(adjacency))
        return [
            round(size / 16.0, 6),
            round(min(1.0, density), 6),
            round(min(1.0, mean_degree / 8.0), 6),
            round(min(1.0, variance / 16.0), 6),
            round(components / max(1.0, size), 6),
            round(min(1.0, clustering * 4.0), 6),
            round(min(1.0, diameter / 16.0), 6),
            round(spectral[0], 6),
            round(spectral[1], 6),
            round(spectral[2], 6),
        ]

    def _profile(self, structure: DomainStructure) -> Dict[str, Any]:
        embedding = self._embedding(structure)
        return {
            "wl_signature": self._wl_signature(structure),
            "embedding": embedding,
        }

    @staticmethod
    def _distance(left: Sequence[float], right: Sequence[float]) -> float:
        return math.sqrt(sum((a - b) ** 2 for a, b in zip(left, right)))

    # ------------------------------------------------------------------
    # Isomorphism discovery
    # ------------------------------------------------------------------

    def find_isomorphisms(
        self,
        structure_id: Optional[str] = None,
        cross_domain_only: bool = False,
        min_similarity: Optional[float] = None,
        include_self: bool = False,
    ) -> List[Dict[str, Any]]:
        """Compare structures in the shared embedding space."""
        threshold = (
            self.similarity_threshold
            if min_similarity is None
            else max(0.0, min(1.0, float(min_similarity)))
        )
        with self._lock:
            if structure_id is not None:
                source = self._structures.get(structure_id)
                if source is None:
                    return []
                candidates = list(self._structures.values())
            else:
                candidates = list(self._structures.values())
                source = None

            profiles = {
                item.id: self._profile(item) for item in candidates
            }
            matches: List[Dict[str, Any]] = []
            if source is not None:
                left_items = [source]
            else:
                left_items = candidates

            for left in left_items:
                left_profile = profiles[left.id]
                for right in candidates:
                    if left.id == right.id and not include_self:
                        continue
                    if right.id == left.id:
                        continue
                    if cross_domain_only and left.domain == right.domain:
                        continue
                    right_profile = profiles[right.id]
                    self._comparisons += 1
                    distance = self._distance(
                        left_profile["embedding"], right_profile["embedding"]
                    )
                    similarity = round(1.0 / (1.0 + distance), 6)
                    same_wl = (
                        left_profile["wl_signature"]
                        == right_profile["wl_signature"]
                        and len(left.nodes) == len(right.nodes)
                    )
                    if same_wl:
                        similarity = 1.0
                    if not same_wl and similarity < threshold:
                        continue
                    matches.append(
                        {
                            "source": left.id,
                            "source_domain": left.domain,
                            "target": right.id,
                            "target_domain": right.domain,
                            "similarity": similarity,
                            "structural_isomorphism": same_wl,
                            "embedding_distance": round(distance, 6),
                            "source_embedding": left_profile["embedding"],
                            "target_embedding": right_profile["embedding"],
                            "source_wl": left_profile["wl_signature"],
                            "target_wl": right_profile["wl_signature"],
                        }
                    )

            matches.sort(
                key=lambda item: (
                    item["structural_isomorphism"],
                    item["similarity"],
                ),
                reverse=True,
            )
            self._isomorphisms += sum(
                1 for item in matches if item["structural_isomorphism"]
            )
            return matches

    # ------------------------------------------------------------------
    # Solution transfer
    # ------------------------------------------------------------------

    def _bfs_order(self, structure: DomainStructure) -> List[str]:
        adjacency = structure.adjacency()
        if not adjacency:
            return []
        start = min(
            adjacency,
            key=lambda node: (len(adjacency[node]), node),
        )
        order: List[str] = []
        seen = {start}
        queue = [start]
        while queue:
            node = queue.pop(0)
            order.append(node)
            for neighbor in adjacency.get(node, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    queue.append(neighbor)
        for node in sorted(adjacency):
            if node not in seen:
                order.append(node)
                seen.add(node)
        return order

    def transfer_solution(
        self,
        source_id: str,
        target_id: str,
        solution_index: int = 0,
    ) -> Dict[str, Any]:
        """Map a registered solution from one structure onto its analogue."""
        with self._lock:
            source = self._structures.get(source_id)
            target = self._structures.get(target_id)
            solutions = self._solutions.get(source_id) or []
            if source is None or target is None:
                return {
                    "transferred": False,
                    "reason": "unknown_structure",
                    "source": source_id,
                    "target": target_id,
                }
            if not solutions:
                return {
                    "transferred": False,
                    "reason": "no_solution_registered",
                    "source": source_id,
                    "target": target_id,
                }
            solution = solutions[max(0, min(len(solutions) - 1, int(solution_index)))]
            source_profile = self._profile(source)
            target_profile = self._profile(target)
            same_wl = source_profile["wl_signature"] == target_profile["wl_signature"]
            distance = self._distance(
                source_profile["embedding"], target_profile["embedding"]
            )
            similarity = 1.0 if same_wl else round(1.0 / (1.0 + distance), 6)

            source_order = self._bfs_order(source)
            target_order = self._bfs_order(target)
            mapping = {
                source_node: target_order[index]
                for index, source_node in enumerate(source_order)
                if index < len(target_order)
            }
            record = {
                "source": source_id,
                "target": target_id,
                "source_domain": source.domain,
                "target_domain": target.domain,
                "solution": solution.get("name", "unnamed"),
                "approach": solution.get("approach", ""),
                "structural_isomorphism": same_wl,
                "similarity": similarity,
                "confidence": round(
                    similarity * (1.0 if same_wl else 0.8), 6
                ),
                "node_mapping": mapping,
                "transferred": True,
                "directive": (
                    f"Transfer '{solution.get('name', 'solution')}' from {source.domain} "
                    f"({source.name}) to {target.domain} ({target.name}) via mapping "
                    + ", ".join(f"{k}->{v}" for k, v in list(mapping.items())[:6])
                    + "."
                ),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
            self._transfers.append(record)
            if len(self._transfers) > _MAX_TRANSFERS:
                self._transfers = self._transfers[-_MAX_TRANSFERS:]
        self.save()
        return record

    def auto_transfer(
        self,
        target_id: Optional[str] = None,
        cross_domain_only: bool = True,
        limit: int = 1,
    ) -> List[Dict[str, Any]]:
        """Discover the strongest isomorphic pairs and transfer solutions."""
        matches = self.find_isomorphisms(
            structure_id=target_id,
            cross_domain_only=cross_domain_only,
            include_self=False,
        )
        transfers: List[Dict[str, Any]] = []
        used: set = set()
        for match in matches:
            if len(transfers) >= max(0, int(limit)):
                break
            source_id = match["source"]
            target = match["target"]
            if target_id is not None:
                source_id, target = target, match["source"]
                if target != target_id:
                    continue
            if (source_id, target) in used:
                continue
            used.add((source_id, target))
            if not self._solutions.get(source_id):
                continue
            transfers.append(self.transfer_solution(source_id, target))
        return transfers

    def analyze_pipeline(
        self,
        structure_id: str,
        module_edges: Iterable[Sequence[str]],
        modules: Iterable[str],
    ) -> Dict[str, Any]:
        """Register the live module graph and map it to known exemplars."""
        nodes = list(dict.fromkeys(str(module) for module in modules))
        edges = [
            (str(edge[0]), str(edge[1]))
            for edge in module_edges
            if len(edge) >= 2
        ]
        existing = structure_id in self._structures
        structure = DomainStructure(
            id=structure_id,
            domain="compiler_theory",
            name="Live pipeline dependency graph",
            nodes=nodes,
            edges=edges,
            metadata={"source": "core.pipeline", "modules": len(nodes)},
            created_at=(
                self._structures[structure_id].created_at
                if existing
                else datetime.now(timezone.utc).isoformat()
            ),
        )
        self.register_structure(structure, overwrite=True)
        matches = self.find_isomorphisms(
            structure_id=structure_id,
            cross_domain_only=True,
        )
        transfers = self.auto_transfer(
            target_id=structure_id,
            cross_domain_only=True,
            limit=1,
        ) if matches else []
        return {
            "structure_id": structure_id,
            "nodes": len(nodes),
            "edges": len(edges),
            "profile": self._profile(structure),
            "matches": matches[:3],
            "transfers": transfers,
        }

    # ------------------------------------------------------------------
    # Telemetry / persistence
    # ------------------------------------------------------------------

    def get_telemetry(self) -> Dict[str, Any]:
        with self._lock:
            structures = [
                {
                    "id": item.id,
                    "domain": item.domain,
                    "name": item.name,
                    "nodes": len(item.nodes),
                    "edges": len(item.edges),
                }
                for item in self._structures.values()
            ]
            return {
                "available": True,
                "engine": "analogical_manifold_bridge",
                "domains": list(DOMAINS),
                "structure_count": len(self._structures),
                "structures": structures[-12:],
                "solution_registry": {
                    key: [solution.get("name") for solution in values]
                    for key, values in self._solutions.items()
                },
                "comparisons": self._comparisons,
                "isomorphisms_found": self._isomorphisms,
                "transfers": len(self._transfers),
                "recent_transfers": list(self._transfers[-3:]),
                "embedding_dimensions": 10,
            }

    def to_dict(self) -> Dict[str, Any]:
        with self._lock:
            return {
                "version": 1,
                "comparisons": self._comparisons,
                "isomorphisms": self._isomorphisms,
                "structures": [
                    item.to_dict() for item in list(self._structures.values())[-_MAX_STRUCTURES:]
                ],
                "solutions": {
                    key: list(values) for key, values in self._solutions.items()
                },
                "transfers": list(self._transfers[-_MAX_TRANSFERS:]),
            }

    def from_dict(self, data: Dict[str, Any]) -> None:
        if not isinstance(data, dict):
            return
        with self._lock:
            self._comparisons = max(self._comparisons, int(data.get("comparisons", 0)))
            self._isomorphisms = max(self._isomorphisms, int(data.get("isomorphisms", 0)))
            for payload in data.get("structures") or []:
                if isinstance(payload, dict) and payload.get("id"):
                    structure = DomainStructure.from_dict(payload)
                    self._structures[structure.id] = structure
            for key, values in (data.get("solutions") or {}).items():
                if isinstance(values, list):
                    self._solutions[str(key)] = [
                        dict(value) for value in values if isinstance(value, dict)
                    ]
            for record in data.get("transfers") or []:
                if isinstance(record, dict):
                    self._transfers.append(record)

    def save(self) -> Optional[Path]:
        if self.state_path is None:
            return None
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.state_path.with_suffix(self.state_path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        tmp_path.replace(self.state_path)
        return self.state_path

    def _load(self) -> None:
        if self.state_path is None or not self.state_path.is_file():
            return
        try:
            self.from_dict(json.loads(self.state_path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, OSError):
            logger.warning("Manifold state at %s is unreadable; using exemplars.", self.state_path)
