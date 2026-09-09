# backend.hosts — host profile selection.
#
# One profile is chosen at import time from VERSION and reused for the whole
# process, because VERSION is a deployment setting: a single FastAPI instance
# serves exactly one iDEAL host. If that ever needs to change (one process
# serving both 5.5 and 6.0 callers), get_profile() gains a ctx parameter and
# every call site already passes one — that is why callers ask for a profile
# rather than importing Ideal55Profile directly.

from __future__ import annotations

import logging
from functools import lru_cache

from ..config.settings import APP_VERSION, is_legacy_mode
from .base import HostProfile, HostProfileError

logger = logging.getLogger(__name__)


@lru_cache(maxsize=None)
def get_profile(version: str | None = None) -> HostProfile:
    """The HostProfile for `version` (defaults to the configured VERSION)."""
    resolved = version or APP_VERSION

    if is_legacy_mode(resolved):
        from .ideal_55 import Ideal55Profile

        profile: HostProfile = Ideal55Profile()
    else:
        try:
            from .ideal_60 import Ideal60Profile
        except ImportError as exc:  # pragma: no cover - until Phase 4 lands
            raise HostProfileError(
                f"VERSION={resolved!r} selects the iDEAL 6.0 host profile, which is "
                "not implemented yet (Phase 4 of INTEGRATION_PLAN.md). Set VERSION=5.5 "
                "to run against a single-tenant repository."
            ) from exc

        profile = Ideal60Profile()

    logger.info(
        "[hosts] VERSION=%s -> profile=%s (requires_tenant=%s)",
        resolved, profile.name, profile.requires_tenant,
    )
    return profile


__all__ = ["HostProfile", "HostProfileError", "get_profile"]
