"""Pure transformation and audit primitives for exp18e.

The formal campaign lives in :mod:`tools.run_deadline_feasibility_calibration`.
Keeping its input transformation here makes the calibrated scenarios auditable
without running a workload generator or a scheduler.
"""
from __future__ import annotations

import copy
import hashlib
import json
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction
from pathlib import Path
from typing import Any, Iterable

from tools.h2s_jrs_backend import (DEFAULT_CANDIDATE_PATHS, DEFAULT_QUANTUM_NS,
    FORMAL_MEMORY_LIMIT_MB, FORMAL_SEED, FORMAL_THREADS)
from tools.jrs_wa_adapter import canonical_json_bytes, seconds_to_ns

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "results/realistic_tsn_pf_cost"
OUT = ROOT / "results/deadline_feasibility_calibration"
ORDER = ("M_RING", "M_REDSTAR", "M_ROR", "L_RING", "L_REDSTAR", "L_ROR")
HYPERCYCLE_NS = 8_000_000
ALIGNMENT_NS = 100


@dataclass(frozen=True)
class Alpha:
    tag: str
    numerator: int
    denominator: int

    @property
    def decimal(self) -> str:
        return format(Decimal(self.numerator) / Decimal(self.denominator), "f")

    @property
    def label(self) -> str:
        return f"{self.numerator}/{self.denominator}"


ALPHAS = (Alpha("A100", 1, 1), Alpha("A110", 11, 10), Alpha("A120", 6, 5),
          Alpha("A125", 5, 4), Alpha("A133", 4, 3))

# These tree digests cover every regular file below the historical result roots.
# They are computed before exp18e is materialized and rechecked after reporting.
FROZEN_TREE_SHA256 = {
    "results/realistic_tsn_pf_cost": "4626bf9853a735e95d949d8056600f8d076744cfea9ed0aaf743ce425449f175",
    "results/p0_hnf_diagnosis": "b15351bd19ef7ec6956eda42e8a48aba908940d6d9412cbfdeb5d2f967c49155",
    "results/candidate_k_sensitivity": "024586fbe2f9d4d9a816212996fad1d928d1f046b51b5273c6d2d01d6ce08408",
    "results/h2s_multistart_sensitivity": "beed316c5c9901c6ed9248d59d331b67d35721947ba4c7f2fa2023d91b87ede3",
}


class DeadlineQuantizationAmbiguous(ValueError):
    """A pre-registered deadline cannot be exactly represented at 100 ns."""


class DerivedScenarioIntegrityError(ValueError):
    """A field outside the permitted deadline transformation changed."""


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical_sha256(value: Any) -> str:
    return hashlib.sha256(canonical_json_bytes(value)).hexdigest()


def tree_sha256(root: Path) -> str:
    """Hash path names and bytes, so additions/deletions are also detectable."""
    digest = hashlib.sha256()
    for path in sorted(root.rglob("*")):
        if path.is_file():
            relative = path.relative_to(root).as_posix().encode("utf-8")
            digest.update(len(relative).to_bytes(8, "big"))
            digest.update(relative)
            digest.update(path.read_bytes())
    return digest.hexdigest()


def assert_frozen_tree() -> dict[str, str]:
    actual = {name: tree_sha256(ROOT / name) for name in FROZEN_TREE_SHA256}
    mismatch = {name: {"expected": FROZEN_TREE_SHA256[name], "actual": actual[name]}
                for name in actual if actual[name] != FROZEN_TREE_SHA256[name]}
    if mismatch:
        raise RuntimeError(f"FROZEN_HISTORICAL_ARTIFACT_CHANGED: {mismatch}")
    return actual


def flow_kind(flow_id: str) -> str:
    labels = {"SF": "SensorFast", "SC": "SensorCyclic", "CMD": "ControlCommand",
              "STAT": "MachineStatus", "COORD": "MachineCoordination",
              "IC_PREV": "InterCellPrevious", "IC_NEXT": "InterCellNext"}
    return labels[next(prefix for prefix in ("IC_PREV", "IC_NEXT", "SF", "SC", "CMD", "STAT", "COORD")
                       if flow_id.startswith(prefix))]


