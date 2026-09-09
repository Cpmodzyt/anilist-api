#!/usr/bin/env bash
# run the custom AniList API locally ($PORT or 8000)
pip install --break-system-packages -q -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port "${PORT:-8000}"
