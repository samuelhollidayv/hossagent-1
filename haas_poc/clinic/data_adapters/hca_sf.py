import os
import snowflake.connector

def load_clinic_events_from_hca(
    start_date,
    table_name="BT_HCA_HCDM.RXP_PRESCRIPTIONS",
):
    """
    Canonical clinic approval attempt loader.
    ONLY loads rows where an approval attempt actually occurred.
    """

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
    events = []

    sql = f"""
        SELECT
            CLINIC_ID,
            IS_VET_DIET_FLAG,
            APPROVAL_CHANNEL,
            RX_STATUS,
            APPROVED_NUMERATOR,
            PRESCRIBED_DATETIME
        FROM {table_name}
        WHERE PRESCRIBED_DATETIME >= %s
          AND APPROVED_DENOMINATOR = TRUE
          AND CLINIC_ID IS NOT NULL
    """

    try:
        cur.execute(sql, (start_date,))
        for r in cur.fetchall():
            rx_status = (r[3] or "").strip()

            if r[4]:
                outcome = "SUCCESS"
            elif rx_status == "DENIED":
                outcome = "HARD_FAIL"
            elif rx_status in ("SYSTEM_CLOSED", "CLOSED", "CANCELED"):
                outcome = "SOFT_FAIL"
            else:
                outcome = "IN_FLIGHT"

            events.append({
                "clinic_id": int(r[0]),
                "product": "VET_DIET" if r[1] else "RX",
                "approval_channel": (r[2] or "UNKNOWN").strip(),
                "outcome": outcome,
                "event_date": r[5],
            })
    finally:
        cur.close()
        conn.close()

    return events
