import os
import snowflake.connector

LOOKBACK_DATE = "2025-12-01"

RX_MIN_N = 50
VD_MIN_N = 25

RX_APPROVAL_DELTA = -10.0
VD_APPROVAL_DELTA = -5.0


def connect():
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database="EDLDB",
        schema="BT_HCA_HCDM",
        authenticator="externalbrowser",
    )
    cur = conn.cursor()
    cur.execute(f"USE WAREHOUSE {os.getenv('SNOWFLAKE_WAREHOUSE')}")
    return conn, cur


def main():
    conn, cur = connect()

    # --- global baselines ---
    cur.execute(f"""
        SELECT
            IS_VET_DIET_FLAG,
            APPROVAL_CHANNEL,
            SUM(PRESCRIPTIONS) AS n,
            SUM(APPROVED_PRESCRIPTIONS) / NULLIF(SUM(PRESCRIPTIONS),0) AS approval_rate
        FROM PHRS_PRESCRIPTIONS
        WHERE COMMON_DATE >= '{LOOKBACK_DATE}'
        GROUP BY 1,2
        HAVING SUM(PRESCRIPTIONS) > 0
    """)

    baselines = {
        (bool(is_vd), ch): rate
        for is_vd, ch, _, rate in cur.fetchall()
    }

    # --- clinic-level aggregation ---
    cur.execute(f"""
        SELECT
            p.CLINIC_ID,
            p.CLINIC_NAME,
            p.CLINIC_TYPE,
            h.IS_VET_DIET_FLAG,
            h.APPROVAL_CHANNEL,
            SUM(h.PRESCRIPTIONS) AS n,
            SUM(h.APPROVED_PRESCRIPTIONS) AS approved,
            SUM(h.DENIED_PRESCRIPTIONS) AS denied
        FROM PHRS_PRESCRIPTIONS h
        JOIN INT_PRODUCT_PRESCRIPTIONS p
          ON p.CLINIC_ID IS NOT NULL
        WHERE h.COMMON_DATE >= '{LOOKBACK_DATE}'
        GROUP BY 1,2,3,4,5
        HAVING SUM(h.PRESCRIPTIONS) > 0
    """)

    print("\n=== ACTIONABLE CLINICS (PHRS) ===\n")

    for clinic_id, name, ctype, is_vd, ch, n, a, d in cur.fetchall():
        a = a or 0
        d = d or 0
        n = n or 0

        # volume gate
        if not is_vd and n < RX_MIN_N:
            continue
        if is_vd and n < VD_MIN_N:
            continue

        baseline = baselines.get((bool(is_vd), ch))
        if baseline is None:
            continue

        approval_rate = 100.0 * a / n
        baseline_rate = 100.0 * baseline
        delta = approval_rate - baseline_rate

        # deviation gate
        if not is_vd and delta > RX_APPROVAL_DELTA:
            continue
        if is_vd and delta > VD_APPROVAL_DELTA:
            continue

        product = "VET_DIET" if is_vd else "RX"

        print(
            f"{product:<8} | "
            f"{name[:38]:38} | "
            f"{ctype[:20]:20} | "
            f"{ch:<14} | "
            f"appr={approval_rate:6.2f}% "
            f"(Δ {delta:6.2f}) | "
            f"n={n:,}"
        )

    cur.close()
    conn.close()


if __name__ == "__main__":
    main()
