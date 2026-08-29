# -*- coding: utf-8 -*-
"""Map host nekro data paths to the URI NapCat can open.

nekro_agent sees /root/srv/nekro_agent; NapCat sees the same tree at
/app/nekro_agent_data. Sending file:///root/srv/... causes ENOENT.
"""
from __future__ import annotations

import os
from pathlib import Path

DATA_DIR = os.environ.get("NEKRO_DATA_DIR", "/root/srv/nekro_agent")
NAPCAT_DATA_DIR = "/app/nekro_agent_data"


def napcat_file_uri(image_path) -> str:
    path = Path(image_path).resolve()
    data = Path(DATA_DIR).resolve()
    try:
        rel = path.relative_to(data)
        shared = Path(NAPCAT_DATA_DIR) / rel
        return shared.as_uri()
    except ValueError:
        return path.as_uri()
