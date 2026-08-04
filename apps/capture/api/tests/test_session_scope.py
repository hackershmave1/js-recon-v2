"""Unit tests for the pure session-scope helpers (api/app/session_scope.py).

Loaded by file path so the module's stdlib-only helpers can be tested without the
full app/package import chain (run via pytest, or standalone: python this_file).
"""
import importlib.util
import pathlib

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "app" / "session_scope.py"
_spec = importlib.util.spec_from_file_location("session_scope", _MODULE_PATH)
session_scope = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(session_scope)

host_of = session_scope.host_of
derive_root_domains = session_scope.derive_root_domains
normalize_root_domains = session_scope.normalize_root_domains


def test_host_of_strips_scheme_www_port_userinfo_and_path():
    assert host_of("https://www.Example.com/a/b?x=1") == "example.com"
    assert host_of("http://api.example.com:8443/v1") == "api.example.com"
    assert host_of("app.example.com:8443") == "app.example.com"      # bare host:port
    assert host_of("user:pass@host.example.com") == "host.example.com"  # bare userinfo
    assert host_of("WWW.Foo.COM") == "foo.com"
    assert host_of("http://1.2.3.4:9000") == "1.2.3.4"
    assert host_of("wwwx.example.com") == "wwwx.example.com"          # not a www. prefix
    assert host_of("") == ""
    assert host_of(None) == ""


def test_normalize_root_domains_dedupes_normalizes_and_drops_blanks():
    out = normalize_root_domains([
        "https://app.target.com:8443/login",
        "API.target.com",
        "app.target.com",          # dup of the first once normalized
        "  ",                       # blank
        "user@admin.target.com",   # userinfo stripped
    ])
    assert out == ["app.target.com", "api.target.com", "admin.target.com"]


def test_normalize_root_domains_respects_limit_and_handles_none():
    assert normalize_root_domains(None) == []
    assert normalize_root_domains(["a.com", "b.com", "c.com"], limit=2) == ["a.com", "b.com"]


def test_derive_root_domains_orders_by_frequency_and_caps():
    urls = [
        "https://www.acme.com/a.js",
        "https://acme.com/b.js",
        "https://cdn.other.com/c.js",
        "https://acme.com/d.js",
    ]
    roots = derive_root_domains(urls, limit=5)
    assert roots[0] == "acme.com"          # most frequent (3, www-merged)
    assert "cdn.other.com" in roots
    assert len(derive_root_domains(urls, limit=1)) == 1


if __name__ == "__main__":
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            fn()
            print(f"  ok  {name}")
    print("session_scope tests: all assertions passed")
