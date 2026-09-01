"""Backend-neutral offline recovery synthesis contract used by exp13."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any


class BackendStatus(str, Enum):
    SUCCESS_H2S = "SUCCESS_H2S"
    SUCCESS_CELF_FALLBACK = "SUCCESS_CELF_FALLBACK"
    HEURISTIC_NOT_FOUND = "HEURISTIC_NOT_FOUND"
    TIME_LIMIT = "TIME_LIMIT"
    OPTIMAL = "OPTIMAL"
    FEASIBLE_NOT_OPTIMAL = "FEASIBLE_NOT_OPTIMAL"
    INFEASIBLE = "INFEASIBLE"
    TIME_LIMIT_WITH_INCUMBENT = "TIME_LIMIT_WITH_INCUMBENT"
    TIME_LIMIT_NO_INCUMBENT = "TIME_LIMIT_NO_INCUMBENT"
    MEMORY_LIMIT = "MEMORY_LIMIT"
    MODEL_BUILD_ERROR = "MODEL_BUILD_ERROR"
    UNSUPPORTED = "UNSUPPORTED"
    INVALID_INPUT = "INVALID_INPUT"
    OUTPUT_INVALID = "OUTPUT_INVALID"
    BACKEND_ERROR = "BACKEND_ERROR"
    ERROR = "ERROR"
    GUROBI_LICENSE_UNAVAILABLE = "GUROBI_LICENSE_UNAVAILABLE"
    GUROBI_LICENSE_CAPACITY_LIMIT = "GUROBI_LICENSE_CAPACITY_LIMIT"


@dataclass(frozen=True)
class RecoverySynthesisRequest:
    scenario_path: Path
    disabled_links: tuple[str, ...] = ()
    healthy_primary_routes: dict[str, dict[str, Any]] = field(default_factory=dict)
    affected_flow_ids: tuple[str, ...] = ()
    solver_timeout_s: int = 30
    route_scope: str = "affected-only"
    forwarding_model: str = "stream-aware"
    output_directory: Path | None = None


@dataclass
class RecoverySynthesisResult:
    backend: str
    status: BackendStatus
    feasible: bool = False
    optimal_proven: bool = False
    logical_routes: list[dict[str, Any]] = field(default_factory=list)
    schedule_windows: list[dict[str, Any]] = field(default_factory=list)
    profile: dict[str, Any] | None = None
    objective: float | None = None
    statistics: dict[str, Any] = field(default_factory=dict)
    timings_ms: dict[str, float] = field(default_factory=dict)
    diagnostic: str = ""

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["status"] = self.status.value
        return value


class RecoverySynthesisBackend(ABC):
    name: str

    @abstractmethod
    def synthesize(self, request: RecoverySynthesisRequest) -> RecoverySynthesisResult:
        raise NotImplementedError


class LegacyBfsZ3Backend(RecoverySynthesisBackend):
    """Descriptor for the production C++ BFS+Z3 backend.

    Exp13 invokes the existing OMNeT precompute pipeline for actual legacy
    synthesis; this class keeps runner configuration backend-neutral and never
    reimplements that production solver in Python.
    """

    name = "legacy-bfs-z3"

    def synthesize(self, request: RecoverySynthesisRequest) -> RecoverySynthesisResult:
        return RecoverySynthesisResult(
            backend=self.name,
            status=BackendStatus.UNSUPPORTED,
            diagnostic="Legacy BFS+Z3 is invoked through the existing C++ precompute pipeline",
        )
