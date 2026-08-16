import tomllib
from pathlib import Path

from recon.findings.techdetect import dataset


def test_dataset_resolves_via_package_data():
    # importlib.resources must find the vendored JSON — the wheel-drops-data class of
    # bug (the Kingfisher AKIA rule) only bites once package-data is declared.
    techs, categories, commit = dataset.load_raw()
    assert techs and categories and commit


def test_techdetect_data_is_declared_package_data():
    # techdetect/ -> findings/ -> recon/ -> src/ -> platform/ -> apps/ -> repo root:
    # 6 levels up (see recon.api.app._default_dist's parents[3] from api/ to
    # platform/ for the same up-the-tree counting convention).
    root = Path(__file__).resolve().parents[6]  # repo root
    pyproject = tomllib.loads((root / "apps/platform/pyproject.toml").read_text(encoding="utf-8"))
    package_data = pyproject["tool"]["setuptools"]["package-data"]
    assert "recon.findings.techdetect_data" in package_data
    assert any(g.endswith("*.json") for g in package_data["recon.findings.techdetect_data"])


def test_gpl_notice_names_the_enthec_dataset_server_side_only():
    root = Path(__file__).resolve().parents[6]
    notice = (root / "NOTICE").read_text(encoding="utf-8")
    assert "enthec" in notice and "GPL-3.0" in notice and "server-side" in notice.lower()
