"""Hermetic unit tests for the cleartext internal-IP detector (info-disclosure).

Pure ``str`` in / ``list[InternalIpSighting]`` out — no DB, no engine, no marker, so it
runs in the fast lane. Exercises the five locked ranges, the boundary guards (no embedded
or trailing-dot mis-slices), the octet/range rejections, and the per-blob cap.
"""

from __future__ import annotations

import pytest

from recon.findings.internal_ip import find_internal_ips

# (source, expected_value, expected_category) — one clean sighting each of the five ranges.
_POSITIVES = [
    ("10.0.0.1", "rfc1918"),  # 10.0.0.0/8
    ("10.255.255.255", "rfc1918"),  # /8 upper edge
    ("172.16.0.1", "rfc1918"),  # 172.16.0.0/12 lower edge
    ("172.31.255.254", "rfc1918"),  # /12 upper edge (172.31)
    ("192.168.0.1", "rfc1918"),  # 192.168.0.0/16
    ("127.0.0.1", "loopback"),  # 127.0.0.0/8
    ("127.255.255.255", "loopback"),  # /8 upper edge
    ("169.254.0.1", "link-local"),  # 169.254.0.0/16
]


@pytest.mark.parametrize(("value", "category"), _POSITIVES)
def test_positive_ranges_are_detected_and_classified(value: str, category: str) -> None:
    text = f'const host = "{value}";'
    sightings = find_internal_ips(text)

    assert len(sightings) == 1
    sighting = sightings[0]
    assert sighting.value == value
    assert sighting.category == category
    # offsets index the decoded source string (match.span()) and round-trip to the value
    assert text[sighting.offset_start : sighting.offset_end] == value
    assert sighting.line == 1


def test_line_number_is_one_based_from_preceding_newlines() -> None:
    text = "row0\nrow1\nconst h = '192.168.1.1';\nrow3"
    (sighting,) = find_internal_ips(text)

    assert sighting.value == "192.168.1.1"
    assert sighting.category == "rfc1918"
    assert sighting.line == 3  # two '\n' precede the offset -> line 3


def test_multiple_sightings_kept_in_source_order_with_distinct_offsets() -> None:
    text = "a=10.0.0.1;b=127.0.0.1;c=169.254.9.9;"
    sightings = find_internal_ips(text)

    assert [s.value for s in sightings] == ["10.0.0.1", "127.0.0.1", "169.254.9.9"]
    assert [s.category for s in sightings] == ["rfc1918", "loopback", "link-local"]
    offsets = [s.offset_start for s in sightings]
    assert offsets == sorted(offsets) and len(set(offsets)) == 3


# --- negatives: public / out-of-range / invalid octet -----------------------------------


@pytest.mark.parametrize(
    "value",
    [
        "8.8.8.8",  # public
        "1.2.3.4",  # public
        "172.15.0.1",  # just below the /12 block (172.16-172.31)
        "172.32.0.1",  # just above the /12 block
        "169.253.0.1",  # link-local is /16 at 169.254 only
        "126.0.0.1",  # loopback is 127/8 only
        "11.0.0.1",  # rfc1918 10/8 only
        "256.1.1.1",  # octet > 255 -> not a valid address
        "300.300.300.300",  # every octet out of range
    ],
)
def test_public_or_out_of_range_addresses_are_skipped(value: str) -> None:
    assert find_internal_ips(f'x = "{value}"') == []


# --- negatives: boundary guards ---------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "v1.2.3.4",  # leading alnum -> lookbehind rejects (also public anyway)
        "x10.0.0.1",  # a quad glued to a leading word char
        "10.0.0.1x",  # a quad glued to a trailing word char
        "build.10.0.0.1",  # leading dot -> lookbehind rejects
        "10.0.0.1.7",  # trailing dot+digit -> lookahead rejects the 4th octet
    ],
)
def test_embedded_quads_are_not_matched(text: str) -> None:
    assert find_internal_ips(text) == []


def test_longest_last_octet_wins() -> None:
    # The greedy 4th octet + trailing boundary guard force ".10", never a truncated ".1".
    (sighting,) = find_internal_ips("192.168.1.10")

    assert sighting.value == "192.168.1.10"


def test_trailing_dot_does_not_mis_slice_a_shorter_quad() -> None:
    # `192.168.1.1.5` must NOT yield `192.168.1.1`: the '.' after the 4th octet trips the
    # lookahead, and every shifted 4-octet window is preceded by `[\w.]`, so nothing matches.
    sightings = find_internal_ips("192.168.1.1.5")

    assert [s.value for s in sightings] == []
    assert "192.168.1.1" not in {s.value for s in sightings}


# --- cap --------------------------------------------------------------------------------


def test_per_blob_cap_stops_after_the_limit() -> None:
    # A blob dense with dotted-quads must not blow up emit: detection stops at the cap.
    text = " ".join(["10.0.0.1"] * 6000)

    assert len(find_internal_ips(text)) == 5000  # default cap
    assert len(find_internal_ips(text, cap=10)) == 10  # override honored
