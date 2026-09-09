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
# attribute value all vary. See INTEGRATION_PLAN.md section 1 for the verified
# comparison, and section 5 for the bugs that arose from assuming they didn't.

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from typing import Sequence

from ..config.context import RequestContext
from ..config.settings import BASE_PATH, PATH_OVERRIDES


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
        allow-list and every request 403s. See INTEGRATION_PLAN.md B1.
        """

    # ── File shapes: period master ─────────────────────────────────────────────

    period_id_attr: str = "Period_Id"

    @property
    def period_freq_attr(self) -> str | None:
        """Attribute carrying the frequency code, or None when the master has no
        such column (6.0's Period.xml is Id + PeriodName only, so period labels
        there must be resolved via a return's PeriodId instead)."""
        return "Frequency"

    # ── File shapes: role access ───────────────────────────────────────────────

    def option_id(self, option: str) -> str | None:
        """Map a logical permission name to this host's OptionId value.

        5.5 uses readable strings ("CreateInstance"); 6.0 uses opaque numeric
        ids. Returns None when this host has no id for the option, which callers
        must treat as "cannot determine" rather than "denied", so a missing
        mapping does not masquerade as a permission decision.
        """
        return option

    # ── Helper for subclasses ──────────────────────────────────────────────────

    @staticmethod
    def _override(key: str, derived: str) -> str:
        """Return the DV_* env override for `key` if set, else the derived path.

        Lets a deployment with a non-standard layout pin one file without
        needing a bespoke profile.
        """
        return PATH_OVERRIDES.get(key) or derived

    @property
    def base_path(self) -> str:
        return BASE_PATH

    def describe(self, ctx: RequestContext) -> dict[str, str]:
        """Resolved paths for this context — powers /health and startup logging.

        Exists so a misconfigured deployment can be diagnosed from the health
        endpoint instead of by reading source and guessing.
        """
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
