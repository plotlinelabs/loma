"""Loma Devices: agent access to Android emulators / iOS simulators on enrolled runners.

Layout:
  store.py    Mongo collections, ids, token hashing, indexes
  hub.py      in-process registry of connected runners + request/response RPC
  builds.py   short-lived build blobs and GitHub Actions artifact resolution
  service.py  the one policy layer (ACL, leases, argument validation, audit)
              used by the isolated tool gateway, the legacy CLI and the dashboard
"""
