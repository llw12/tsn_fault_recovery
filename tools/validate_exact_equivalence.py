#!/usr/bin/env python3
"""Build the per-member validation matrix and finalize synthesized classes."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.exact_equivalence import finalize_class_store


FIELDS = [
    "scenario", "class_id", "fault_id", "class_size", "profile_id", "profile_sha256",
    "same_profile_for_all_members", "activation_ok", "failed_link_avoided", "forwarding_valid",
    "tt_sent", "tt_received", "tt_lost", "deadline_miss_count", "deadline_miss_ratio",
    "first_success_after_fault_s", "recovery_duration_us", "stable_post_recovery_start_s",
    "post_recovery_delivery_ok", "post_recovery_deadline_ok", "runtime_route_solver_invocations",
    "runtime_z3_solver_invocations", "runtime_profile_synthesis_invocations", "validation_pass", "diagnostic",
]


def truth(value) -> bool:
    return str(value).lower() in {"1", "true", "yes"}


def build_rows(generated: Path, run_id: str) -> list[dict]:
    store = json.loads((generated / "profiles/exact_equivalence/store.json").read_text())
    rows = []
    for class_id, entry in store["classes"].items():
        for fault in entry["members"]:
            run = ROOT / "results/scenarios" / store["scenario_name"] / "offline-exact-equivalence" / fault / run_id
            summary_path = run / "summary.csv"
            if not summary_path.exists():
                raise RuntimeError(f"missing exact-equivalence validation run: {summary_path}")
            summary = next(csv.DictReader(summary_path.open()))
            profile = json.loads((run / "recovery_profile.json").read_text())
            avoids = all(fault not in route["link_path"] for route in profile["logical_routes"])
            activation = summary["activation_time_s"] != ""
            forwarding = activation
            zero_runtime = all(int(summary[field]) == 0 for field in (
                "runtime_route_solver_invocations", "runtime_z3_solver_invocations", "runtime_profile_synthesis_invocations"))
            delivery = truth(summary["post_recovery_delivery_ok"]); deadlines = truth(summary["post_recovery_deadline_ok"])
            passed = activation and avoids and forwarding and zero_runtime and delivery and deadlines and summary["first_success_after_fault_s"] != ""
            diagnostic = "" if passed else ";".join(name for name, ok in (
                ("activation", activation), ("failed-link-avoidance", avoids), ("forwarding", forwarding),
                ("zero-runtime-synthesis", zero_runtime), ("stable-delivery", delivery), ("stable-deadlines", deadlines)) if not ok)
            rows.append({
                "scenario": store["scenario_name"], "class_id": class_id, "fault_id": fault,
                "class_size": len(entry["members"]), "profile_id": entry["profile_id"],
                "profile_sha256": entry["profile_sha256"], "same_profile_for_all_members": True,
                "activation_ok": activation, "failed_link_avoided": avoids, "forwarding_valid": forwarding,
                "tt_sent": int(summary["tt_sent"]), "tt_received": int(summary["tt_received"]),
                "tt_lost": int(summary["tt_lost"]), "deadline_miss_count": int(summary["deadline_miss_count"]),
                "deadline_miss_ratio": float(summary["deadline_miss_ratio"]),
                "first_success_after_fault_s": summary["first_success_after_fault_s"],
                "recovery_duration_us": float(summary["recovery_duration_s"]) * 1e6,
                "stable_post_recovery_start_s": summary["stable_post_recovery_start_s"],
                "post_recovery_delivery_ok": delivery, "post_recovery_deadline_ok": deadlines,
                "runtime_route_solver_invocations": int(summary["runtime_route_solver_invocations"]),
                "runtime_z3_solver_invocations": int(summary["runtime_z3_solver_invocations"]),
                "runtime_profile_synthesis_invocations": int(summary["runtime_profile_synthesis_invocations"]),
                "validation_pass": passed, "diagnostic": diagnostic,
            })
    for class_id in {row["class_id"] for row in rows}:
        hashes = {row["profile_sha256"] for row in rows if row["class_id"] == class_id}
        same = len(hashes) == 1
        for row in rows:
            if row["class_id"] == class_id:
                row["same_profile_for_all_members"] = same
                row["validation_pass"] = row["validation_pass"] and same
    return rows


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--scenario-name", required=True)
    parser.add_argument("--run-id", required=True)
    args = parser.parse_args()
    generated = ROOT / "generated" / args.scenario_name
    rows = build_rows(generated, args.run_id)
    output = generated / "profiles/exact_equivalence/class_validation.csv"
    with output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=FIELDS, lineterminator="\n"); writer.writeheader(); writer.writerows(rows)
    store = finalize_class_store(generated / "profiles/exact_equivalence/store.json", rows)
    print(output)
    print(f"validation_rows={len(rows)} passed={sum(row['validation_pass'] for row in rows)} classes={len(store['classes'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
