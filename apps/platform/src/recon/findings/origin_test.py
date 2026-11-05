"""Third-party code detection by package path (GLOSSARY: App code / Third-party code). Real
shapes from a capture: pnpm's `.pnpm/<name>@<version>[_<peer>@<v>]/node_modules/<name>/…`."""

from __future__ import annotations

import pytest

from recon.findings import origin

PNPM_SCOPED = "node_modules/.pnpm/@statsig+client-core@3.31.2/node_modules/@statsig/client-core/src/EventLogger.js"
PNPM_PEER = (
    "node_modules/.pnpm/@module-federation+runtime-core@2.6.0_node-fetch@2.7.0/"
    "node_modules/@module-federation/runtime-core/dist/plugins/snapshot/SnapshotHandler.cjs"
)


@pytest.mark.parametrize(
    ("path", "package"),
    [
        (PNPM_SCOPED, ("@statsig/client-core", "3.31.2")),
        (PNPM_PEER, ("@module-federation/runtime-core", "2.6.0")),  # peer suffix dropped
        (
            "node_modules/.pnpm/lodash-es@4.17.21/node_modules/lodash-es/_Set.js",
            ("lodash-es", "4.17.21"),
        ),
        (
            "webpack/acme/node_modules/axios/lib/core/Axios.js",
            ("axios", None),
        ),  # npm: no version in path
        ("node_modules/@babel/runtime/helpers/esm/defineProperty.js", ("@babel/runtime", None)),
        (".yarn/cache/react-npm-18.2.0-abc.zip/node_modules/react/index.js", ("react", None)),
        ("bower_components/jquery/dist/jquery.js", ("jquery", None)),
    ],
)
def test_third_party_files_name_their_package(path, package):
    assert origin.is_third_party(path)
    assert origin.package_of(path) == package


@pytest.mark.parametrize(
    "path",
    [
        "packages/core-web/dist/logger/logger.service.js",  # the app's own monorepo package
        "src/api/users.ts",
        "webpack/acme/./src/node_modules_helper.js",  # a name, not a node_modules folder
        "https://acme.io/static/app.js",  # a bundle URL: unclassified, never guessed
        "input.js",
        "",
    ],
)
def test_app_code_and_bundles_are_not_third_party(path):
    assert not origin.is_third_party(path)
    assert origin.package_of(path) is None


def test_a_package_treated_as_app_code_is_not_third_party():
    private = "node_modules/.pnpm/@acme+api-client@1.0.0/node_modules/@acme/api-client/index.js"
    assert origin.is_third_party(private)
    assert not origin.is_third_party(private, app_packages=["@acme/*"])
    assert not origin.is_third_party(private, app_packages=["@acme/api-client"])
    assert origin.is_third_party(private, app_packages=["@acme/other", "acme"])
