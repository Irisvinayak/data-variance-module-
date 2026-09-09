# ideal_55.py — HostProfile for iDEAL 5.5 (ASP.NET MVC, single-tenant).
#
# Layout, verified against D:\Repo5.5:
#
#   <Base>\Database\Returns.xml              <Returns>/<Return Id=.. Name=..>
#   <Base>\Database\NonXBRLReturns.xml
#   <Base>\Database\XML_User.xml             LoginId, DepartmentId, RoleId
#   <Base>\Database\XML_Dept.xml             DeptId, Forms="a|b|c", NXForms
#   <Base>\Database\XML_RoleAccess.xml       OptionId="CreateInstance", RoleId
#   <Base>\Database\XML_Period.xml           Period_Id, Frequency, PeriodName
#   <Base>\Database\<returnId>\XML_Query.xml
#   <Base>\Instance\<returnId>\
#
# There is no tenancy here: one repository serves the whole installation, so
# every path ignores ctx. The parameter is still accepted so call sites are
# identical across host versions.

from __future__ import annotations

import os

from ..config.context import RequestContext
from .base import HostProfile


class Ideal55Profile(HostProfile):
    name = "iDEAL 5.5"
    requires_tenant = False

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate_context(self, ctx: RequestContext) -> None:
        # Nothing to validate: paths do not depend on identity under 5.5, and a
        # missing/unknown login is an authorisation concern handled in
        # backend/auth/, not a path-resolution one. Kept as an explicit no-op
        # rather than omitted so the contract stays visible.
        return None

    # ── Repository paths ───────────────────────────────────────────────────────

    def db_dir(self, ctx: RequestContext) -> str:
        return os.path.join(self.base_path, "Database")

    def returns_xml_path(self, ctx: RequestContext) -> str:
        return self._override("returns_xml", os.path.join(self.db_dir(ctx), "Returns.xml"))

    def non_xbrl_returns_xml_path(self, ctx: RequestContext) -> str:
        return self._override(
            "non_xbrl_returns_xml", os.path.join(self.db_dir(ctx), "NonXBRLReturns.xml")
        )

    def table_mapping_base_dir(self, ctx: RequestContext) -> str:
        return self._override("table_mapping_base", self.db_dir(ctx))

    def instance_base_dir(self, ctx: RequestContext) -> str:
        return self._override("instance_base", os.path.join(self.base_path, "Instance"))

    def user_xml_path(self, ctx: RequestContext) -> str:
        return self._override("user_xml", os.path.join(self.db_dir(ctx), "XML_User.xml"))

    def department_xml_path(self, ctx: RequestContext) -> str:
        return self._override("department_xml", os.path.join(self.db_dir(ctx), "XML_Dept.xml"))

    def role_access_xml_path(self, ctx: RequestContext) -> str:
        return self._override(
            "role_access_xml", os.path.join(self.db_dir(ctx), "XML_RoleAccess.xml")
        )

    def period_xml_path(self, ctx: RequestContext) -> str:
        return self._override("period_xml", os.path.join(self.db_dir(ctx), "XML_Period.xml"))

    # ── File shapes ────────────────────────────────────────────────────────────

    @property
    def returns_row_tag(self) -> str:
        return "Return"

    @property
    def dept_id_attr(self) -> str:
        return "DeptId"

    @property
    def dept_forms_attr(self) -> str:
        return "Forms"

    @property
    def dept_nx_forms_attr(self) -> str:
        return "NXForms"

    @property
    def forms_delimiter(self) -> str:
        return "|"

    # XML_Period.xml carries a real Frequency column, so a frequency code maps
    # straight to a period name here. (6.0's Period.xml does not — see B5.)
    period_id_attr = "Period_Id"

    @property
    def period_freq_attr(self) -> str | None:
        return "Frequency"

    # 5.5's XML_RoleAccess.xml uses readable option names, so the logical name
    # is the wire value; the base-class identity mapping is already correct.
