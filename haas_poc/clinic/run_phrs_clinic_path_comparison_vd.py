import os
import snowflake.connector
from collections import defaultdict

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

    cur.execute("""
        SELECT
            APPROVAL_CHANNEL,
            SUM(PRESCRIPTIONS) AS n,
            SUM(APPROVED_PRESCRIPTIONS) AS approved,
            SUM(DENIED_PRESCRIPTIONS) AS denied
        FROM PHRS_PRESCRIPTIONS
        WHERE COMMON_DATE >= '2025-12-01'
          AND IS_VET_DIET_FLAG = TRUE
        GROUP BY 1
        HAVING SUM(PRESCRIPTIONS) > 0
        ORDER BY n DESC
    """)

    rows = cur.fetchall()

    print("\n=== PHRS PATH COMPARISON — VET DIET ONLY ===\n")
    for ch, n, a, d in rows:
        ch = ch or 'UNKNOWN'
        appr = 100.0 * a / n if n else 0
        deny = 100.0 * d / n if n else 0
        print(f"{ch:<18} appr={appr:6.2f}%  deny={deny:6.2f}%  n={n:,}")

    cur.close()
    conn.close()

if __name__ == "__main__":
    main()
