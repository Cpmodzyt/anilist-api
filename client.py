"""
AniList custom client — scrapes https://anilist.co/graphql (same-domain endpoint)
with Cloudflare bypass via curl_cffi Chrome impersonation.

Why this works while https://graphql.anilist.co is down (403 "temporarily disabled"):
  - anilist.co frontend uses relative "/graphql" on the SAME domain (see main.*.js:
    `t.browser ? "/graphql" : "http://nginx/graphql"`)
  - it auths with `x-csrf-token: window.al_token` + `laravel_session` cookie,
    both obtained from a normal GET to anilist.co (which passes Cloudflare with
    proper TLS fingerprint).
  - curl_cffi impersonate="chrome120" gives us that fingerprint.

No use of graphql.anilist.co at all. Pure anilist.co scrape.
"""
from __future__ import annotations

import re
import time
import threading
from typing import Any, Dict, Optional

from curl_cffi import requests as cr

AL_BASE = "https://anilist.co"
AL_GRAPHQL = f"{AL_BASE}/graphql"
TOKEN_RE = re.compile(r'window\.al_token\s*=\s*"([^"]+)"')

# Banner image helper required by spec:
#   image = f"https://img.anili.st/media/{aid}"
def anilist_st_banner(aid: int) -> str:
    return f"https://img.anili.st/media/{aid}"


class AniListError(Exception):
    def __init__(self, message: str, status: int = 502, payload: Any = None):
        super().__init__(message)
        self.status = status
        self.payload = payload


class AniListClient:
    """Thread-safe scraper client for anilist.co/graphql."""

    def __init__(self, impersonate: str = "chrome120", timeout: int = 30):
        self.impersonate = impersonate
        self.timeout = timeout
        self._lock = threading.Lock()
        self._session: Optional[cr.Session] = None
        self._token: Optional[str] = None
        self._token_at: float = 0
        self._new_session()

    def _new_session(self):
        self._session = cr.Session(impersonate=self.impersonate)
        self._token = None
        self._token_at = 0

    def _ensure_token(self, force: bool = False) -> str:
        # refresh token if missing or older than 30 min
        if not force and self._token and (time.time() - self._token_at) < 1800:
            return self._token
        assert self._session is not None
        # GET any page — search page is light and always returns al_token
        r = self._session.get(
            f"{AL_BASE}/search/anime",
            timeout=self.timeout,
            headers={
                "Accept": "text/html,application/xhtml+xml",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": f"{AL_BASE}/",
            },
        )
        if r.status_code != 200:
            raise AniListError(f"Failed to fetch al_token page: HTTP {r.status_code}", status=502)
        m = TOKEN_RE.search(r.text)
        if not m:
            # Cloudflare challenge page? rotate session once
            if "challenge-platform" in r.text or "Just a moment" in r.text:
                raise AniListError("Cloudflare challenge hit while fetching al_token", status=503)
            raise AniListError("Could not parse window.al_token from anilist.co", status=502)
        self._token = m.group(1)
        self._token_at = time.time()
        return self._token

    def graphql(
        self,
        query: str,
        variables: Optional[Dict[str, Any]] = None,
        referer: str = f"{AL_BASE}/search/anime",
        retries: int = 2,
    ) -> Dict[str, Any]:
        """POST a GraphQL query to anilist.co/graphql. Returns parsed JSON (AniList shape)."""
        last_err: Optional[Exception] = None
        for attempt in range(retries + 1):
            try:
                with self._lock:
                    token = self._ensure_token(force=(attempt > 0))
                    assert self._session is not None
                    headers = {
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "Origin": AL_BASE,
                        "Referer": referer,
                        "x-csrf-token": token,
                        "X-Requested-With": "XMLHttpRequest",
                    }
                    resp = self._session.post(
                        AL_GRAPHQL,
                        headers=headers,
                        json={"query": query, "variables": variables or {}},
                        timeout=self.timeout,
                    )
                # token expired -> Laravel returns 419
                if resp.status_code == 419 and attempt < retries:
                    with self._lock:
                        self._token = None
                    continue
                if resp.status_code in (403,) and "cloudflare" in resp.text.lower() and attempt < retries:
                    with self._lock:
                        self._new_session()
                    time.sleep(1)
                    continue
                if resp.status_code != 200:
                    raise AniListError(
                        f"anilist.co/graphql HTTP {resp.status_code}: {resp.text[:500]}",
                        status=502,
                    )
                data = resp.json()
                # AniList returns {"data":..., "errors":...} — pass through untouched
                # If CSRF error, refresh once
                if isinstance(data, dict) and data.get("errors") and attempt < retries:
                    msg = str(data["errors"])
                    if "csrf" in msg.lower() or "token" in msg.lower() or "unauthenticated" in msg.lower():
                        with self._lock:
                            self._token = None
                        continue
                return data
            except AniListError as e:
                last_err = e
                if attempt < retries:
                    time.sleep(0.8 * (attempt + 1))
                    continue
                raise
            except Exception as e:  # network / json errors
                last_err = e
                if attempt < retries:
                    with self._lock:
                        try:
                            self._new_session()
                        except Exception:
                            pass
                    time.sleep(1)
                    continue
                raise AniListError(f"GraphQL request failed: {e}", status=502)
        raise AniListError(f"GraphQL request failed after retries: {last_err}", status=502)


