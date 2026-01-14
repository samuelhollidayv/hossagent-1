def classify_outcome(rx_status, state_change_reason):
    """
    Maps raw RX_STATUS + STATE_CHANGE_REASON
    into customer-meaningful outcomes.
    """

    rs = (rx_status or "").upper()
    reason = (state_change_reason or "").upper()

    # --- SUCCESS ---
    if rs == "APPROVED":
        return "SUCCESS"

    # --- HARD FAILURES (clinic / medical) ---
    if rs == "DENIED":
        return "FAIL_HARD"

    # --- SOFT FAILURES (process / abandonment) ---
    if rs in ("SYSTEM_CLOSED", "CANCELED", "CLOSED"):
        return "FAIL_SOFT"

    # --- STILL IN PROGRESS / FRICTION ---
    if rs in (
        "VET_REVIEW",
        "AWAITING_CUSTOMER",
        "PHARMACIST_REVIEW",
        "VALIDATED",
        "SYSTEM_PENDING",
    ):
        return "IN_PROGRESS"

    return "UNKNOWN"
