#include "TimeTickConverter.h"

#include <cmath>

using namespace omnetpp;

namespace tsn_fault_recovery {

int64_t TimeTickConverter::exactTicks(simtime_t value, simtime_t quantum, const char *label)
{
    if (value < SIMTIME_ZERO || quantum <= SIMTIME_ZERO)
        throw cRuntimeError("%s and time quantum must be non-negative/positive", label);
    long double ratio = static_cast<long double>(value.dbl()) / quantum.dbl();
    int64_t ticks = std::llround(ratio);
    if (std::fabs(ratio - ticks) > 1e-9L)
        throw cRuntimeError("%s=%s is not exactly representable by quantum=%s",
                label, value.str().c_str(), quantum.str().c_str());
    return ticks;
}

int64_t TimeTickConverter::serializationTicks(int packetBytes, int frameOverheadBytes,
        double linkBitrate, simtime_t quantum)
{
    if (packetBytes <= 0 || frameOverheadBytes < 0 || linkBitrate <= 0 || quantum <= SIMTIME_ZERO)
        throw cRuntimeError("Invalid serialization parameters");
    long double seconds = static_cast<long double>(packetBytes + frameOverheadBytes) * 8.0L / linkBitrate;
    long double ticks = seconds / quantum.dbl();
    return static_cast<int64_t>(std::ceil(ticks - 1e-12L));
}

} // namespace tsn_fault_recovery
