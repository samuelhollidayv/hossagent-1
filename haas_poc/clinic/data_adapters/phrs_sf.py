cat > haas_poc/clinic/data_adapters/phrs_sf.py <<'PY'
import os
from typing import Dict, List, Any
import snowflake.connector

# -----------------------------
# Configuration
# -----------------------------

DEFECT_PRESCRIPTION_COLS = [
    "UPDATED_CLINIC_PRESCRIPTIONS",
    "UPDATED_PET_PRESCRIPTIONS",
    "CLINIC_CHANGED_TO_CMM_PRESCRIPTIONS",
    "UPDATED_DIRECTIONS_POST_APPROVAL_PRESCRIPTIONS",
    "ALL_LOCATIONS_CLINIC_PRESCRIPTIONS",
    "SINGLE_LINE_PILL_PRESCRIPTIONS",
    "CUSTOMER_MUST_CALL_VET_PRESCRIPTIONS",
    "CUSTOMER_MUST_PICKUP_PRESCRIPTION_PRESCRIPTIONS",
    "CUSTOMER_OR_PET_NOT_FOUND_PRESCRIPTIONS",
    "CUSTOMER_PAYMENT_DUE_TO_CLINIC_PRESCRIPTIONS",
    "CUSTOMER_PROVIDED_INCORRECT_CLINIC_PHONE_PRESCRIPTIONS",
    "CUSTOMER_TO_SIGN_RELEASE_WAIVER_AT_CLINIC_PRESCRIPTIONS",
    "PHARMACIST_CONTACT_VET_PRESCRIPTIONS",
    "CLINIC_STATUS_INACTIVE_PRESCRIPTIONS",
    "CLINIC_STATUS_UNVERIFIED_PRESCRIPTIONS",
    "VET_STATUS_UNVERIFIED_PRESCRIPTIONS",
    "VET_LICENSE_INVALID_PRESCRIPTIONS",
    "PET_STATUS_INACTIVE_PRESCRIPTIONS",
    "CORPORATE_CLINIC_DEACTIVATIONS_PRESCRIPTIONS",
    "FREQUENCY_MISMATCH_PRESCRIPTIONS",
]

PIZZA_TRACKERS = [
    "MAIL_IN_PRESCRIPTION_PIZZA_TRACKER",
    "PENDING_PHARMACIST_REVIEW_PIZZA_TRACKER",
    "PENDING_VET_APPROVAL_PIZZA_TRACKER",
    "PLEASE_CONTACT_US_PIZZA_TRACKER",
    "PLEASE_CONTACT_VET_PIZZA_TRACKER",
]

BASE_COLS = [
    "RX_CREATION_DATETIME",
    "APPROVAL_CHANNEL",
    "INITIATION_CHANNEL",
    "INITIATION_REASON",
    "IS_VET_DIET_FLAG",
    "RX_STATUS",
    "STATE_CHANGE_REASON",
    "BUSINESS_CHANNEL_NAME",
    "AUTOSHIP",
    "CANCELED_BY_CUSTOMER",
    "PRESCRIPTIONS",
    "APPROVED_PRESCRIPTIONS",
    "DENIED_PRESCRIPTIONS",
]

# PHRS is aggregated — no clinic grain exists
CLINIC_ID_FALLBACK = "BUSINESS_UNIT"


# -----------------------------
# Helpers
# -----------------------------

def _connect():
    return snowflake.connector.connect(
        account=os.getenv("SNOWFLAKE_ACCOUNT"),
        user=os.getenv("SNOWFLAKE_USER"),
        role=os.getenv("SNOWFLAKE_ROLE"),
        warehouse=os.getenv("SNOWFLAKE_WAREHOUSE"),
        database="EDLDB",
        schema="BT_HCA_HCDM",
        authenticator="externalbrowser",
    )


def _get_cols(cur) -> List[str]:
    cur.execute("""
        SELECT column_name
        FROM INFORMATION_SCHEMA.COLUMNS
        WHERE table_schema = 'BT_HCA_HCDM'
          AND table_name   = 'PHRS_PRESCRIPTIONS'
    """)
    return [r[0] for r in cur.fetchall()]


# -----------------------------
# Main loader
# -----------------------------

def load_phrs_events_from_hca(start_date: str) -> List[Dict[str, Any]]:
    """
    Load PHRS-prescriptions as weighted events for downstream path comparison.
    """
    conn = _connect()
    cur = conn.cursor()
    cur.execute(f"USE WAREHOUSE {os.getenv('SNOWFLAKE_WAREHOUSE')}")

    try:
        existing = set(_get_cols(cur))

        required = {"RX_CREATION_DATETIME", "PRESCRIPTIONS"}
        missing = required - existing
        if missing:
            raise RuntimeError(f"PHRS_PRESCRIPTIONS missing required columns: {missing}")

        select_cols = []
        for c in [CLINIC_ID_FALLBACK] + BASE_COLS + DEFECT_PRESCRIPTION_COLS + PIZZA_TRACKERS:
            if c in existing and c not in select_cols:
                select_cols.append(c)

        sel = ",\n  ".join(select_cols)

        sql = f"""
SELECT
  {sel}
FROM PHRS_PRESCRIPTIONS
WHERE RX_CREATION_DATETIME >= %s
"""
        cur.execute(sql, (start_date,))
        rows = cur.fetchall()
        colnames = [d[0] for d in cur.description]
        idx = {c: i for i, c in enumerate(colnames)}

        events: List[Dict[str, Any]] = []

        for r in rows:
            n = int(r[idx["PRESCRIPTIONS"]] or 0)
            if n == 0:
                continue

            approved = int(r[idx["APPROVED_PRESCRIPTIONS"]] or 0)
            denied   = int(r[idx["DENIED_PRESCRIPTIONS"]] or 0)
            is_vd    = bool(r[idx["IS_VET_DIET_FLAG"]]) if "IS_VET_DIET_FLAG" in idx else False

            defect_hits = []

            for c in DEFECT_PRESCRIPTION_COLS:
                if c in idx and r[idx[c]] not in (None, 0):
                    defect_hits.append(c)

            for c in PIZZA_TRACKERS:
                if c in idx and r[idx[c]] not in (None, 0, False):
                    defect_hits.append(c)

            events.append({
                "clinic_id": str(r[idx[CLINIC_ID_FALLBACK]]),
                "product": "VET_DIET" if is_vd else "RX",
                "approval_channel": (r[idx["APPROVAL_CHANNEL"]] or "").strip(),
                "initiation_channel": (r[idx["INITIATION_CHANNEL"]] or "").strip(),
                "rx_status": (r[idx["RX_STATUS"]] or "").strip(),
                "state_change_reason": (r[idx["STATE_CHANGE_REASON"]] or "").strip(),
                "event_date": r[idx["RX_CREATION_DATETIME"]],
                "n": n,
                "approved": approved,
                "denied": denied,
                "defect_any": bool(defect_hits),
                "defects": defect_hits,
            })

        return events

    finally:
        try:
            cur.close()
        finally:
            conn.close()
