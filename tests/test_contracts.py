"""Contracts the repository keeps with the machine it deploys to.

Three failures shipped in one day and no unit test could have seen any of them,
because none of them were about behaviour:

  - `openai` was pip-installed on the laptop and missing from requirements.txt.
    The box therefore had no agentic path at all: every agentic job died with
    ModuleNotFoundError one stage after the plan.
  - `TERAC_API_KEY` lived in Key Vault and in the local `.env` and nowhere in
    `.env.example`, so it never reached the box and `/v1/terac/status` answered
    with an empty org.
  - Swapping the text provider took six tests with it, because nothing asserted
    which provider the pipeline actually uses.

So these tests read the repository — its imports, its settings, its env
template — instead of calling into it. Every scan walks the source rather than
carrying a hardcoded list, so they keep holding as the code grows, and each one
is paired with a guard that fails if the walk stops finding anything.
"""

from __future__ import annotations

import ast
import re
import sys
from functools import lru_cache
from importlib.metadata import packages_distributions
from pathlib import Path

import pytest

from vira import llm
from vira.config import Settings

ROOT = Path(__file__).resolve().parent.parent
REQUIREMENTS = ROOT / "requirements.txt"
ENV_EXAMPLE = ROOT / ".env.example"

# Vendored, generated or not-ours trees. `video/` and `ui/` are Node.
IGNORED_DIRS = {
    ".git", ".venv", ".pytest_cache", "__pycache__", "node_modules",
    "out", "ui", "video", "examples", "sql", "docs",
}


@lru_cache
def source_files() -> tuple[Path, ...]:
    """Every Python file this repo owns, tests and entry-point scripts included."""
    return tuple(
        p for p in sorted(ROOT.rglob("*.py"))
        if not IGNORED_DIRS & set(p.relative_to(ROOT).parts)
    )


@lru_cache
def _trees() -> tuple[tuple[Path, ast.Module], ...]:
    return tuple((p, ast.parse(p.read_text(), filename=str(p))) for p in source_files())


def _rel(path: Path) -> str:
    return str(path.relative_to(ROOT))


# --- a. every third-party import is a declared dependency --------------------


def _imported_roots(tree: ast.Module) -> set[str]:
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        # A relative import is this repo talking to itself.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


@lru_cache
def _local_modules() -> frozenset[str]:
    return frozenset(
        {p.name for p in ROOT.iterdir() if (p / "__init__.py").exists()}
        | {p.stem for p in ROOT.glob("*.py")}
    )


@lru_cache
def third_party_imports() -> frozenset[str]:
    """Top-level module names imported from outside the standard library."""
    roots: set[str] = set()
    for _, tree in _trees():
        roots |= _imported_roots(tree)
    return frozenset(
        r for r in roots
        if r not in sys.stdlib_module_names and r not in _local_modules()
    )


def _canonical(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).strip().lower()


@lru_cache
def declared_requirements() -> frozenset[str]:
    """Distribution names in requirements.txt, extras and pins stripped."""
    names: set[str] = set()
    for line in REQUIREMENTS.read_text().splitlines():
        line = line.split("#")[0].strip()
        if not line:
            continue
        if m := re.match(r"^([A-Za-z0-9._-]+)", line):
            names.add(_canonical(m.group(1)))
    return frozenset(names)


def _distributions_for(module: str) -> set[str]:
    """The package(s) that provide an import name — `pydantic_settings` ships
    as `pydantic-settings`, `sqlalchemy` as `SQLAlchemy`. An import we cannot
    resolve is judged under its own name, which is the honest guess."""
    found = packages_distributions().get(module)
    return {_canonical(d) for d in found} if found else {_canonical(module)}


def test_every_third_party_import_is_declared_in_requirements():
    declared = declared_requirements()
    missing = {
        module: sorted(_distributions_for(module))
        for module in sorted(third_party_imports())
        if not _distributions_for(module) & declared
    }
    assert not missing, (
        f"imported by this repo, absent from requirements.txt: {missing}. "
        "Installed on a laptop is not installed on the box."
    )


def test_the_import_scan_reaches_the_source():
    """A walk that quietly stops matching would leave the test above passing
    forever with nothing behind it."""
    assert {"httpx", "openai", "pydantic"} <= third_party_imports()
    assert len(source_files()) > 30


# --- b. every key a deployer must set is in the env template -----------------


def _env_template_keys() -> set[str]:
    keys: set[str] = set()
    for line in ENV_EXAMPLE.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        keys.add(line.split("=", 1)[0].strip().upper())
    return keys


def _settings_needing_the_environment() -> set[str]:
    """Settings with no usable default: unset means the feature is off.

    These are exactly the keys that have to travel to a new machine, and the
    only place a deployer looks for that list is `.env.example`.
    """
    return {
        name.upper()
        for name, field in Settings.model_fields.items()
        if field.is_required() or field.default is None
    }


def test_every_setting_that_needs_the_environment_is_in_the_env_template():
    missing = sorted(_settings_needing_the_environment() - _env_template_keys())
    assert not missing, (
        f"in vira/config.py, invisible in .env.example: {missing}. "
        "A key nobody can see is a key that never reaches the box."
    )


