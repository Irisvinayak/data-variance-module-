# service.py — resolves a login to the set of return ids it may access.
#
# The algorithm is identical on both iDEAL hosts:
#
#   login_id --(user master)--> DepartmentId --(department master)--> return ids
#
# What differs between 5.5 and 6.0 is only *shape*: which file, which attribute
# name, and which delimiter separates the return ids inside a single attribute
# value. All three come from the host profile, so this module has no host
# branching of its own.
#
# The delimiter in particular is not a cosmetic detail. 5.5 writes
# Forms="2001|2007|2002" and 6.0 writes ReturnId="2029,4089,4070". Splitting the
# latter on "|" does not raise — it yields one token, "2029,4089,4070", which
# matches no return id. Every user then resolves to an empty allow-list and
# every request 403s with a message about department access. See
# INTEGRATION_PLAN.md B1.

from __future__ import annotations

import logging
import os
import threading
import time

from ..config import ANONYMOUS, AUTH_TTL_SEC, RequestContext
from ..data.xml_loader import load_xml_tree
from ..hosts import get_profile

logger = logging.getLogger(__name__)

# Attribute-name escape hatches. The profile supplies the correct defaults for
# each host; these env vars exist only for a deployment whose master files were
# hand-edited to non-standard attribute names.
_USER_LOGIN_ATTR_OVERRIDE = os.getenv("XML_USER_LOGIN_ATTR", "").strip()
_USER_DEPT_ATTR_OVERRIDE  = os.getenv("XML_USER_DEPT_ATTR",  "").strip()
_USER_ROLE_ATTR_OVERRIDE  = os.getenv("XML_USER_ROLE_ATTR",  "").strip()
_DEPT_ID_ATTR_OVERRIDE    = os.getenv("XML_DEPT_ID_ATTR",    "").strip()
_DEPT_FORMS_ATTR_OVERRIDE = os.getenv("XML_DEPT_FORMS_ATTR", "").strip()
_DEPT_NX_ATTR_OVERRIDE    = os.getenv("XML_DEPT_NX_ATTR",    "").strip()

_AUTH_TTL = AUTH_TTL_SEC

# Keyed by RequestContext.cache_key -> (tenant_id, login_id). Tenant is part of
# the key in both modes: it is always "" under 5.5, and under 6.0 the same
# login_id in two tenants is two different users with two different allow-lists.
_forms_cache: dict = {}
_create_cache: dict = {}
_lock = threading.Lock()


# ── Attribute resolution ───────────────────────────────────────────────────────

def _attrs(profile) -> dict:
    return {
        "login": _USER_LOGIN_ATTR_OVERRIDE or profile.user_login_attr,
        "dept":  _USER_DEPT_ATTR_OVERRIDE  or profile.user_dept_attr,
        "role":  _USER_ROLE_ATTR_OVERRIDE  or profile.user_role_attr,
        "dept_id":    _DEPT_ID_ATTR_OVERRIDE    or profile.dept_id_attr,
        "dept_forms": _DEPT_FORMS_ATTR_OVERRIDE or profile.dept_forms_attr,
        "dept_nx":    _DEPT_NX_ATTR_OVERRIDE    or profile.dept_nx_forms_attr,
    }


def _split_ids(raw: str, delimiter: str) -> set:
    """Split an allowed-returns attribute value into a set of return ids.

    Tolerates both delimiters regardless of which the profile declares. The
    profile's delimiter is the documented one, but the two repositories have
    each been hand-edited over the years (5.5's XML_Dept.xml carries a few
    comma-joined rows) and a stray separator must not silently drop a user's
    access. Splitting on both is safe here because a return id never contains
    either character.
    """
    if not raw:
        return set()
    separators = {delimiter, "|", ","}
    tokens = [raw]
    for sep in separators:
        tokens = [part for token in tokens for part in token.split(sep)]
    return {t.strip() for t in tokens if t.strip()}


# ── Public API ─────────────────────────────────────────────────────────────────

