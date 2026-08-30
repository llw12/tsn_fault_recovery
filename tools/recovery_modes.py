"""Recovery-mode registry: scenario data stays independent of method choice."""

from dataclasses import dataclass


@dataclass(frozen=True)
class RecoveryMode:
    name: str
    implemented: bool
    profile_provider: str | None


MODES = {
    "no-recovery": RecoveryMode("no-recovery", True, None),
    "online": RecoveryMode("online", True, "online-at-fault"),
    "offline-per-failure": RecoveryMode("offline-per-failure", True, "precomputed-per-failure"),
    "offline-exact-equivalence": RecoveryMode("offline-exact-equivalence", True, "precomputed-exact-class"),
    "offline-approx-equivalence": RecoveryMode("offline-approx-equivalence", True, "precomputed-approximate-class"),
    "offline-cluster": RecoveryMode("offline-cluster", False, "precomputed-cluster"),
}


def require_implemented(name: str) -> RecoveryMode:
    mode = MODES[name]
    if not mode.implemented:
        raise NotImplementedError(f"NOT_IMPLEMENTED: recovery mode {name}")
    return mode
