#ifndef __TSN_FAULT_RECOVERY_TIMETICKCONVERTER_H
#define __TSN_FAULT_RECOVERY_TIMETICKCONVERTER_H

#include <cstdint>

#include <omnetpp.h>

namespace tsn_fault_recovery {

class TimeTickConverter
{
  public:
    static int64_t exactTicks(omnetpp::simtime_t value, omnetpp::simtime_t quantum, const char *label);
    static int64_t serializationTicks(int packetBytes, int frameOverheadBytes, double linkBitrate,
            omnetpp::simtime_t quantum);
};

} // namespace tsn_fault_recovery

#endif
