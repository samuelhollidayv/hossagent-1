from collections import defaultdict
from haas_poc.clinic.load_events import load_events

DIGITAL_KEYWORDS = {"DIGITAL","EMAIL","FAX","VERBAL","PH","PBC","VET"}
MAIL_KEYWORDS = {"MAIL","SNAIL_MAIL"}
UPLOAD_KEYWORDS = {"UPLOAD"}

def bucket_channel(raw):
    c = raw.upper()
    if any(k in c for k in UPLOAD_KEYWORDS):
        return "UPLOAD"
    if any(k in c for k in MAIL_KEYWORDS):
        return "MAIL"
    if any(k in c for k in DIGITAL_KEYWORDS):
        return "DIGITAL"
    return None

def confidence_tier(adv):
    if adv is None:
        return "No clear advantage"
    if adv >= 1.5:
        return "Strongly recommended"
    if adv >= 1.15:
        return "Recommended"
    return "Slight edge"

# ---- Load canonical clinic events (Snowflake) ----
events = load_events()

# ---- Aggregate by clinic + channel ----
stats = defaultdict(lambda: defaultdict(lambda: {"total": 0, "success": 0}))

for e in events:
    bucket = bucket_channel(e["approval_channel"])
    if not bucket:
        continue

    stats[e["clinic_id"]][bucket]["total"] += 1

    # treat non-declined as success (can refine later)
    if "DECLINED" not in e["order_status"].upper():
        stats[e["clinic_id"]][bucket]["success"] += 1

print("# HOSS — Clinic Approval Path Readout (Snowflake-backed)\n")

printed = 0
for clinic_id, channels in stats.items():
    if "DIGITAL" not in channels or channels["DIGITAL"]["total"] < 20:
        continue

    digital_rate = (
        channels["DIGITAL"]["success"] / channels["DIGITAL"]["total"]
        if channels["DIGITAL"]["total"] > 0 else None
    )

    best = "DIGITAL"
    best_rate = digital_rate

    for ch, d in channels.items():
        if d["total"] < 10:
            continue
        rate = d["success"] / d["total"]
        if best_rate is None or rate > best_rate:
            best = ch
            best_rate = rate

    adv = None
    if best != "DIGITAL" and digital_rate:
        adv = round(best_rate / digital_rate, 2)

    tier = confidence_tier(adv)

    print(f"## Clinic {clinic_id}")
    print(f"- Best path: {best}")
    print(f"- Confidence: {tier}")

    if adv:
        print(f"- {best} succeeds ~{adv}× more often than digital")

    print("\n---\n")

    printed += 1
    if printed >= 15:
        break
