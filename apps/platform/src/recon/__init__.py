"""Recon platform — async recon pipeline.

Slice 1 (async spine): a recon run exists as a persisted state machine, work is
enqueued off the request thread, and status streams back over SSE with a poll
fallback. See ARCHITECTURE in the module docstrings and the REQ-* tags in code.
"""

__version__ = "0.1.0"