def ns_field(flow: dict[str, Any], field: str) -> int:
    return seconds_to_ns(flow[field], f"{flow['id']}.{field}")


def seconds_from_ns(value_ns: int) -> float:
    """Serialize an already exact integer-nanosecond value without using float maths.

    The float is only the legacy JSON schema representation.  The exact value is
    first made as Decimal and is immediately round-tripped by ``seconds_to_ns``.
    """
    value = float(Decimal(value_ns) / Decimal(1_000_000_000))
    if seconds_to_ns(value, "derived_deadline") != value_ns:
        raise DeadlineQuantizationAmbiguous(f"legacy seconds representation lost {value_ns} ns")
    return value


def transformed_deadline_ns(original_deadline_ns: int, period_ns: int, alpha: Alpha) -> tuple[int, bool]:
    scaled = Fraction(original_deadline_ns * alpha.numerator, alpha.denominator)
    # Apply the mathematical D<=T cap before judging representability.  For
    # example 400 us * 4/3 is fractional, but min(400 us * 4/3, 500 us) is the
    # exactly representable 500 us period—not a rounded value.
    capped = scaled >= period_ns
    selected = Fraction(period_ns, 1) if capped else scaled
    if selected.denominator != 1:
        raise DeadlineQuantizationAmbiguous(
            f"DEADLINE_QUANTIZATION_AMBIGUOUS: {original_deadline_ns} * {alpha.label} is not integer ns")
    derived = selected.numerator
    if derived % ALIGNMENT_NS:
        raise DeadlineQuantizationAmbiguous(
            f"DEADLINE_QUANTIZATION_AMBIGUOUS: {derived} ns is not {ALIGNMENT_NS} ns aligned")
    return derived, capped


def deadline_values(flow: dict[str, Any], alpha: Alpha) -> dict[str, Any]:
    period_ns = ns_field(flow, "period_s")
    original_ns = ns_field(flow, "schedule_deadline_budget_s")
    end_to_end_ns = ns_field(flow, "deadline_e2e_s")
    if original_ns != end_to_end_ns:
        raise DerivedScenarioIntegrityError(f"{flow['id']} has divergent original deadline fields")
    derived_ns, capped = transformed_deadline_ns(original_ns, period_ns, alpha)
    return {"period_ns": period_ns, "original_deadline_ns": original_ns,
            "derived_deadline_ns": derived_ns, "capped_at_period": capped}


def derive_scenario(parent: dict[str, Any], scenario_id: str, alpha: Alpha) -> dict[str, Any]:
    """Return an exp18e name-only plus deadline-only derivative of ``parent``."""
    derived = copy.deepcopy(parent)
    derived["scenario_name"] = f"{scenario_id}_{alpha.tag}"
    for flow in derived["tt_flows"]:
        values = deadline_values(flow, alpha)
        deadline_seconds = seconds_from_ns(values["derived_deadline_ns"])
        flow["schedule_deadline_budget_s"] = deadline_seconds
        flow["deadline_e2e_s"] = deadline_seconds
    return derived


def topology_projection(scenario: dict[str, Any]) -> dict[str, Any]:
    return {"network": scenario["network"], "nodes": scenario["nodes"], "links": scenario["links"]}


def semantic_projection_without_deadlines(scenario: dict[str, Any]) -> dict[str, Any]:
    projection = copy.deepcopy(scenario)
    # A new derived identifier is required to prevent overwrite; it is not an
    # experimental workload semantic.  Deadline fields are compared separately.
    projection.pop("scenario_name", None)
    for flow in projection.get("tt_flows", []):
        flow.pop("schedule_deadline_budget_s", None)
        flow.pop("deadline_e2e_s", None)
    return projection


def _differences(left: Any, right: Any, path: str = "") -> list[str]:
    if type(left) is not type(right):
        return [path or "<root>"]
    if isinstance(left, dict):
        found: list[str] = []
        for key in sorted(set(left) | set(right)):
            next_path = f"{path}.{key}" if path else str(key)
            if key not in left or key not in right:
                found.append(next_path)
            else:
                found.extend(_differences(left[key], right[key], next_path))
        return found
    if isinstance(left, list):
        found = []
        if len(left) != len(right):
            found.append(f"{path}.length")
        for index, (a, b) in enumerate(zip(left, right)):
            found.extend(_differences(a, b, f"{path}[{index}]"))
        return found
    return [] if left == right else [path or "<root>"]


