"""Third-party code vs app code (GLOSSARY), by a source file's PACKAGE PATH.

A recovered original's path keeps the package manager's layout — npm/Yarn classic
``node_modules/<pkg>/…``, pnpm ``node_modules/.pnpm/<pkg>@<version>[_<peer>@<v>]/node_modules/<pkg>/…``,
Yarn PnP ``.yarn/cache/…``, Bower ``bower_components/<pkg>/…`` — and Sourcemapper's rewriting keeps
those segments. A path without them is app code: the target's own monorepo packages
(``packages/core-web/…``) resolve to their real path, not through ``node_modules``. A bundle URL is
never classified (there is no way to split a map-less bundle by path).
"""

from __future__ import annotations

import re
from collections.abc import Iterable

_SEGMENT = re.compile(r"(?:^|/)(node_modules|\.yarn|bower_components)/")
# The package directory after the LAST package-manager folder: `@scope/name` or `name`.
# A lookahead (not a consumed "/"), so a package nested inside another
# (`node_modules/foo/node_modules/@bar/baz/…`) is still found: the innermost one owns the file.
_PACKAGE = re.compile(r"(?:^|/)(?:node_modules|bower_components)/((?:@[^/]+/)?[^/.][^/]*)(?=/)")
# pnpm's store folder: `<name with / as +>@<version>[_<peer>@<version>…]`.
_PNPM = re.compile(r"(?:^|/)\.pnpm/((?:@[^/@]+\+)?[^/@]+)@([^/_]+)")


def package_of(path: str) -> tuple[str, str | None] | None:
    """``(package name, version or None)`` for a third-party file, else ``None``."""
    if not path or "://" in path or not _SEGMENT.search(path):
        return None
    names = _PACKAGE.findall(path)
    if not names:
        return None
    name = names[-1]
    pnpm = _PNPM.search(path)
    version = pnpm.group(2) if pnpm and pnpm.group(1).replace("+", "/") == name else None
    return name, version


def is_third_party(path: str, app_packages: Iterable[str] = ()) -> bool:
    """Third-party code, unless its package is one the researcher treats as app code (an exact
    name, or ``@scope/*`` for a whole private scope)."""
    package = package_of(path)
    if package is None:
        return False
    return not any(_matches(package[0], pattern) for pattern in app_packages)


def _matches(name: str, pattern: str) -> bool:
    pattern = pattern.strip()
    if pattern.endswith("/*"):
        return name.startswith(pattern[:-1])
    return name == pattern
