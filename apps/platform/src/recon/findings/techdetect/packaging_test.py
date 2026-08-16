import tomllib
from pathlib import Path

import pytest

from recon.findings.techdetect import dataset


def _find_repo_root() -> Path | None:
    """Walk up from this test file to the repo root, identified by the pair of
    markers unique to it (a root ``NOTICE`` next to ``apps/platform/pyproject.toml``).

    Returns None when the source tree isn't present -- e.g. running from the
    installed wheel inside the app image, whose layout is ``/app/src/...`` and
    carries neither the repo-root ``NOTICE`` nor the ``apps/platform`` prefix. A
    hardcoded ``parents[N]`` is fragile across those depths: it overshoots the
    filesystem root in the image (IndexError). The two assertions below are
    inherently about the SOURCE repo, so they skip when it isn't locatable and
    stay covered by the host-tests lane, which runs on a real source checkout.
    The in-image guarantee that the data actually ships is
    ``test_dataset_resolves_via_package_data`` (importlib.resources), which runs
    everywhere."""
    for parent in Path(__file__).resolve().parents:
        if (parent / "NOTICE").is_file() and (parent / "apps/platform/pyproject.toml").is_file():
            return parent
    return None


def test_dataset_resolves_via_package_data():
    # importlib.resources must find the vendored JSON -- the wheel-drops-data class of
    # bug (the Kingfisher AKIA rule) only bites once package-data is declared. This is
    # the runtime proof the data ships, and it runs in the app image too.
    techs, categories, commit = dataset.load_raw()
    assert techs and categories and commit


def test_techdetect_data_is_declared_package_data():
    root = _find_repo_root()
    if root is None:
        pytest.skip("source tree not present (installed image); covered by the host-tests lane")
    pyproject = tomllib.loads((root / "apps/platform/pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "recon.findings.techdetect_data" in package_data
    globs = package_data["recon.findings.techdetect_data"]
    assert any(g.endswith("*.json") for g in globs)
    # commit.txt is load-bearing (dataset.load_raw reads it for dataset_commit()) but
    # doesn't match "*.json" -- it needs its OWN explicit glob entry or the wheel
    # drops it silently (the same wheel-drops-data class of bug as the JSON above).
    assert "commit.txt" in globs


def test_gpl_notice_names_the_enthec_dataset_server_side_only():
    root = _find_repo_root()
    if root is None:
        pytest.skip("source tree not present (installed image); covered by the host-tests lane")
    notice = (root / "NOTICE").read_text(encoding="utf-8")
    assert "enthec" in notice and "GPL-3.0" in notice and "server-side" in notice.lower()
