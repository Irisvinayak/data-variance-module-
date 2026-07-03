from __future__ import annotations

import logging
import os
import time

from .config import BASE_PATH, XML_TENANT_PATH, XML_ROLE_ACCESS_PATH
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

_USER_LOGIN_ATTR = os.getenv("XML_USER_LOGIN_ATTR", "LoginId")
_USER_DEPT_ATTR  = os.getenv("XML_USER_DEPT_ATTR",  "DepartmentId")
_USER_ROLE_ATTR  = os.getenv("XML_USER_ROLE_ATTR",  "RoleId")
_DEPT_ID_ATTR    = os.getenv("XML_DEPT_ID_ATTR",    "Id")
_DEPT_FORMS_ATTR = os.getenv("XML_DEPT_FORMS_ATTR", "ReturnId")
_DEPT_NX_ATTR    = os.getenv("XML_DEPT_NX_ATTR",    "NXReturnId")

_AUTH_TTL = float(os.getenv("AUTH_TTL_SEC", "3600"))

# Cache keys are (tenant_id, login_id) tuples
_forms_cache  : dict = {}
_create_cache : dict = {}


# ── Tenant resolution ──────────────────────────────────────────────────────────

def _get_tenant_db_dir(tenant_id: str) -> str | None:
    """
    Look up tenant_id in XML_Tenant.xml and return the path to its
    Database folder:  BASE_PATH / <TenantId> / Database

    Returns None if the tenant is not found or its Status != 'true'.
    """
    root = load_xml_tree(XML_TENANT_PATH, "XML_Tenant.xml")
    if root is None:
        logger.error("[AUTH] Cannot load XML_Tenant.xml (path=%s)", XML_TENANT_PATH)
        return None

    for el in root.findall("Row"):
        if el.attrib.get("TenantId", "").strip() == tenant_id:
            status = el.attrib.get("Status", "false").strip().lower()
            if status != "true":
                logger.warning(
                    "[AUTH] Tenant %r is inactive (Status=%s)", tenant_id, status
                )
                return None
            db_dir = os.path.join(BASE_PATH, tenant_id, "Database")
            logger.debug("[AUTH] Resolved tenant_id=%r → db_dir=%r", tenant_id, db_dir)
            return db_dir

    logger.warning("[AUTH] tenant_id=%r not found in XML_Tenant.xml", tenant_id)
    return None


def _tenant_xml_path(tenant_id: str, filename: str) -> str | None:
    """Return the full path to filename inside the tenant's Database folder."""
    db_dir = _get_tenant_db_dir(tenant_id)
    if db_dir is None:
        return None
    return os.path.join(db_dir, filename)


# ── Public auth API ────────────────────────────────────────────────────────────

def get_allowed_form_ids(login_id: str, tenant_id: str):
    """
    Return the set of allowed return IDs for (tenant_id, login_id), or None
    if the user is not found.  Results are TTL-cached.
    """
    clean = login_id.strip()
    t_id  = tenant_id.strip()
    if not clean or not t_id:
        logger.warning(
            "[AUTH] get_allowed_form_ids called with empty login_id=%r or tenant_id=%r",
            clean, t_id,
        )
        return None

    cache_key = (t_id, clean)
    entry = _forms_cache.get(cache_key)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]

    result = _resolve_allowed_forms(clean, t_id)
    _forms_cache[cache_key] = (result, time.monotonic())
    logger.info(
        "[AUTH] resolved | tenant_id=%r | login_id=%r | result=%s",
        t_id, clean,
        f"{len(result)} form(s)" if result is not None else "USER NOT FOUND",
    )
    return result


def is_return_allowed(login_id: str, tenant_id: str, return_id: str) -> bool:
    allowed = get_allowed_form_ids(login_id, tenant_id)
    if allowed is None:
        logger.warning(
            "[AUTH] DENIED (user not found) | tenant_id=%r | login_id=%r | return_id=%r",
            tenant_id, login_id, return_id,
        )
        return False
    permitted = str(return_id).strip() in allowed
    if not permitted:
        logger.warning(
            "[AUTH] DENIED (not in allowed set) | tenant_id=%r | login_id=%r | "
            "return_id=%r | allowed_sample=%s",
            tenant_id, login_id, return_id, sorted(allowed)[:15],
        )
    else:
        logger.debug(
            "[AUTH] ALLOWED | tenant_id=%r | login_id=%r | return_id=%r",
            tenant_id, login_id, return_id,
        )
    return permitted


def invalidate(login_id: str, tenant_id: str) -> None:
    key = (tenant_id.strip(), login_id.strip())
    _forms_cache.pop(key, None)
    logger.debug("[AUTH] cache invalidated | tenant_id=%r | login_id=%r", tenant_id, login_id)


# ── Internal resolution ────────────────────────────────────────────────────────

