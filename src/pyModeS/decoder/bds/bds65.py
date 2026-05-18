"""BDS 6,5 -- ADS-B Aircraft Operational Status (TC=31).

Subtypes by ADS-B version:
- v0 (DO-260): only subtype 0 (airborne) is defined; surface BDS65
  did not exist.
- v1 (DO-260A) and v2 (DO-260B): subtype 0 (airborne) and
  subtype 1 (surface) are both defined.

Payload layout (bits numbered 0=MSB … 55=LSB of the 56-bit ME word):

    bits  0-4   TC (= 31)
    bits  5-7   subtype
    bits  8-23  capability class codes (16 bits, raw + decoded)

      Airborne (subtype 0):
        bits  8-9   reserved
        bit  10     cc_acas    - TCAS Resolution Advisory active
        bit  11     cc_cdti    - Cockpit Display of Traffic Information
        bits 12-13  reserved
        bit  14     cc_arv     - Air-Referenced Velocity Report Capability
        bit  15     cc_ts      - Target State Report Capability
        bits 16-17  cc_tc      - Target Trajectory Change Report Capability
                                 0=none, 1=TC+0 only, 2=multiple TC, 3=reserved
        bits 18-23  reserved

      Surface (subtype 1):
        bits  8-9   reserved
        bit  10     cc_poe     - Position Offset Applied
        bit  11     cc_es1090  - ADS-B 1090ES Receive Capability
        bits 12-13  reserved
        bit  14     cc_b2_low  - Class B2 Ground Vehicle, < 70 W
        bit  15     cc_uat_in  - UAT Receive Capability
        bits 16-18  cc_nac_v   - Navigation Accuracy Category for Velocity
        bit  19     cc_nic_c   - NIC Supplement used on the Surface
        bits 20-23  reserved

    bits 24-39  operational mode codes (16 bits, raw + decoded)

      Surface (subtype 1) only:
        bits 24-27  om_lw_codes - Aircraft length/width codes

      Shared OperationalMode fields (bits 24-31 for airborne,
      bits 28-35 for surface after the L/W nibble):
        bits 24-25  reserved
        bit  26     om_tcas_ra_active
        bit  27     om_ident_switch_active
        bit  28     om_reserved_recv_atc_service
        bit  29     om_single_antenna_flag
        bits 30-31  om_system_design_assurance (2-bit)

      Surface (subtype 1) only:
        bits 32-39  om_gps_antenna_offset

    bits 40-42  ADS-B version (3 bits)

    Version-dependent fields (bits 43-55):

      Airborne v1 (DO-260A):
        bit  43     nic_s   - NIC supplement
        bits 44-47  nac_p
        bits 48-49  baq     - Barometric Altitude Quality
        bits 50-51  sil
        bit  52     bai     - Barometric Altitude Integrity (= NIC_baro)
        bit  53     hrd     - 0=true north, 1=magnetic north
        bits 54-55  reserved

      Airborne v2 (DO-260B):
        bit  43     nic_a   - NIC supplement A
        bits 44-47  nac_p
        bits 48-49  gva     - Geometry Vertical Accuracy
        bits 50-51  sil
        bit  52     bai     - Barometric Altitude Integrity (= NIC_baro)
        bit  53     hrd
        bit  54     sil_s   - SIL supplement (0=per hour, 1=per sample)
        bit  55     reserved

      Surface v1 (DO-260A):
        bit  43     nic_s
        bits 44-47  nac_p
        bits 48-49  reserved
        bits 50-51  sil
        bit  52     tah     - Track Angle or Heading
        bit  53     hrd
        bits 54-55  reserved

      Surface v2 (DO-260B):
        bit  43     nic_a
        bits 44-47  nac_p
        bits 48-49  reserved
        bits 50-51  sil
        bit  52     tah
        bit  53     hrd
        bit  54     sil_s
        bit  55     reserved
"""

from typing import Any


