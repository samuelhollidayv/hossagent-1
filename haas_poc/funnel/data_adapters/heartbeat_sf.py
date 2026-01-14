import os
import snowflake.connector

def load_funnel_snapshots_from_heartbeat(
    start_date,
    view_name="EDLDB.ECOM_SANDBOX.HEARTBEAT_SESSION_ORDER_LINE",
):
    conn = snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        role=os.getenv("SNOWFLAKE_ROLE"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database=os.getenv("SNOWFLAKE_DATABASE"),
        schema=os.getenv("SNOWFLAKE_SCHEMA"),
        authenticator="externalbrowser",
    )

    cur = conn.cursor()
    snapshots = []

    sql = f"""
        SELECT
            SESSION_DATE,
            DEVICE_CATEGORY,
            BUSINESS_CHANNEL_NAME,
            COUNT(DISTINCT SESSION_ID) AS total_sessions,
            COUNT(DISTINCT ORDER_ID)   AS purchase_sessions,
            SUM(UNITS_SOLD)            AS units_sold,
            SUM(GROSS_SALES)           AS gross_sales,
            SUM(NET_SALES)             AS net_sales
        FROM {view_name}
        WHERE SESSION_DATE >= %s
        GROUP BY 1,2,3
    """

    try:
        cur.execute(sql, (start_date,))
        for r in cur.fetchall():
            snapshots.append({
                "date": r[0],
                "group": {
                    "DEVICE_CATEGORY": r[1],
                    "CHANNEL": r[2],
                },
                "kpis": {
                    "Total Sessions": float(r[3] or 0),
                    "Purchase Sessions": float(r[4] or 0),
                    "Units Sold": float(r[5] or 0),
                    "Gross Sales": float(r[6] or 0),
                    "Net Sales": float(r[7] or 0),
                }
            })
    finally:
        cur.close()
        conn.close()

    return snapshots
