# ideal_60.py — HostProfile for iDEAL 6.0 (React + .NET Web API, multi-tenant).
#
# Layout, verified against D:\Repo6:
#
#   <Base>\XML_Tenant.xml                     Row TenantId=.. Status="true"
#   <Base>\<TenantId>\DataBase\Return.xml          <Document>/<Row Id=.. Name=..>
#   <Base>\<TenantId>\DataBase\NonXBRLReturn.xml
#   <Base>\<TenantId>\DataBase\User.xml            LoginId, DepartmentId, RoleId
#   <Base>\<TenantId>\DataBase\Department.xml      Id, ReturnId="a,b,c", NXReturnId
#   <Base>\<TenantId>\DataBase\RoleAccess.xml      OptionId numeric, RoleId
#   <Base>\<TenantId>\DataBase\Period.xml          Id, PeriodName  (no Frequency)
#   <Base>\<TenantId>\DataBase\<returnId>\XML_Query.xml   (or Query.xml)
#   <Base>\<TenantId>\Instance\<returnId>\
#
# Four things here differ from what the earlier 6.0 branch assumed, each
# verified against the repository rather than inferred. See INTEGRATION_PLAN.md
# section 5 for the full findings:
#
#   B1  Department.xml separates return ids with COMMAS, not pipes.
#   B2  RoleAccess.xml is per-tenant, not global under <Base>\Database\.
#   B4  Instance is per-tenant; <Base>\Instance does not exist at all.
#   B5  Period.xml has no Frequency column (frequency lives on Return.xml@RepFreq).
#
# Note the folder is spelled "DataBase" here and "Database" in 5.5. Windows does
# not care, but the constant is spelled as it actually appears on disk so a
# future move to a case-sensitive filesystem or a container does not silently
# break path resolution.

from __future__ import annotations

import logging
import os
import threading
import time

from ..config.context import RequestContext
from ..config.settings import AUTH_TTL_SEC, BASE_PATH_60
from ..data.xml_loader import load_xml_tree
from .base import HostProfile, HostProfileError

logger = logging.getLogger(__name__)

_DB_FOLDER = "DataBase"

# Numeric OptionId values for iDEAL 6.0's RoleAccess.xml. 5.5 uses readable
# names ("CreateInstance"); 6.0 uses opaque integers, and the mapping is owned
# by the .NET application, not by this module. Configure via env once the .NET
# team confirms the ids — until then option_id() returns None and callers treat
# the permission as indeterminate rather than denied (INTEGRATION_PLAN B3/Q1).
_OPTION_ID_ENV = {
    "CreateInstance": "DV_OPTION_ID_CREATE_INSTANCE",
    "DataVariation":  "DV_OPTION_ID_DATA_VARIATION",
}