def derived_integrity(parent: dict[str, Any], derived: dict[str, Any], scenario_id: str,
                      alpha: Alpha) -> dict[str, Any]:
    non_deadline = _differences(semantic_projection_without_deadlines(parent),
                                semantic_projection_without_deadlines(derived))
    parent_flows = {flow["id"]: flow for flow in parent["tt_flows"]}
    child_flows = {flow["id"]: flow for flow in derived["tt_flows"]}
    deadline_differences = 0
    if set(parent_flows) != set(child_flows):
        non_deadline.append("tt_flows.flow_ids")
    for flow_id in sorted(parent_flows):
        before, after = parent_flows[flow_id], child_flows[flow_id]
        expected = deadline_values(before, alpha)["derived_deadline_ns"]
        for field in ("schedule_deadline_budget_s", "deadline_e2e_s"):
            if ns_field(after, field) != expected:
                non_deadline.append(f"tt_flows[{flow_id}].{field}.unexpected")
            if ns_field(before, field) != ns_field(after, field):
                deadline_differences += 1
    row = {"scenario": scenario_id, "derived_scenario": f"{scenario_id}_{alpha.tag}",
           "alpha": alpha.label, "alpha_num": alpha.numerator, "alpha_den": alpha.denominator,
           "parent_scenario_sha": canonical_sha256(parent), "derived_scenario_sha": canonical_sha256(derived),
           "parent_topology_sha": canonical_sha256(topology_projection(parent)),
           "derived_topology_sha": canonical_sha256(topology_projection(derived)),
           "non_deadline_semantic_diff_count": len(non_deadline),
           "non_deadline_semantic_diff_paths": ";".join(non_deadline),
           "deadline_diff_count": deadline_differences}
    row["integrity_pass"] = (not non_deadline and row["parent_topology_sha"] == row["derived_topology_sha"])
    return row


