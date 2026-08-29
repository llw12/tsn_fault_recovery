#include "BfsRouteSolver.h"
#include <algorithm>
#include <map>
#include <queue>
#include <stdexcept>

namespace tsn_fault_recovery {
struct BfsPredecessor { std::string nodeId; std::string linkId; };

LogicalRoute BfsRouteSolver::solve(const NetworkGraph& graph, const std::string& flowId,
        const std::string& source, const std::string& destination,
        const std::set<std::string>& disabledLinks) const
{
    if (!graph.hasNode(source) || !graph.hasNode(destination))
        throw std::invalid_argument("BFS route endpoint is not in NetworkGraph");
    std::queue<std::string> pending;
    std::map<std::string, BfsPredecessor> predecessor;
    predecessor[source] = {"", ""}; pending.push(source);
    while (!pending.empty() && !predecessor.count(destination)) {
        auto current = pending.front(); pending.pop();
        for (const auto& [neighbor, linkId] : graph.neighbors(current, disabledLinks)) {
            if (predecessor.count(neighbor)) continue;
            predecessor[neighbor] = {current, linkId}; pending.push(neighbor);
        }
    }
    if (!predecessor.count(destination))
        throw std::runtime_error("BFS found no route for flow " + flowId + " from " + source + " to " + destination);
    LogicalRoute route; route.flowId = flowId;
    std::string current = destination; route.nodePath.push_back(current);
    while (current != source) {
        const auto& step = predecessor.at(current);
        route.linkPath.push_back(step.linkId); current = step.nodeId; route.nodePath.push_back(current);
    }
    std::reverse(route.nodePath.begin(), route.nodePath.end());
    std::reverse(route.linkPath.begin(), route.linkPath.end());
    return route;
}
} // namespace tsn_fault_recovery
