"""Unit tests for the pure project-config helpers (api/app/project_config.py).

Loaded by file path so the stdlib-only helpers test without the app import chain
(run via pytest, or standalone: python this_file)."""
import importlib.util
import pathlib

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "project_config.py"
_spec = importlib.util.spec_from_file_location("project_config", _MODULE_PATH)
project_config = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(project_config)

system_defaults = project_config.system_defaults
deep_merge = project_config.deep_merge
validate_config = project_config.validate_config
resolve_effective_config = project_config.resolve_effective_config
split_effective = project_config.split_effective


def test_resolve_inherits_all_when_no_overrides():
    d = system_defaults()
    d["scope"]["rootDomains"] = ["*.acme.com"]
    eff, keys = resolve_effective_config(d, None)
    assert eff["scope"]["rootDomains"] == ["*.acme.com"]
    assert keys == []


def test_resolve_override_replaces_per_field_and_records_key():
    d = system_defaults()
    d["scope"]["rootDomains"] = ["*.acme.com"]
    eff, keys = resolve_effective_config(d, {"scope": {"rootDomains": ["app.acme.com"]}})
    assert eff["scope"]["rootDomains"] == ["app.acme.com"]                 # replaced
    assert eff["scope"]["includeSubdomains"] == d["scope"]["includeSubdomains"]  # inherited
    assert keys == ["scope.rootDomains"]


def test_resolve_list_override_is_replace_not_union():
    d = system_defaults()
    d["denylist"]["rules"] = [{"tag": "a", "pattern": "*.a.com"}]
    eff, keys = resolve_effective_config(d, {"denylist": {"rules": []}})
    assert eff["denylist"]["rules"] == []                                  # replaced, not union
    assert keys == ["denylist.rules"]


def test_validate_rejects_bad_out_of_scope_mode():
    d = system_defaults()
    d["capture"]["outOfScopeMode"] = "nope"
    try:
        validate_config(d)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_validate_rejects_max_asset_mb_over_10():
    d = system_defaults()
    d["capture"]["maxAssetMb"] = 25
    try:
        validate_config(d)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_deep_merge_patch_leaf_wins_and_preserves_siblings():
    base = system_defaults()
    merged = deep_merge(base, {"analysis": {"analyzeOnUpload": True}})
    assert merged["analysis"]["analyzeOnUpload"] is True
    assert merged["analysis"]["captureSourceMaps"] == base["analysis"]["captureSourceMaps"]


def test_split_effective_separates_scope_from_rest():
    scope, cap = split_effective(system_defaults())
    assert set(scope) == {"rootDomains", "includeSubdomains"}
    assert set(cap) == {"capture", "denylist", "analysis"}


def test_validate_partial_only_checks_present_sections():
    validate_config({"analysis": {"analyzeOnUpload": True, "captureSourceMaps": False}}, partial=True)


if __name__ == "__main__":
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            _fn()
            print(f"  ok  {_name}")
    print("project_config tests: all assertions passed")
