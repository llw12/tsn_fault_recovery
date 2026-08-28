#include <memory>
#include <sstream>
#include <unordered_map>

#include <omnetpp.h>

#include "inet/common/packet/Packet.h"

using namespace omnetpp;
using namespace inet;

namespace tsn_fault_recovery {

namespace {

std::vector<std::string> splitWords(const char *text)
{
    std::istringstream stream(text);
    std::vector<std::string> words;
    std::string word;
    while (stream >> word)
        words.push_back(word);
    return words;
}

} // namespace

class PacketIdentityRecorder : public cSimpleModule, public cListener
{
  private:
    struct FlowVectors
    {
        std::string flowId;
        cComponent *source = nullptr;
        cComponent *destination = nullptr;
        std::unique_ptr<cOutVector> sent;
        std::unique_ptr<cOutVector> received;
    };

    std::vector<FlowVectors> flows;
    simsignal_t sentSignal;
    simsignal_t receivedSignal;
    bool subscribed = false;

    void unsubscribeSources()
    {
        if (!subscribed)
            return;
        for (auto& flow : flows) {
            flow.source->unsubscribe(sentSignal, this);
            flow.destination->unsubscribe(receivedSignal, this);
            flow.source = nullptr;
            flow.destination = nullptr;
        }
        subscribed = false;
    }

    int parseSequence(const std::string& flowId, const char *packetName) const
    {
        std::string prefix = flowId + "-";
        std::string name = packetName;
        if (name.rfind(prefix, 0) != 0)
            throw cRuntimeError("Packet '%s' does not carry expected explicit identity prefix '%s'",
                    packetName, prefix.c_str());
        size_t consumed = 0;
        int sequence = std::stoi(name.substr(prefix.size()), &consumed);
        if (consumed != name.size() - prefix.size())
            throw cRuntimeError("Packet '%s' has an invalid sequence suffix", packetName);
        return sequence;
    }

  protected:
    virtual void initialize() override
    {
        if (!par("enabled").boolValue())
            return;
        auto ids = splitWords(par("flowIds").stringValue());
        auto sources = splitWords(par("sourceModules").stringValue());
        auto destinations = splitWords(par("destinationModules").stringValue());
        if (ids.empty() || ids.size() != sources.size() || ids.size() != destinations.size())
            throw cRuntimeError("PacketIdentityRecorder flow/module lists must have equal non-zero length");
        sentSignal = registerSignal("packetSent");
        receivedSignal = registerSignal("packetReceived");
        cModule *network = getParentModule();
        for (size_t i = 0; i < ids.size(); ++i) {
            cComponent *source = network->getModuleByPath(sources[i].c_str());
            cComponent *destination = network->getModuleByPath(destinations[i].c_str());
            if (source == nullptr || destination == nullptr)
                throw cRuntimeError("Cannot resolve identity recorder endpoints '%s'/'%s'",
                        sources[i].c_str(), destinations[i].c_str());
            FlowVectors vectors;
            vectors.flowId = ids[i];
            vectors.source = source;
            vectors.destination = destination;
            vectors.sent = std::make_unique<cOutVector>((ids[i] + ".sentSequence").c_str());
            vectors.received = std::make_unique<cOutVector>((ids[i] + ".receivedSequence").c_str());
            flows.push_back(std::move(vectors));
            source->subscribe(sentSignal, this);
            destination->subscribe(receivedSignal, this);
        }
        subscribed = true;
    }

    virtual void handleMessage(cMessage *) override
    {
        throw cRuntimeError("PacketIdentityRecorder does not accept messages");
    }

    virtual void finish() override
    {
        unsubscribeSources();
    }

    virtual void receiveSignal(cComponent *source, simsignal_t signalId, cObject *value, cObject *) override
    {
        auto *packet = dynamic_cast<Packet *>(value);
        if (packet == nullptr)
            throw cRuntimeError("PacketIdentityRecorder received a non-packet signal value");
        for (auto& flow : flows) {
            if (source == flow.source && signalId == sentSignal) {
                flow.sent->record(parseSequence(flow.flowId, packet->getName()));
                return;
            }
            if (source == flow.destination && signalId == receivedSignal) {
                flow.received->record(parseSequence(flow.flowId, packet->getName()));
                return;
            }
        }
        throw cRuntimeError("PacketIdentityRecorder received a signal from an unregistered source");
    }

  public:
    virtual ~PacketIdentityRecorder() = default;
};

Define_Module(PacketIdentityRecorder);

} // namespace tsn_fault_recovery
