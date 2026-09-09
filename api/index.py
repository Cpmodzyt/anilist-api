"""Vercel serverless entrypoint — exposes the same FastAPI `app`."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app import app  # noqa: E402  (Vercel looks for `app` in api/index.py)