class Ideal60Profile(HostProfile):
    name = "iDEAL 6.0"
    requires_tenant = True
    default_base_path = BASE_PATH_60

    # ── Tenant registry ────────────────────────────────────────────────────────
    # Cached: XML_Tenant.xml is read on every request otherwise, and it changes
    # only when a tenant is provisioned.

    _registry_lock = threading.Lock()
    _registry: dict | None = None
    _registry_ts: float = 0.0

    def tenant_xml_path(self) -> str:
        return self._override("tenant_xml", os.path.join(self.base_path, "XML_Tenant.xml"))

    def _active_tenants(self) -> dict:
        """{TenantId: attrib} for tenants whose Status is true."""
        cls = type(self)
        now = time.monotonic()
        with cls._registry_lock:
            if cls._registry is not None and (now - cls._registry_ts) < AUTH_TTL_SEC:
                return cls._registry

        path = self.tenant_xml_path()
        root = load_xml_tree(path, os.path.basename(path))
        registry: dict = {}
        if root is None:
            logger.error("[hosts/6.0] Cannot load tenant registry (path=%s)", path)
        else:
            for el in root.findall("Row"):
                tid = el.attrib.get("TenantId", "").strip()
                if not tid:
                    continue
                if el.attrib.get("Status", "false").strip().lower() != "true":
                    logger.info("[hosts/6.0] tenant %r is inactive — skipping", tid)
                    continue
                registry[tid] = el.attrib
            logger.info(
                "[hosts/6.0] tenant registry loaded | %d active | ids=%s",
                len(registry), sorted(registry),
            )

        with cls._registry_lock:
            cls._registry = registry
            cls._registry_ts = now
        return registry

    @classmethod
    def invalidate_tenant_registry(cls) -> None:
        with cls._registry_lock:
            cls._registry = None
            cls._registry_ts = 0.0

    # ── Validation ─────────────────────────────────────────────────────────────

    def validate_context(self, ctx: RequestContext) -> None:
        if not ctx.tenant_id:
            raise HostProfileError(
                "iDEAL 6.0 requires a tenantId — there is no tenant-free repository "
                "to read. Ensure the host application forwards tenantId, or set "
                "VERSION=5.5 for a single-tenant installation."
            )

        registry = self._active_tenants()
        if ctx.tenant_id not in registry:
            raise HostProfileError(
                f"Tenant {ctx.tenant_id!r} is not an active tenant in "
                f"{os.path.basename(self.tenant_xml_path())} "
                f"(active: {sorted(registry) or 'none'})."
            )

        # A tenant can be registered and active yet have no usable repository —
        # D:\Repo6\1002 is exactly that, carrying only User.xml. Without this
        # check the request would proceed and return an empty return list, which
        # reads as "you have no returns" rather than "this tenant is not
        # provisioned". Fail loudly instead.
        returns_xml = self.returns_xml_path(ctx)
        if not os.path.isfile(returns_xml):
            raise HostProfileError(
                f"Tenant {ctx.tenant_id!r} is active in the registry but its "
                f"repository is not provisioned: expected {returns_xml}. "
                "Check the tenant folder, or deactivate the tenant in "
                f"{os.path.basename(self.tenant_xml_path())}."
            )

    # ── Repository paths ───────────────────────────────────────────────────────

    def db_dir(self, ctx: RequestContext) -> str:
        return os.path.join(self.base_path, str(ctx.tenant_id), _DB_FOLDER)

    def returns_xml_path(self, ctx: RequestContext) -> str:
        return self._override("returns_xml", os.path.join(self.db_dir(ctx), "Return.xml"))

    def non_xbrl_returns_xml_path(self, ctx: RequestContext) -> str:
        return self._override(
            "non_xbrl_returns_xml", os.path.join(self.db_dir(ctx), "NonXBRLReturn.xml")
        )

    def table_mapping_base_dir(self, ctx: RequestContext) -> str:
        return self._override("table_mapping_base", self.db_dir(ctx))

    def instance_base_dir(self, ctx: RequestContext) -> str:
        # Per-tenant. <Base>\Instance does not exist in Repo6 (B4).
        return self._override(
            "instance_base", os.path.join(self.base_path, str(ctx.tenant_id), "Instance")
        )

    def user_xml_path(self, ctx: RequestContext) -> str:
        return self._override("user_xml", os.path.join(self.db_dir(ctx), "User.xml"))

    def department_xml_path(self, ctx: RequestContext) -> str:
        return self._override("department_xml", os.path.join(self.db_dir(ctx), "Department.xml"))

    def role_access_xml_path(self, ctx: RequestContext) -> str:
        # Per-tenant (B2), and named RoleAccess.xml — not XML_RoleAccess.xml.
        return self._override("role_access_xml", os.path.join(self.db_dir(ctx), "RoleAccess.xml"))

    def period_xml_path(self, ctx: RequestContext) -> str:
        return self._override("period_xml", os.path.join(self.db_dir(ctx), "Period.xml"))

    # ── File shapes ────────────────────────────────────────────────────────────

    @property
    def returns_row_tag(self) -> str:
        return "Row"

    @property
    def query_xml_filenames(self) -> tuple:
        # Most return folders use XML_Query.xml; some (e.g. tenant 1001 return
        # 4061) use Query.xml. Both are tried, in this order.
        return ("XML_Query.xml", "Query.xml")

    @property
    def dept_id_attr(self) -> str:
        return "Id"

    @property
    def dept_forms_attr(self) -> str:
        return "ReturnId"

    @property
    def dept_nx_forms_attr(self) -> str:
        return "NXReturnId"

    @property
    def forms_delimiter(self) -> str:
        return ","

    # Period.xml carries Id + PeriodName only — there is no frequency column to
    # key on, so period_freq_attr is None and period_lookup degrades to "no
    # label available" rather than guessing (B5). The reporting frequency itself
    # is read from Return.xml@RepFreq, which service.py already does for both
    # hosts, so variance date validation is unaffected.
    period_id_attr = "Id"

    @property
    def period_freq_attr(self) -> str | None:
        return None

    # ── Role access ────────────────────────────────────────────────────────────

    def option_id(self, option: str) -> str | None:
        env_name = _OPTION_ID_ENV.get(option)
        if env_name is None:
            return None
        value = os.getenv(env_name, "").strip()
        return value or None
