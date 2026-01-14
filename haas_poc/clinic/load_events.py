import os

# Source selector:
# - default: RXP (existing behavior)
# - set HOSS_SOURCE=PHRS to use BT_HCA_HCDM.PHRS_PRESCRIPTIONS (Defects semantics)
HOSS_SOURCE = (os.getenv("HOSS_SOURCE") or "RXP").upper()

from haas_poc.clinic.data_adapters.hca_sf import load_clinic_events_from_hca

def load_events():
    if HOSS_SOURCE == "PHRS":
        from haas_poc.clinic.data_adapters.phrs_sf import load_phrs_events_from_hca
        return load_phrs_events_from_hca("2025-12-01")
    # default: keep existing RXP behavior
    from haas_poc.clinic.data_adapters.hca_sf import load_clinic_events_from_hca
    return load_clinic_events_from_hca("2025-12-01")

