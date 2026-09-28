import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from autoscribe.ingest import IngestError, ingest_committed_sources


class IngestTests(unittest.TestCase):
    def git(self, repo, *args):
        return subprocess.run(["git", "-C", str(repo), *args], check=True, stdout=subprocess.PIPE, text=True).stdout.strip()

    def make_pair(self, td):
        root = Path(td)
        client = root / "client"
        bare = root / "server.git"
        client.mkdir()
        self.git(client, "init", "-q", "-b", "master")
        self.git(client, "config", "user.name", "Test")
        self.git(client, "config", "user.email", "test@example.invalid")
        (client / "README.md").write_text("repo metadata\n")
        (client / "Note.md").write_text("---\nslug: psg.test\n---\nBody one\n")
        self.git(client, "add", ".")
        self.git(client, "commit", "-q", "-m", "initial")
        subprocess.run(["git", "clone", "--bare", "-q", str(client), str(bare)], check=True)
        self.git(client, "remote", "add", "server", str(bare))
        return client, bare

    def test_reads_eligible_file_from_bare_commit(self):
        with tempfile.TemporaryDirectory() as td:
            client, bare = self.make_pair(td)
            (client / "README.md").write_text("changed metadata\n")
            (client / "Note.md").write_text("---\nslug: psg.test\n---\nBody two\n")
            self.git(client, "add", ".")
            self.git(client, "commit", "-q", "-m", "dispatch")
            commit = self.git(client, "rev-parse", "HEAD")
            self.git(client, "push", "-q", "server", "master")
            out = ingest_committed_sources(bare, commit)
            self.assertEqual(len(out), 1)
            self.assertEqual(out[0]["identity"], "psg.test")
            self.assertEqual(out[0]["content"], "Body two\n")

    def test_does_not_require_server_worktree(self):
        with tempfile.TemporaryDirectory() as td:
            client, bare = self.make_pair(td)
            (client / "Note.md").write_text("---\nslug: psg.test\n---\nCommitted only\n")
            self.git(client, "add", "Note.md")
            self.git(client, "commit", "-q", "-m", "dispatch")
            commit = self.git(client, "rev-parse", "HEAD")
            self.git(client, "push", "-q", "server", "master")
            shutil.rmtree(client)
            out = ingest_committed_sources(bare, commit)
            self.assertEqual(out[0]["content"], "Committed only\n")

    def test_metadata_only_commit_is_not_eligible(self):
        with tempfile.TemporaryDirectory() as td:
            client, bare = self.make_pair(td)
            (client / "README.md").write_text("metadata only change\n")
            self.git(client, "add", "README.md")
            self.git(client, "commit", "-q", "-m", "dispatch")
            commit = self.git(client, "rev-parse", "HEAD")
            self.git(client, "push", "-q", "server", "master")
            with self.assertRaisesRegex(IngestError, "no eligible"):
                ingest_committed_sources(bare, commit)

    def test_rejects_merge_dispatch_commit(self):
        with tempfile.TemporaryDirectory() as td:
            client, bare = self.make_pair(td)
            self.git(client, "checkout", "-q", "-b", "side")
            (client / "Side.md").write_text("---\nslug: psg.side\n---\nSide\n")
            self.git(client, "add", "Side.md")
            self.git(client, "commit", "-q", "-m", "side")
            self.git(client, "checkout", "-q", "master")
            (client / "Master.md").write_text("---\nslug: psg.master\n---\nMaster\n")
            self.git(client, "add", "Master.md")
            self.git(client, "commit", "-q", "-m", "master")
            self.git(client, "merge", "--no-ff", "-q", "side", "-m", "merge dispatch")
            commit = self.git(client, "rev-parse", "HEAD")
            self.git(client, "push", "-q", "server", "master")
            with self.assertRaisesRegex(IngestError, "merge dispatch commits"):
                ingest_committed_sources(bare, commit)


if __name__ == "__main__":
    unittest.main()