def get_allowed_form_ids(ctx: RequestContext = ANONYMOUS):
    """The set of return ids this login may access, or None if not found.

    None and set() mean different things and callers depend on the difference:
    None is "no such user" (401/403 territory), set() is "known user with no
    department grants" (a configuration problem to report, not an auth failure).
    """
    if not ctx.login_id:
        logger.warning("[AUTH] get_allowed_form_ids called with empty login_id | %s", ctx)
        return None

    key = ctx.cache_key
    with _lock:
        entry = _forms_cache.get(key)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]

    result = _resolve_allowed_forms(ctx)
    with _lock:
        _forms_cache[key] = (result, time.monotonic())
    logger.info(
        "[AUTH] resolved | %s | result=%s", ctx,
        f"{len(result)} form(s)" if result is not None else "USER NOT FOUND",
    )
    return result


def is_return_allowed(ctx: RequestContext, return_id: str) -> bool:
    allowed = get_allowed_form_ids(ctx)
    if allowed is None:
        logger.warning("[AUTH] DENIED (user not found) | %s | return_id=%r", ctx, return_id)
        return False
    permitted = str(return_id).strip() in allowed
    if not permitted:
        logger.warning(
            "[AUTH] DENIED (not in allowed set) | %s | return_id=%r | allowed_sample=%s",
            ctx, return_id, sorted(allowed)[:15],
        )
    else:
        logger.debug("[AUTH] ALLOWED | %s | return_id=%r", ctx, return_id)
    return permitted


def invalidate(ctx: RequestContext) -> None:
    with _lock:
        _forms_cache.pop(ctx.cache_key, None)
    logger.debug("[AUTH] cache invalidated | %s", ctx)


# ── Internal resolution ────────────────────────────────────────────────────────

def _resolve_allowed_forms(ctx: RequestContext):
    profile = get_profile()
    profile.validate_context(ctx)
    attrs = _attrs(profile)

    user_path = profile.user_xml_path(ctx)
    user_root = load_xml_tree(user_path, os.path.basename(user_path))
    if user_root is None:
        logger.error(
            "[AUTH] Cannot load user master (path=%s) — denying all access | %s",
            user_path, ctx,
        )
        return None

    login_lower = ctx.login_id.lower()
    dept_id = None
    matched_attrs = None
    for el in user_root.findall("Row"):
        if el.attrib.get(attrs["login"], "").strip().lower() == login_lower:
            dept_id = el.attrib.get(attrs["dept"], "").strip()
            matched_attrs = el.attrib
            logger.debug("[AUTH] user match | %s | dept_id=%r", ctx, dept_id)
            break

    if dept_id is None:
        logger.warning(
            "[AUTH] login_id=%r not found in %s | login_attr=%r",
            ctx.login_id, os.path.basename(user_path), attrs["login"],
        )
        return None

    if not dept_id:
        # Known user, but no department on the row. Distinguished from
        # "user not found" so the operator sees a data problem, not a login one.
        logger.warning(
            "[AUTH] login_id=%r has empty %s in %s | user_row_attrs=%s",
            ctx.login_id, attrs["dept"], os.path.basename(user_path), matched_attrs,
        )
        return set()

    dept_path = profile.department_xml_path(ctx)
    dept_root = load_xml_tree(dept_path, os.path.basename(dept_path))
    if dept_root is None:
        logger.error("[AUTH] Cannot load department master (path=%s) | %s", dept_path, ctx)
        return None

    delimiter = profile.forms_delimiter
    for el in dept_root.findall("Row"):
        if el.attrib.get(attrs["dept_id"], "").strip() == dept_id:
            xbrl_ids = _split_ids(el.attrib.get(attrs["dept_forms"], ""), delimiter)
            nx_ids   = _split_ids(el.attrib.get(attrs["dept_nx"], ""), delimiter)
            all_ids  = xbrl_ids | nx_ids
            logger.info(
                "[AUTH] dept resolved | %s | dept_id=%r | xbrl=%d | nx=%d | total=%d",
                ctx, dept_id, len(xbrl_ids), len(nx_ids), len(all_ids),
            )
            return all_ids

    # Log every available department id so a mismatch (user's DepartmentId=103
    # vs department master Id=100) is visible without opening the XML by hand.
    available = [el.attrib.get(attrs["dept_id"], "") for el in dept_root.findall("Row")]
    logger.warning(
        "[AUTH] dept_id=%r (from user %s) not found in %s | %s | available_ids=%s | "
        "dept_id_attr=%r",
        dept_id, attrs["dept"], os.path.basename(dept_path), ctx, available,
        attrs["dept_id"],
    )
    return set()


