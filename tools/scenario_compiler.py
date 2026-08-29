"""Deterministic compiler from scenario schema v1 to OMNeT++ artifacts."""

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

from tools.scenario_model import ScenarioModel, load_scenario, write_canonical
from tools.profile_store import solver_config_hash


def _seconds(value: float) -> str:
    return f"{value:.12g}s"


def build_port_map(model: ScenarioModel) -> dict:
    """Assign ethN indexes using exactly the sorted NED connection order."""
    next_port = {node.id: 0 for node in model.nodes}
    links = {}
    for link in model.links:
        endpoints = []
        for node_id in (link.endpoint_a, link.endpoint_b):
            index = next_port[node_id]
            next_port[node_id] += 1
            endpoints.append({
                "node": node_id,
                "interface": f"eth{index}",
                "egress_path": f"{node_id}.eth[{index}]",
            })
        endpoints[0]["peer"] = link.endpoint_b
        endpoints[1]["peer"] = link.endpoint_a
        links[link.id] = {"a": endpoints[0], "b": endpoints[1]}
    return {"schema_version": 1, "scenario_name": model.scenario_name, "links": links}


def _position(index: int, count: int, *, y: int) -> tuple[int, int]:
    spacing = 800 // max(count - 1, 1)
    return 100 + index * spacing, y


def render_ned(model: ScenarioModel) -> str:
    package = model.scenario_name
    lines = [
        f"package {package};", "",
        "import inet.common.Module;",
        "import inet.common.scenario.ScenarioManager;",
        "import inet.networklayer.configurator.contract.IL3NetworkConfigurator;",
        "import inet.networklayer.configurator.contract.INetworkConfigurator;",
        "import inet.node.ethernet.EthernetLink;",
        "import inet.node.tsn.TsnDevice;",
        "import inet.node.tsn.TsnSwitch;",
        "import inet.visualizer.contract.IIntegratedVisualizer;",
        "import tsn_fault_recovery.control.PacketIdentityRecorder;",
        "import tsn_fault_recovery.control.ProfileSwitcher;",
        "import tsn_fault_recovery.control.ScenarioRecoveryController;", "",
        "network ScenarioNetwork extends Module", "{", "    parameters:",
        "        bool hasGlobalArp = default(true);",
        "        *.ipv4.arp.typename = default(hasGlobalArp ? \"GlobalArp\" : \"Arp\");",
        "    submodules:",
    ]
    end_systems = [node for node in model.nodes if node.type == "end_system"]
    switches = [node for node in model.nodes if node.type == "switch"]
    for index, node in enumerate(end_systems):
        x, y = _position(index, len(end_systems), y=420)
        lines += [f"        {node.id}: TsnDevice {{", f"            @display(\"p={x},{y}\");", "        }"]
    for index, node in enumerate(switches):
        x, y = _position(index, len(switches), y=240)
        lines += [f"        {node.id}: TsnSwitch {{", f"            @display(\"p={x},{y}\");", "        }"]
    lines += [
        "        visualizer: <default(firstAvailableOrEmpty(\"IntegratedCanvasVisualizer\"))> like IIntegratedVisualizer if typename != \"\" { @display(\"p=80,70;is=s\"); }",
        "        configurator: <default(\"Ipv4NetworkConfigurator\")> like IL3NetworkConfigurator if typename != \"\" { @display(\"p=170,70;is=s\"); }",
        "        macForwardingTableConfigurator: <default(hasGlobalArp ? \"MacForwardingTableConfigurator\" : \"\")> like INetworkConfigurator if typename != \"\" { @display(\"p=260,70;is=s\"); }",
        "        scenarioManager: ScenarioManager { @display(\"p=350,70;is=s\"); }",
        "        profileSwitcher: ProfileSwitcher { @display(\"p=440,70;is=s\"); }",
        "        scenarioRecoveryController: ScenarioRecoveryController { @display(\"p=530,70;is=s\"); }",
        "        packetIdentityRecorder: PacketIdentityRecorder { @display(\"p=620,70;is=s\"); }",
        "    connections:",
    ]
    for link in model.links:
        channel = f"EthernetLink {{ datarate = {link.bitrate_bps:.12g}bps; delay = {_seconds(link.propagation_delay_s)}; }}"
        lines.append(f"        {link.endpoint_a}.ethg++ <--> {channel} <--> {link.endpoint_b}.ethg++; // {link.id}")
    lines += ["}", ""]
    return "\n".join(lines)


def _quoted_words(values: list[str]) -> str:
    return " ".join(values)


def _mapping(entries: list[str]) -> str:
    return "[" + ", ".join(entries) + "]"