def expected_instance_count(scenario: dict[str, Any]) -> int:
    cycle_ns = seconds_to_ns(scenario["simulation"]["cycle_time_s"], "cycle")
    return sum(cycle_ns // ns_field(flow, "period_s") for flow in scenario["tt_flows"])


def deadline_calibration_rows(reference_scenario: dict[str, Any]) -> list[dict[str, Any]]:
    # Every scale has the same role-based timing palette; de-duplicate by kind.
    by_kind: dict[str, dict[str, Any]] = {}
    for flow in reference_scenario["tt_flows"]:
        by_kind.setdefault(flow_kind(flow["id"]), flow)
    rows = []
    for kind, flow in sorted(by_kind.items()):
        for alpha in ALPHAS:
            values = deadline_values(flow, alpha)
            rows.append({"flow_kind": kind, "original_period_ns": values["period_ns"],
                         "original_deadline_ns": values["original_deadline_ns"],
                         "alpha_num": alpha.numerator, "alpha_den": alpha.denominator,
                         "alpha": alpha.label, "derived_deadline_ns": values["derived_deadline_ns"],
                         "deadline_over_period": f"{values['derived_deadline_ns']}/{values['period_ns']}",
                         "capped_at_period": values["capped_at_period"]})
    return rows


def flow_transform_rows(parent: dict[str, Any], source_id: str, alpha: Alpha) -> list[dict[str, Any]]:
    rows = []
    for flow in sorted(parent["tt_flows"], key=lambda row: row["id"]):
        values = deadline_values(flow, alpha)
        rows.append({"source_scenario": source_id, "derived_scenario": f"{source_id}_{alpha.tag}",
                     "flow_id": flow["id"], "flow_kind": flow_kind(flow["id"]),
                     "period_ns": values["period_ns"], "release_ns": ns_field(flow, "release_offset_s"),
                     "original_deadline_ns": values["original_deadline_ns"],
                     "derived_deadline_ns": values["derived_deadline_ns"], "alpha": alpha.label,
                     "alpha_num": alpha.numerator, "alpha_den": alpha.denominator,
                     "deadline_changed": values["original_deadline_ns"] != values["derived_deadline_ns"],
                     "capped_at_period": values["capped_at_period"]})
    return rows


def cycle_boundary_rows(parent: dict[str, Any], source_id: str, alpha: Alpha,
                        checker_semantics_verified: bool) -> list[dict[str, Any]]:
    cycle_ns = seconds_to_ns(parent["simulation"]["cycle_time_s"], "cycle")
    rows = []
    for flow in sorted(parent["tt_flows"], key=lambda row: row["id"]):
        values = deadline_values(flow, alpha)
        period_ns, release_ns = values["period_ns"], ns_field(flow, "release_offset_s")
        instance_count = cycle_ns // period_ns
        last_release = release_ns + (instance_count - 1) * period_ns
        last_deadline = last_release + values["derived_deadline_ns"]
        rows.append({"scenario": source_id, "derived_scenario": f"{source_id}_{alpha.tag}",
                     "flow_id": flow["id"], "period_ns": period_ns, "release_ns": release_ns,
                     "derived_deadline_ns": values["derived_deadline_ns"],
                     "last_instance_release_ns": last_release, "last_absolute_deadline_ns": last_deadline,
                     "crosses_hyperperiod_boundary": last_deadline > cycle_ns,
                     "checker_semantics_verified": checker_semantics_verified})
    return rows


def backend_config() -> dict[str, Any]:
    return {"route_scope": "ALL_REROUTE", "primary_algorithm": "H2S", "celf_fallback": True,
            "routing": "DIJKSTRA_OVERLAP", "candidate_paths_k": DEFAULT_CANDIDATE_PATHS,
            "quantum_ns": DEFAULT_QUANTUM_NS, "global_seed": FORMAL_SEED,
            "backend_seed": FORMAL_SEED, "threads": FORMAL_THREADS, "timeout_s_per_backend": 30,
            "memory_limit_mb": FORMAL_MEMORY_LIMIT_MB, "h2s_tiebreak_mode": "BASELINE",
            "h2s_tiebreak_seed": 0, "multistart": False}


def assert_backend_config(config: dict[str, Any]) -> None:
    expected = backend_config()
    if config != expected:
        raise RuntimeError(f"BACKEND_CONFIGURATION_DRIFT: expected {expected}, got {config}")


def materialize_derived_scenarios(output: Path = OUT) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]],
                                                                  list[dict[str, Any]], list[dict[str, Any]]]:
    """Read the six frozen JSON scenarios and make the 30 deadline-only files."""
    scenarios: dict[str, dict[str, Any]] = {}
    transforms: list[dict[str, Any]] = []
    integrity_rows: list[dict[str, Any]] = []
    boundary_rows: list[dict[str, Any]] = []
    derived_root = output / "derived_scenarios"
    for scenario_id in ORDER:
        source_path = SOURCE / "scenarios" / f"{scenario_id}.json"
        parent = json.loads(source_path.read_text(encoding="utf-8"))
        scenarios[scenario_id] = parent
        for alpha in ALPHAS:
            derived = derive_scenario(parent, scenario_id, alpha)
            path = derived_root / f"{scenario_id}_{alpha.tag}.json"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(canonical_json_bytes(derived))
            transforms.extend(flow_transform_rows(parent, scenario_id, alpha))
            integrity_rows.append(derived_integrity(parent, derived, scenario_id, alpha))
            boundary_rows.extend(cycle_boundary_rows(parent, scenario_id, alpha, False))
    return scenarios, transforms, integrity_rows, boundary_rows


def load_derived(output: Path, scenario_id: str, alpha: Alpha) -> dict[str, Any]:
    return json.loads((output / "derived_scenarios" / f"{scenario_id}_{alpha.tag}.json").read_text(encoding="utf-8"))


def iter_alpha_ids(alphas: Iterable[Alpha] = ALPHAS) -> Iterable[tuple[str, Alpha]]:
    for alpha in alphas:
        for scenario_id in ORDER:
            yield scenario_id, alpha
