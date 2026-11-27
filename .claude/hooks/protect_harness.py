#!/usr/bin/env python3
"""PreToolUse hook (Bash): refuse shell commands that write to protected paths.

The Edit/Write/NotebookEdit tools are blocked on these paths by permissions.deny in
.claude/settings.json. This hook closes the obvious shell route: a command that names
a protected path and also contains a write operation is denied. It is a heuristic
(a program could still write a file it doesn't name), so the authoritative
protection is CODEOWNERS review of every change to these paths.
"""
import json
import re
import sys

PROTECTED = [
    r"(?<![\w.-])harness/(?!-)",      # harness/ (not harness-out/)
    r"\.tools/",
    r"docs/rvfi\.md",
    r"amarino/rvfi\.py",
    r"tools/setup_harness_tools\.sh",
    r"\.github/",
    r"\.claude/",
    r"CLAUDE\.md",
]
WRITES = [
    r">", r"\btee\b", r"\bsed\b[^|;&]*\s-i", r"\bperl\b[^|;&]*\s-i", r"\bmv\b", r"\bcp\b", r"\brm\b",
    r"\brmdir\b", r"\bln\b", r"\btouch\b", r"\bchmod\b", r"\btruncate\b", r"\bdd\b", r"\binstall\b",
    r"\bpatch\b", r"\bgit\s+(apply|am|checkout|restore|reset|stash|mv|rm|revert|cherry-pick|merge|rebase|pull)\b",
    r"\bopen\(", r"\.write", r"write_text", r"unlink", r"\brsync\b", r"\bunzip\b", r"\btar\b",
]


def main():
    data = json.load(sys.stdin)
    cmd = data.get("tool_input", {}).get("command", "")
    hits = [p for p in PROTECTED if re.search(p, cmd)]
    if hits and any(re.search(w, cmd) for w in WRITES):
        json.dump({"hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": (
                "This command looks like it writes to a protected harness/spec path. The verification "
                "harness, its spec (expected failures, platform), the RVFI port and the CI/agent settings "
                "are changed only by humans, through a reviewed PR (see CLAUDE.md). If you believe a "
                "harness change is needed, stop and explain it to the user instead."),
        }}, sys.stdout)
    return 0


if __name__ == "__main__":
    sys.exit(main())
