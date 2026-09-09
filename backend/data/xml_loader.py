# xml_loader.py — Reusable, safe XML file loader.

from __future__ import annotations

import io
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