# ── Role-based access ──────────────────────────────────────────────────────────

def get_user_role_id(ctx: RequestContext):
    profile = get_profile()
    profile.validate_context(ctx)
    attrs = _attrs(profile)

    user_path = profile.user_xml_path(ctx)
    user_root = load_xml_tree(user_path, os.path.basename(user_path))
    if user_root is None:
        logger.error("[AUTH_ROLE] Cannot load user master (path=%s) | %s", user_path, ctx)
        return None

    login_lower = ctx.login_id.lower()
    for el in user_root.findall("Row"):
        if el.attrib.get(attrs["login"], "").strip().lower() == login_lower:
            role_id = el.attrib.get(attrs["role"], "").strip()
            return role_id or None

    logger.warning("[AUTH_ROLE] login_id=%r not found in user master | %s", ctx.login_id, ctx)
    return None


def load_role_access_xml(ctx: RequestContext = ANONYMOUS):
    profile = get_profile()
    profile.validate_context(ctx)
    path = profile.role_access_xml_path(ctx)
    return load_xml_tree(path, os.path.basename(path))


def validate_create_instance_access(role_id: str, ctx: RequestContext = ANONYMOUS):
    """Whether role_id may create an instance.

    Returns True/False, or None when this host has no OptionId for the
    permission — "cannot determine" is not the same as "denied", and callers
    must not present a missing mapping as a permission decision.
    """
    profile = get_profile()
    option = profile.option_id("CreateInstance")
    if option is None:
        logger.warning(
            "[AUTH_ROLE] profile=%s has no OptionId mapped for CreateInstance — "
            "cannot evaluate (see INTEGRATION_PLAN.md B3/Q1)", profile.name,
        )
        return None

    root = load_role_access_xml(ctx)
    if root is None:
        logger.error(
            "[AUTH_ROLE] Cannot load role-access master — denying CreateInstance | role_id=%r",
            role_id,
        )
        return False

    for el in root.findall("Row"):
        if (
            el.attrib.get("RoleId", "").strip() == role_id
            and el.attrib.get("OptionId", "").strip() == option
        ):
            allowed = el.attrib.get("HasNew", "false").strip().lower() == "true"
            logger.info(
                "[AUTH_ROLE] role_id=%r OptionId=%r CreateInstance allowed=%s",
                role_id, option, allowed,
            )
            return allowed

    logger.warning(
        "[AUTH_ROLE] No OptionId=%r row for role_id=%r in role-access master",
        option, role_id,
    )
    return False


def can_generate_instance(ctx: RequestContext) -> bool:
    if not ctx.login_id:
        return False

    key = ctx.cache_key
    with _lock:
        entry = _create_cache.get(key)
    if entry and (time.monotonic() - entry[1]) < _AUTH_TTL:
        return entry[0]

    role_id = get_user_role_id(ctx)
    verdict = validate_create_instance_access(role_id, ctx) if role_id else False
    # An indeterminate verdict (None) is treated as "not permitted" at this
    # boundary — the caller needs a bool — but it was logged as indeterminate
    # above so it is not mistaken for a real denial when diagnosing.
    result = bool(verdict)

    with _lock:
        _create_cache[key] = (result, time.monotonic())
    logger.info(
        "[AUTH_ROLE] %s role_id=%r can_generate_instance=%s (verdict=%r)",
        ctx, role_id, result, verdict,
    )
    return result


def invalidate_role_cache(ctx: RequestContext) -> None:
    with _lock:
        _create_cache.pop(ctx.cache_key, None)
