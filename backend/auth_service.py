# auth_service.py — User → Department → Allowed FormIds (Return access) lookup.
#
# Flow:
#   1. Receive loginId from frontend query param (passed by .NET host app)
#   2. Parse XML_User.xml  : LoginId  → DepartmentId
#   3. Parse XML_Dept.xml  : DeptId   → pipe-separated FormIds + NXForms
#   4. Return set[str] of allowed FormIds (return IDs) for the user
#
# XML attribute mapping (from your actual XML files):
#   XML_User.xml  → LoginId="iris810"   DepartmentId="0"   RoleId="101"
#   XML_Dept.xml  → DeptId="101"        Forms="2001|2007"  NXForms="6001|6002"
#
# Results are TTL-cached per login_id (default 1 hour) to avoid repeated XML reads.

from __future__ import annotations

import logging
import os
import time

from .config import XML_DEPT_PATH, XML_USER_PATH, XML_ROLE_ACCESS_PATH
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

# ── Attribute name constants (match your actual XML files exactly) ─────────────
_USER_LOGIN_ATTR : str = os.getenv("XML_USER_LOGIN_ATTR", "LoginId")        # XML_User.xml
_USER_DEPT_ATTR  : str = os.getenv("XML_USER_DEPT_ATTR",  "DepartmentId")   # XML_User.xml  ← NOTE: not DeptId
_USER_ROLE_ATTR  : str = os.getenv("XML_USER_ROLE_ATTR",  "RoleId")         # XML_User.xml
_DEPT_ID_ATTR    : str = os.getenv("XML_DEPT_ID_ATTR",    "DeptId")         # XML_Dept.xml  ← NOTE: not DepartmentId
_DEPT_FORMS_ATTR : str = os.getenv("XML_DEPT_FORMS_ATTR", "Forms")          # XML_Dept.xml  (XBRL returns)
_DEPT_NX_ATTR    : str = os.getenv("XML_DEPT_NX_ATTR",    "NXForms")        # XML_Dept.xml  (non-XBRL returns)

# TTL in seconds — override via AUTH_TTL_SEC env var
_AUTH_TTL: float = float(os.getenv("AUTH_TTL_SEC", "3600"))

# Per-login TTL cache:  { login_id: (allowed_form_ids | None, timestamp) }
_forms_cache  : dict[str, tuple[set[str] | None, float]] = {}
_create_cache : dict[str, tuple[bool, float]]             = {}


# ══════════════════════════════════════════════════════════════════════════════
# Primary public API
# ══════════════════════════════════════════════════════════════════════════════

def get_allowed_form_ids(login_id: str) -> set[str] | None:
    """Resolve the set of return/form IDs this user is allowed to access.

    Steps:
      login_id → DepartmentId (XML_User.xml) → DeptId → Forms + NXForms (XML_Dept.xml)

    Returns
    -------
    None        User not found in XML_User.xml — deny all access.
    set[str]    Pipe-separated FormIds + NXForms combined into one set.
                Empty set = department found but no forms assigned.
    """
    clean = login_id.strip()
    if not clean:
        logger.warning("[AUTH] get_allowed_form_ids called with empty login_id")
        return None

    # Return cached result if still fresh
    entry = _forms_cache.get(clean)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        logger.debug(
            "[AUTH] cache hit | login_id=%r | forms=%s",
            clean,
            len(entry[0]) if entry[0] is not None else "NOT FOUND",
        )
        return entry[0]

    result = _resolve_allowed_forms(clean)
    _forms_cache[clean] = (result, time.monotonic())
    logger.info(
        "[AUTH] resolved | login_id=%r | result=%s",
        clean,
        f"{len(result)} form(s)" if result is not None else "USER NOT FOUND",
    )
    return result


def is_return_allowed(login_id: str, return_id: str) -> bool:
    """Check whether a specific return ID is in the user's allowed set.

    This is the primary per-request authorization check called by API routes.

    Parameters
    ----------
    login_id  : str  — login identifier from frontend ?loginId= query param
    return_id : str  — the return ID being accessed (e.g. "2001", "4016")

    Returns
    -------
    True   User exists AND return_id is in their Forms/NXForms set.
    False  User not found OR return_id not in their allowed set.
    """
    allowed = get_allowed_form_ids(login_id)

    if allowed is None:
        # User not found in XML_User.xml at all
        logger.warning(
            "[AUTH] DENIED (user not found) | login_id=%r | return_id=%r",
            login_id, return_id,
        )
        return False

    permitted = str(return_id).strip() in allowed

    if not permitted:
        logger.warning(
            "[AUTH] DENIED (not in allowed set) | login_id=%r | return_id=%r | "
            "allowed_sample=%s",
            login_id,
            return_id,
            sorted(allowed)[:15],   # log first 15 to avoid flooding logs
        )
    else:
        logger.debug(
            "[AUTH] ALLOWED | login_id=%r | return_id=%r", login_id, return_id
        )

    return permitted


def invalidate(login_id: str) -> None:
    """Evict a user's cached entry so the next request re-reads the XML."""
    key = login_id.strip()
    _forms_cache.pop(key, None)
    logger.debug("[AUTH] cache invalidated | login_id=%r", key)


# ══════════════════════════════════════════════════════════════════════════════
# Internal XML resolution
# ══════════════════════════════════════════════════════════════════════════════

