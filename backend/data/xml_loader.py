# xml_loader.py — Reusable, safe XML file loader.

from __future__ import annotations

import logging
import os
import re
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)

# Matches any <?xml ... ?> processing instruction
_XML_DECL_RE = re.compile(rb"<\?xml[^?]*\?>", re.IGNORECASE)


def _sanitise_xml_bytes(raw: bytes) -> bytes:
    """
    Remove duplicate <?xml ...?> declarations from the file body.

    Some Returns.xml files have a second (or more) XML declaration embedded
    inside the document (e.g. after the root opening tag), which is invalid
    and causes ElementTree to raise 'XML or text declaration not at start of
    entity'.  We keep only the very first declaration and strip the rest.
    """
    declarations = list(_XML_DECL_RE.finditer(raw))
    if len(declarations) <= 1:
        return raw  # nothing to fix

    # Build result: keep bytes before + including first decl, then strip all
    # subsequent occurrences.
    first_end = declarations[0].end()
    head = raw[:first_end]
    tail = _XML_DECL_RE.sub(b"", raw[first_end:])
    fixed = head + tail
    logger.warning(
        "[xml_loader] Removed %d extra XML declaration(s) from document",
        len(declarations) - 1,
    )
    return fixed


def max_mtime(*paths: str) -> float:
    """Latest mtime across *paths*, or 0.0 if none exist / are readable.

    Used to key a cache on "has any source file changed" rather than pure
    elapsed time, so an external edit (e.g. someone widening a department's
    return-id access) is picked up on the next lookup instead of waiting out
    a TTL. 0.0 never matches a real mtime, so a missing file always forces a
    reload rather than silently pinning a cache entry forever.
    """
    latest = 0.0
    for path in paths:
        if not path:
            continue
        try:
            latest = max(latest, os.path.getmtime(path))
        except OSError:
            continue
    return latest


def load_xml_tree(path: str, label: str = "") -> ET.Element | None:
    """Parse an XML file and return its root element, or None on failure."""
    display = label or os.path.basename(path)

    if not path:
        logger.error("[xml_loader] %s: path is empty — check config.py", display)
        return None

    if not os.path.isfile(path):
        logger.error("[xml_loader] %s not found at path: %s", display, path)
        return None

    try:
        with open(path, "rb") as fh:
            raw = fh.read()

        raw = _sanitise_xml_bytes(raw)

        root = ET.fromstring(raw)
        logger.debug("[xml_loader] Loaded %s (%d top-level children)", display, len(root))
        return root
    except ET.ParseError as exc:
        logger.error("[xml_loader] XML parse error in %s: %s", display, exc)
        return None
    except OSError as exc:
        logger.error("[xml_loader] Cannot read %s: %s", display, exc)
        return None
