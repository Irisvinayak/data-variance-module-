# xml_loader.py — Reusable, safe XML file loader.

from __future__ import annotations

import logging
import os
import xml.etree.ElementTree as ET

logger = logging.getLogger(__name__)


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
        tree = ET.parse(path)
        root = tree.getroot()
        logger.debug("[xml_loader] Loaded %s (%d top-level children)", display, len(root))
        return root
    except ET.ParseError as exc:
        logger.error("[xml_loader] XML parse error in %s: %s", display, exc)
        return None
    except OSError as exc:
        logger.error("[xml_loader] Cannot read %s: %s", display, exc)
        return None
