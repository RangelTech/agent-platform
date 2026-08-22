"""Load immutable, versioned product documentation packages."""

import json
from functools import lru_cache
from importlib.resources import files


@lru_cache(maxsize=1)
def ragentes_guide() -> dict:
    resource = files("app.product_guides").joinpath("ragentes-v1.json")
    return json.loads(resource.read_text(encoding="utf-8"))
