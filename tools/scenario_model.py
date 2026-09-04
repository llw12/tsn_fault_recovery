"""Canonical scenario model, loading and semantic validation."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from tools.simple_yaml import load as load_yaml
from tools.units import UnitError, parse_bitrate, parse_bytes, parse_time


class ScenarioValidationError(ValueError):
    pass


@dataclass(frozen=True)
class Node:
    id: str
    type: str


@dataclass(frozen=True)
class Link:
    id: str
    endpoint_a: str
    endpoint_b: str
    bitrate_bps: float
    propagation_delay_s: float


@dataclass(frozen=True)
class TTFlow:
    id: str
    source: str
    destination: str
    packet_size_bytes: int
    period_s: float
    deadline_e2e_s: float
    schedule_deadline_budget_s: float
    release_offset_s: float
    pcp: int
    traffic_class: int


@dataclass(frozen=True)
class BEFlow:
    id: str
    source: str
    destination: str
    packet_size_bytes: int
    interval_s: float
    release_offset_s: float
    pcp: int
    traffic_class: int


@dataclass(frozen=True)
class SimulationConfig:
    duration_s: float
    cycle_time_s: float
    time_quantum_s: float
    failure_time_s: float
    solver_delay_s: float
    random_seed: int


@dataclass(frozen=True)
class NetworkConfig:
    default_bitrate_bps: float
    default_propagation_delay_s: float


@dataclass(frozen=True)
class SchedulingConfig:
    ingress_margin_s: float
    hop_margin_s: float
    endpoint_budget_s: float
    frame_overhead_bytes: int
    be_traffic_class: int


@dataclass(frozen=True)
class CandidateSelection:
    mode: str
    scope: str | None
    criterion: str | None
    exclude: tuple[str, ...]


@dataclass(frozen=True)
class ScenarioModel:
    schema_version: int
    scenario_name: str
    forwarding_model: str
    simulation: SimulationConfig
    network: NetworkConfig
    scheduling: SchedulingConfig
    nodes: tuple[Node, ...]
    links: tuple[Link, ...]
    tt_flows: tuple[TTFlow, ...]
    be_flows: tuple[BEFlow, ...]
    candidate_selection: CandidateSelection
    fault_candidates: tuple[str, ...]

    def canonical_dict(self) -> dict:
        policy = {"mode": self.candidate_selection.mode, "exclude": list(self.candidate_selection.exclude)}
        if self.candidate_selection.scope is not None:
            policy["scope"] = self.candidate_selection.scope
        if self.candidate_selection.criterion is not None:
            policy["criterion"] = self.candidate_selection.criterion
        value = {
            "schema_version": self.schema_version,
            "scenario_name": self.scenario_name,
            "simulation": asdict(self.simulation),
            "network": asdict(self.network),
            "scheduling": asdict(self.scheduling),
            "nodes": [asdict(item) for item in self.nodes],
            "links": [asdict(item) for item in self.links],
            "tt_flows": [asdict(item) for item in self.tt_flows],
            "be_flows": [asdict(item) for item in self.be_flows],
            "fault_candidate_policy": policy,
            "fault_candidates": list(self.fault_candidates),
        }
        if self.forwarding_model != "destination-mac":
            value["forwarding_model"] = self.forwarding_model
        return value

    def canonical_json(self, *, include_hash: bool = True) -> str:
        value = self.canonical_dict()
        if include_hash:
            value["scenario_sha256"] = self.sha256()
        return json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"

    def sha256(self) -> str:
        payload = json.dumps(self.canonical_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=False)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _require_mapping(raw: dict, key: str) -> dict:
    value = raw.get(key)
    if not isinstance(value, dict):
        raise ScenarioValidationError(f"{key} must be a mapping")
    return value


def _require_list(raw: dict, key: str) -> list:
    value = raw.get(key)
    if not isinstance(value, list):
        raise ScenarioValidationError(f"{key} must be a list")
    return value


def _duplicates(values: list[str]) -> list[str]:
    seen, duplicates = set(), set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return sorted(duplicates)


def load_scenario(path: str | Path) -> ScenarioModel:
    source = Path(path)
    raw = load_yaml(source)
    if not isinstance(raw, dict):
        raise ScenarioValidationError("scenario document must be a mapping")
    if raw.get("schema_version") != 1:
        raise ScenarioValidationError(f"unsupported schema_version {raw.get('schema_version')!r}; expected 1")
    name = raw.get("name")
    if not isinstance(name, str) or not _IDENTIFIER.fullmatch(name):
        raise ScenarioValidationError("name must be a valid NED/Python identifier")
    forwarding_model = raw.get("forwardingModel", "destination-mac")
    if forwarding_model not in {"destination-mac", "stream-aware"}:
        raise ScenarioValidationError("forwardingModel must be destination-mac or stream-aware")

    simulation_raw = _require_mapping(raw, "simulation")
    network_raw = _require_mapping(raw, "network")
    default_link = _require_mapping(network_raw, "default_link")
    topology = _require_mapping(raw, "topology")
    traffic = _require_mapping(raw, "traffic")
    faults = _require_mapping(raw, "faults")
    scheduling_raw = raw.get("scheduling", {})
    if not isinstance(scheduling_raw, dict):
        raise ScenarioValidationError("scheduling must be a mapping")

    try:
        simulation = SimulationConfig(
            duration_s=parse_time(simulation_raw["duration"]),
            cycle_time_s=parse_time(simulation_raw["cycle_time"]),
            time_quantum_s=parse_time(simulation_raw["time_quantum"]),
            failure_time_s=parse_time(simulation_raw.get("failure_time", "5ms"), allow_zero=True),
            solver_delay_s=parse_time(simulation_raw.get("solver_delay", "1ms"), allow_zero=True),
            random_seed=int(simulation_raw.get("random_seed", 0)),
        )
        network = NetworkConfig(
            default_bitrate_bps=parse_bitrate(default_link["bitrate"]),
            default_propagation_delay_s=parse_time(default_link.get("propagation_delay", "0us"), allow_zero=True),
        )
        scheduling = SchedulingConfig(
            ingress_margin_s=parse_time(scheduling_raw.get("ingress_margin", "40us"), allow_zero=True),
            hop_margin_s=parse_time(scheduling_raw.get("hop_margin", "5us"), allow_zero=True),
            endpoint_budget_s=parse_time(scheduling_raw.get("endpoint_budget", "320us"), allow_zero=True),
            frame_overhead_bytes=parse_bytes(scheduling_raw.get("frame_overhead", "64B")),
            be_traffic_class=int(scheduling_raw.get("be_traffic_class", 0)),
        )
    except (KeyError, TypeError, ValueError, UnitError) as error:
        raise ScenarioValidationError(str(error)) from error

    end_system_raw = _require_list(topology, "end_systems")
    switch_raw = _require_list(topology, "switches")
    node_pairs = [(item.get("id"), "end_system") for item in end_system_raw] + [(item.get("id"), "switch") for item in switch_raw]
    node_ids = [item[0] for item in node_pairs]
    duplicates = _duplicates(node_ids)
    if duplicates:
        raise ScenarioValidationError(f"duplicate node id: {', '.join(duplicates)}")
    if any(not isinstance(node_id, str) or not _IDENTIFIER.fullmatch(node_id) for node_id in node_ids):
        raise ScenarioValidationError("all node ids must be valid NED identifiers")
    nodes = tuple(sorted((Node(node_id, node_type) for node_id, node_type in node_pairs), key=lambda item: item.id))
    node_set = set(node_ids)

    link_items = _require_list(topology, "links")
    link_ids = [item.get("id") for item in link_items]
    duplicates = _duplicates(link_ids)
    if duplicates:
        raise ScenarioValidationError(f"duplicate link id: {', '.join(duplicates)}")
    links = []
    for item in link_items:
        link_id = item.get("id")
        endpoints = item.get("endpoints")
        if not isinstance(link_id, str) or not _IDENTIFIER.fullmatch(link_id):
            raise ScenarioValidationError(f"invalid link id {link_id!r}")
        if not isinstance(endpoints, list) or len(endpoints) != 2:
            raise ScenarioValidationError(f"link {link_id} must have exactly two endpoints")
        missing = [endpoint for endpoint in endpoints if endpoint not in node_set]
        if missing:
            raise ScenarioValidationError(f"link {link_id} endpoint does not exist: {', '.join(missing)}")
        try:
            bitrate = parse_bitrate(item["bitrate"]) if "bitrate" in item else network.default_bitrate_bps
            delay = parse_time(item["propagation_delay"], allow_zero=True) if "propagation_delay" in item else network.default_propagation_delay_s
        except UnitError as error:
            raise ScenarioValidationError(str(error)) from error
        links.append(Link(link_id, endpoints[0], endpoints[1], bitrate, delay))
    links = tuple(sorted(links, key=lambda item: item.id))

    tt_items = traffic.get("tt_flows", [])
    be_items = traffic.get("be_flows", [])
    if not isinstance(tt_items, list) or not isinstance(be_items, list):
        raise ScenarioValidationError("traffic flow collections must be lists")
    flow_ids = [item.get("id") for item in tt_items + be_items]
    duplicates = _duplicates(flow_ids)
    if duplicates:
        raise ScenarioValidationError(f"duplicate flow id: {', '.join(duplicates)}")

    def common(item: dict) -> tuple[str, str, str, int, int, int]:
        flow_id, source_id, destination_id = item.get("id"), item.get("source"), item.get("destination")
        if not isinstance(flow_id, str) or not _IDENTIFIER.fullmatch(flow_id):
            raise ScenarioValidationError(f"invalid flow id {flow_id!r}")
        if source_id not in node_set:
            raise ScenarioValidationError(f"flow {flow_id} source does not exist: {source_id}")
        if destination_id not in node_set:
            raise ScenarioValidationError(f"flow {flow_id} destination does not exist: {destination_id}")
        if source_id == destination_id:
            raise ScenarioValidationError(f"flow {flow_id} source and destination must differ")
        try:
            packet_size = parse_bytes(item["packet_size"])
            pcp = int(item["pcp"])
            traffic_class = int(item["traffic_class"])
        except (KeyError, TypeError, ValueError, UnitError) as error:
            raise ScenarioValidationError(f"flow {flow_id}: {error}") from error
        if traffic_class < 0:
            raise ScenarioValidationError(f"flow {flow_id} traffic class must be non-negative")
        if pcp < 0 or pcp > 7:
            raise ScenarioValidationError(f"flow {flow_id} PCP must be in [0,7]")
        return flow_id, source_id, destination_id, packet_size, pcp, traffic_class

    tt_flows = []
    for item in tt_items:
        flow_id, source_id, destination_id, packet_size, pcp, traffic_class = common(item)
        try:
            period = parse_time(item["period"])
            deadline = parse_time(item["deadline"])
            release = parse_time(item.get("release_offset", "0us"), allow_zero=True)
        except (KeyError, UnitError) as error:
            raise ScenarioValidationError(f"flow {flow_id}: {error}") from error
        # A scenario cycle is the scheduling hyperperiod, not a common TT
        # period.  Mixed-period workloads are valid when every period divides
        # that hyperperiod; this keeps periodic instance accounting exact.
        if simulation.cycle_time_s / period != round(simulation.cycle_time_s / period):
            raise ScenarioValidationError(f"flow {flow_id} period must divide cycle_time")
        if deadline > period:
            raise ScenarioValidationError(f"flow {flow_id} deadline exceeds period")
        if release < 0 or release >= period:
            raise ScenarioValidationError(f"flow {flow_id} has invalid release offset")
        budget = deadline - scheduling.endpoint_budget_s
        if budget <= 0:
            raise ScenarioValidationError(f"flow {flow_id} endpoint budget leaves no positive schedule deadline budget")
        tt_flows.append(TTFlow(flow_id, source_id, destination_id, packet_size, period, deadline, budget, release, pcp, traffic_class))
    be_flows = []
    for item in be_items:
        flow_id, source_id, destination_id, packet_size, pcp, traffic_class = common(item)
        try:
            interval = parse_time(item["interval"])
            release = parse_time(item.get("release_offset", "0us"), allow_zero=True)
        except (KeyError, UnitError) as error:
            raise ScenarioValidationError(f"flow {flow_id}: {error}") from error
        if release < 0 or release >= interval:
            raise ScenarioValidationError(f"flow {flow_id} has invalid release offset")
        be_flows.append(BEFlow(flow_id, source_id, destination_id, packet_size, interval, release, pcp, traffic_class))

    if faults.get("model") != "single_link":
        raise ScenarioValidationError("only faults.model=single_link is supported")
    link_set = set(link_ids)
    selection_raw = faults.get("candidate_selection", {"mode": "explicit"})
    if not isinstance(selection_raw, dict):
        raise ScenarioValidationError("faults.candidate_selection must be a mapping")
    mode = selection_raw.get("mode")
    if mode not in {"explicit", "auto"}:
        raise ScenarioValidationError("candidate selection mode must be explicit or auto")
    exclude = selection_raw.get("exclude", faults.get("exclude", []))
    if not isinstance(exclude, list) or any(not isinstance(item, str) for item in exclude):
        raise ScenarioValidationError("candidate selection exclude must be a list of link IDs")
    missing_excludes = [candidate for candidate in exclude if candidate not in link_set]
    if missing_excludes:
        raise ScenarioValidationError(f"excluded candidate link does not exist: {', '.join(missing_excludes)}")
    if _duplicates(exclude):
        raise ScenarioValidationError("candidate selection exclude contains duplicates")
    candidates = faults.get("candidates", [])
    if mode == "explicit" and (not isinstance(candidates, list) or not candidates):
        raise ScenarioValidationError("explicit fault candidates must be a non-empty list")
    if mode == "auto" and candidates:
        raise ScenarioValidationError("auto candidate selection must not declare candidates")
    if not isinstance(candidates, list):
        raise ScenarioValidationError("fault candidates must be a list")
    missing_faults = [candidate for candidate in candidates if candidate not in link_set]
    if missing_faults:
        raise ScenarioValidationError(f"fault candidate link does not exist: {', '.join(missing_faults)}")
    if scheduling.be_traffic_class < 0:
        raise ScenarioValidationError("BE traffic class must be non-negative")
    if _duplicates(candidates):
        raise ScenarioValidationError("fault candidates contain duplicates")
    if mode == "auto":
        scope = selection_raw.get("scope")
        criterion = selection_raw.get("criterion")
        if scope != "switch-switch":
            raise ScenarioValidationError("auto candidate selection requires scope=switch-switch")
        if criterion != "tt-primary-route-used":
            raise ScenarioValidationError("auto candidate selection requires criterion=tt-primary-route-used")
    else:
        scope = selection_raw.get("scope")
        criterion = selection_raw.get("criterion")
    selection = CandidateSelection(mode, scope, criterion, tuple(sorted(exclude)))
    return ScenarioModel(1, name, forwarding_model, simulation, network, scheduling, nodes, links,
            tuple(sorted(tt_flows, key=lambda item: item.id)), tuple(sorted(be_flows, key=lambda item: item.id)),
            selection, tuple(sorted(candidates)))


def write_canonical(model: ScenarioModel, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(model.canonical_json(), encoding="utf-8")
