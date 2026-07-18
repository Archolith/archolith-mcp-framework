"""GitMixin — automatic git add+commit for MCP servers.

Provides:
- git_auto_commit(): stages one or more files and commits only those paths
- Reads GIT_AUTO_COMMIT env var (set to "1" to enable); no-op if unset
- Reads GIT_REPO_ROOT env var for the repo path; defaults to cwd

Git operations are best-effort: failures are logged but never crash
the caller. The file mutation that triggered the commit always
succeeds even if the git step fails.
"""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path
from typing import Sequence

logger = logging.getLogger(__name__)

# Conventional-commit action verbs mapped from operation type
_COMMIT_ACTIONS: dict[str, str] = {
    "write": "write",
    "write_chunk": "write",
    "append": "append",
    "archive": "archive",
    "restore": "restore",
    "delete": "delete",
}


class GitMixin:
    """Mixin for automatic git commits on file mutations in MCP servers.

    Compose with BaseGatewayServer via multiple inheritance::

        class MyServer(BaseGatewayServer, GitMixin):
            ...

    Set GIT_AUTO_COMMIT=1 to enable. The mixin is a no-op when disabled.
    """

    _git_enabled: bool | None = None
    _git_repo_root: str | None = None

    def _is_git_enabled(self) -> bool:
        """Check if git auto-commit is enabled (cached on first call)."""
        if self._git_enabled is None:
            self._git_enabled = os.environ.get("GIT_AUTO_COMMIT", "") == "1"
        return self._git_enabled

    def _get_git_repo_root(self) -> str:
        """Return the git repo root from env, caching on first call.

        Defaults to the current working directory if GIT_REPO_ROOT is unset.
        """
        if self._git_repo_root is None:
            self._git_repo_root = os.environ.get("GIT_REPO_ROOT", os.getcwd())
        return self._git_repo_root

    def git_auto_commit(
        self,
        file_paths: str | Path | Sequence[str | Path],
        operation: str,
        label: str,
    ) -> dict[str, str | bool | None]:
        """Stage one or more files and commit them to git.

        Best-effort: if git is not enabled, the file is not in a git repo,
        or any git command fails, the failure is logged and the method
        returns a result dict with ``committed=False``. The calling tool
        should NOT fail — the file mutation has already succeeded.

        Args:
            file_paths: One or more absolute paths to stage and commit.
                A single path (str or Path) or a sequence of paths.
                Multiple paths are useful for archive/restore operations
                where both a deletion and an addition must be staged.
            operation: The artifact operation that triggered the commit
                (``"write"``, ``"write_chunk"``, ``"append"``,
                ``"archive"``, ``"restore"``, ``"delete"``).
            label: Short human-readable label for the commit message,
                typically ``"{artifact_type}/{filename}"``.

        Returns:
            Dict with keys:
            - ``committed`` (bool): whether the commit succeeded
            - ``commit_hash`` (str|None): short hash if committed
            - ``error`` (str|None): error message if commit failed
        """
        if not self._is_git_enabled():
            return {"committed": False, "commit_hash": None, "error": None}

        # Normalize to list of Path objects
        if isinstance(file_paths, (str, Path)):
            paths = [Path(file_paths)]
        else:
            paths = [Path(p) for p in file_paths]

        repo_root = Path(self._get_git_repo_root()).resolve()
        relative_paths: list[str] = []
        for path in paths:
            resolved_path = path.resolve()
            try:
                relative_paths.append(str(resolved_path.relative_to(repo_root)))
            except ValueError:
                logger.warning(
                    "git auto-commit path %s is outside repo root %s",
                    resolved_path,
                    repo_root,
                )
                return {
                    "committed": False,
                    "commit_hash": None,
                    "error": "path outside repo root",
                }

        action = _COMMIT_ACTIONS.get(operation, operation)
        message = "chore(artifacts): %s %s" % (action, label)

        try:
            # Stage all files (even deleted files — git add records the removal)
            add_args = ["git", "add", "--"] + relative_paths
            add_result = subprocess.run(
                add_args,
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=10,
            )
            if add_result.returncode != 0:
                logger.warning(
                    "git add failed for %s: %s", label, add_result.stderr.strip()
                )
                return {
                    "committed": False,
                    "commit_hash": None,
                    "error": add_result.stderr.strip(),
                }

            # Commit
            commit_result = subprocess.run(
                ["git", "commit", "--only", "-m", message, "--", *relative_paths],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=10,
            )
            if commit_result.returncode != 0:
                # "nothing to commit" is not an error — file may already be
                # committed (e.g. overwrite with identical content)
                stderr_lower = commit_result.stderr.lower()
                stdout_lower = commit_result.stdout.lower()
                if "nothing to commit" in stderr_lower or "nothing to commit" in stdout_lower:
                    logger.info("git commit: nothing to commit for %s", label)
                    return {"committed": False, "commit_hash": None, "error": None}
                logger.warning(
                    "git commit failed for %s: %s",
                    label,
                    commit_result.stderr.strip(),
                )
                return {
                    "committed": False,
                    "commit_hash": None,
                    "error": commit_result.stderr.strip(),
                }

            # Get short hash of the new commit
            hash_result = subprocess.run(
                ["git", "rev-parse", "--short", "HEAD"],
                capture_output=True,
                text=True,
                cwd=str(repo_root),
                timeout=5,
            )
            commit_hash = hash_result.stdout.strip() if hash_result.returncode == 0 else None

            logger.info(
                "git auto-commit: %s [%s]", message, commit_hash or "?"
            )
            return {"committed": True, "commit_hash": commit_hash, "error": None}

        except subprocess.TimeoutExpired:
            logger.warning("git auto-commit timed out for %s", label)
            return {"committed": False, "commit_hash": None, "error": "timeout"}
        except FileNotFoundError:
            logger.warning("git binary not found; skipping auto-commit")
            return {
                "committed": False,
                "commit_hash": None,
                "error": "git not found",
            }
        except Exception as e:
            logger.warning("git auto-commit error for %s: %s", label, e)
            return {"committed": False, "commit_hash": None, "error": str(e)}
