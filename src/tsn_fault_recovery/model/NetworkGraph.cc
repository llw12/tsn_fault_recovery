#include "NetworkGraph.h"
#include <algorithm>
#include <stdexcept>

namespace tsn_fault_recovery {
void NetworkGraph::addNode(const GraphNode& node) {
    if (node.id.empty() || nodes.count(node.id)) throw std::invalid_argument("NetworkGraph node ID is empty or duplicated: " + node.id);
    nodes.emplace(node.id, node);
}
void NetworkGraph::addLink(const GraphLink& link) {
    if (link.id.empty() || links.count(link.id)) throw std::invalid_argument("NetworkGraph link ID is empty or duplicated: " + link.id);
    if (!hasNode(link.endpointA) || !hasNode(link.endpointB) || link.endpointA == link.endpointB) throw std::invalid_argument("NetworkGraph link has invalid endpoints: " + link.id);
    links.emplace(link.id, link);
}
bool NetworkGraph::hasNode(const std::string& id) const { return nodes.count(id) != 0; }
bool NetworkGraph::hasLink(const std::string& id) const { return links.count(id) != 0; }
const GraphNode& NetworkGraph::getNode(const std::string& id) const { auto it=nodes.find(id); if(it==nodes.end()) throw std::out_of_range("Unknown graph node: "+id); return it->second; }
const GraphLink& NetworkGraph::getLink(const std::string& id) const { auto it=links.find(id); if(it==links.end()) throw std::out_of_range("Unknown graph link: "+id); return it->second; }
std::vector<std::pair<std::string, std::string>> NetworkGraph::neighbors(const std::string& nodeId, const std::set<std::string>& disabledLinks) const {
    if (!hasNode(nodeId)) throw std::out_of_range("Unknown graph node: " + nodeId);
    std::vector<std::pair<std::string,std::string>> result;
    for (const auto& [id,link] : links) { if(disabledLinks.count(id)) continue; if(link.endpointA==nodeId) result.emplace_back(link.endpointB,id); else if(link.endpointB==nodeId) result.emplace_back(link.endpointA,id); }
    std::sort(result.begin(),result.end()); return result;
}
} // namespace tsn_fault_recovery
