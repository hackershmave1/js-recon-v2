"""The "third_party" hidden reason (GLOSSARY: Third-party code), per the #181 decision and its
design review: hidden only when EVERY occurrence comes from a third-party package file, never a
confirmed secret, never a call to the target's own origin (relative) or an in-scope host."""

from __future__ import annotations

from recon.findings import visibility

LIB = "node_modules/.pnpm/@statsig+client-core@3.31.2/node_modules/@statsig/client-core/src/NetworkCore.js"
LIB2 = "node_modules/.pnpm/lodash-es@4.17.21/node_modules/lodash-es/_Set.js"
APP = "src/api/users.ts"
FP = ["acme.io"]


def _why(ftype, occurrences, first_party=FP, value="", app_packages=()):
    return visibility.why_hidden(
        ftype,
        value,
        [h for h, _p in occurrences],
        first_party,
        occurrences=occurrences,
        app_packages=app_packages,
    )


def test_an_off_scope_call_only_from_package_code_is_third_party():
    assert _why("endpoint", [("featureassets.org", LIB)]) == visibility.THIRD_PARTY


def test_a_relative_call_from_package_code_is_the_apps_own_surface():
    # Design review HIGH: `/api/auth/session` from an auth library hits the target's origin.
    assert _why("endpoint", [(None, LIB)]) is None
    assert _why("param", [(None, LIB)]) is None


def test_an_in_scope_host_keeps_it_visible():
    assert _why("endpoint", [("api.acme.io", LIB)]) is None


def test_any_app_or_bundle_occurrence_keeps_it_visible():
    assert _why("endpoint", [("featureassets.org", LIB), ("featureassets.org", APP)]) is None
    assert _why("endpoint", [("featureassets.org", LIB), ("featureassets.org", "input.js")]) is None
    assert _why("endpoint", [("featureassets.org", LIB), ("featureassets.org", None)]) is None
    assert _why("endpoint", [("featureassets.org", "https://acme.io/app.js")]) is None  # a bundle


def test_confirmed_secrets_are_never_hidden_as_third_party():
    assert _why("secret", [(None, LIB)]) is None
    assert _why("secret_suspected", [(None, LIB)]) == visibility.THIRD_PARTY


def test_hostless_sinks_in_package_code_are_hidden():
    assert _why("storage_sink", [(None, LIB2)]) == visibility.THIRD_PARTY


def test_a_package_treated_as_app_code_is_shown():
    assert _why("endpoint", [("featureassets.org", LIB)], app_packages=["@statsig/*"]) is None


def test_noise_still_wins_over_third_party():
    assert _why("endpoint", [("api.mixpanel.com", LIB)]) == visibility.NOISE