# ---------------------------------------------------------------------------
# Reusable GraphQL queries (original AniList style — same field names)
# ---------------------------------------------------------------------------

FULL_MEDIA_QUERY = """
query ($id: Int, $type: MediaType) {
  Media(id: $id, type: $type) {
    id
    idMal
    title { romaji english native userPreferred }
    synonyms
    type
    format
    status
    description(asHtml: false)
    startDate { year month day }
    endDate { year month day }
    season
    seasonYear
    seasonInt
    episodes
    duration
    chapters
    volumes
    countryOfOrigin
    source
    hashtag
    trailer { id site thumbnail }
    updatedAt
    coverImage { extraLarge large medium color }
    bannerImage
    genres
    synonyms
    averageScore
    meanScore
    popularity
    favourites
    isAdult
    siteUrl
    rankings { rank type format year season allTime context }
    tags { name rank isMediaSpoiler isAdult }
    externalLinks { url site type language }
    streamingEpisodes { title thumbnail url site }
    studios { nodes { id name isAnimationStudio siteUrl } }
    nextAiringEpisode { airingAt timeUntilAiring episode }
    airingSchedule(page: 1, perPage: 10, notYetAired: true) {
      nodes { id airingAt timeUntilAiring episode }
    }
    relations {
      edges {
        relationType(version: 2)
        node {
          id title { romaji english native userPreferred }
          type format status coverImage { large }
          averageScore popularity siteUrl
        }
      }
    }
    characters(page: 1, perPage: 25, sort: [ROLE, RELEVANCE, ID]) {
      edges {
        role
        node { id name { full native userPreferred } image { large medium } siteUrl }
        voiceActors(language: JAPANESE, sort: [RELEVANCE, ID]) {
          id name { full native userPreferred } image { large } siteUrl language: languageV2
        }
      }
    }
    staff(page: 1, perPage: 25, sort: [RELEVANCE, ID]) {
      edges {
        role
        node { id name { full native userPreferred } image { large } siteUrl }
      }
    }
    reviews(page: 1, perPage: 10, sort: [RATING_DESC, ID]) {
      nodes { id summary rating ratingAmount body(asHtml: false) siteUrl createdAt user { id name avatar { large } } }
    }
    recommendations(page: 1, perPage: 10, sort: [RATING_DESC, ID]) {
      nodes {
        id rating user { id name }
        mediaRecommendation {
          id title { romaji english native userPreferred }
          coverImage { large } averageScore popularity siteUrl
        }
      }
    }
    stats {
      scoreDistribution { score amount }
      statusDistribution { status amount }
    }
  }
}
"""