def test_the_env_template_advertises_nothing_the_code_reads():
    """The reverse direction, and it is how a removed provider leaves a ghost:
    a template key nothing loads sends whoever deploys hunting for a secret
    that changes nothing."""
    unread = sorted(
        _env_template_keys() - {name.upper() for name in Settings.model_fields}
    )
    assert not unread, f"in .env.example, read by nothing in vira/config.py: {unread}"


# --- c. the text provider is Azure, and Anthropic is gone --------------------


def test_the_configured_text_provider_is_azure(cfg):
    assert cfg.llm_provider == "azure"


async def test_every_completion_goes_through_the_one_azure_path(monkeypatch):
    """`complete` is the whole text surface. If a second provider is ever wired
    in behind it, this call stops arriving here and the failure is loud."""
    seen: list[tuple[str, str, int]] = []

    async def only_path(prompt, *, system, max_tokens):
        seen.append((prompt, system, max_tokens))
        return "answered", "stop"

    monkeypatch.setattr(llm, "_azure", only_path)
    text, stop = await llm.complete("the prompt", system="the system", max_tokens=99)

    assert seen == [("the prompt", "the system", 99)]
    assert (text, stop) == ("answered", "stop")


def test_nothing_in_this_repo_imports_anthropic():
    """Anthropic was removed rather than left as a fallback. A dormant branch
    nobody exercises is a trap, and the account behind it is unfunded."""
    offenders = sorted(
        f"{_rel(path)}:{node.lineno}"
        for path, tree in _trees()
        for node in ast.walk(tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for name in (
            [a.name for a in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
        )
        if name.split(".")[0] == "anthropic"
    )
    assert not offenders, f"the removed provider is back: {offenders}"


# --- d. no module reads a setting that no longer exists ----------------------
#
# The provider swap deleted `llm_model` from Settings. Nothing failed at import
# time, because `s.llm_model` is only evaluated when the line runs — and the
# lines that read it sit in the recipe snapshot, at the very end of a job that
# has already spent several minutes and real money.

_NESTED = (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)


def _own_nodes(scope: ast.AST) -> list[ast.AST]:
    """Everything in this scope, stopping at the boundary of a nested one."""
    out: list[ast.AST] = []
    stack = list(ast.iter_child_nodes(scope))
    while stack:
        node = stack.pop()
        out.append(node)
        if not isinstance(node, _NESTED):
            stack.extend(ast.iter_child_nodes(node))
    return out


def _bound_names(target: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(target) if isinstance(n, ast.Name)}


def _is_settings_call(node: ast.AST) -> bool:
    return isinstance(node, ast.Call) and getattr(node.func, "id", "") == "settings"


def _settings_reads(path: Path, tree: ast.Module) -> list[tuple[str, int, str]]:
    """(file, line, attribute) for every attribute read off a Settings object.

    Scoped per function, because `s = settings()` in one function and
    `for s in shots` in another are different `s`. Where a single scope binds
    the same name both ways, the name is dropped rather than guessed at.
    """
    reads: list[tuple[str, int, str]] = []
    scopes: list[ast.AST] = [tree]
    scopes += [n for n in ast.walk(tree) if isinstance(n, _NESTED)]

    for scope in scopes:
        own = _own_nodes(scope)
        from_settings: set[str] = set()
        otherwise: set[str] = set()
        for node in own:
            if isinstance(node, ast.Assign):
                names = set().union(*(_bound_names(t) for t in node.targets))
                (from_settings if _is_settings_call(node.value) else otherwise).update(names)
            elif isinstance(node, (ast.For, ast.AsyncFor, ast.comprehension)):
                otherwise |= _bound_names(node.target)
            elif isinstance(node, ast.withitem) and node.optional_vars is not None:
                otherwise |= _bound_names(node.optional_vars)
            elif isinstance(node, ast.NamedExpr):
                otherwise |= _bound_names(node.target)
            elif isinstance(node, ast.arg):
                otherwise.add(node.arg)

        handles = from_settings - otherwise
        for node in own:
            if not isinstance(node, ast.Attribute):
                continue
            value = node.value
            if (isinstance(value, ast.Name) and value.id in handles) or _is_settings_call(value):
                reads.append((_rel(path), node.lineno, node.attr))
    return reads


@lru_cache
def settings_reads() -> tuple[tuple[str, int, str], ...]:
    return tuple(
        read
        for path, tree in _trees()
        if "tests" not in path.relative_to(ROOT).parts
        for read in _settings_reads(path, tree)
    )


def test_every_setting_the_source_reads_still_exists():
    fields = set(Settings.model_fields)
    gone = sorted(
        f"{file}:{line} reads settings().{attr}"
        for file, line, attr in settings_reads()
        if attr not in fields
    )
    assert not gone, f"removed from vira/config.py but still read: {gone}"


def test_the_settings_scan_reaches_the_source():
    attrs = {attr for _, _, attr in settings_reads()}
    assert {"evidence_floor", "agent_model", "fps"} <= attrs


@pytest.mark.parametrize("removed", ["llm_model", "anthropic_api_key"])
def test_the_deleted_provider_settings_are_really_gone(removed):
    assert removed not in Settings.model_fields
