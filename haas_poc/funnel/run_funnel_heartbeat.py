from collections import defaultdict
from haas_poc.funnel.data_adapters.heartbeat_sf import load_funnel_snapshots_from_heartbeat

def pct_change(cur, prev):
    if prev == 0:
        return None
    return (cur - prev) / prev

snapshots = load_funnel_snapshots_from_heartbeat("2025-12-01")

by_segment = defaultdict(dict)

for s in snapshots:
    key = (s["group"]["DEVICE_CATEGORY"], s["group"]["CHANNEL"])
    by_segment[key][s["date"]] = s["kpis"]

print("# Funnel Readout — Heartbeat (Delta-based)\n")

for (device, channel), days in by_segment.items():
    dates = sorted(days.keys())
    if len(dates) < 2:
        continue

    cur_d, prev_d = dates[-1], dates[-2]
    cur, prev = days[cur_d], days[prev_d]

    print(f"## Segment: DEVICE={device} | CHANNEL={channel}")
    print(f"Baseline: {prev_d} → {cur_d}\n")

    deltas = []
    for kpi in cur:
        d = pct_change(cur[kpi], prev[kpi])
        if d is None:
            continue
        deltas.append((kpi, cur[kpi], prev[kpi], d))

    deltas.sort(key=lambda x: abs(x[3]), reverse=True)

    print("**Top movers**")
    for kpi, c, p, d in deltas[:8]:
        sign = "+" if d > 0 else ""
        print(f"- {kpi}: {c:.0f} vs {p:.0f} ({sign}{d:.1%})")

    print("\n---\n")
