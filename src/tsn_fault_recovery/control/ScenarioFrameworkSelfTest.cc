#include <map>
#include <omnetpp.h>
#include "AffectedFlowAnalyzer.h"
#include "BfsRouteSolver.h"
#include "ScenarioRuntimeAdapter.h"

using namespace omnetpp;
namespace tsn_fault_recovery {
namespace { void require(bool condition,const char *message){if(!condition)throw cRuntimeError("Scenario framework self-test failed: %s",message);} }
class ScenarioFrameworkSelfTest : public cSimpleModule {
  protected: void initialize() override {
    NetworkGraph graph; for(const char *id:{"es1","es2","a","b","c","es5","es6"})graph.addNode({id,id[0]=='e'?NodeType::END_SYSTEM:NodeType::SWITCH});
    graph.addLink({"l1","es1","a",1e9,0});graph.addLink({"l2","es2","b",1e9,0});graph.addLink({"l3","a","b",1e9,0});graph.addLink({"l4","a","c",1e9,0});graph.addLink({"l5","b","c",1e9,0});graph.addLink({"l6","c","es5",1e9,0});graph.addLink({"l7","b","es6",1e9,0});
    BfsRouteSolver bfs;
    auto one=bfs.solve(graph,"TT1","es1","es5"); require(one.nodePath==std::vector<std::string>({"es1","a","c","es5"}),"basic BFS route");
    auto failed=bfs.solve(graph,"TT1","es1","es5",{"l4"}); require(failed.nodePath==std::vector<std::string>({"es1","a","b","c","es5"}),"failed link excluded");
    auto two=bfs.solve(graph,"TT2","es2","es6"); require(two.nodePath!=one.nodePath,"flows have independent routes");
    std::map<std::string,LogicalRoute> routes{{"TT1",one},{"TT2",two}}; auto affected=AffectedFlowAnalyzer::affectedFlowIds(routes,"l4"); require(affected==std::vector<std::string>({"TT1"}),"affected flow detection");
    require(routes.at("TT2").linkPath==two.linkPath,"unaffected route preserved");
    ScenarioRuntimeAdapter adapter({{{"l4","a"},{"eth2","a.eth[2]"}},{{"l6","c"},{"eth1","c.eth[1]"}}});
    auto paths=adapter.egressPaths(one,graph); require(paths==std::vector<std::string>({"a.eth[2]","c.eth[1]"}),"logical route compiles through port map");
    EV_INFO<<"SCENARIO_FRAMEWORK_SELF_TESTS PASS count=6"<<endl;
  }
};
Define_Module(ScenarioFrameworkSelfTest);
}
