from collections import Counter, defaultdict
import os, snowflake.connector

def connect():
    return snowflake.connector.connect(
      account=os.getenv("SNOWFLAKE_ACCOUNT"),
      user=os.getenv("SNOWFLAKE_USER"),
      role=os.getenv("SNOWFLAKE_ROLE"),
      warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
      database="EDLDB",
      schema="BT_HCA_HCDM",
      authenticator="externalbrowser",
    )

def main():
    conn = connect()
    cur = conn.cursor()
    try:
        cur.execute("""
SELECT
  COMMON_DATE,
  APPROVAL_CHANNEL,
  INITIATION_CHANNEL,
  IS_VET_DIET_FLAG,
  RX_STATUS,
  STATE_CHANGE_REASON,
  PRESCRIPTIONS,
  APPROVED_PRESCRIPTIONS,
  DENIED_PRESCRIPTIONS,
  MAIL_IN_PRESCRIPTION_PIZZA_TRACKER,
  PENDING_PHARMACIST_REVIEW_PIZZA_TRACKER,
  PENDING_VET_APPROVAL_PIZZA_TRACKER,
  PLEASE_CONTACT_US_PIZZA_TRACKER,
  PLEASE_CONTACT_VET_PIZZA_TRACKER
FROM PHRS_PRESCRIPTIONS
WHERE COMMON_DATE >= '2025-12-01'
""")
        rows = cur.fetchall()
        cols = [d[0] for d in cur.description]
        ix = {c:i for i,c in enumerate(cols)}

        total = 0
        approved = 0
        denied = 0
        by_channel = defaultdict(lambda: [0,0,0])  # total, approved, denied
        by_init = defaultdict(lambda: [0,0,0])
        by_pizza = Counter()

        for r in rows:
            n = int(r[ix["PRESCRIPTIONS"]] or 0)
            a = int(r[ix["APPROVED_PRESCRIPTIONS"]] or 0)
            d = int(r[ix["DENIED_PRESCRIPTIONS"]] or 0)

            total += n
            approved += a
            denied += d

            ch = (r[ix["APPROVAL_CHANNEL"]] or "UNKNOWN").strip()
            it = (r[ix["INITIATION_CHANNEL"]] or "UNKNOWN").strip()
            by_channel[ch][0] += n; by_channel[ch][1] += a; by_channel[ch][2] += d
            by_init[it][0] += n; by_init[it][1] += a; by_init[it][2] += d

            for k in [
                "MAIL_IN_PRESCRIPTION_PIZZA_TRACKER",
                "PENDING_PHARMACIST_REVIEW_PIZZA_TRACKER",
                "PENDING_VET_APPROVAL_PIZZA_TRACKER",
                "PLEASE_CONTACT_US_PIZZA_TRACKER",
                "PLEASE_CONTACT_VET_PIZZA_TRACKER",
            ]:
                v = r[ix[k]]
                if v not in (None, 0, False):
                    by_pizza[k] += n

        def pct(x, denom): return 0.0 if denom == 0 else 100.0 * x / denom

        print(f"\nPHRS TOTAL PRESCRIPTIONS: {total:,}")
        print(f"APPROVED: {approved:,} ({pct(approved,total):.2f}%)")
        print(f"DENIED:   {denied:,} ({pct(denied,total):.2f}%)")
        print(f"OTHER:    {max(total-approved-denied,0):,} ({pct(max(total-approved-denied,0),total):.2f}%)")

        print("\n=== By APPROVAL_CHANNEL ===")
        for ch,(t,a,d) in sorted(by_channel.items(), key=lambda x: x[1][0], reverse=True)[:25]:
            print(f"{ch:<18} total={t:>10,}  appr%={pct(a,t):>6.2f}  deny%={pct(d,t):>6.2f}  other%={pct(max(t-a-d,0),t):>6.2f}")

        print("\n=== By INITIATION_CHANNEL ===")
        for it,(t,a,d) in sorted(by_init.items(), key=lambda x: x[1][0], reverse=True)[:25]:
            print(f"{it:<18} total={t:>10,}  appr%={pct(a,t):>6.2f}  deny%={pct(d,t):>6.2f}  other%={pct(max(t-a-d,0),t):>6.2f}")

        print("\n=== Pizza tracker incidence (weighted) ===")
        for k,c in by_pizza.most_common():
            print(f"{k:<45} {c:>10,}  ({pct(c,total):.2f}%)")

    finally:
        try: cur.close()
        finally: conn.close()

if __name__ == "__main__":
    main()
