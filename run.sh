#!/usr/bin/env bash
# run the custom AniList API
pip install --break-system-packages -q -r requirements.txt
uvicorn app:app --host 0.0.0.0 --port 8000
