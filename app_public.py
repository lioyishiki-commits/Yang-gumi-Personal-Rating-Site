from __future__ import annotations

import os


# The public share page is the real Yang-gumi interface in read-only mode.
# It intentionally reuses app.py so the shared site stays visually and
# behaviorally aligned with the private site.
os.environ["YANGGUMI_READ_ONLY"] = "1"

import app  # noqa: F401,E402
