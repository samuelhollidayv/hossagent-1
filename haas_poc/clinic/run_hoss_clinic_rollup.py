from collections import defaultdict
from haas_poc.clinic.outcome_classifier import classify_outcome
import snowflake.connector
import os

# --- Snowflake connection (SSO) ---
conn = snowflake.connector.connect(
    account=os.getenv("SNOWFLAKE_ACCOUNT"),
    user=os.getenv("SNOWFLAKE_USER"),
    role=os.getenv("SNOWFLAKE_ROLE"),
    warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
    database="EDLDB",
    schema="BT_HCA_HCDM",
    authenticator="externalbrowser",
)

cur = conn.cursor()

sql = """
SELECT
    CLINIC_ID,
    IS_VET_DIET_FLAG,
    APPROVAL_CHANNEL,
    RX_STATUS,
    STATE_CHANGE_REASON
FROM RXP_PRESCRIPTIONS
WHERE PRESCRIBED_DATETIME >= '2025-12-01'
  AND CLINIC_ID IS NOT NULL
"""

cur.execute(sql)

stats = defaultdict(lambda: defaultdict(lambda: defaultdict(int)))

for clinic_id, is_vet_diet, channel, rx_status, reason in cur.fetchall():
    product = "VET_DIET" if is_vet_diet else "RX"
    channel = (channel or "UNKNOWN").upper()
    outcome = classify_outcome(rx_status, reason)

    stats[(clinic_id, product)][channel][outcome] += 1
    stats[(clinic_id, product)][channel]["TOTAL"] += 1

cur.close()
conn.close()

# --- Print report ---
print("\n# HOSS — Clinic Approval Behavior (Real Outcomes)\n")

shown = 0
for (clinic_id, product), channels in stats.items():
    total = sum(v["TOTAL"] for v in channels.values())
    if total < 100:
        continue  # noise filter

    print(f"## Clinic {clinic_id} | Product: {product}")
    print(f"Total prescriptions: {total}")

    for channel, c in channels.items():
        t = c["TOTAL"]
        if t < 10:
            continue

        success = c["SUCCESS"] / t * 100
        hard = c["FAIL_HARD"] / t * 100
        soft = c["FAIL_SOFT"] / t * 100
        inflight = c["IN_PROGRESS"] / t * 100

        print(
            f"- {channel:<10} | total={t:5d} | "
            f"success={success:5.1f}% | "
            f"hard_fail={hard:5.1f}% | "
            f"soft_fail={soft:5.1f}% | "
            f"in_flight={inflight:5.1f}%"
        )

    print()
    shown += 1
    if shown >= 20:
        break
