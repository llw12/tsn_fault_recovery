#ifndef __TSN_FAULT_RECOVERY_NETWORKGRAPH_H
#define __TSN_FAULT_RECOVERY_NETWORKGRAPH_H

#include <map>
#include <set>
#include <string>
#include <vector>

namespace tsn_fault_recovery {

enum class NodeType { END_SYSTEM, SWITCH };

struct GraphNode { std::string id; NodeType type = NodeType::SWITCH; };
struct GraphLink { std::string id; std::string endpointA; std::string endpointB; double bitrate = 0; double propagationDelay = 0; };
struct LogicalRoute { std::string flowId; std::vector<std::string> nodePath; std::vector<std::string> linkPath; };

class NetworkGraph
{
  private:
    std::map<std::string, GraphNode> nodes;
    std::map<std::string, GraphLink> links;
  public:
    void addNode(const GraphNode& node);
    void addLink(const GraphLink& link);
    bool hasNode(const std::string& id) const;
    bool hasLink(const std::string& id) const;
    const GraphNode& getNode(const std::string& id) const;
    const GraphLink& getLink(const std::string& id) const;
    std::vector<std::pair<std::string, std::string>> neighbors(const std::string& nodeId,
            const std::set<std::string>& disabledLinks = {}) const;
    const std::map<std::string, GraphNode>& getNodes() const { return nodes; }
    const std::map<std::string, GraphLink>& getLinks() const { return links; }
};

} // namespace tsn_fault_recovery
#endif
