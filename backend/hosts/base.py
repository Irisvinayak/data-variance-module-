# base.py — the HostProfile contract.
#
# A HostProfile is the single object that knows which iDEAL application this
# module is embedded in. It answers exactly two kinds of question:
#
#   1. Where is file X for this request?          (path methods)
#   2. What is that file's shape?                 (element/attribute properties)
#
# Everything else in the codebase — the variance engine, the SQL layer, the NLP
# retriever, the React UI — is host-agnostic and must stay that way. If you find
# yourself writing `if is_legacy_mode():` outside this package, the difference
# belongs here instead.
#
# The 5.5 and 6.0 repositories differ in more than base path: filenames, XML
# root/row element names, attribute names, AND the delimiter inside a single
# attribute value all vary. See docs/integration-plan.md section 1 for the verified
# comparison, and section 5 for the bugs that arose from assuming they didn't.

from __future__ import annotations

import logging
import os
import threading
from abc import ABC, abstractmethod
from collections.abc import Mapping, Sequence

from ..config.context import RequestContext
from ..config.settings import (
    BASE_PATH_OVERRIDE, FISCAL_YEAR_START_MONTH_OVERRIDE, PATH_OVERRIDES,
)

logger = logging.getLogger(__name__)

# Every frequency code calculate_variance understands (its _MONTHLY,
# _QUARTERLY, ... sets). A RepFreq outside this set is treated as missing.
KNOWN_FREQ_CODES = frozenset({
    "M", "MONTHLY", "Q", "QUARTERLY", "H", "HALFYEARLY", "HY", "FH", "C", "CH",
    "A", "ANNUAL", "Y", "FY", "B", "CY", "W", "WEEKLY", "F", "FORTNIGHTLY",
    "HM", "D", "DAILY", "G",
})

# Period.xml@PeriodName -> frequency code, for 6.0 whose Period.xml has no
# Frequency column (B5). Normalised: lowercase, spaces/punctuation removed.
_PERIOD_NAME_FREQ = {
    "daily": "D", "weekly": "W", "fortnightly": "F", "halfmonthly": "F",
    "monthly": "M", "quarterly": "Q", "halfyearly": "H", "yearly": "Y",
    "halfyearlycalendaryear": "C", "yearlycalendaryear": "B",
}


class HostProfileError(RuntimeError):
    """Raised when a request cannot be served by this host profile — e.g. a 6.0
    request with no tenant, or a tenant that is not usable. Callers translate
    this into an HTTP error; it is a configuration/authorisation fault, never a
    programming bug, so it carries a message meant for a human operator."""


