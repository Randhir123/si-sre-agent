"""
Safe subprocess runner.

This is the security boundary of the whole agent. The LLM proposes commands
indirectly (by choosing tools + arguments), but ONLY commands that pass these
checks ever reach a real shell. The checks are deny-by-default:

  1. The binary must be in ALLOWED_BINARIES.
  2. The command must not contain any token in DENY_TOKENS (delete, apply, etc).
  3. Commands run with shell=False (no shell interpolation, no pipes/;/&&).

If you want the agent to do more, you widen the allowlist deliberately — the
agent can never widen it itself.
"""
from __future__ import annotations

import shlex
import subprocess
from dataclasses import dataclass

# Only these binaries can ever be invoked.
ALLOWED_BINARIES = {"kubectl", "ibmcloud", "kafka-consumer-groups.sh", "kafka-topics.sh"}

# Any of these tokens anywhere in the argument list => hard refusal.
# These are the verbs/flags that mutate state.
DENY_TOKENS = {
    "delete", "create", "apply", "edit", "patch", "replace", "scale",
    "rollout", "drain", "cordon", "uncordon", "label", "annotate",
    "set", "exec", "cp", "attach", "taint",
    "topic-create", "topic-delete", "topic-update",
    "group-delete", "group-reset",
    "--force", "-f",  # block file-apply and force flags
}

# Subcommands we explicitly allow per binary (extra layer beyond DENY_TOKENS).
ALLOWED_SUBCOMMANDS = {
    "kubectl": {"get", "describe", "logs", "top", "events", "version", "config", "port-forward"},
    "ibmcloud": {"es", "target", "login", "plugin"},
    "kafka-consumer-groups.sh": {"--describe", "--list", "--bootstrap-server"},
    "kafka-topics.sh": {"--describe", "--list", "--bootstrap-server"},
}


@dataclass
class CommandResult:
    ok: bool
    stdout: str
    stderr: str
    refused_reason: str | None = None

    def as_observation(self) -> str:
        if self.refused_reason:
            return f"[REFUSED] {self.refused_reason}"
        out = self.stdout.strip()
        err = self.stderr.strip()
        if err and not out:
            return f"[stderr] {err}"
        if err:
            return f"{out}\n[stderr] {err}"
        return out or "[no output]"


# Shell metacharacters that signal an attempt to chain/redirect commands.
# We run with shell=False so these are already inert, but we reject them
# explicitly as defense-in-depth and to keep refusals legible.
SHELL_METACHARS = (";", "&&", "||", "|", ">", "<", "`", "$(", "&", "\n")


def _violation(cmd_tokens: list[str]) -> str | None:
    """Return a refusal reason if the command is unsafe, else None."""
    if not cmd_tokens:
        return "empty command"

    # Reject any token containing a shell metacharacter outright.
    for tok in cmd_tokens:
        for meta in SHELL_METACHARS:
            if meta in tok:
                return f"shell metacharacter '{meta}' is not allowed"

    binary = cmd_tokens[0].split("/")[-1]  # strip any path
    if binary not in ALLOWED_BINARIES:
        return f"binary '{binary}' is not in the read-only allowlist"

    lowered = [t.lower() for t in cmd_tokens]

    # Deny mutating tokens anywhere.
    for tok in lowered[1:]:
        if tok in DENY_TOKENS:
            return f"token '{tok}' is a mutating/forbidden operation"

    # Require an allowed subcommand to appear.
    allowed = ALLOWED_SUBCOMMANDS.get(binary, set())
    if allowed and not any(tok in allowed for tok in lowered[1:]):
        return (
            f"no recognized read-only subcommand for '{binary}'. "
            f"allowed: {sorted(allowed)}"
        )

    return None


def run_readonly(command: str, timeout: int = 60) -> CommandResult:
    """
    Parse and run a command string, enforcing the read-only policy.

    command is split with shlex (no shell), so pipes, redirects, ;, && etc.
    are treated as literal arguments and will simply fail the allowlist —
    they cannot chain a second command.
    """
    try:
        tokens = shlex.split(command)
    except ValueError as e:
        return CommandResult(False, "", "", f"could not parse command: {e}")

    reason = _violation(tokens)
    if reason:
        return CommandResult(False, "", "", reason)

    try:
        proc = subprocess.run(
            tokens,
            capture_output=True,
            text=True,
            timeout=timeout,
            shell=False,  # critical: never use a shell
        )
        return CommandResult(
            ok=(proc.returncode == 0),
            stdout=proc.stdout,
            stderr=proc.stderr,
        )
    except subprocess.TimeoutExpired:
        return CommandResult(False, "", "", f"command timed out after {timeout}s")
    except FileNotFoundError:
        return CommandResult(False, "", "", f"binary not found: {tokens[0]}")
