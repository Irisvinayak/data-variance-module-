from __future__ import annotations

import logging
import os
import time

from .config import XML_DEPT_PATH, XML_USER_PATH, XML_ROLE_ACCESS_PATH
from .xml_loader import load_xml_tree

logger = logging.getLogger(__name__)

_USER_LOGIN_ATTR = os.getenv("XML_USER_LOGIN_ATTR", "LoginId")
_USER_DEPT_ATTR  = os.getenv("XML_USER_DEPT_ATTR",  "DepartmentId")
_USER_ROLE_ATTR  = os.getenv("XML_USER_ROLE_ATTR",  "RoleId")
_DEPT_ID_ATTR    = os.getenv("XML_DEPT_ID_ATTR",    "DeptId")
_DEPT_FORMS_ATTR = os.getenv("XML_DEPT_FORMS_ATTR", "Forms")
_DEPT_NX_ATTR    = os.getenv("XML_DEPT_NX_ATTR",    "NXForms")

_AUTH_TTL = float(os.getenv("AUTH_TTL_SEC", "3600"))
_forms_cache  = {}
_create_cache = {}


def get_allowed_form_ids(login_id: str):
    clean = login_id.strip()
    if not clean:
        logger.warning("[AUTH] get_allowed_form_ids called with empty login_id")
        return None
    entry = _forms_cache.get(clean)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]
    result = _resolve_allowed_forms(clean)
    _forms_cache[clean] = (result, time.monotonic())
    logger.info(
        "[AUTH] resolved | login_id=%r | result=%s", clean,
        f"{len(result)} form(s)" if result is not None else "USER NOT FOUND",
    )
    return result


def is_return_allowed(login_id: str, return_id: str) -> bool:
    allowed = get_allowed_form_ids(login_id)
    if allowed is None:
        logger.warning("[AUTH] DENIED (user not found) | login_id=%r | return_id=%r", login_id, return_id)
        return False
    permitted = str(return_id).strip() in allowed
    if not permitted:
        logger.warning(
            "[AUTH] DENIED (not in allowed set) | login_id=%r | return_id=%r | allowed_sample=%s",
            login_id, return_id, sorted(allowed)[:15],
        )
    else:
        logger.debug("[AUTH] ALLOWED | login_id=%r | return_id=%r", login_id, return_id)
    return permitted


def invalidate(login_id: str) -> None:
    key = login_id.strip()
    _forms_cache.pop(key, None)
    logger.debug("[AUTH] cache invalidated | login_id=%r", key)


def _resolve_allowed_forms(login_id: str):
    user_root = load_xml_tree(XML_USER_PATH, "XML_User.xml")
    if user_root is None:
        logger.error("[AUTH] Cannot load XML_User.xml (path=%s) — denying all access", XML_USER_PATH)
        return None

    login_lower = login_id.lower()
    dept_id = None
    for el in user_root.findall("Row"):
        if el.attrib.get(_USER_LOGIN_ATTR, "").strip().lower() == login_lower:
            dept_id = el.attrib.get(_USER_DEPT_ATTR, "").strip()
            logger.debug("[AUTH] XML_User.xml match | login_id=%r | dept_id=%r", login_id, dept_id)
            break

    if dept_id is None:
        logger.warning("[AUTH] login_id=%r not found in XML_User.xml", login_id)
        return None

    dept_root = load_xml_tree(XML_DEPT_PATH, "XML_Dept.xml")
    if dept_root is None:
        logger.error("[AUTH] Cannot load XML_Dept.xml (path=%s)", XML_DEPT_PATH)
        return None

    for el in dept_root.findall("Row"):
        if el.attrib.get(_DEPT_ID_ATTR, "").strip() == dept_id:
            forms_raw = el.attrib.get(_DEPT_FORMS_ATTR, "")
            nx_raw    = el.attrib.get(_DEPT_NX_ATTR, "")
            xbrl_ids  = {f.strip() for f in forms_raw.split("|") if f.strip()}
            nx_ids    = {f.strip() for f in nx_raw.split("|")    if f.strip()}
            all_ids   = xbrl_ids | nx_ids
            logger.info(
                "[AUTH] dept resolved | login_id=%r | dept_id=%r | xbrl=%d | nx=%d | total=%d",
                login_id, dept_id, len(xbrl_ids), len(nx_ids), len(all_ids),
            )
            return all_ids

    logger.warning("[AUTH] DeptId=%r not found in XML_Dept.xml | login_id=%r", dept_id, login_id)
    return set()


def get_user_role_id(login_id: str):
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
    return load_xml_tree(XML_ROLE_ACCESS_PATH, "XML_RoleAccess.xml")


def validate_create_instance_access(role_id: str) -> bool:
    root = load_role_access_xml()
    if root is None:
        logger.error("[AUTH_ROLE] Cannot load XML_RoleAccess.xml — denying CreateInstance | role_id=%r", role_id)
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


def can_generate_instance(login_id: str) -> bool:
    clean = login_id.strip()
    if not clean:
        return False
    entry = _create_cache.get(clean)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]
    role_id = get_user_role_id(clean)
    result  = validate_create_instance_access(role_id) if role_id else False
    _create_cache[clean] = (result, time.monotonic())
    logger.info("[AUTH_ROLE] login_id=%r role_id=%r can_generate_instance=%s", clean, role_id, result)
    return result


def invalidate_role_cache(login_id: str) -> None:
    _create_cache.pop(login_id.strip(), None)