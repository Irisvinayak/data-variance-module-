# period_lookup.py — reads XML_Period.xml purely for its human PeriodName
# labels (Period_Id -> Frequency/EBRFrequency/PeriodName/AdvanceNotificationDays).
#
# This file already exists in the deployment (it drives notification-scheduling
# elsewhere in the wider iDEAL system) and was previously unused by this app.
# Deliberately NOT the source of the date-membership rule — "is 30-JUN a valid
# quarterly date" is decided in exactly one place,
# calculate_variance.validate_reporting_date(), which this module does not
# duplicate. XML_Period.xml's Frequency column is a superset of what
# validate_reporting_date implements (it also names period codes no return in
# Returns.xml actually uses — see the RepFreq census in the module docstring
# below) and its EBRFrequency column is a different axis entirely (submission
# classification, not physical periodicity), so treating it as a second rule
# table would only invite the two to drift apart.
#
# Measured RepFreq values actually used across Returns.xml: '', A, D, F, H, M,
# Q, W, Y — i.e. the primary Frequency codes plus 'A' for annual-financial
# variants (CIMS_RAQ(Annually) et al.), which validate_reporting_date already
# groups with 'Y' as Mar-31. B/C/Z/HM/G/E and the EBRFrequency-only codes
# (ADHOC/QF/QAD/HYO/FF/AFY/AD/MRF) are declared in the master but not used as
# any return's RepFreq today; period_name_for_freq() simply returns None for
# them, degrading gracefully rather than guessing.

from __future__ import annotations

import logging
import os
import threading
import time
from typing import Any, Dict, Optional

from .config import XML_PERIOD_PATH
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

_TTL = float(os.getenv("DV_XML_PERIOD_TTL_SEC", "3600"))
_cache: Optional[Dict[str, Dict[str, Any]]] = None
_cache_ts: float = 0.0
_lock = threading.Lock()


def _build() -> Dict[str, Dict[str, Any]]:
    """{UPPERCASE Frequency code: {period_id, period_name, advance_notification_days}}.

    Keyed on the primary `Frequency` attribute only — this app's Returns.xml
    RepFreq values are drawn from that column, never from EBRFrequency (see
    module docstring). Rows with an empty Frequency are skipped: they exist in
    the master purely to carry an EBRFrequency-only code."""
    root = load_xml_tree(XML_PERIOD_PATH, label="XML_Period.xml")
    if root is None:
        return {}

    out: Dict[str, Dict[str, Any]] = {}
    for row in root.findall("Row"):
        freq = (row.attrib.get("Frequency") or "").strip().upper()
        if not freq:
            continue
        out[freq] = {
            "period_id": row.attrib.get("Period_Id"),
            "period_name": (row.attrib.get("PeriodName") or "").strip(),
            "advance_notification_days": row.attrib.get("AdvanceNotificationDays"),
        }
    logger.info("[period_lookup] Loaded %d frequency code(s) from %s", len(out), XML_PERIOD_PATH)
    return out


def _get() -> Dict[str, Dict[str, Any]]:
    global _cache, _cache_ts
    now = time.monotonic()
    with _lock:
        if _cache is not None and (now - _cache_ts) < _TTL:
            return _cache
    built = _build()
    with _lock:
        _cache = built
        _cache_ts = now
    return built


def period_name_for_freq(freq: str) -> Optional[str]:
    """The human PeriodName for a RepFreq code (e.g. 'Q' -> 'Quarterly'), or
    None when the master has no row for it — callers must treat that as
    "no label available", not an error; frontend/src/types.js's freqLabel()
    already has an independent, richer fallback table for display."""
    if not freq:
        return None
    entry = _get().get(freq.strip().upper())
    return entry["period_name"] if entry else None


def invalidate() -> None:
    """Force the next call to re-read the XML (tests / admin action)."""
    global _cache, _cache_ts
    with _lock:
        _cache = None
        _cache_ts = 0.0
