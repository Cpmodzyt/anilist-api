# anilist-api — Custom AniList API (anilist.co scraper)

> Created by [**Itzmecp**](https://t.me/itzmecp)

## What's that?

`https://graphql.anilist.co` has been down for a long time
(`403: The AniList API has been temporarily disabled due to severe stability issues.`).

`anilist-api` is a **drop-in custom replacement** that **scrapes `https://anilist.co` directly**
and returns data in the **exact same style/fields as the original AniList GraphQL API**.
It does **NOT** use `graphql.anilist.co` at all.

How it works (see `client.py`):
- `GET https://anilist.co/search/anime` with `curl_cffi` `impersonate="chrome120"`
  (real Chrome TLS fingerprint → Cloudflare bypass).
- Parse `window.al_token = "..."` from HTML + keep `laravel_session` cookie.
- `POST https://anilist.co/graphql` with `x-csrf-token: <al_token>`,
  `Origin: https://anilist.co`, `Referer: https://anilist.co/...`.
  (Found in `main.*.js`: `t.browser ? "/graphql" : "http://nginx/graphql"`.)
- Auto-refresh token on `419` / CSRF errors, rotate session on Cloudflare block.
- Tiny in-memory TTL cache to avoid hammering anilist.co.

Banner image (per spec):
```python
image = f"https://img.anili.st/media/{aid}"
```
Every media object also returns `bannerAnilistSt` with that URL.
`GET /banner/{id}` 302-redirects to it.

Example — Your Name (`21519`) returns exactly what anilist.co shows:
`Kimi no Na wa. / Your Name. / 君の名は。`, Movie, 1 ep, 107 min (1 hour, 47 mins),
FINISHED, Summer 2016, Aug 26 2016, 86%/86%, pop 712912, fav 46646,
CoMix Wave + producers, `#16 highest rated all time / #2 most popular all time`,
Drama/Romance/Supernatural, hashtag `#君の名は。`, ORIGINAL, full synonyms,
characters/staff/reviews/stats/relations.

## How to use

### 1. Install & run

```bash
git clone https://github.com/Cpmodzyt/anilist-api.git
cd anilist-api
pip install -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
# docs: http://localhost:8000/docs
# health: http://localhost:8000/health
```

Or with Docker:
```bash
docker build -t anilist-api .
docker run -p 8000:8000 -e PORT=8000 anilist-api
# health: http://localhost:8000/health
```

### 2. GraphQL passthrough (EXACT original AniList style)

```bash
# search (same as original API)
curl -X POST http://localhost:8000/ \
 -H 'Content-Type: application/json' \
 -d '{"query":"query($s:String){Page(page:1,perPage:5){media(search:$s,type:ANIME){id title{romaji english native} siteUrl}}}","variables":{"s":"Your Name"}}'

# media by id
curl -X POST http://localhost:8000/graphql \
 -H 'Content-Type: application/json' \
 -d '{"query":"query($id:Int){Media(id:$id){id title{romaji english native} format episodes averageScore}}","variables":{"id":21519}}'

# airing
curl -X POST http://localhost:8000/ \
 -H 'Content-Type: application/json' \
 -d '{"query":"query{Page(page:1,perPage:5){airingSchedules(notYetAired:true sort:[TIME]){id episode airingAt media{id title{romaji}}}}}","variables":{}}'
```

Python:
```python
import requests
r = requests.post("http://localhost:8000/", json={
  "query": "query($id:Int){Media(id:$id){id title{romaji english native} format episodes averageScore}}",
  "variables": {"id": 21519}
})
print(r.json())
```

### 3. REST convenience (same field names + `bannerAnilistSt`)

```bash
curl 'http://localhost:8000/search/anime?q=Your%20Name'
curl 'http://localhost:8000/search/anime?q=Tensei%20Shitara%20Slime&perPage=5'
curl 'http://localhost:8000/anime/21519'        # full info: Your Name
curl 'http://localhost:8000/anime/182205'       # Tensura Season 4 (next EP in nextAiringEpisode)
curl 'http://localhost:8000/manga/30013'
curl 'http://localhost:8000/media/21519'        # auto ANIME->MANGA fallback
curl 'http://localhost:8000/airing?page=1&perPage=10'
curl 'http://localhost:8000/schedule?days=7'    # weekly schedule + scheduleByDay grouping
curl 'http://localhost:8000/character/121514'   # Mitsuha Miyamizu
curl 'http://localhost:8000/staff/96117'        # Makoto Shinkai
curl 'http://localhost:8000/studio/291'         # CoMix Wave
curl -L 'http://localhost:8000/banner/21519' -o banner.jpg  # via img.anili.st
```

`GET /anime/21519` returns the full `Media` object:
format/status/episodes/duration/season/scores/popularity/favourites/studios/
rankings/genres/titles/synonyms/hashtag/source/trailer/cover/banner/
tags/externalLinks/streamingEpisodes/relations/characters/staff/reviews/
recommendations/stats/nextAiringEpisode + `bannerAnilistSt`, `durationText`,
`releaseDateText`, `seasonText`.

### 4. Endpoints summary

| Method | Path | Description |
|---|---|---|
| POST | `/`, `/graphql` | GraphQL passthrough (original AniList style) |
| GET | `/search/anime?q=&page=&perPage=` | Search anime |
| GET | `/search/manga?q=&page=&perPage=` | Search manga |
| GET | `/anime/{id}` | Full anime info |
| GET | `/manga/{id}` | Full manga info |
| GET | `/media/{id}` | Anime→Manga fallback |
| GET | `/airing?page=&perPage=` | Upcoming airing episodes |
| GET | `/schedule?days=7` | Weekly schedule + `scheduleByDay` |
| GET | `/character/{id}` | Character info |
| GET | `/staff/{id}` | Staff info |
| GET | `/studio/{id}` | Studio info |
| GET | `/banner/{id}` | 302 → `https://img.anili.st/media/{id}` |
| GET | `/health`, `/` | Status / index |

## Deploy (Koyeb / Vercel / Render / Railway / Heroku / Docker / any web host)

No API keys needed. The server reads `$PORT` (injected by all these platforms).

| Platform | How |
|---|---|
| **Koyeb** | New Service → deploy `Cpmodzyt/anilist-api` via `Dockerfile` (or import `koyeb.yaml`). Health check path `/health`. Set `API_KEY` in service Environment for key auth. |
| **Vercel** | Import repo → deploy. Set `API_KEY` env in project settings, then send `x-api-key` / `Bearer` on every request. Routed via `vercel.json` → `api/index.py` (serverless). Note: cold starts refetch the token (~1 s); Hobby functions time out after 10 s — normal queries take 1–4 s. |
| **Render** | New Web Service → use `render.yaml` (prompts for `API_KEY`) — or build `pip install -r requirements.txt`, start `uvicorn app:app --host 0.0.0.0 --port $PORT`, health path `/health`, add `API_KEY` env to require a key. |
| **Railway / Heroku** | Deploy repo → auto-detected `Procfile` (`web: uvicorn ... --port $PORT`) + `runtime.txt`. Add `API_KEY` in Variables/Config Vars for key auth. |
| **Fly.io / Northflank / self-host** | `fly launch` with the `Dockerfile`, or `docker build/run` as above (pass `-e API_KEY=...` to require a key). |
| **Local** | `bash run.sh` (uses `$PORT` or 8000). |

Env vars:

| Var | Required | Default | Purpose |
|---|---|---|---|
| `PORT` | No (hosts set it) | `8000` | Listen port |
| `API_KEY` | No | _(empty = open)_ | If set, **every request** (except `/health` + docs) must send `x-api-key: <key>` **or** `Authorization: Bearer <key>`, else `401`. Set it in Vercel/Koyeb/Render dashboard. |

### API key auth (Vercel-style)

```bash
# host env:  API_KEY=your-secret-key
# every request sends one of:
curl -H 'x-api-key: your-secret-key' 'https://your-app.vercel.app/anime/21519'
curl -H 'Authorization: Bearer your-secret-key' -X POST https://your-app.vercel.app/ \
 -H 'Content-Type: application/json' \
 -d '{"query":"query($id:Int){Media(id:$id){id title{romaji}}}","variables":{"id":21519}}'
# no/wrong key -> 401 {"detail": "Invalid or missing API key..."}
# GET /health stays open (platform health checks can't send headers)
```

## Project structure

```
anilist-api/
├── app.py            # FastAPI server (REST + GraphQL passthrough)
├── client.py         # anilist.co scraper + Cloudflare bypass + queries
├── api/index.py      # Vercel serverless entrypoint (same app)
├── Dockerfile        # Koyeb / Render / Railway / Fly / self-host
├── koyeb.yaml        # Koyeb one-click service
├── render.yaml       # Render one-click service
├── Procfile          # Heroku / Railway / Render
├── runtime.txt       # Python version pin
├── vercel.json       # Vercel routing
├── requirements.txt
├── run.sh
└── README.md
```

## Notes / limitations

- Read-only (queries). Mutations / auth-required user lists are not supported.
- Depends on anilist.co HTML/JS structure (`window.al_token`, `/graphql`). If they change it, update `client.py`.
- Be nice: responses are cached (media 5 min, search 2 min, airing 90 s). Don't abuse.
- `img.anili.st` banners are a third-party service keyed by AniList ID.

## Credits

- Created by [**Itzmecp**](https://t.me/itzmecp)
- Data source: https://anilist.co (scraped, Cloudflare bypass via `curl_cffi`)
- Banners: `https://img.anili.st/media/{aid}`
- Not affiliated with AniList.
