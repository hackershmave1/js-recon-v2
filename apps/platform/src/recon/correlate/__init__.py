"""Correlate stage — REQ-C3 runtime host resolution.

Matches the request URLs a capture run observed (``discover.assets`` ``requests_ref``)
to the static endpoint findings, recovering the real host/URL an endpoint lives on and
attaching it as ground-truth runtime evidence. Runs in the (previously no-op) CORRELATING
stage, after ANALYZE has written findings and before finalize's spec reclassify.
"""
