#include "BfsRouteSolver.h"

#include <algorithm>
#include <map>
#include <queue>

#include "JointProfileSolver.h"

using namespace omnetpp;

namespace tsn_fault_recovery {

namespace {

struct Predecessor
{
    std::string previousNode;
    int previousEgressIndex = -1;
};

} // namespace

RoutePath BfsRouteSolver::solve(cModule *network, const FaultEvent& fault)
{
    cModule *start = network->getSubmodule(fault.routeStart.c_str());
    cModule *destination = network->getSubmodule(fault.destination.c_str());
    if (start == nullptr || destination == nullptr)
        throw cRuntimeError("BFS cannot resolve route endpoints '%s' and '%s'",
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
        for (int index = 0; index < node->gateSize("ethg"); ++index) {
            cGate *output = node->gate("ethg$o", index);
            if (output == nullptr || output->getNextGate() == nullptr)
                continue;
            cGate *endGate = output->getPathEndGate();
            cModule *neighbor = endGate == nullptr ? nullptr : endGate->getOwnerModule();
            while (neighbor != nullptr && neighbor->getParentModule() != network)
                neighbor = neighbor->getParentModule();
            if (neighbor == nullptr || predecessor.find(neighbor->getName()) != predecessor.end())
                continue;
            predecessor[neighbor->getName()] = {node->getName(), index};
            pending.push(neighbor);
        }
    }
    if (predecessor.find(destination->getName()) == predecessor.end())
        throw cRuntimeError("BFS found no route from '%s' to '%s' after fault %s-%s",
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

    RoutePath result;
    result.nodePath = reverseNodes;
    for (size_t hop = 0; hop < reverseEgressIndices.size(); ++hop) {
        const std::string& nodeName = reverseNodes[hop];
        int egressIndex = reverseEgressIndices[hop];
        cModule *node = network->getSubmodule(nodeName.c_str());
        if (node == nullptr || node->getSubmodule("macTable") == nullptr)
            throw cRuntimeError("BFS route contains unsupported forwarding node '%s'", nodeName.c_str());
        std::string interfaceName = "eth" + std::to_string(egressIndex);
        result.routes.push_back({nodeName, fault.destination, interfaceName});
        result.egressInterfacePaths.push_back(nodeName + ".eth[" + std::to_string(egressIndex) + "]");
    }
    return result;
}

} // namespace tsn_fault_recovery
