from collections import defaultdict
from haas_poc.clinic.load_events import load_events

# ---- Channel bucketing (same logic you validated earlier) ----
DIGITAL_KEYS = {"DIGITAL", "CUSTOMER_EMAIL", "EMAIL", "FAX", "VET"}
MAIL_KEYS = {"CUSTOMER_MAIL", "MAIL"}
PHONE_KEYS = {"PH", "PHONE"}

def bucket_channel(raw):
    c = (raw or "").upper()
    if any(k in c for k in MAIL_KEYS):
        return "MAIL"
    if any(k in c for k in PHONE_KEYS):
        return "PHONE"
    if any(k in c for k in DIGITAL_KEYS):
        return "DIGITAL"
    return "OTHER"

# ---- Load canonical events ----
events = load_events()

# ---- Aggregate ----
stats = defaultdict(lambda: defaultdict(lambda: {
    "total": 0,
    "approved": 0,
    "denied": 0,
}))

for e in events:
    clinic_id = e["clinic_id"]
    product = e["product"]              # RX | VET_DIET
    channel = bucket_channel(e["approval_channel"])
    status = e["rx_status"].upper()

    stats[(clinic_id, product)][channel]["total"] += 1

    if status == "APPROVED":
        stats[(clinic_id, product)][channel]["approved"] += 1
    elif status == "DENIED":
        stats[(clinic_id, product)][channel]["denied"] += 1

# ---- Render report ----
print("# HOSS — Clinic Approval Behavior Report (Real Data)\n")

printed = 0

for (clinic_id, product), channels in stats.items():
    # Require some signal to avoid noise
    total_events = sum(v["total"] for v in channels.values())
    if total_events < 100:
        continue

    print(f"## Clinic {clinic_id} | Product: {product}")
    print(f"Total prescriptions: {total_events}")

    for channel, m in sorted(channels.items(), key=lambda x: x[1]["total"], reverse=True):
        if m["total"] == 0:
            continue

        approval_rate = m["approved"] / m["total"] if m["total"] else 0
        denial_rate = m["denied"] / m["total"] if m["total"] else 0

        print(
            f"- {channel:8} | "
            f"total={m['total']:6} | "
            f"approved={approval_rate:5.1%} | "
            f"denied={denial_rate:5.1%}"
        )

    print()
    printed += 1
    if printed >= 20:
        break
