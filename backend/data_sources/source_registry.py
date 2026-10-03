"""
backend/data_sources/source_registry.py
==========================================
Central registry of Phase 1 data-source adapters. This is a lookup table,
not a production inference hook -- nothing in backend/pipeline.py or
forecast_action.py calls into this module in Phase 1.
"""
from __future__ import annotations

from typing import Dict, List, Type

from . import BaseSourceAdapter

_REGISTRY: Dict[str, Type[BaseSourceAdapter]] = {}
_FACTORY_KWARGS: Dict[str, dict] = {}


def register(name: str, **default_kwargs):
    """Class decorator: register an adapter class under `name`."""

    def _wrap(cls: Type[BaseSourceAdapter]) -> Type[BaseSourceAdapter]:
        _REGISTRY[name] = cls
        _FACTORY_KWARGS[name] = default_kwargs
        return cls

    return _wrap


def get_source(name: str) -> BaseSourceAdapter:
    try:
        cls = _REGISTRY[name]
    except KeyError as exc:
        raise KeyError(
            f"unknown data source {name!r}; registered sources: {sorted(_REGISTRY)}"
        ) from exc
    return cls(**_FACTORY_KWARGS.get(name, {}))


def list_sources() -> List[str]:
    return sorted(_REGISTRY)


def get_all_metadata() -> Dict[str, dict]:
    """Collect every registered source's metadata as plain dicts. Never raises --
    an adapter that blows up is reported as UNAVAILABLE with the exception as the reason,
    rather than taking down the whole report."""
    out: Dict[str, dict] = {}
    for name in list_sources():
        try:
            out[name] = get_source(name).get_metadata().to_dict()
        except Exception as exc:  # noqa: BLE001 -- intentionally broad: this is a reporting layer
            out[name] = {
                "source_name": name,
                "source_product": "unknown",
                "observation_time_utc": None,
                "acquisition_time_utc": None,
                "valid_time_utc": None,
                "spatial_resolution": "n/a",
                "temporal_resolution": "n/a",
                "status": "UNAVAILABLE",
                "freshness_minutes": None,
                "provenance": f"adapter {name!r} raised {type(exc).__name__}: {exc}",
                "quality_flags": ["adapter_error"],
            }
    return out


# Import adapter modules for their registration side-effects. Done at the
# bottom to avoid a circular import (each adapter module imports from this
# package's __init__, not from source_registry).
from . import gfs as _gfs  # noqa: E402
from . import himawari as _himawari  # noqa: E402
from . import metar as _metar  # noqa: E402
from . import dem as _dem  # noqa: E402
from . import imdaa as _imdaa  # noqa: E402
from . import imerg as _imerg  # noqa: E402
from . import gmgsi as _gmgsi  # noqa: E402

register("gfs_vobl", scope="vobl")(_gfs.GFSSource)
register("gfs_pan_india", scope="pan_india")(_gfs.GFSSource)
register("himawari")(_himawari.HimawariSource)
register("metar")(_metar.METARSource)
register("dem")(_dem.DEMSource)
register("imdaa")(_imdaa.IMDAASource)
register("imerg")(_imerg.IMERGSource)
register("gmgsi")(_gmgsi.GMGSISource)

__all__ = ["register", "get_source", "list_sources", "get_all_metadata"]
