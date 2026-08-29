#include "LegacyRuntimeTopologyAdapter.h"
#include <algorithm>
#include <set>

using namespace omnetpp;
namespace tsn_fault_recovery {

namespace {
std::string neighborName(cModule *network, cGate *output) {
    if (!output || !output->getNextGate()) return "";
    cGate *end = output->getPathEndGate();
    cModule *neighbor = end ? end->getOwnerModule() : nullptr;
    while (neighbor && neighbor->getParentModule() != network) neighbor = neighbor->getParentModule();
    return neighbor ? neighbor->getName() : "";
}
std::string linkId(std::string a, std::string b) { if (b < a) std::swap(a,b); return "legacy__" + a + "__" + b; }
}

LegacyGraphCapture LegacyRuntimeTopologyAdapter::capture(cModule *network)
{
    LegacyGraphCapture result;
    std::vector<cModule *> nodes;
    for (cModule::SubmoduleIterator iterator(network); !iterator.end(); ++iterator) {
        cModule *node = *iterator;
        if (!node->hasGate("ethg")) continue;
        nodes.push_back(node);
        result.graph.addNode({node->getName(), node->getSubmodule("macTable") ? NodeType::SWITCH : NodeType::END_SYSTEM});
    }
    std::set<std::string> added;
    for (cModule *node : nodes) {
        for (int index=0; index<node->gateSize("ethg"); ++index) {
            std::string neighbor = neighborName(network, node->gate("ethg$o", index));
            if (neighbor.empty()) continue;
            std::string id = linkId(node->getName(), neighbor);
            result.egressInterfaces[{id,node->getName()}] = "eth" + std::to_string(index);
            if (added.insert(id).second) result.graph.addLink({id,node->getName(),neighbor,0,0});
        }
    }
    return result;
}

RoutePath LegacyRuntimeTopologyAdapter::compile(const LogicalRoute& logical, const std::string& destination,
        const LegacyGraphCapture& capture, cModule *network)
{
    RoutePath result; result.nodePath = logical.nodePath;
    for (size_t hop=0; hop<logical.linkPath.size(); ++hop) {
        const auto& node = logical.nodePath[hop];
        cModule *module = network->getSubmodule(node.c_str());
        if (!module || !module->getSubmodule("macTable")) continue;
        auto interface = capture.egressInterfaces.at({logical.linkPath[hop],node});
        result.routes.push_back({node,destination,interface,logical.flowId,logical.linkPath[hop]});
        std::string index = interface.substr(3);
        result.egressInterfacePaths.push_back(node + ".eth[" + index + "]");
    }
    return result;
}
} // namespace tsn_fault_recovery
