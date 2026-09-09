"""
Custom AniList-compatible API — scrapes https://anilist.co (NOT graphql.anilist.co).

Endpoints
---------
GraphQL passthrough (EXACT original AniList style):
  POST /            {query, variables, operationName?}
  POST /graphql     same

REST convenience (same field names as AniList, plus `bannerAnilistSt`):
  GET  /search/anime?q=Your Name&page=1&perPage=10
  GET  /search/manga?q=...&...
  GET  /anime/{id}            full info (Your Name example)
  GET  /manga/{id}
  GET  /airing?page=1&perPage=20            upcoming episodes
  GET  /schedule?page=1&perPage=20&days=7   weekly schedule grouped by day
  GET  /character/{id}
  GET  /staff/{id}
  GET  /studio/{id}
  GET  /banner/{id}          302 redirect -> https://img.anili.st/media/{id}
  GET  /health
"""
from __future__ import annotations

import time
from typing import Any, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel

from client import (
    AIRING_QUERY,
    CHARACTER_QUERY,
    FULL_MEDIA_QUERY,
    SEARCH_MEDIA_QUERY,
    STAFF_QUERY,
    STUDIO_QUERY,
    AniListClient,
    AniListError,
    anilist_st_banner,
)

app = FastAPI(title="Custom AniList API (anilist.co scraper)", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

client = AniListClient()

# ---- tiny in-memory TTL cache (reduces hits to anilist.co) ----
_cache: Dict[str, Any] = {}
_cache_ts: Dict[str, float] = {}
CACHE_TTL = {
    "media": 300,
    "search": 120,
    "airing": 90,
    "misc": 300,
}


def _cache_get(key: str, ttl: int) -> Optional[Any]:
    if key in _cache and (time.time() - _cache_ts.get(key, 0)) < ttl:
        return _cache[key]
    return None


def _cache_set(key: str, value: Any):
    _cache[key] = value
    _cache_ts[key] = time.time()
    # cap size
    if len(_cache) > 2000:
        oldest = sorted(_cache_ts, key=_cache_ts.get)[:500]
        for k in oldest:
            _cache.pop(k, None)
            _cache_ts.pop(k, None)


def enrich_media(m: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """Add `bannerAnilistSt` = https://img.anili.st/media/{aid} (required spec)."""
    if not m or not isinstance(m, dict):
        return m
    aid = m.get("id")
    if aid:
        m["bannerAnilistSt"] = anilist_st_banner(aid)
        # convenience: human-readable duration / dates like the anilist page
        try:
            dur = m.get("duration")
            if isinstance(dur, int) and dur:
                m["durationText"] = f"{dur // 60} hour, {dur % 60} mins" if dur >= 60 else f"{dur} mins"
            sd = m.get("startDate") or {}
            if sd.get("year"):
                import calendar
                mon = calendar.month_abbr[sd.get("month") or 1]
                m["releaseDateText"] = f"{mon} {sd.get('day')}, {sd.get('year')}"
            if m.get("season") and m.get("seasonYear"):
                m["seasonText"] = f"{m['season'].title()} {m['seasonYear']}"
        except Exception:
            pass
    return m


def enrich_page_media(page: Dict[str, Any]) -> Dict[str, Any]:
    try:
        for m in page.get("data", {}).get("Page", {}).get("media", []) or []:
            enrich_media(m)
        for a in page.get("data", {}).get("Page", {}).get("airingSchedules", []) or []:
            if isinstance(a, dict) and isinstance(a.get("media"), dict):
                enrich_media(a["media"])
    except Exception:
        pass
    return page


class GQLBody(BaseModel):
    query: str
    variables: Optional[Dict[str, Any]] = None
    operationName: Optional[str] = None


def _err(e: Exception):
    if isinstance(e, AniListError):
        raise HTTPException(status_code=e.status, detail=str(e))
    raise HTTPException(status_code=502, detail=str(e))


# ------------------------------------------------------------------ health/root
@app.get("/health")
def health():
    return {"ok": True, "source": "https://anilist.co/graphql (scraped, cloudflare-bypass)", "graphql_anilist_co_used": False}


@app.get("/")
def root():
    return {
        "name": "Custom AniList API",
        "source": "scrapes https://anilist.co — does NOT use https://graphql.anilist.co",
        "banner_format": "https://img.anili.st/media/{aid}",
        "graphql": "POST /  or  POST /graphql  with {query, variables}",
        "rest": [
            "GET /search/anime?q=Your Name",
            "GET /anime/21519",
            "GET /airing",
            "GET /schedule",
            "GET /banner/21519 (redirects to img.anili.st)",
        ],
    }


# ------------------------------------------------- GraphQL passthrough (exact)
@app.post("/")
async def gql_root(body: GQLBody, request: Request):
    try:
        referer = str(request.headers.get("referer") or "https://anilist.co/search/anime")
        data = client.graphql(body.query, body.variables or {}, referer=referer)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


@app.post("/graphql")
async def gql_alias(body: GQLBody, request: Request):
    return await gql_root(body, request)


# ------------------------------------------------------------- search
@app.get("/search/anime")
def search_anime(
    q: str = Query(..., alias="q", description="search text, e.g. Your Name"),
    query: Optional[str] = None,
    page: int = 1,
    perPage: int = 10,
    sort: Optional[str] = None,
    format: Optional[str] = None,
    status: Optional[str] = None,
    season: Optional[str] = None,
    seasonYear: Optional[int] = None,
    genre: Optional[str] = None,
):
    search = query or q
    sort_list = [sort] if sort else ["SEARCH_MATCH", "POPULARITY_DESC"]
    ck = f"search:anime:{search}:{page}:{perPage}:{sort_list}:{format}:{status}:{season}:{seasonYear}:{genre}"
    hit = _cache_get(ck, CACHE_TTL["search"])
    if hit:
        return JSONResponse(hit)
    variables: Dict[str, Any] = {
        "search": search, "page": page, "perPage": min(perPage, 50),
        "type": "ANIME", "sort": sort_list,
    }
    if format:
        variables["format"] = format
    if status:
        variables["status"] = status
    if season:
        variables["season"] = season
    if seasonYear:
        variables["seasonYear"] = seasonYear
    if genre:
        variables["genre"] = genre
    try:
        data = client.graphql(SEARCH_MEDIA_QUERY, variables)
        data = enrich_page_media(data)
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


@app.get("/search/manga")
def search_manga(
    q: str = Query(..., alias="q"),
    query: Optional[str] = None,
    page: int = 1,
    perPage: int = 10,
):
    search = query or q
    ck = f"search:manga:{search}:{page}:{perPage}"
    hit = _cache_get(ck, CACHE_TTL["search"])
    if hit:
        return JSONResponse(hit)
    try:
        data = client.graphql(
            SEARCH_MEDIA_QUERY,
            {"search": search, "page": page, "perPage": min(perPage, 50), "type": "MANGA", "sort": ["SEARCH_MATCH", "POPULARITY_DESC"]},
        )
        data = enrich_page_media(data)
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


# ------------------------------------------------------------- media by id
def _media_by_id(aid: int, mtype: str):
    ck = f"media:{mtype}:{aid}"
    hit = _cache_get(ck, CACHE_TTL["media"])
    if hit:
        return hit
    data = client.graphql(FULL_MEDIA_QUERY, {"id": aid, "type": mtype})
    try:
        m = data.get("data", {}).get("Media")
        if m:
            enrich_media(m)
    except Exception:
        pass
    # 404 parity with original API
    if not data.get("data", {}).get("Media"):
        raise HTTPException(status_code=404, detail={"errors": [{"message": "Not Found.", "status": 404}], "data": {"Media": None}})
    _cache_set(ck, data)
    return data


@app.get("/anime/{aid}")
def get_anime(aid: int):
    try:
        return JSONResponse(_media_by_id(aid, "ANIME"))
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


@app.get("/manga/{aid}")
def get_manga(aid: int):
    try:
        return JSONResponse(_media_by_id(aid, "MANGA"))
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# Back-compat generic (type auto: try ANIME then MANGA)
@app.get("/media/{aid}")
def get_media(aid: int):
    try:
        try:
            return JSONResponse(_media_by_id(aid, "ANIME"))
        except HTTPException as h:
            if getattr(h, "status_code", 0) != 404:
                raise
            return JSONResponse(_media_by_id(aid, "MANGA"))
    except HTTPException:
        raise
    except Exception as e:
        _err(e)


# ------------------------------------------------------------- airing/schedule
@app.get("/airing")
def airing(
    page: int = 1,
    perPage: int = 20,
    airingAt_greater: Optional[int] = None,
    airingAt_lesser: Optional[int] = None,
    notYetAired: bool = True,
):
    ck = f"airing:{page}:{perPage}:{airingAt_greater}:{airingAt_lesser}:{notYetAired}"
    hit = _cache_get(ck, CACHE_TTL["airing"])
    if hit:
        return JSONResponse(hit)
    variables: Dict[str, Any] = {
        "page": page, "perPage": min(perPage, 50),
        "notYetAired": notYetAired, "sort": ["TIME"],
    }
    if airingAt_greater is not None:
        variables["airingAt_greater"] = airingAt_greater
    if airingAt_lesser is not None:
        variables["airingAt_lesser"] = airingAt_lesser
    try:
        data = client.graphql(AIRING_QUERY, variables)
        data = enrich_page_media(data)
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


@app.get("/schedule")
def schedule(
    page: int = 1,
    perPage: int = 50,
    days: int = 7,
):
    """Weekly schedule list: airing episodes for next `days` days, grouped by day."""
    now = int(time.time())
    variables = {
        "page": page, "perPage": min(perPage, 50),
        "airingAt_greater": now - 86400,
        "airingAt_lesser": now + days * 86400,
        "sort": ["TIME"],
    }
    ck = f"schedule:{page}:{perPage}:{days}:{now // 300}"
    hit = _cache_get(ck, CACHE_TTL["airing"])
    if hit:
        return JSONResponse(hit)
    try:
        data = client.graphql(AIRING_QUERY, variables)
        data = enrich_page_media(data)
        # add grouped view WITHOUT breaking original shape: keep data + add `scheduleByDay`
        try:
            from collections import defaultdict
            import datetime
            grouped: Dict[str, List[Any]] = defaultdict(list)
            for a in data.get("data", {}).get("Page", {}).get("airingSchedules", []) or []:
                d = datetime.datetime.utcfromtimestamp(a["airingAt"]).strftime("%Y-%m-%d")
                grouped[d].append(a)
            data["scheduleByDay"] = dict(grouped)
        except Exception:
            pass
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


# ------------------------------------------------------------- misc entities
@app.get("/character/{cid}")
def get_character(cid: int):
    ck = f"character:{cid}"
    hit = _cache_get(ck, CACHE_TTL["misc"])
    if hit:
        return JSONResponse(hit)
    try:
        data = client.graphql(CHARACTER_QUERY, {"id": cid})
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


@app.get("/staff/{sid}")
def get_staff(sid: int):
    ck = f"staff:{sid}"
    hit = _cache_get(ck, CACHE_TTL["misc"])
    if hit:
        return JSONResponse(hit)
    try:
        data = client.graphql(STAFF_QUERY, {"id": sid})
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


@app.get("/studio/{sid}")
def get_studio(sid: int):
    ck = f"studio:{sid}"
    hit = _cache_get(ck, CACHE_TTL["misc"])
    if hit:
        return JSONResponse(hit)
    try:
        data = client.graphql(STUDIO_QUERY, {"id": sid})
        _cache_set(ck, data)
        return JSONResponse(data)
    except Exception as e:
        _err(e)


# ------------------------------------------------------------- banner (img.anili.st)
@app.get("/banner/{aid}")
def banner(aid: int):
    # as required: image = f"https://img.anili.st/media/{aid}"
    return RedirectResponse(f"https://img.anili.st/media/{aid}", status_code=302)


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=False)
