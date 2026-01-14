import os
import snowflake.connector
from collections import defaultdict

# ---------------- CONFIG ----------------
START_DATE = '2025-12-01'

RX_MIN_N = 200
VD_MIN_N = 100

ALT_RX_MIN_N = 100
ALT_VD_MIN_N = 50

RX_APPROVAL_DELTA = -10.0   # pts below baseline
VD_APPROVAL_DELTA = -8.0

WAREHOUSE = os.getenv("SNOWFLAKE_WAREHOUSE")

# Baselines (from PHRS global)
BASELINES = {
    (False, "DIGITAL"): 0.7456,
    (False, "VET"): 0.6970,
    (False, "CUSTOMER_MAIL"): 0.2005,
    (True,  "PH"): 0.8642,
}

ALLOWED_PATHS = {
    False: {"DIGITAL", "VET", "CUSTOMER_MAIL", "VERBAL"},
    True:  {"PH", "FAX", "CUSTOMER_UPLOAD", "CUSTOMER_EMAIL", "VERBAL", "CLINIC_EMAIL", "SNAIL_MAIL"},
}

# ---------------- CONNECT ----------------
def connect():
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        database="EDLDB",
        schema="BT_HCA_HCDM",
        warehouse=WAREHOUSE,
        authenticator="externalbrowser",
    )
    cur = conn.cursor()
    cur.execute(f"USE WAREHOUSE {WAREHOUSE}")
    return conn, cur

# ---------------- MAIN ----------------
def main():
    conn, cur = connect()

    cur.execute(f"""
        SELECT
            CLINIC_ID,
            IS_VET_DIET_FLAG,
            APPROVAL_CHANNEL,
            COUNT(*) AS n,
            SUM(IFF(rx_was_Approved,1,0)) AS approved
        FROM INT_PRODUCT_PRESCRIPTIONS
        WHERE PRESCRIBED_DATETIME >= '{START_DATE}'
          AND APPROVAL_CHANNEL IS NOT NULL
        GROUP BY 1,2,3
        HAVING COUNT(*) > 0
    """)

    rows = cur.fetchall()

    data = defaultdict(lambda: defaultdict(dict))

    for cid, is_vd, ch, n, a in rows:
        if ch not in ALLOWED_PATHS[bool(is_vd)]:
            continue
        a = a or 0
        data[cid][bool(is_vd)][ch] = (n, a)

    print("\n=== 🚨 ACTIONABLE CLINIC INSIGHTS ===\n")

    for clinic_id, products in data.items():
        for is_vd, paths in products.items():
            product = "VET_DIET" if is_vd else "RX"

            for ch, (n, a) in paths.items():
                if (not is_vd and n < RX_MIN_N) or (is_vd and n < VD_MIN_N):
                    continue

                baseline = BASELINES.get((is_vd, ch))
                if baseline is None:
                    continue

                appr = 100.0 * a / n
                delta = appr - (baseline * 100.0)

                if (not is_vd and delta > RX_APPROVAL_DELTA) or (is_vd and delta > VD_APPROVAL_DELTA):
                    continue

                best_alt = None
                best_alt_rate = appr

                for alt, (an, aa) in paths.items():
                    if alt == ch:
                        continue
                    min_n = ALT_VD_MIN_N if is_vd else ALT_RX_MIN_N
                    if an < min_n:
                        continue
                    rate = 100.0 * aa / an
                    if rate > best_alt_rate + 5.0:
                        best_alt_rate = rate
                        best_alt = alt

                print(f"Clinic {clinic_id} | {product}")
                print(f"Path: {ch}")
                print(f"Approval rate: {appr:5.1f}% (baseline {baseline*100:4.1f}%, Δ {delta:5.1f} pts)")
                print(f"Volume: {n:,} prescriptions")

                if best_alt:
                    print("What this means:")
                    print(f"• {ch} underperforms clinic alternatives.")
                    print(f"• {best_alt} performs +{best_alt_rate-appr:4.1f} pts higher.")
                    print("Recommended action:")
                    print(f"→ Product: de-bias {ch} in favor of {best_alt}.")
                else:
                    print("What this means:")
                    print("• This path underperforms baseline.")
                    print("• No alternate path currently outperforms it at this clinic.")
                    print("Recommended action:")
                    print("→ Clinic Care: investigate clinic workflow / responsiveness.")

                print("-" * 72)

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
