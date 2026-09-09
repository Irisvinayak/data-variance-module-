# context.py — the per-request identity that path resolution depends on.
#
# Before this existed, every XML path was a module-level constant computed once
# at import time. That works only while there is exactly one repository folder
# for the whole process, which is true for iDEAL 5.5 and false for 6.0, where
# every file lives under BASE_PATH/<TenantId>/. Rather than thread a bare
# `tenant_id: str` through a dozen signatures — and then thread the next host
# difference through them all over again — the data layer takes a context.
#
# In 5.5 mode tenant_id is always "" and the 5.5 profile ignores it, so the
# same call sites serve both hosts.

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class RequestContext:
    """Who is asking, and which tenant's repository answers them.

    login_id : the LoginId supplied by the .NET host. In 5.5 a short username
               ("iris810"); in 6.0 an email ("vaibhav@irisindia.net"). Never
               parsed — only matched against the user master.
    tenant_id: "" under iDEAL 5.5 (no tenancy). Required and validated against
               XML_Tenant.xml under 6.0.
    """

    login_id: str = ""
    tenant_id: str = ""

    def __post_init__(self) -> None:
        # Normalise once, here, so no downstream caller has to remember to
        # .strip() — cache keys are built from these fields and " 1001" must
        # not become a second cache entry for tenant 1001.
        object.__setattr__(self, "login_id", (self.login_id or "").strip())
        object.__setattr__(self, "tenant_id", (self.tenant_id or "").strip())

    @property
    def cache_key(self) -> tuple[str, str]:
        """Key for auth caches. Includes tenant in both modes — harmless in 5.5
        (always ""), essential in 6.0, and keeps one cache shape for both."""
        return (self.tenant_id, self.login_id)

    def __str__(self) -> str:  # log-friendly, avoids f-string noise at call sites
        return f"login_id={self.login_id!r} tenant_id={self.tenant_id!r}"


# A context for code paths that legitimately have no request identity: startup
# diagnostics, the /health endpoint, and offline scripts under scripts/. Under
# 5.5 this resolves exactly like a real request, since paths do not depend on
# identity there. Under 6.0 the profile rejects it, which is the correct
# outcome — there is no such thing as a tenant-free repository lookup.
ANONYMOUS = RequestContext()
