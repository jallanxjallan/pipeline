import subprocess
import tempfile
import unittest
from pathlib import Path

from autoscribe.responses import (
    ResponsesError,
    add_worktree,
    commit_response,
    current_blob,
    existing_call_commit,
    find_identity_path,
    remove_worktree,
    resolve_repo,
)


def git(repo: Path, *args: str) -> str:
    cp = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return cp.stdout


class ResponsesTests(unittest.TestCase):
    def make_repo(self, root: Path):
        repos = root / "Repos"
        repos.mkdir()
        bare = repos / "Book.git"
        subprocess.run(["git", "init", "--bare", "-q", str(bare)], check=True)
        client = root / "client"
        subprocess.run(["git", "clone", "-q", str(bare), str(client)], check=True)
        git(client, "config", "user.name", "Test")
        git(client, "config", "user.email", "test@example.invalid")
        git(client, "switch", "-q", "-c", "master")
        target = client / "Contents" / "Opening.md"
        target.parent.mkdir(parents=True)
        target.write_text("---\nslug: psg.opening\ntitle: Opening\n---\nOriginal body.\n", encoding="utf-8")
        git(client, "add", ".")
        git(client, "commit", "-q", "-m", "Seed")
        git(client, "push", "-q", "origin", "master")
        subprocess.run(["git", "-C", str(bare), "symbolic-ref", "HEAD", "refs/heads/master"], check=True)
        return repos, bare, client

    def test_finds_identity_after_path_move(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            repos, bare, client = self.make_repo(root)
            original_blob = current_blob(bare, "Contents/Opening.md")
            moved = client / "Drafts" / "Opening.md"
            moved.parent.mkdir()
            git(client, "mv", "Contents/Opening.md", "Drafts/Opening.md")
            git(client, "commit", "-q", "-m", "Move file")
            git(client, "push", "-q", "origin", "master")
            self.assertEqual(resolve_repo(repos, "Book.git"), bare.resolve())
            self.assertEqual(find_identity_path(bare, "psg.opening"), "Drafts/Opening.md")
            self.assertEqual(current_blob(bare, "Drafts/Opening.md"), original_blob)

    def test_one_response_becomes_one_commit_and_preserves_frontmatter(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repos, bare, _client = self.make_repo(root)
            before = int(git(bare, "rev-list", "--count", "master").strip())
            worktree = root / "response-worktree"
            add_worktree(bare, worktree)
            try:
                commit = commit_response(
                    worktree,
                    path="Contents/Opening.md",
                    content="Rewritten body.\n",
                    commit_message="Rewrite Opening",
                    call_id="01CALL",
                )
            finally:
                remove_worktree(bare, worktree)
            after = int(git(bare, "rev-list", "--count", "master").strip())
            self.assertEqual(after, before + 1)
            text = git(bare, "show", "master:Contents/Opening.md")
            self.assertEqual(text, "---\nslug: psg.opening\ntitle: Opening\n---\nRewritten body.\n")
            message = git(bare, "show", "-s", "--format=%B", commit)
            self.assertIn("Rewrite Opening", message)
            self.assertIn("AutoScribe-Call: 01CALL", message)
            self.assertEqual(existing_call_commit(bare, "01CALL"), commit)

    def test_duplicate_identity_is_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            _repos, bare, client = self.make_repo(root)
            duplicate = client / "Duplicate.md"
            duplicate.write_text("---\nslug: psg.opening\n---\nDuplicate.\n", encoding="utf-8")
            git(client, "add", "Duplicate.md")
            git(client, "commit", "-q", "-m", "Duplicate")
            git(client, "push", "-q", "origin", "master")
            with self.assertRaises(ResponsesError):
                find_identity_path(bare, "psg.opening")


if __name__ == "__main__":
    unittest.main()
