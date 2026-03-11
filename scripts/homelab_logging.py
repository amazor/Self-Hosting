"""Step-based progress tracking and summary formatting for homelab scripts.

Provides StepTracker — the primary user-facing output mechanism for deploy
and bootstrap scripts.  Designed to replace bare ``log.info()`` calls with
structured, countable steps that show progress even without verbose mode.

Verbosity levels:
  - default:  step counter + outcome (success/warn/fail)
  - verbose (-v):  step counter + per-step detail lines + outcome
  - (future -vv):  all of the above + DEBUG-level log output
"""

from __future__ import annotations

import os
import sys


# ---------------------------------------------------------------------------
# Output helpers
# ---------------------------------------------------------------------------

def _write(msg: str, *, indent: int = 0) -> None:
    """Write a line to stderr (same stream as logging)."""
    prefix = "  " * indent
    sys.stderr.write(f"{prefix}{msg}\n")
    sys.stderr.flush()


# ---------------------------------------------------------------------------
# StepContext — context manager for a single step
# ---------------------------------------------------------------------------

class _StepContext:
    """Tracks a single step's lifecycle.

    Returned by ``StepTracker.step()``.  On exit the step is marked as
    *success* unless ``tracker.fail()`` or an unhandled exception occurred.
    """

    def __init__(self, tracker: StepTracker, name: str) -> None:
        self._tracker = tracker
        self._name = name
        self._finished = False

    def __enter__(self) -> _StepContext:
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> bool:  # noqa: ANN001
        if exc_type is not None and not self._finished:
            self._tracker.fail(f"{self._name} failed: {exc_val}")
            self._finished = True
            return False
        if not self._finished:
            self._tracker._auto_success(self._name)
            self._finished = True
        return False

    def mark_done(self) -> None:
        self._finished = True


# ---------------------------------------------------------------------------
# StepTracker
# ---------------------------------------------------------------------------

class StepTracker:
    """Track and display numbered step progress with an end summary.

    Parameters
    ----------
    total:
        Expected number of steps.  May be adjusted via ``set_total()``.
    verbose:
        When True, ``detail()`` calls are printed.
    indent:
        Extra leading indentation level (for nested trackers, e.g.
        bootstrap running inside deploy).
    """

    def __init__(
        self,
        total: int,
        *,
        verbose: bool = False,
        indent: int = 0,
    ) -> None:
        self.total = total
        self.current = 0
        self.verbose = verbose
        self.indent = indent
        self._results: list[tuple[str, str]] = []   # (status, message)
        self._warnings: list[str] = []
        self._actions: list[str] = []

    # -- total adjustment ---------------------------------------------------

    def set_total(self, total: int) -> None:
        self.total = total

    # -- step lifecycle -----------------------------------------------------

    def step(self, name: str) -> _StepContext:
        """Start a new numbered step.  Returns a context manager."""
        self.current += 1
        _write(
            f"[{self.current}/{self.total}] {name}...",
            indent=self.indent,
        )
        return _StepContext(self, name)

    # -- within-step output -------------------------------------------------

    def detail(self, msg: str) -> None:
        """Print a detail line (only shown in verbose mode)."""
        if self.verbose:
            _write(f"      {msg}", indent=self.indent)

    def success(self, msg: str) -> None:
        """Mark the current step as successful with a message."""
        _write(f"      \u2713 {msg}", indent=self.indent)
        self._results.append(("ok", msg))

    def warn(self, msg: str) -> None:
        """Record a warning within the current step."""
        _write(f"      \u26a0 {msg}", indent=self.indent)
        self._results.append(("warn", msg))
        self._warnings.append(msg)

    def fail(self, msg: str) -> None:
        """Record a failure within the current step."""
        _write(f"      \u2717 {msg}", indent=self.indent)
        self._results.append(("fail", msg))

    def add_action(self, msg: str) -> None:
        """Register a post-deploy manual action for the end summary."""
        self._actions.append(msg)

    # -- internal -----------------------------------------------------------

    def _auto_success(self, step_name: str) -> None:
        """Called by _StepContext when no explicit success/fail was recorded."""
        last_status = self._results[-1][0] if self._results else None
        if last_status in ("ok", "fail"):
            return
        self.success(step_name)

    # -- summary ------------------------------------------------------------

    @property
    def passed(self) -> int:
        return sum(1 for s, _ in self._results if s == "ok")

    @property
    def failed(self) -> int:
        return sum(1 for s, _ in self._results if s == "fail")

    def print_summary(
        self,
        stack: str,
        *,
        config_path: str | None = None,
        overlays: list[tuple[str, bool]] | None = None,
    ) -> None:
        """Print a formatted end-of-run summary block."""
        w = _write
        bar = f"\u2501\u2501\u2501 {stack} " + "\u2501" * max(1, 38 - len(stack))
        w("")
        w(bar, indent=self.indent)

        if config_path:
            w(f"  Config:    {config_path}", indent=self.indent)

        if overlays:
            parts = []
            for label, enabled in overlays:
                mark = "\u2713" if enabled else "\u2717"
                parts.append(f"{label} {mark}")
            w(f"  Overlays:  {'  '.join(parts)}", indent=self.indent)

        total = self.passed + self.failed + len(self._warnings)
        w(f"  Status:    {self.passed}/{total} steps passed", indent=self.indent)

        if self._warnings:
            w("", indent=self.indent)
            w("  Warnings:", indent=self.indent)
            for msg in self._warnings:
                w(f"    - {msg}", indent=self.indent)

        if self._actions:
            w("", indent=self.indent)
            w("  \u26a0 Action required:", indent=self.indent)
            for msg in self._actions:
                for i, line in enumerate(msg.splitlines()):
                    prefix = "    - " if i == 0 else "      "
                    w(f"{prefix}{line}", indent=self.indent)

        w("\u2501" * (len(bar)), indent=self.indent)


# ---------------------------------------------------------------------------
# Convenience: create a tracker with deploy-mode awareness
# ---------------------------------------------------------------------------

def make_tracker(
    total: int,
    *,
    verbose: bool = False,
) -> StepTracker:
    """Create a StepTracker, auto-detecting indent from HOMELAB_DEPLOY."""
    deploy_mode = bool(os.environ.get("HOMELAB_DEPLOY"))
    return StepTracker(
        total,
        verbose=verbose,
        indent=1 if deploy_mode else 0,
    )
