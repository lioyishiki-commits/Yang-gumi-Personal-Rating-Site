from __future__ import annotations

import os
from pathlib import Path


# The public share page is the real Yang-gumi interface in read-only mode.
# It intentionally reuses app.py so the shared site stays visually and
# behaviorally aligned with the private site.
os.environ["YANGGUMI_READ_ONLY"] = "1"

# Streamlit reruns this launcher after every interaction.  A normal
# ``import app`` only executes once because Python keeps imported modules in
# sys.modules, which leaves every later navigation rerun blank. Compile and
# execute the shared main script explicitly on every pass so the public site
# stays interactive while still using the exact same UI and data code as the
# owner site.
APP_PATH = Path(__file__).with_name("app.py")
globals()["__file__"] = str(APP_PATH)
exec(compile(APP_PATH.read_bytes(), str(APP_PATH), "exec"), globals(), globals())
