# Timing semantics audit

The static detector calls the qualified `quantize_flow` adapter conversion. It uses the adapter's one-time frame overhead, `ceil` transmission conversion, integer 100 ns ticks, quantized release offset, relative deadline, and `fixed release=true` input. It then validates those fields against frozen exp18h upstream JSON rather than reconstructing them from floating-point seconds.