def _resolve_allowed_forms(login_id: str, tenant_id: str):
    user_path = _tenant_xml_path(tenant_id, "user.xml")
    if user_path is None:
        return None

    user_root = load_xml_tree(user_path, "user.xml")
    if user_root is None:
        logger.error(
            "[AUTH] Cannot load user.xml | tenant_id=%r | path=%s", tenant_id, user_path
        )
        return None

    login_lower = login_id.lower()
    dept_id = None
    matched_user_attrs = None
    for el in user_root.findall("Row"):
        if el.attrib.get(_USER_LOGIN_ATTR, "").strip().lower() == login_lower:
            dept_id = el.attrib.get(_USER_DEPT_ATTR, "").strip()
            matched_user_attrs = el.attrib
            logger.debug(
                "[AUTH] user.xml match | tenant_id=%r | login_id=%r | dept_id=%r",
                tenant_id, login_id, dept_id,
            )
            break

    if dept_id is None:
        all_logins = [
            el.attrib.get(_USER_LOGIN_ATTR, "") for el in user_root.findall("Row")
        ]
        logger.warning(
            "[AUTH] login_id=%r not found in user.xml | tenant_id=%r | "
            "available_logins=%s",
            login_id, tenant_id, all_logins,
        )
        return None

    if not dept_id:
        logger.warning(
            "[AUTH] login_id=%r has empty/missing %s in user.xml | tenant_id=%r | "
            "user_row_attrs=%s",
            login_id, _USER_DEPT_ATTR, tenant_id, matched_user_attrs,
        )
        return set()

    dept_path = _tenant_xml_path(tenant_id, "department.xml")
    if dept_path is None:
        return None

    dept_root = load_xml_tree(dept_path, "department.xml")
    if dept_root is None:
        logger.error(
            "[AUTH] Cannot load department.xml | tenant_id=%r | path=%s", tenant_id, dept_path
        )
        return None

    for el in dept_root.findall("Row"):
        if el.attrib.get(_DEPT_ID_ATTR, "").strip() == dept_id:
            forms_raw = el.attrib.get(_DEPT_FORMS_ATTR, "")
            nx_raw    = el.attrib.get(_DEPT_NX_ATTR, "")
            xbrl_ids  = {f.strip() for f in forms_raw.split("|") if f.strip()}
            nx_ids    = {f.strip() for f in nx_raw.split("|")    if f.strip()}
            all_ids   = xbrl_ids | nx_ids
            logger.info(
                "[AUTH] dept resolved | tenant_id=%r | login_id=%r | dept_id=%r | "
                "xbrl=%d | nx=%d | total=%d",
                tenant_id, login_id, dept_id, len(xbrl_ids), len(nx_ids), len(all_ids),
            )
            return all_ids

    # No matching department row found — log every available DeptId so the
    # mismatch (e.g. user.xml DepartmentId="103" vs department.xml Id="100")
    # is immediately visible without manual file inspection.
    available_dept_ids = [
        el.attrib.get(_DEPT_ID_ATTR, "") for el in dept_root.findall("Row")
    ]
    logger.warning(
        "[AUTH] DeptId=%r (from user.xml DepartmentId attribute) not found in "
        "department.xml | tenant_id=%r | login_id=%r | "
        "available_department_ids=%s | dept_id_attr_name=%r | user_dept_attr_name=%r",
        dept_id, tenant_id, login_id, available_dept_ids,
        _DEPT_ID_ATTR, _USER_DEPT_ATTR,
    )
    return set()


# ── Role-based access (global XML_RoleAccess.xml — unchanged) ─────────────────

def get_user_role_id(login_id: str, tenant_id: str):
    user_path = _tenant_xml_path(tenant_id, "user.xml")
    if user_path is None:
        return None

    user_root = load_xml_tree(user_path, "user.xml")
    if user_root is None:
        logger.error(
            "[AUTH_ROLE] Cannot load user.xml | tenant_id=%r | path=%s", tenant_id, user_path
        )
        return None

    login_lower = login_id.strip().lower()
    for el in user_root.findall("Row"):
        if el.attrib.get(_USER_LOGIN_ATTR, "").strip().lower() == login_lower:
            role_id = el.attrib.get(_USER_ROLE_ATTR, "").strip()
            return role_id if role_id else None

    logger.warning(
        "[AUTH_ROLE] login_id=%r not found in user.xml | tenant_id=%r", login_id, tenant_id
    )
    return None


def load_role_access_xml():
    return load_xml_tree(XML_ROLE_ACCESS_PATH, "XML_RoleAccess.xml")


def validate_create_instance_access(role_id: str) -> bool:
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
            allowed = el.attrib.get("HasNew", "false").strip().lower() == "true"
            logger.info("[AUTH_ROLE] role_id=%r CreateInstance allowed=%s", role_id, allowed)
            return allowed
    logger.warning("[AUTH_ROLE] No CreateInstance row found for role_id=%r", role_id)
    return False


def can_generate_instance(login_id: str, tenant_id: str) -> bool:
    clean = login_id.strip()
    t_id  = tenant_id.strip()
    if not clean or not t_id:
        return False

    cache_key = (t_id, clean)
    entry = _create_cache.get(cache_key)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]

    role_id = get_user_role_id(clean, t_id)
    result  = validate_create_instance_access(role_id) if role_id else False
    _create_cache[cache_key] = (result, time.monotonic())
    logger.info(
        "[AUTH_ROLE] login_id=%r tenant_id=%r role_id=%r can_generate_instance=%s",
        clean, t_id, role_id, result,
    )
    return result


def invalidate_role_cache(login_id: str, tenant_id: str) -> None:
    _create_cache.pop((tenant_id.strip(), login_id.strip()), None)