#include "DeterministicJointProfileSolver.h"

#include <algorithm>
#include <map>
#include <queue>

#include "PipelineScheduleGenerator.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

struct Predecessor
{
    std::string previousNode;
    int previousEgressIndex = -1;
};

} // namespace

SolverOutput DeterministicJointProfileSolver::solve(cModule *network, const FaultEvent& fault, const SolverInput& input)
{
    if (input.affectedFlows.size() != 1)
        throw cRuntimeError("The deterministic online backend currently supports exactly one affected TT flow, got %zu",
                input.affectedFlows.size());
    const AffectedFlow& ttFlow = input.affectedFlows.front();
    cModule *start = network->getSubmodule(fault.routeStart.c_str());
    cModule *destination = network->getSubmodule(fault.destination.c_str());
    if (start == nullptr || destination == nullptr)
        throw cRuntimeError("Online solver cannot resolve route endpoints '%s' and '%s'",
                fault.routeStart.c_str(), fault.destination.c_str());

    std::queue<cModule *> pending;
    std::map<std::string, Predecessor> predecessor;
    predecessor[start->getName()] = {"", -1};
    pending.push(start);

    while (!pending.empty() && predecessor.find(destination->getName()) == predecessor.end()) {
        cModule *node = pending.front();
        pending.pop();
        if (!node->hasGate("ethg"))
            continue;
        int gateCount = node->gateSize("ethg");
        for (int index = 0; index < gateCount; ++index) {
            cGate *output = node->gate("ethg$o", index);
            if (output == nullptr || output->getNextGate() == nullptr)
                continue;
            cGate *endGate = output->getPathEndGate();
            cModule *neighbor = endGate == nullptr ? nullptr : endGate->getOwnerModule();
            while (neighbor != nullptr && neighbor->getParentModule() != network)
                neighbor = neighbor->getParentModule();
            if (neighbor == nullptr)
                continue;
            std::string neighborName = neighbor->getName();
            if (predecessor.find(neighborName) != predecessor.end())
                continue;
            predecessor[neighborName] = {node->getName(), index};
            pending.push(neighbor);
        }
    }
    if (predecessor.find(destination->getName()) == predecessor.end())
        throw cRuntimeError("Online solver found no route from '%s' to '%s' after fault %s-%s",
                fault.routeStart.c_str(), fault.destination.c_str(),
                fault.failedEndpointA.c_str(), fault.failedEndpointB.c_str());

    std::vector<std::string> reverseNodes;
    std::vector<int> reverseEgressIndices;
    std::string current = destination->getName();
    reverseNodes.push_back(current);
    while (current != start->getName()) {
        const auto& step = predecessor.at(current);
        reverseEgressIndices.push_back(step.previousEgressIndex);
        current = step.previousNode;
        reverseNodes.push_back(current);
    }
    std::reverse(reverseNodes.begin(), reverseNodes.end());
    std::reverse(reverseEgressIndices.begin(), reverseEgressIndices.end());

    SolverOutput output;
    output.nodePath = reverseNodes;
    output.profile.profileId = "online-joint";
    std::vector<std::string> egressPaths;
    for (size_t hop = 0; hop < reverseEgressIndices.size(); ++hop) {
        const std::string& nodeName = reverseNodes[hop];
        int egressIndex = reverseEgressIndices[hop];
        cModule *node = network->getSubmodule(nodeName.c_str());
        if (node == nullptr || node->getSubmodule("macTable") == nullptr)
            throw cRuntimeError("Online route contains unsupported forwarding node '%s'", nodeName.c_str());
        std::string interfaceName = "eth" + std::to_string(egressIndex);
        output.profile.routes.push_back({nodeName, fault.destination, interfaceName});
        egressPaths.push_back(nodeName + ".eth[" + std::to_string(egressIndex) + "]");
    }
    output.profile.gateSchedules = PipelineScheduleGenerator::generate(
            egressPaths, input.cycleTime, input.ttWindow, ttFlow.packetBytes,
            input.frameOverheadBytes, input.linkBitrate, ttFlow.trafficClass, input.beTrafficClass);
    return output;
}

} // namespace tsn_fault_recovery
