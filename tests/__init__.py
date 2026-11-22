"""Every test runs on a scratch data folder, never the real one (set before any test imports drift)."""
import os
import tempfile

os.environ.setdefault("DRIFT_DATA", tempfile.mkdtemp(prefix="drift-test-"))
