"""Locates the Medals-AI repo and materializes both Pillar Summarizer source
generations so the fidelity scripts in this folder can import and call the real
prompt builders / retry loop directly.

v1 = azure-functions/pillar-summarizer         (identical on main and core42-tests)
v2 = azure-functions-unified/pillar-summarizer (exists only on core42-tests)

Set MEDALS_AI_REPO to override the default location.
"""

import os
import pathlib
import subprocess
import sys
import tempfile

# .../sword/prompt-playbox/prompt-lab/tools/pillar_summarizer_fidelity/_source.py
# parents: [0]=this dir [1]=tools [2]=prompt-lab [3]=prompt-playbox [4]=sword
DEFAULT_REPO = pathlib.Path(__file__).resolve().parents[4] / "Medals-AI"
REPO = pathlib.Path(os.environ.get("MEDALS_AI_REPO", DEFAULT_REPO))

_MODULES = ("__init__.py", "exceptions.py", "logging_utils.py", "models.py", "utils.py", "summarizer.py")

_SOURCES = {
    "ps_v1": ("main", "azure-functions/pillar-summarizer/core"),
    "ps_v2": ("origin/core42-tests", "azure-functions-unified/pillar-summarizer/core"),
}


def _git(*args) -> bytes:
    return subprocess.check_output(["git", "-C", str(REPO), *args])


def materialize() -> pathlib.Path:
    """Extract both generations into a temp dir, put it on sys.path, return it."""
    if not (REPO / ".git").exists():
        sys.exit(
            f"Medals-AI repo not found at {REPO}.\n"
            "Set MEDALS_AI_REPO to its path, e.g.\n"
            "  MEDALS_AI_REPO=/path/to/Medals-AI python verify_loop.py"
        )

    root = pathlib.Path(tempfile.mkdtemp(prefix="ps_fidelity_"))
    for pkg, (ref, path) in _SOURCES.items():
        d = root / pkg
        d.mkdir()
        for mod in _MODULES:
            blob = _git("show", f"{ref}:{path}/{mod}")
            # The real __init__.py eagerly imports the Azure Functions entrypoint
            # chain; blanked so these packages import with nothing but pydantic.
            (d / mod).write_bytes(b"" if mod == "__init__.py" else blob)

    sys.path.insert(0, str(root))
    return root


def assert_v1_branch_agnostic() -> None:
    """v1 is only unambiguous because azure-functions/pillar-summarizer is
    identical on both branches -- unlike record-evaluator and attempt-comparator,
    where it is NOT (see schemas/SCHEMAS.md provenance note). Re-checked here so
    an upstream change that breaks that assumption fails loudly."""
    diff = _git("diff", "--stat", "main", "origin/core42-tests", "--", "azure-functions/pillar-summarizer/")
    if diff.strip():
        sys.exit(
            "azure-functions/pillar-summarizer now DIFFERS between main and core42-tests.\n"
            "The 'v1 is unambiguous' assumption in schemas/SCHEMAS.md no longer holds -- "
            "decide which branch's version v1 should mean before trusting these results.\n\n"
            + diff.decode(errors="replace")
        )