class HostProfile(ABC):
    """Resolves repository file locations for one iDEAL host version."""

    #: Human label used in logs and /health output, e.g. "iDEAL 5.5".
    name: str = "unknown"

    #: True when a request without a tenant_id cannot be served (iDEAL 6.0).
    requires_tenant: bool = False

    # ── Validation ─────────────────────────────────────────────────────────────

    @abstractmethod
    def validate_context(self, ctx: RequestContext) -> None:
        """Raise HostProfileError if this context cannot address a repository.

        Called before any path resolution. Its job is to turn a silent
        wrong-answer (an empty result set from a folder that does not exist)
        into a loud, diagnosable failure.
        """

    # ── Repository paths ───────────────────────────────────────────────────────

    @abstractmethod
    def db_dir(self, ctx: RequestContext) -> str:
        """The repository's database folder for this request. Every other path
        method below is derived from this one."""

    @abstractmethod
    def returns_xml_path(self, ctx: RequestContext) -> str:
        """The XBRL returns master."""

    @abstractmethod
    def non_xbrl_returns_xml_path(self, ctx: RequestContext) -> str:
        """The non-XBRL returns master."""

    @abstractmethod
    def table_mapping_base_dir(self, ctx: RequestContext) -> str:
        """Parent of the per-return folders holding TblPath targets."""

    @abstractmethod
    def instance_base_dir(self, ctx: RequestContext) -> str:
        """Parent of the per-return generated-instance folders."""

    @abstractmethod
    def user_xml_path(self, ctx: RequestContext) -> str:
        """The user master: LoginId -> DepartmentId, RoleId."""

    @abstractmethod
    def department_xml_path(self, ctx: RequestContext) -> str:
        """The department master: dept id -> allowed return ids."""

    @abstractmethod
    def role_access_xml_path(self, ctx: RequestContext) -> str:
        """The role-access master: RoleId + OptionId -> HasNew/HasEdit/..."""

    @abstractmethod
    def period_xml_path(self, ctx: RequestContext) -> str:
        """The reporting-period master (frequency codes / period names)."""

    def query_xml_candidates(self, ctx: RequestContext, return_id: str) -> Sequence[str]:
        """Candidate paths for a return's query definition, in priority order.

        A list rather than a single path because the 6.0 repository is
        mid-migration: most return folders carry XML_Query.xml but some (e.g.
        tenant 1001, return 4061) carry Query.xml instead. Callers try each in
        turn and use the first that exists.
        """
        base = os.path.join(self.table_mapping_base_dir(ctx), str(return_id))
        return [
            os.path.join(base, filename) for filename in self.query_xml_filenames
        ]

    # ── File shapes: returns master ────────────────────────────────────────────

    @property
    @abstractmethod
    def returns_row_tag(self) -> str:
        """Child element name holding one return: <Return> in 5.5, <Row> in 6.0."""

    @property
    def query_xml_filenames(self) -> Sequence[str]:
        return ("XML_Query.xml",)

    # ── File shapes: user master ───────────────────────────────────────────────

    user_login_attr: str = "LoginId"
    user_dept_attr:  str = "DepartmentId"
    user_role_attr:  str = "RoleId"

    # ── File shapes: department master ─────────────────────────────────────────

    @property
    @abstractmethod
    def dept_id_attr(self) -> str:
        """Primary key attribute: DeptId in 5.5, Id in 6.0."""

    @property
    @abstractmethod
    def dept_forms_attr(self) -> str:
        """Allowed XBRL return ids: Forms in 5.5, ReturnId in 6.0."""

    @property
    @abstractmethod
    def dept_nx_forms_attr(self) -> str:
        """Allowed non-XBRL return ids: NXForms in 5.5, NXReturnId in 6.0."""

    @property
    @abstractmethod
    def forms_delimiter(self) -> str:
        """Separator inside the allowed-returns attribute value.

        5.5 uses '|' ("2001|2007|2002"); 6.0 uses ',' ("2029,4089,4070").
        Getting this wrong does not raise — it yields one nonsense token that
        matches no return id, so every user silently resolves to an empty
        allow-list and every request 403s. See docs/integration-plan.md B1.
        """

    # ── File shapes: role access ───────────────────────────────────────────────

    def option_id(self, option: str) -> str | None:
        """Map a logical permission name to this host's OptionId value.

        5.5 uses readable strings ("CreateInstance"); 6.0 uses opaque numeric
        ids. Returns None when this host has no id for the option, which callers
        must treat as "cannot determine" rather than "denied", so a missing
        mapping does not masquerade as a permission decision.
        """
        return option

    # ── Calendar facts ─────────────────────────────────────────────────────────

    #: Month the fiscal year starts in, when DV_FISCAL_YEAR_START_MONTH is unset.
    default_fiscal_year_start_month: int = 4

    @property
    def fiscal_year_start_month(self) -> int:
        """What "FY25" / "Q1FY25" mean in a natural-language query. 4 means the
        Indian Apr-Mar year (FY25 = Apr-2024..Mar-2025); 1 is the calendar year."""
        return FISCAL_YEAR_START_MONTH_OVERRIDE or self.default_fiscal_year_start_month

    _period_lock = threading.Lock()
    _period_cache: dict = {}

    def _period_frequencies(self, ctx: RequestContext) -> Mapping[str, str]:
        """{period id: frequency code} from this host's period master. 5.5 carries
        a Frequency column; 6.0 has only PeriodName, which is mapped by name."""
        from ..data.xml_loader import load_xml_tree, max_mtime

        path = self.period_xml_path(ctx)
        mtime = max_mtime(path)
        key = (ctx.tenant_id, path)
        with self._period_lock:
            hit = self._period_cache.get(key)
            if hit and hit[0] == mtime:
                return hit[1]

        out: dict = {}
        root = load_xml_tree(path, os.path.basename(path))
        for el in (root.findall("Row") if root is not None else []):
            pid = (el.attrib.get("Period_Id") or el.attrib.get("Id") or "").strip()
            code = (el.attrib.get("Frequency") or "").strip().upper()
            if code not in KNOWN_FREQ_CODES:
                name = "".join(ch for ch in el.attrib.get("PeriodName", "").lower() if ch.isalnum())
                code = _PERIOD_NAME_FREQ.get(name, "")
            if pid and code:
                out[pid] = code
        with self._period_lock:
            self._period_cache[key] = (mtime, out)
        return out

    def resolve_frequency(self, return_row: Mapping[str, str], ctx: RequestContext) -> str:
        """The reporting frequency of one returns-master row.

        RepFreq when it is a code the variance engine knows; otherwise the
        row's PeriodId looked up in the period master. 6.0 has returns with
        RepFreq="x" (e.g. 4089, PeriodId 108 = half-yearly calendar), which
        would otherwise silently compute monthly comparisons. "" when neither
        source gives a usable code, so callers keep their existing default."""
        raw = (return_row.get("RepFreq") or "").strip().upper()
        if raw in KNOWN_FREQ_CODES:
            return raw
        pid = (return_row.get("PeriodId") or "").strip()
        code = self._period_frequencies(ctx).get(pid, "") if pid else ""
        if code:
            logger.info(
                "[hosts] return %s: RepFreq=%r unusable -> %s from PeriodId=%s",
                return_row.get("Id"), raw, code, pid,
            )
        return code

    # ── Helper for subclasses ──────────────────────────────────────────────────

    @staticmethod
    def _override(key: str, derived: str) -> str:
        """Return the DV_* env override for `key` if set, else the derived path.

        Lets a deployment with a non-standard layout pin one file without
        needing a bespoke profile.
        """
        return PATH_OVERRIDES.get(key) or derived

    #: Repository root for this host when DV_BASE_PATH is not set. Subclasses
    #: set it from their own DV_BASE_PATH_xx setting.
    default_base_path: str = ""

    @property
    def base_path(self) -> str:
        """This profile's repository root.

        Derived from the profile rather than from a module-level constant so
        that a profile is never handed the other host's root — the two
        repositories have incompatible layouts, and pointing 6.0 at the 5.5
        tree fails in ways that read like a permissions problem.
        """
        return BASE_PATH_OVERRIDE or self.default_base_path

    def describe(self, ctx: RequestContext) -> dict[str, str]:
        """Resolved paths for this context — powers /health and startup logging.

        Exists so a misconfigured deployment can be diagnosed from the health
        endpoint instead of by reading source and guessing.
        """
        # Validate first: under 6.0 an unusable context must be reported as
        # such, not rendered as a plausible-looking path with an empty tenant
        # segment that no one can act on.
        self.validate_context(ctx)
        return {
            "profile":                self.name,
            "base_path":              self.base_path,
            "db_dir":                 self.db_dir(ctx),
            "returns_xml":            self.returns_xml_path(ctx),
            "non_xbrl_returns_xml":   self.non_xbrl_returns_xml_path(ctx),
            "table_mapping_base_dir": self.table_mapping_base_dir(ctx),
            "instance_base_dir":      self.instance_base_dir(ctx),
            "user_xml":               self.user_xml_path(ctx),
            "department_xml":         self.department_xml_path(ctx),
            "role_access_xml":        self.role_access_xml_path(ctx),
            "period_xml":             self.period_xml_path(ctx),
        }
