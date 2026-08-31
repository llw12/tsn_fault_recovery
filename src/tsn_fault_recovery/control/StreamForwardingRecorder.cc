#include <fstream>
#include <map>
#include <sstream>
#include <tuple>

#include <omnetpp.h>

#include "inet/common/packet/Packet.h"
#include "inet/linklayer/common/VlanTag_m.h"

using namespace omnetpp;
using namespace inet;

namespace tsn_fault_recovery {

namespace {
std::vector<std::string> splitWords(const char *text)
{
    std::istringstream input(text);
    std::vector<std::string> values;
    std::string value;
    while (input >> value) values.push_back(value);
    return values;
}
}

class StreamForwardingRecorder : public cSimpleModule, public cListener
{
  private:
    struct Egress { std::string switchId; std::string interfaceName; };
    simsignal_t packetSentToLowerSignal;
    std::map<cComponent *, Egress> subscriptions;
    std::map<int, std::string> flowByHandle;
    std::map<std::tuple<std::string, int, std::string>, long> counts;

  protected:
    virtual void initialize() override
    {
        if (!par("enabled").boolValue()) return;
        auto switches = splitWords(par("switches").stringValue());
        auto flowIds = splitWords(par("flowIds").stringValue());
        auto handles = splitWords(par("streamHandles").stringValue());
        if (flowIds.size() != handles.size())
            throw cRuntimeError("StreamForwardingRecorder flowIds/streamHandles size mismatch");
        for (size_t i = 0; i < flowIds.size(); ++i)
            flowByHandle.emplace(std::stoi(handles[i]), flowIds[i]);
        packetSentToLowerSignal = registerSignal("packetSentToLower");
        auto *network = getParentModule();
        for (const auto& switchId : switches) {
            auto *switchModule = network->getSubmodule(switchId.c_str());
            if (switchModule == nullptr)
                throw cRuntimeError("Cannot resolve switch '%s' for stream forwarding recorder", switchId.c_str());
            int size = switchModule->getSubmoduleVectorSize("eth");
            for (int index = 0; index < size; ++index) {
                std::string path = switchId + ".eth[" + std::to_string(index) + "].macLayer.outboundEmitter";
                auto *component = network->findModuleByPath(path.c_str());
                if (component == nullptr)
                    throw cRuntimeError("Cannot resolve Ethernet MAC '%s'", path.c_str());
                subscriptions.emplace(component, Egress{switchId, "eth" + std::to_string(index)});
                component->subscribe(packetSentToLowerSignal, this);
            }
        }
    }

    virtual void handleMessage(cMessage *) override
    {
        throw cRuntimeError("StreamForwardingRecorder does not accept messages");
    }

    virtual void receiveSignal(cComponent *source, simsignal_t signalId, cObject *value, cObject *) override
    {
        if (signalId != packetSentToLowerSignal) return;
        auto subscription = subscriptions.find(source);
        auto *packet = dynamic_cast<Packet *>(value);
        if (subscription == subscriptions.end() || packet == nullptr)
            throw cRuntimeError("Invalid stream forwarding observation");
        int handle = -1;
        if (auto tag = packet->findTag<VlanReq>()) handle = tag->getVlanId();
        else if (auto tag = packet->findTag<VlanInd>()) handle = tag->getVlanId();
        if (handle < 0) return;
        const auto& egress = subscription->second;
        ++counts[{egress.switchId, handle, egress.interfaceName}];
        auto flow = flowByHandle.find(handle);
        EV_INFO << "STREAM_FORWARD_OBSERVED switch=" << egress.switchId
                << " streamHandle=" << handle
                << " flow=" << (flow == flowByHandle.end() ? "BE_OR_UNKNOWN" : flow->second)
                << " interface=" << egress.interfaceName << endl;
    }

    virtual void finish() override
    {
        for (const auto& [component, unused] : subscriptions)
            component->unsubscribe(packetSentToLowerSignal, this);
        std::string path = par("outputPath").stringValue();
        if (!path.empty()) {
            std::ofstream output(path);
            if (!output) throw cRuntimeError("Cannot write stream forwarding evidence '%s'", path.c_str());
            output << "switch,flow_id,stream_handle,observed_egress,packet_count\n";
            for (const auto& [key, count] : counts) {
                const auto& [switchId, handle, interfaceName] = key;
                auto flow = flowByHandle.find(handle);
                output << switchId << "," << (flow == flowByHandle.end() ? "BE_OR_UNKNOWN" : flow->second)
                       << "," << handle << "," << interfaceName << "," << count << "\n";
            }
        }
    }
};

Define_Module(StreamForwardingRecorder);

} // namespace tsn_fault_recovery
