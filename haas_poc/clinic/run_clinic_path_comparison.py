from collections import defaultdict
from haas_poc.clinic.load_events import load_events

def main():
    events = load_events()
    phrs_mode = all(e.get("clinic_id") is None for e in events)

    buckets = defaultdict(lambda: defaultdict(lambda: {"n":0,"approved":0,"denied":0}))

    for e in events:
        clinic = "ALL_CLINICS" if phrs_mode else e["clinic_id"]
        path = e.get("approval_channel") or "UNKNOWN"

        n = e.get("n", 1)
        buckets[clinic][path]["n"] += n
        buckets[clinic][path]["approved"] += e.get("approved",0)
        buckets[clinic][path]["denied"] += e.get("denied",0)

    print("\n=== PATH COMPARISON ===")
    print("MODE:", "PHRS (Path Reliability)" if phrs_mode else "RXP (Clinic Execution)")
    print()

    for clinic, paths in buckets.items():
        print(f"\nClinic: {clinic}")
        for p, v in sorted(paths.items(), key=lambda x: -x[1]["n"]):
            t = v["n"]
            a = v["approved"]
            d = v["denied"]
            o = max(t-a-d,0)
            if t == 0: continue
            print(
                f"  {p:<18} "
                f"total={t:>8,} "
                f"appr%={100*a/t:6.2f} "
                f"deny%={100*d/t:6.2f} "
                f"other%={100*o/t:6.2f}"
            )

if __name__ == "__main__":
    main()
