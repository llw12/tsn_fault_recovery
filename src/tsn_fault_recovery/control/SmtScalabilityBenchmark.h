#ifndef __TSN_FAULT_RECOVERY_SMTSCALABILITYBENCHMARK_H
#define __TSN_FAULT_RECOVERY_SMTSCALABILITYBENCHMARK_H

#include <omnetpp.h>

namespace tsn_fault_recovery {

class SmtScalabilityBenchmark : public omnetpp::cSimpleModule
{
  protected:
    virtual void initialize() override;
    virtual void handleMessage(omnetpp::cMessage *) override;
};

} // namespace tsn_fault_recovery
#endif
