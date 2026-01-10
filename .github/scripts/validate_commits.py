#!/usr/bin/env python3
"""
Validate that commit messages follow the Conventional Commits specification.

Conventional Commits format:
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]

Types: feat, fix, docs, style, refactor, perf, test, build, ci, chore, revert
"""

import re
import sys
from subprocess import check_output

# Allowed commit types
ALLOWED_TYPES = [
    "feat",
    "fix",
    "docs",
    "style",
    "refactor",
    "perf",
    "test",
    "build",
    "ci",
    "chore",
    "revert",
]

# Conventional commit pattern
# Format: <type>[optional scope]: <description>
CONVENTIONAL_COMMIT_PATTERN = re.compile(
    r"^(?P<type>" + "|".join(ALLOWED_TYPES) + r")"
    r"(?P<scope>\([^)]+\))?"
    r"(?P<breaking>!)?"
    r":\s+"
    r"(?P<description>.+)"
    r"$"
)

# Merge commit pattern (allow merge commits)
MERGE_COMMIT_PATTERN = re.compile(r"^Merge (branch|pull request|remote-tracking branch)")

# Revert commit pattern
REVERT_COMMIT_PATTERN = re.compile(r"^Revert \".+\"$")


def get_commit_messages(base_ref: str = "HEAD~1") -> list[str]:
    """Get commit messages between base_ref and HEAD."""
    try:
        # Get the range of commits
        commits = check_output(
            ["git", "log", "--format=%s", f"{base_ref}..HEAD"],
            text=True,
        ).strip()
    except Exception as e:
        print(f"Error getting commits: {e}")
        # If comparison fails, try to get just the last commit
        try:
            commits = check_output(
                ["git", "log", "-1", "--format=%s", "HEAD"],
                text=True,
            ).strip()
        except Exception as e2:
            print(f"Error getting last commit: {e2}")
            return []

    if not commits:
        return []

    return [msg for msg in commits.split("\n") if msg.strip()]


def validate_commit_message(message: str) -> tuple[bool, str]:
    """Validate a single commit message."""
    message = message.strip()

    # Allow merge commits
    if MERGE_COMMIT_PATTERN.match(message):
        return True, ""

    # Allow revert commits
    if REVERT_COMMIT_PATTERN.match(message):
        return True, ""

    # Check conventional commit format
    match = CONVENTIONAL_COMMIT_PATTERN.match(message)
    if not match:
        return (
            False,
            f"Commit message does not follow Conventional Commits format.\n"
            f"Expected: <type>[optional scope]: <description>\n"
            f"Got: {message}\n"
            f"Allowed types: {', '.join(ALLOWED_TYPES)}",
        )

    # Check description length (recommended: 50 chars, max 72)
    description = match.group("description")
    if len(description) > 72:
        return (
            False,
            f"Commit description is too long ({len(description)} chars). "
            f"Keep it under 72 characters.\n"
            f"Description: {description}",
        )

    return True, ""


def main():
    """Main validation function."""
    import os

    # Determine the base reference
    # For PRs, GitHub provides base_ref via environment variable
    # For pushes, we compare with the previous commit
    github_base_ref = os.environ.get("GITHUB_BASE_REF")
    github_ref = os.environ.get("GITHUB_REF", "refs/heads/main")

    if github_base_ref:
        # PR context - compare with base branch
        base_ref = github_base_ref
        # Try to fetch the base branch if it's not available locally
        try:
            check_output(["git", "show-ref", "--verify", f"refs/heads/{base_ref}"], text=True)
        except Exception:
            # If local branch doesn't exist, try remote
            try:
                check_output(["git", "show-ref", "--verify", f"refs/remotes/origin/{base_ref}"], text=True)
                base_ref = f"origin/{base_ref}"
            except Exception:
                # Fallback to HEAD~1 if base branch not found
                print(f"Warning: Base branch '{base_ref}' not found, using HEAD~1")
                base_ref = "HEAD~1"
    else:
        # Push to branch - compare with previous commit
        base_ref = "HEAD~1"

    commit_messages = get_commit_messages(base_ref)

    if not commit_messages:
        print("No commits to validate.")
        return 0

    print(f"Validating {len(commit_messages)} commit(s)...\n")

    errors = []
    for i, message in enumerate(commit_messages, 1):
        print(f"Commit {i}: {message[:50]}...")
        is_valid, error_msg = validate_commit_message(message)
        if not is_valid:
            errors.append(f"Commit {i}: {error_msg}")

    if errors:
        print("\n❌ Validation failed:\n")
        for error in errors:
            print(error)
            print()
        print(
            "Please ensure your commit messages follow the Conventional Commits format:\n"
            "<type>[optional scope]: <description>\n\n"
            "Examples:\n"
            "  feat: add new sensor for water consumption\n"
            "  fix(api): handle authentication errors\n"
            "  docs: update installation instructions\n"
            "  chore: update dependencies\n"
        )
        return 1

    print("\n✅ All commit messages are valid!")
    return 0


if __name__ == "__main__":
    sys.exit(main())
