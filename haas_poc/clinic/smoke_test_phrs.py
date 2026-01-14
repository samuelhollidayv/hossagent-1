from collections import Counter, defaultdict
from haas_poc.clinic.load_events import load_events

def main():
    events = load_events()

    # PHRS is aggregated; weight everything by n
    total_n = sum(e["n"] for e in events if e.get("clinic_id") is not None)
    print(f"\nTOTAL PRESCRIPTIONS (weighted): {total_n:,}\n")

    # Core reliability metrics:
    # - clean_success: approved w/ no defect signal
    # - defect_recovered: approved but defect was hit
    # - defect_failed: denied (defect likely, but we treat denied as failed)
    # - in_flight: not approved/denied (remaining)
    clean_success = 0
    defect_recovered = 0
    defect_failed = 0
    in_flight = 0

    # Also show top defect flags (weighted)
    defect_counter = Counter()

    for e in events:
        n = e["n"]
        approved = e.get("approved", 0)
        denied = e.get("denied", 0)
        defect_any = bool(e.get("defect_any"))

        # approvals/denials are counts in the cohort row
        # remaining are "not resolved" in this cohort slice
        unresolved = max(n - approved - denied, 0)

        if not defect_any:
            clean_success += approved
        else:
            defect_recovered += approved
            for d in e.get("defects", []):
                defect_counter[d] += n  # weight by cohort size

        defect_failed += denied
        in_flight += unresolved

    def pct(x): return (100.0 * x / total_n) if total_n else 0.0

    print("=== OUTCOMES (Defects-aware) ===")
    print(f"CLEAN_SUCCESS     {clean_success:>10,}  ({pct(clean_success):5.1f}%)")
    print(f"DEFECT_RECOVERED  {defect_recovered:>10,}  ({pct(defect_recovered):5.1f}%)")
    print(f"DEFECT_FAILED     {defect_failed:>10,}  ({pct(defect_failed):5.1f}%)")
    print(f"IN_FLIGHT         {in_flight:>10,}  ({pct(in_flight):5.1f}%)")

    print("\n=== TOP DEFECT FLAGS (weighted by PRESCRIPTIONS) ===")
    for d, c in defect_counter.most_common(20):
        print(f"{d:<55} {c:>12,}  ({pct(c):5.1f}%)")

if __name__ == "__main__":
    main()
