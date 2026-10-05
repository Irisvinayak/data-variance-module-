# validation.py — shape checks for client-supplied identifiers.
#
# return_id and table_name arrive from the browser and end up inside
# filesystem paths (<repo>\<return_id>\TableMapping.xml) and f-string SQL
# (FROM <table>). Both used to be trusted as given (code review SEC-09,
# SEC-10): with auth on, the exact-id access check happened to stop a hostile
# return_id, but with auth off a value like "..\..\x" walked out of the
# repository, and nothing enforced that a table name was only a name.
#
# These checks are deliberately SHAPE checks, not lookups: they reject what can
# never be a legitimate identifier and accept everything real. Real ids are
# numeric or short slugs; real table names are Oracle identifiers, optionally
# schema-qualified (the service layer adds the schema itself).

from __future__ import annotations

import re

from .calculate_variance import is_safe_identifier

_RETURN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{0,63}$")


class InvalidInput(ValueError):
    """A client-supplied identifier has a shape that can never be legitimate.

    The message is safe to show to the client (it never echoes the value).
    """


def ensure_safe_return_id(value: object) -> str:
    text = "" if value is None else str(value).strip()
    if not _RETURN_ID_RE.match(text):
        raise InvalidInput("Invalid return identifier.")
    return text


def ensure_safe_table_ref(value: object) -> str:
    """`TABLE` or `SCHEMA.TABLE`, each part a plain Oracle identifier."""
    text = "" if value is None else str(value).strip()
    parts = text.split(".")
    if not 1 <= len(parts) <= 2 or not all(is_safe_identifier(p) for p in parts):
        raise InvalidInput("Invalid table name.")
    return text
