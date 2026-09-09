# Instrumentation design

`--diagnostic-trace` is default-off and is only passed to H2S. The observer returns void, copies JSON state only, never calls RNG, never invokes placement a second time, and writes actual scheduler/configuration/search events. Trace-off passes a null observer.
