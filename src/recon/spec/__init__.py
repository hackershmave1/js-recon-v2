"""Spec feature — ingest an uploaded OpenAPI/Swagger spec and classify observed
client findings against it to flag undocumented ("shadow") endpoints.

``ingest`` turns an untrusted spec upload into a validated, structural
``IngestedSpec`` (documented operation set + resolved server bases); a later
module (``classify``) reduces both sides to one canonical compare-key and
buckets each finding as documented/shadow/unresolved.
Design: docs/superpowers/specs/2026-07-28-shadow-api-detection-design.md §4-5.
"""
