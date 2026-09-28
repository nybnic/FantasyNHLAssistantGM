"""Name normalization shared by every source that identifies players or teams
by name rather than NHL id."""
from __future__ import annotations

import re
import unicodedata


def normalize_name(name: str) -> str:
    """'Tim Stützle' -> 'tim stutzle', "K'Andre Miller" -> 'kandre miller',
    'St. Louis Blues' -> 'st louis blues'."""
    ascii_name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z ]", "", ascii_name.lower().replace("-", " ")).strip()