def decode_bds65(payload: int) -> dict[str, Any]:
    """Decode a BDS 6,5 payload (ADS-B operational status).

    Args:
        payload: The 56-bit ME payload as an integer (TC=31 in bits 0-4).

    Returns:
        A flat dict with all decoded sub-fields. Capability class and
        operational mode fields are inlined with a ``cc_`` / ``om_``
        prefix alongside their raw 16-bit counterparts
        (``capability_class_raw``, ``operational_mode_raw``).
        Version- and subtype-conditional fields are added as top-level
        keys only when defined (e.g. bai/tah, gva/baq, sil_s,
        nic_s/nic_a).
    """

    subtype = (payload >> 48) & 0x7   # bits  5-7
    version = (payload >> 13) & 0x7   # bits 40-42

    cc = (payload >> 32) & 0xFFFF     # bits  8-23  raw 16-bit window
    om = (payload >> 16) & 0xFFFF     # bits 24-39  raw 16-bit window

    # ------------------------------------------------------------------
    # Always-present result with raw capability/mode words
    # ------------------------------------------------------------------

    result: dict[str, Any] = {
        "subtype":               subtype,
        "version":               version,
        "capability_class_raw":  cc,
        "operational_mode_raw":  om,
    }

    # ------------------------------------------------------------------
    # Capability class (bits 8-23) — flattened with cc_ prefix
    # ------------------------------------------------------------------

    if subtype == 0:                  # airborne
        result["cc_acas"]   = bool((cc >> 13) & 0x1)   # bit 10
        result["cc_cdti"]   = bool((cc >> 12) & 0x1)   # bit 11
        result["cc_arv"]    = bool((cc >>  9) & 0x1)   # bit 14
        result["cc_ts"]     = bool((cc >>  8) & 0x1)   # bit 15
        result["cc_tc"]     = (cc >> 6) & 0x3          # bits 16-17
    elif subtype == 1:                # surface
        result["cc_poe"]    = bool((cc >> 13) & 0x1)   # bit 10
        result["cc_es1090"] = bool((cc >> 12) & 0x1)   # bit 11
        result["cc_b2_low"] = bool((cc >>  9) & 0x1)   # bit 14
        result["cc_uat_in"] = bool((cc >>  8) & 0x1)   # bit 15
        result["cc_nac_v"]  = (cc >> 5) & 0x7          # bits 16-18
        result["cc_nic_c"]  = (cc >> 4) & 0x1          # bit 19

    # ------------------------------------------------------------------
    # Operational mode (bits 24-39) — flattened with om_ prefix
    # ------------------------------------------------------------------

    if subtype == 0:                  # airborne — OM struct at bits 24-31
        result["om_tcas_ra_active"]            = bool((om >> 13) & 0x1)   # bit 26
        result["om_ident_switch_active"]       = bool((om >> 12) & 0x1)   # bit 27
        result["om_reserved_recv_atc_service"] = bool((om >> 11) & 0x1)   # bit 28
        result["om_single_antenna_flag"]       = bool((om >> 10) & 0x1)   # bit 29
        result["om_system_design_assurance"]   = (om >> 8) & 0x3           # bits 30-31
    elif subtype == 1:                # surface — OM struct at bits 24-31
        result["om_lw_codes"]                  = (om >> 12) & 0xF         # bits 24-27
        result["om_tcas_ra_active"]            = bool((om >>  9) & 0x1)   # bit 30
        result["om_ident_switch_active"]       = bool((om >>  8) & 0x1)   # bit 31
        result["om_reserved_recv_atc_service"] = bool((om >>  7) & 0x1)   # bit 32
        result["om_single_antenna_flag"]       = bool((om >>  6) & 0x1)   # bit 33
        result["om_system_design_assurance"]   = (om >> 4) & 0x3          # bits 34-35
        result["om_gps_antenna_offset"]        = om & 0xFF                # bits 32-39

    # ------------------------------------------------------------------
    # Version-dependent tail fields (bits 43-55), added conditionally
    # ------------------------------------------------------------------

    if subtype == 0:                  # airborne
        if version == 1:              # DO-260A
            result["nic_s"] = (payload >> 12) & 0x1   # bit 43
            result["nac_p"] = (payload >>  8) & 0xF   # bits 44-47
            result["baq"]   = (payload >>  6) & 0x3   # bits 48-49
            result["sil"]   = (payload >>  4) & 0x3   # bits 50-51
            result["bai"]   = (payload >>  3) & 0x1   # bit 52  (= NIC_baro)
            result["hrd"]   = (payload >>  2) & 0x1   # bit 53
        elif version == 2:            # DO-260B
            result["nic_a"] = (payload >> 12) & 0x1   # bit 43
            result["nac_p"] = (payload >>  8) & 0xF   # bits 44-47
            result["gva"]   = (payload >>  6) & 0x3   # bits 48-49
            result["sil"]   = (payload >>  4) & 0x3   # bits 50-51
            result["bai"]   = (payload >>  3) & 0x1   # bit 52  (= NIC_baro)
            result["hrd"]   = (payload >>  2) & 0x1   # bit 53
            result["sil_s"] = (payload >>  1) & 0x1   # bit 54

    elif subtype == 1:                # surface
        if version == 1:              # DO-260A
            result["nic_s"] = (payload >> 12) & 0x1   # bit 43
            result["nac_p"] = (payload >>  8) & 0xF   # bits 44-47
            # bits 48-49: reserved
            result["sil"]   = (payload >>  4) & 0x3   # bits 50-51
            result["tah"]   = (payload >>  3) & 0x1   # bit 52
            result["hrd"]   = (payload >>  2) & 0x1   # bit 53
        elif version == 2:            # DO-260B
            result["nic_a"] = (payload >> 12) & 0x1   # bit 43
            result["nac_p"] = (payload >>  8) & 0xF   # bits 44-47
            # bits 48-49: reserved
            result["sil"]   = (payload >>  4) & 0x3   # bits 50-51
            result["tah"]   = (payload >>  3) & 0x1   # bit 52
            result["hrd"]   = (payload >>  2) & 0x1   # bit 53
            result["sil_s"] = (payload >>  1) & 0x1   # bit 54

    return result