"""Minimal .env loader so there is no extra dependency.

Must be imported and run before finder.config, which reads the environment at
import time.
"""

import os


def load(path=None):
    path = path or os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env")
    if not os.path.exists(path):
        return False

    with open(path, "r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            key = key.strip()
            value = value.strip().strip("'\"")
            # Real environment variables win over the file.
            if key and key not in os.environ:
                os.environ[key] = value
    return True
