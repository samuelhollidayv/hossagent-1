import os
import snowflake.connector

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

def run(product_flag, label):
    conn, cur = connect()

    cur.execute(f"""
        SELECT
            APPROVAL_CHANNEL,
            SUM(PRESCRIPTIONS) AS n,
            SUM(APPROVED_PRESCRIPTIONS) AS approved,
            SUM(DENIED_PRESCRIPTIONS) AS denied
        FROM PHRS_PRESCRIPTIONS
        WHERE COMMON_DATE >= '2025-12-01'
          AND IS_VET_DIET_FLAG = {product_flag}
        GROUP BY 1
        HAVING SUM(PRESCRIPTIONS) > 0
        ORDER BY n DESC
    """)

    print(f"\n=== PHRS PATH COMPARISON — {label} ===\n")
    for ch, n, a, d in cur.fetchall():
        ch = ch or "UNKNOWN"

        if n is None or n == 0:
            continue
        a = a or 0
        d = d or 0
        appr = 100.0 * a / n
        deny = 100.0 * d / n
        print(f"{ch:<18} appr={appr:6.2f}%  deny={deny:6.2f}%  n={n:,}")

    cur.close()
    conn.close()

def main():
    run("FALSE", "RX ONLY")
    run("TRUE", "VET DIET ONLY")

if __name__ == "__main__":
    main()
