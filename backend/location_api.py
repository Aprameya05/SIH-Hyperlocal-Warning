"""
backend/location_api.py -- location-based API (v1). Mounted by backend/unified_api.py.

  GET /v1/meta                         artifact versions, cycle, coverage counts
  GET /v1/search?q=                    places (GeoNames), states, districts
  GET /v1/resolve?lat=&lon=            point -> DRIFT cell (or an explicit "not covered" with nearest covered cell)
  GET /v1/location?cell_id= | lat=&lon=  [&hour=index]   full traceable record (fields, hazards, static, provenance)
  GET /v1/region?state=&district=      regional summary
  GET /v1/compare?cells=a,b,c          side-by-side records (max 6)
  GET /v1/coverage                     data/model availability by region and hazard

Caching: responses carry an ETag derived from the artifact version + request; the store reloads whenever an artifact's
mtime changes, so a new ingestion invalidates every cached response. `Cache-Control: no-cache` makes clients revalidate.
"""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, HTTPException, Query, Request, Response

from backend.location_service import Store

router = APIRouter(prefix="/v1", tags=["location"])
_store: Optional[Store] = None


def store() -> Store:
    global _store
    if _store is None:
        _store = Store()
    else:
        _store.reload()
    return _store


def _reply(request: Request, response: Response, payload: dict):
    body = repr(sorted(payload.items(), key=lambda kv: kv[0])).encode()
    etag = '"%s"' % hashlib.sha1((store().version + request.url.path + str(request.url.query)).encode() + hashlib.sha1(body).digest()).hexdigest()[:20]
    response.headers["ETag"] = etag
    response.headers["Cache-Control"] = "no-cache"
    if request.headers.get("if-none-match") == etag:
        return Response(status_code=304, headers={"ETag": etag, "Cache-Control": "no-cache"})
    return payload


def _cell(s: Store, cell_id, lat, lon):
    if cell_id:
        if cell_id not in s.idx:
            raise HTTPException(404, f"unknown cell_id {cell_id}")
        return cell_id
    if lat is None or lon is None:
        raise HTTPException(422, "give cell_id or lat and lon")
    r = s.resolve(lat, lon)
    if not r["covered"]:
        raise HTTPException(404, detail={"error": "NOT_COVERED", **r})
    return r["cell_id"]


@router.get("/meta")
def meta(request: Request, response: Response):
    s = store()
    out = {"artifact_version": s.version, "n_cells": len(s.cells), "resolution_deg": 0.25, "grid": s.grid_doc["meta"] if s.grid_doc else None,
           "fields_available": s.fields_ok, "fields_problems": s.fields_problems[:5]}
    if s.fields_ok:
        m = s.fields_doc["meta"]
        out["cycle"] = {"model_init_utc": m["cycle_init_utc"], "fhours": m["fhours"], "generated_at_utc": m["generated_at_utc"]}
    out["thunderstorm_model_available"] = s.ts_ok
    return _reply(request, response, out)


@router.get("/search")
def search(request: Request, response: Response, q: str = Query(..., min_length=2), limit: int = 10):
    return _reply(request, response, {"query": q, "results": store().search(q, min(limit, 25))})


@router.get("/resolve")
def resolve(request: Request, response: Response, lat: float = Query(..., ge=-90, le=90), lon: float = Query(..., ge=-180, le=180)):
    s = store()
    r = s.resolve(lat, lon)
    if r["covered"]:
        r["location"] = s.location_info(r["cell_id"])
    return _reply(request, response, r)


@router.get("/location")
def location(request: Request, response: Response, cell_id: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None,
             hour: Optional[int] = None):
    s = store()
    cid = _cell(s, cell_id, lat, lon)
    try:
        rec = s.record(cid, hour)
    except IndexError:
        raise HTTPException(422, "hour index out of range")
    return _reply(request, response, rec)


@router.get("/region")
def region(request: Request, response: Response, state: Optional[str] = None, district: Optional[str] = None, hour: Optional[int] = None):
    if not state and not district:
        raise HTTPException(422, "give state and/or district")
    try:
        return _reply(request, response, store().region(state, district, hour))
    except KeyError:
        raise HTTPException(404, "no cells match")


@router.get("/compare")
def compare(request: Request, response: Response, cells: str = Query(...), hour: Optional[int] = None):
    s = store()
    ids = [c for c in cells.split(",") if c][:6]
    out = []
    for c in ids:
        if c not in s.idx:
            raise HTTPException(404, f"unknown cell_id {c}")
        out.append(s.record(c, hour))
    return _reply(request, response, {"hour": hour, "locations": out})


@router.get("/health")
def health(request: Request, response: Response, probe: bool = False):
    """Dependency-level health (server clock). `probe=true` also HEADs the upstream sources from this environment."""
    h = store().health(probe=probe)
    response.headers["Cache-Control"] = "no-store"
    return h


@router.get("/ready")
def ready(response: Response):
    """200 only when the grid, a verified and FRESH GFS cycle, and the static layer are all present; 503 otherwise."""
    h = store().health()
    response.headers["Cache-Control"] = "no-store"
    if not h["ready"]:
        raise HTTPException(503, detail={"error": "NOT_READY", "status": h["status"], "components": h["components"]})
    return {"ready": True, "status": h["status"], "checked_at_utc": h["checked_at_utc"]}


@router.get("/explain")
def explain(request: Request, response: Response, hazard: str = Query(..., pattern="^(thunderstorm|extreme_rainfall|flash_flood)$"),
            cell_id: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None, lead: Optional[int] = None):
    s = store()
    cid = _cell(s, cell_id, lat, lon)
    return _reply(request, response, s.explain(cid, hazard, lead))


@router.get("/alert-eligibility")
def alert_eligibility(request: Request, response: Response, cell_id: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None):
    s = store()
    cid = _cell(s, cell_id, lat, lon)
    response.headers["Cache-Control"] = "no-store"
    return s.alert_eligibility(cid)


@router.get("/forecast")
def forecast(request: Request, response: Response, cell_id: Optional[str] = None, lat: Optional[float] = None, lon: Optional[float] = None):
    """The full forecast timeline for a location: every forecast hour's values with its own valid time and status."""
    s = store()
    cid = _cell(s, cell_id, lat, lon)
    rec = s.record(cid)
    if not rec.get("cycle"):
        raise HTTPException(503, detail={"error": "FIELDS_UNAVAILABLE", "problems": rec["availability"]["problems"]})
    hours = rec["cycle"]["hours"]
    series = {k: {"unit": f["unit"], "kind": f["kind"], "values": f["series"]} for k, f in rec["fields"].items()}
    return _reply(request, response, {"location": rec["location"], "model_init_utc": rec["cycle"]["model_init_utc"], "hours": hours, "variables": series,
                                      "operational_lead_hours": None, "operational_lead_status": rec["cycle"]["operational_lead_status"]})


@router.get("/coverage")
def coverage(request: Request, response: Response):
    from backend.coverage import coverage_matrix
    return _reply(request, response, coverage_matrix(store()))