SEARCH_MEDIA_QUERY = """
query ($search: String, $page: Int, $perPage: Int, $type: MediaType, $sort: [MediaSort], $format: MediaFormat, $status: MediaStatus, $season: MediaSeason, $seasonYear: Int, $genre: String, $isAdult: Boolean) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage perPage }
    media(search: $search, type: $type, sort: $sort, format: $format, status: $status, season: $season, seasonYear: $seasonYear, genre: $genre, isAdult: $isAdult) {
      id
      idMal
      title { romaji english native userPreferred }
      type
      format
      status
      description(asHtml: false)
      startDate { year month day }
      endDate { year month day }
      season
      seasonYear
      episodes
      duration
      countryOfOrigin
      source
      hashtag
      trailer { id site thumbnail }
      coverImage { extraLarge large medium color }
      bannerImage
      genres
      synonyms
      averageScore
      meanScore
      popularity
      favourites
      siteUrl
      rankings { rank type allTime context }
      studios { nodes { id name isAnimationStudio } }
      nextAiringEpisode { airingAt timeUntilAiring episode }
    }
  }
}
"""

AIRING_QUERY = """
query ($page: Int, $perPage: Int, $airingAt_greater: Int, $airingAt_lesser: Int, $notYetAired: Boolean, $episode_greater: Int, $sort: [AiringSort]) {
  Page(page: $page, perPage: $perPage) {
    pageInfo { total currentPage lastPage hasNextPage perPage }
    airingSchedules(airingAt_greater: $airingAt_greater, airingAt_lesser: $airingAt_lesser, notYetAired: $notYetAired, episode_greater: $episode_greater, sort: $sort) {
      id
      airingAt
      timeUntilAiring
      episode
      media {
        id
        idMal
        title { romaji english native userPreferred }
        type
        format
        status
        episodes
        duration
        coverImage { extraLarge large medium color }
        bannerImage
        genres
        averageScore
        meanScore
        popularity
        siteUrl
        season
        seasonYear
        startDate { year month day }
        nextAiringEpisode { airingAt timeUntilAiring episode }
      }
    }
  }
}
"""

CHARACTER_QUERY = """
query ($id: Int) {
  Character(id: $id) {
    id
    name { full native userPreferred }
    image { large medium }
    description(asHtml: false)
    gender
    dateOfBirth { year month day }
    age
    bloodType
    isFavourite
    favourites
    siteUrl
    media(page: 1, perPage: 10, sort: [POPULARITY_DESC]) {
      edges {
        characterRole
        node {
          id title { romaji english native userPreferred }
          coverImage { large } averageScore popularity siteUrl
        }
      }
    }
  }
}
"""

STAFF_QUERY = """
query ($id: Int) {
  Staff(id: $id) {
    id
    name { full native userPreferred }
    image { large medium }
    description(asHtml: false)
    primaryOccupations
    gender
    dateOfBirth { year month day }
    dateOfDeath { year month day }
    age
    yearsActive
    homeTown
    isFavourite
    favourites
    siteUrl
    staffMedia(page: 1, perPage: 10, sort: [POPULARITY_DESC]) {
      edges {
        staffRole
        node {
          id title { romaji english native userPreferred }
          coverImage { large } averageScore popularity siteUrl
        }
      }
    }
    characters(page: 1, perPage: 10, sort: [FAVOURITES_DESC]) {
      edges {
        role
        node { id name { full } image { large } }
      }
    }
  }
}
"""

STUDIO_QUERY = """
query ($id: Int) {
  Studio(id: $id) {
    id
    name
    isAnimationStudio
    siteUrl
    favourites
    media(page: 1, perPage: 25, sort: [POPULARITY_DESC]) {
      nodes {
        id title { romaji english native userPreferred }
        coverImage { large } averageScore popularity siteUrl
        startDate { year } format status
      }
    }
  }
}
"""
