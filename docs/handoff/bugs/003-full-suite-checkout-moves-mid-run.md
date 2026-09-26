# Bug 003: full-suite battery run in a checkout that moves mid-run breaks `inspect.getsource` structural tests

Status: known; workaround adopted (freeze the tree during a battery run)
Found: session 7, 2026-09-26, during the integrated battery around the matcher `2026.09.2` review rounds
Severity: low. Only affects local batteries run in a working tree that is edited or checked out
against while pytest is still running; CI runners and a frozen worktree are unaffected.

## Cause

Some structural/self-consistency tests call `inspect.getsource` (or similar source-introspection
helpers) on modules already imported into the running pytest process. If the checkout under test
moves mid-run — a concurrent `git checkout`, rebase, or file edit lands on disk while the suite is
still executing — the source file `inspect.getsource` reads back no longer matches the bytecode
already loaded into the process. The mismatch surfaces as a structural-test failure that has
nothing to do with the code change itself; it is an artifact of reading a stale-vs-fresh source
file pairing mid-run, not a real regression.

## Fix

Freeze the tree for the duration of any full-suite battery: run batteries from a dedicated
worktree checked out at a fixed OID, and do not `git checkout`, rebase, or hand-edit files in that
tree while the run is in flight. Ordinary single-file or targeted runs are unaffected because they
do not race a moving checkout against `inspect.getsource` calls across the whole module graph.

## Lesson

`inspect.getsource`-based structural tests are only meaningful against a checkout that is stable
for the whole run. Any battery that must claim a full-suite result should execute in a worktree
pinned to one OID, never in a checkout another session (or a rebase/switch in the same session)
might still be mutating.