def render_ini(model: ScenarioModel) -> str:
    package = f"{model.scenario_name}.ScenarioNetwork"
    all_flows = list(model.tt_flows) + list(model.be_flows)
    flows_by_source: dict[str, list] = {}
    flows_by_destination: dict[str, list] = {}
    for flow in all_flows:
        flows_by_source.setdefault(flow.source, []).append(flow)
        flows_by_destination.setdefault(flow.destination, []).append(flow)
    ports = {flow.id: 11000 + index for index, flow in enumerate(sorted(all_flows, key=lambda item: item.id))}
    lines = [
        "[General]", f"network = {package}", f"sim-time-limit = {_seconds(model.simulation.duration_s)}",
        "cmdenv-express-mode = true", "record-eventlog = false", "**.scalar-recording = true", "**.vector-recording = true",
        f"seed-set = {model.simulation.random_seed}", "",
        "*.profileSwitcher.enabled = false", "*.scenarioRecoveryController.enabled = true",
        '*.scenarioRecoveryController.scenario = readJSON("scenario.json")',
        '*.scenarioRecoveryController.portMap = readJSON("port_map.json")',
        '*.scenarioRecoveryController.profile0 = readJSON("profiles/profile0.json")',
        '*.scenarioRecoveryController.offlineProfileStore = readJSON("profiles/profile0.json")',
        "*.packetIdentityRecorder.enabled = true", "",
    ]
    port_map = build_port_map(model)
    for link in model.links:
        for side in ("a", "b"):
            endpoint = port_map["links"][link.id][side]
            index = endpoint["interface"][3:]
            lines.append(f"*.{endpoint['node']}.eth[{index}].bitrate = {link.bitrate_bps:.12g}bps")
    lines.append("")
    for node in sorted((node for node in model.nodes if node.type == "end_system"), key=lambda item: item.id):
        outgoing = sorted(flows_by_source.get(node.id, []), key=lambda item: item.id)
        incoming = sorted(flows_by_destination.get(node.id, []), key=lambda item: item.id)
        lines.append(f"*.{node.id}.numApps = {len(outgoing) + len(incoming)}")
        for index, flow in enumerate(outgoing):
            interval = flow.period_s if hasattr(flow, "period_s") else flow.interval_s
            lines += [
                f'*.{node.id}.app[{index}].typename = "UdpSourceApp"',
                f'*.{node.id}.app[{index}].display-name = "{flow.id}"',
                f'*.{node.id}.app[{index}].io.destAddress = "{flow.destination}"',
                f"*.{node.id}.app[{index}].io.destPort = {ports[flow.id]}",
                f"*.{node.id}.app[{index}].source.packetLength = {flow.packet_size_bytes}B",
                f"*.{node.id}.app[{index}].source.productionInterval = {_seconds(interval)}",
                f"*.{node.id}.app[{index}].source.initialProductionOffset = {_seconds(flow.release_offset_s)}",
                f'*.{node.id}.app[{index}].source.packetNameFormat = "{flow.id}-%c"',
                f'*.{node.id}.app[{index}].source.packetRepresentation = "applicationPacket"',
            ]
        for offset, flow in enumerate(incoming):
            index = len(outgoing) + offset
            lines += [
                f'*.{node.id}.app[{index}].typename = "UdpSinkApp"',
                f'*.{node.id}.app[{index}].display-name = "{flow.id}"',
                f"*.{node.id}.app[{index}].io.localPort = {ports[flow.id]}",
            ]
        if outgoing:
            identifiers = _mapping([f'{{stream: "{flow.id}", packetFilter: expr(udp.destPort == {ports[flow.id]})}}' for flow in outgoing])
            encoders = _mapping([f'{{stream: "{flow.id}", pcp: {flow.pcp}}}' for flow in outgoing])
            lines += [f"*.{node.id}.hasOutgoingStreams = true", f"*.{node.id}.bridging.streamIdentifier.identifier.mapping = {identifiers}", f"*.{node.id}.bridging.streamCoder.encoder.mapping = {encoders}"]
        lines.append("")
    switches = [node.id for node in model.nodes if node.type == "switch"]
    max_class = max([model.scheduling.be_traffic_class] + [flow.traffic_class for flow in all_flows])
    for switch in switches:
        lines += [
            f"*.{switch}.hasEgressTrafficShaping = true",
            f"*.{switch}.bridging.directionReverser.reverser.excludeEncapsulationProtocols = [\"ieee8021qctag\"]",
            f"*.{switch}.eth[*].macLayer.queue.numTrafficClasses = {max_class + 1}",
        ]
        for traffic_class in range(max_class + 1):
            initially_open = "true" if traffic_class == model.scheduling.be_traffic_class else "false"
            lines += [
                f"*.{switch}.eth[*].macLayer.queue.transmissionGate[{traffic_class}].initiallyOpen = {initially_open}",
                f"*.{switch}.eth[*].macLayer.queue.transmissionGate[{traffic_class}].durations = [{_seconds(model.simulation.cycle_time_s / 2)}, {_seconds(model.simulation.cycle_time_s / 2)}]",
            ]
    scenario_value = model.canonical_dict()
    scenario_value["scenario_sha256"] = model.sha256()
    config_hash = solver_config_hash(scenario_value, port_map)
    lines += ["", f'*.packetIdentityRecorder.flowIds = "{_quoted_words([flow.id for flow in all_flows])}"']
    source_modules, destination_modules = [], []
    for flow in all_flows:
        outgoing = sorted(flows_by_source[flow.source], key=lambda item: item.id)
        incoming = sorted(flows_by_destination[flow.destination], key=lambda item: item.id)
        source_modules.append(f"{flow.source}.app[{outgoing.index(flow)}].io")
        destination_modules.append(f"{flow.destination}.app[{len(flows_by_source.get(flow.destination, [])) + incoming.index(flow)}].io")
    lines += [
        f'*.packetIdentityRecorder.sourceModules = "{_quoted_words(source_modules)}"',
        f'*.packetIdentityRecorder.destinationModules = "{_quoted_words(destination_modules)}"', "",
        "[Config ScenarioPrecompute]", "*.scenarioManager.script = xml(\"<scenario/>\")",
        '*.scenarioRecoveryController.mode = "precompute"',
        '*.scenarioRecoveryController.profileOutputPath = "profiles/profile0.json"',
        '*.scenarioRecoveryController.faultAnalysisOutputPath = "fault_analysis.json"', "",
        "[Config ScenarioPerFailurePrecompute]", "sim-time-limit = 0s",
        "*.packetIdentityRecorder.enabled = false", "*.scenarioRecoveryController.mode = \"precompute-per-failure\"",
        '*.scenarioRecoveryController.perFailureProfileDirectory = "profiles/per_failure/raw"',
        '*.scenarioRecoveryController.perFailureReportOutputPath = "profiles/per_failure/precompute_report.json"', "",
    ]
    link_by_id = {link.id: link for link in model.links}
    for fault_id in model.fault_candidates:
        link = link_by_id[fault_id]
        endpoint_a = port_map["links"][fault_id]["a"]
        endpoint_b = port_map["links"][fault_id]["b"]
        time = _seconds(model.simulation.failure_time_s)
        script = (f"<scenario><set-channel-param t='{time}' src-module='{endpoint_a['node']}' "
                  f"src-gate='ethg$o[{endpoint_a['interface'][3:]}]' par='disabled' value='true'/>"
                  f"<set-channel-param t='{time}' src-module='{endpoint_b['node']}' "
                  f"src-gate='ethg$o[{endpoint_b['interface'][3:]}]' par='disabled' value='true'/></scenario>")
        config_suffix = fault_id
        for mode, label in (("no-recovery", "NoRecovery"), ("online", "Online")):
            lines += [
                f"[Config {label}_{config_suffix}]", f'*.scenarioManager.script = xml("{script}")',
                f'*.scenarioRecoveryController.mode = "{mode}"', f'*.scenarioRecoveryController.faultId = "{fault_id}"',
                f'*.scenarioRecoveryController.recoveryProfileOutputPath = "profiles/{mode}_{fault_id}.json"', "",
            ]
        lines += [
            f"[Config Offline_{config_suffix}]", f'*.scenarioManager.script = xml("{script}")',
            '*.scenarioRecoveryController.mode = "offline-per-failure"',
            f'*.scenarioRecoveryController.faultId = "{fault_id}"',
            '*.scenarioRecoveryController.offlineProfileStore = readJSON("profiles/per_failure/runtime_store.json")',
            f'*.scenarioRecoveryController.solverConfigHash = "{config_hash}"',
            '*.scenarioRecoveryController.offlineLookupDelay = 0us', "",
        ]
    return "\n".join(lines)


def compile_scenario(source: str | Path, output_root: str | Path) -> Path:
    model = load_scenario(source)
    destination = Path(output_root) / model.scenario_name
    (destination / "profiles").mkdir(parents=True, exist_ok=True)
    write_canonical(model, destination / "scenario.json")
    (destination / "port_map.json").write_text(json.dumps(build_port_map(model), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (destination / "ScenarioNetwork.ned").write_text(render_ned(model), encoding="utf-8")
    ini = render_ini(model)
    (destination / "base.ini").write_text(ini, encoding="utf-8")
    (destination / "omnetpp.ini").write_text(ini, encoding="utf-8")
    placeholder = destination / "profiles/profile0.json"
    if not placeholder.exists():
        placeholder.write_text("{}\n", encoding="utf-8")
    return destination
