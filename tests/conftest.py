"""Test-session configuration.

Set before any `backend.*` import (settings.py reads the environment once, at
import time). The embedding warm-up the app starts at startup would otherwise
load the real ~1.3 GB model in a background thread every time a test enters
`with TestClient(app)`.
"""

import os

os.environ.setdefault("DV_NLP_WARMUP", "false")
