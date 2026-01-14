from collections import Counter
from haas_poc.clinic.load_events import load_events

events = load_events()

print("Loaded clinic events:", len(events))

counts = Counter(e["outcome"] for e in events)

print("\nOutcome distribution:")
total = sum(counts.values())
for k, v in counts.items():
    print(f"- {k:<15} {v:>10,}  ({v/total:.1%})")

print("\nSample rows:")
for e in events[:10]:
    print(e)