def _resolve_allowed_forms(login_id: str) -> set[str] | None:
    """Read XML files and resolve allowed FormIds. Not cached — use get_allowed_form_ids."""

    # ── Step 1: XML_User.xml — LoginId → DepartmentId ─────────────────────────
    user_root = load_xml_tree(XML_USER_PATH, "XML_User.xml")
    if user_root is None:
        logger.error(
            "[AUTH] Cannot load XML_User.xml (path=%s) — denying all access",
            XML_USER_PATH,
        )
        return None

    login_lower = login_id.lower()
    dept_id: str | None = None

    for el in user_root.findall("Row"):
        xml_login = el.attrib.get(_USER_LOGIN_ATTR, "").strip().lower()
        if xml_login == login_lower:
            dept_id = el.attrib.get(_USER_DEPT_ATTR, "").strip()
            logger.debug(
                "[AUTH] XML_User.xml match | login_id=%r | %s=%r",
                login_id, _USER_DEPT_ATTR, dept_id,
            )
            break

    if dept_id is None:
        logger.warning(
            "[AUTH] login_id=%r not found in XML_User.xml "
            "(searching attr=%r across all <Row> elements)",
            login_id, _USER_LOGIN_ATTR,
        )
        return None

    # ── Step 2: XML_Dept.xml — DeptId → Forms | NXForms ──────────────────────
    dept_root = load_xml_tree(XML_DEPT_PATH, "XML_Dept.xml")
    if dept_root is None:
        logger.error(
            "[AUTH] Cannot load XML_Dept.xml (path=%s) — denying access | login_id=%r",
            XML_DEPT_PATH, login_id,
        )
        return None

    for el in dept_root.findall("Row"):
        xml_dept_id = el.attrib.get(_DEPT_ID_ATTR, "").strip()
        if xml_dept_id == dept_id:
            forms_raw = el.attrib.get(_DEPT_FORMS_ATTR, "")     # e.g. "2001|2007|4016"
            nx_raw    = el.attrib.get(_DEPT_NX_ATTR, "")        # e.g. "6001|6002|6003"

            xbrl_ids = {f.strip() for f in forms_raw.split("|") if f.strip()}
            nx_ids   = {f.strip() for f in nx_raw.split("|")    if f.strip()}
            all_ids  = xbrl_ids | nx_ids

            logger.info(
                "[AUTH] dept resolved | login_id=%r | dept_id=%r | "
                "xbrl=%d | nxbrl=%d | total=%d",
                login_id, dept_id, len(xbrl_ids), len(nx_ids), len(all_ids),
            )
            return all_ids

    # DeptId found in XML_User.xml but no matching row in XML_Dept.xml
    logger.warning(
        "[AUTH] DeptId=%r not found in XML_Dept.xml | login_id=%r — "
        "returning empty set (user exists but has no assigned forms)",
        dept_id, login_id,
    )
    return set()


# ══════════════════════════════════════════════════════════════════════════════
# Role-based access (CreateInstance) — unchanged from original
# ══════════════════════════════════════════════════════════════════════════════

def get_user_role_id(login_id: str) -> str | None:
    """Return the RoleId for the given login_id from XML_User.xml."""
    user_root = load_xml_tree(XML_USER_PATH, "XML_User.xml")
    if user_root is None:
        logger.error("[AUTH_ROLE] Cannot load XML_User.xml (path=%s)", XML_USER_PATH)
        return None

    login_lower = login_id.strip().lower()
    for el in user_root.findall("Row"):
        if el.attrib.get(_USER_LOGIN_ATTR, "").strip().lower() == login_lower:
            role_id = el.attrib.get(_USER_ROLE_ATTR, "").strip()
            return role_id if role_id else None

    logger.warning("[AUTH_ROLE] login_id=%r not found in XML_User.xml", login_id)
    return None


def load_role_access_xml():
    """Load and return the root element of XML_RoleAccess.xml."""
    return load_xml_tree(XML_ROLE_ACCESS_PATH, "XML_RoleAccess.xml")


def validate_create_instance_access(role_id: str) -> bool:
    """Return True if the role is permitted to create instances."""
    root = load_role_access_xml()
    if root is None:
        logger.error(
            "[AUTH_ROLE] Cannot load XML_RoleAccess.xml — denying CreateInstance | role_id=%r",
            role_id,
        )
        return False

    for el in root.findall("Row"):
        if (
            el.attrib.get("RoleId", "").strip() == role_id
            and el.attrib.get("OptionId", "").strip() == "CreateInstance"
        ):
            has_new = el.attrib.get("HasNew", "false").strip().lower()
            allowed = has_new == "true"
            logger.info(
                "[AUTH_ROLE] role_id=%r CreateInstance HasNew=%r → allowed=%s",
                role_id, has_new, allowed,
            )
            return allowed

    logger.warning(
        "[AUTH_ROLE] No CreateInstance row in XML_RoleAccess.xml for role_id=%r", role_id
    )
    return False


def can_generate_instance(login_id: str) -> bool:
    """Return True if the user has permission to generate report instances."""
    clean = login_id.strip()
    if not clean:
        return False

    entry = _create_cache.get(clean)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]

    role_id = get_user_role_id(clean)
    result  = validate_create_instance_access(role_id) if role_id else False
    _create_cache[clean] = (result, time.monotonic())
    logger.info(
        "[AUTH_ROLE] login_id=%r role_id=%r can_generate_instance=%s",
        clean, role_id, result,
    )
    return result


def invalidate_role_cache(login_id: str) -> None:
    """Remove a cached role-access entry so the next request re-reads the XML."""
    _create_cache.pop(login_id.strip(), None)