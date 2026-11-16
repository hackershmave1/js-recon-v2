"""Quarantine of scripts from hosts the researcher has not decided on (ADR-0010).

A feature folder: it owns its logic and tests, and uses only ``recon.storage``, ``recon.db`` and
the shared run helpers. Nothing here is imported by a reader of run assets: that is the point.
"""
