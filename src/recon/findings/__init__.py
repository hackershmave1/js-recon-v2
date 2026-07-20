"""Findings feature — content-addressed finding identity and (slice 2) storage.

The normalizer (``normalize``) turns a raw extracted finding into the stable
``finding_hash`` that keys the REQ-A3 exactly-once outbox and the REQ-D5 diff.
Design: ``docs/req-d3-finding-hash-normalization.md``.
"""
