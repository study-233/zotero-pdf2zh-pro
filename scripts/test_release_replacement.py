"""Verify same-version guards, immutable PyPI reuse, and recoverable publication."""
from contextlib import ExitStack
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

import release_replacement as replacement


class ReplacementTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="release-replacement-")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.git("init", "--quiet")
        self.git("config", "user.name", "Release Test")
        self.git("config", "user.email", "release@example.invalid")
        self.git("remote", "add", "origin", f"https://github.com/{replacement.REPO}.git")
        (self.root / "server").mkdir()
        (self.root / "server/pyproject.toml").write_text(
            '[project]\nname = "zotero-pdf2zh-pro"\nversion = "1.7.0"\n', encoding="utf-8")
        (self.root / "server/server.py").write_text("original server\n", encoding="utf-8")
        (self.root / "LICENSE").write_text("original license\n", encoding="utf-8")
        (self.root / "client.txt").write_text("original client\n", encoding="utf-8")
        self.old = self.commit()
        self.git("tag", "-a", "v1.7.0", "-m", "Original release", self.old)
        self.old_tag = self.git("rev-parse", "refs/tags/v1.7.0")
        (self.root / "client.txt").write_text("updated client\n", encoding="utf-8")
        self.new = self.commit()

    def git(self, *args):
        return replacement.run("git", *args, root=self.root)

    def commit(self):
        self.git("add", "--all")
        self.git("commit", "--quiet", "-m", "Test fixture")
        return self.git("rev-parse", "HEAD")

    def test_client_changes_are_allowed_but_server_or_license_changes_are_rejected(self):
        replacement.validate_source("1.7.0", self.old, self.new, self.root)
        for name in ["server/server.py", "LICENSE", "setup.cfg"]:
            path = self.root / name
            original = path.read_bytes() if path.exists() else None
            path.write_bytes(b"changed packaging input")
            # Include new packaging files in the index; clean-worktree gating is separate.
            self.git("add", name)
            with self.assertRaises(subprocess.CalledProcessError):
                replacement.validate_source("1.7.0", self.old, self.new, self.root)
            self.git("reset", "--quiet", "HEAD", "--", name)
            if original is None:
                path.unlink()
            else:
                path.write_bytes(original)

    def test_unrelated_old_commit_and_different_version_are_rejected(self):
        with self.assertRaises(ValueError):
            replacement.validate_source("1.7.1", self.old, self.new, self.root)
        self.git("checkout", "--quiet", self.old)
        (self.root / "other.txt").write_text("divergent branch", encoding="utf-8")
        divergent = self.commit()
        self.git("checkout", "--quiet", self.new)
        with self.assertRaises(subprocess.CalledProcessError):
            replacement.validate_source("1.7.0", divergent, self.new, self.root)

    def make_pypi(self):
        directory = self.root / "server/dist"
        directory.mkdir(exist_ok=True)
        files = {}
        for name in replacement.pypi_names("1.7.0"):
            (directory / name).write_bytes(name.encode())
            files[name] = {**replacement.digest(directory / name), "url": f"https://files.pythonhosted.org/{name}"}
        return files

    def test_wrong_download_hash_preserves_both_existing_pypi_files(self):
        files = self.make_pypi()
        before = {name: (self.root / "server/dist" / name).read_bytes() for name in files}

        def response(url, **_):
            name = url.rsplit("/", 1)[1]
            payload = name.encode() if name.endswith(".whl") else b"X" * len(name)
            result = io.BytesIO(payload)
            result.url = url
            return result

        with patch.object(replacement, "urlopen", side_effect=response):
            with self.assertRaisesRegex(ValueError, "content mismatch"):
                replacement.download_pypi(self.root / "server/dist", files)
        self.assertEqual(before, {name: (self.root / "server/dist" / name).read_bytes() for name in files})

    def test_extra_pypi_distribution_is_rejected(self):
        files = self.make_pypi()
        entries = [{"filename": name, "size": item["size"], "digests": {"sha256": item["sha256"]}, "url": item["url"]}
                   for name, item in files.items()]
        with patch.object(replacement, "urlopen", return_value=io.BytesIO(json.dumps({"urls": entries}).encode())):
            self.assertEqual(replacement.public_pypi("1.7.0"), files)
        entries.append({**entries[0], "filename": "another-build.whl"})
        with patch.object(replacement, "urlopen", return_value=io.BytesIO(json.dumps({"urls": entries}).encode())):
            with self.assertRaisesRegex(ValueError, "exactly the original"):
                replacement.public_pypi("1.7.0")

    def publication(self, fail_manifest=False, concurrent_tag=False, external_change=None):
        files = self.make_pypi()
        snapshot = {"previousCommit": self.old, "previousTagObject": self.old_tag,
                    "pypiSourceCommit": self.old, "clientCommit": self.new, "pypiArtifacts": files}
        directory = self.root / "dist/release"
        directory.mkdir(parents=True, exist_ok=True)
        replacement.write_json(self.root / "dist/replacement-source.json", snapshot)
        manifest = {"version": "1.7.0", "commit": self.new, "replacement": snapshot, "artifacts": {}}
        for name in replacement.ASSETS:
            (directory / name).write_bytes(("new " + name).encode())
            manifest["artifacts"][name] = replacement.digest(directory / name)
        replacement.write_json(directory / "checksums.json", manifest)
        notes = self.root / "notes.md"
        notes.write_text("new notes", encoding="utf-8")
        original = {name: ("old " + name).encode() for name in replacement.ASSETS}
        state = {"tag": self.old_tag, "assets": dict(original), "notes": "old notes"}
        events = []
        failed = False

        def info(*_):
            return {"id": 7, "draft": False, "prerelease": False, "body": state["notes"],
                    "assets": [{"id": i, "name": name, "size": len(state["assets"][name])}
                               for i, name in enumerate(replacement.ASSETS) if name in state["assets"]]}

        def download(_, target, *__, **___):
            target.mkdir(parents=True)
            for name, payload in state["assets"].items():
                (target / name).write_bytes(payload)
            if failed and external_change == "tag_during_ownership":
                state["tag"] = "f" * 40
            return {name: replacement.digest(target / name) for name in state["assets"]}

        def move(_, expected, new, *__):
            events.append(("tag", expected, new))
            if concurrent_tag and len(events) == 1:
                state["tag"] = "f" * 40
            if state["tag"] != expected:
                raise ValueError("CAS refused")
            state["tag"] = new

        def upload(_, paths, *__):
            nonlocal failed
            for path in paths:
                events.append(("upload", path.name))
                if fail_manifest and not failed and path.name == "update.json":
                    failed = True
                    state["assets"].pop(path.name)  # Model --clobber deleting before failed upload.
                    if external_change == "asset":
                        state["assets"][replacement.ASSETS[0]] = b"another operator's upload"
                    elif external_change == "notes":
                        state["notes"] = "another operator's notes"
                    raise RuntimeError("upload interrupted")
                state["assets"][path.name] = path.read_bytes()

        def set_notes(_, path, *__):
            state["notes"] = path.read_text(encoding="utf-8")

        real_run = replacement.run

        def local_run(*args, **kwargs):
            if args[:2] == ("git", "fetch"):
                return ""
            return real_run(*args, **kwargs)

        def remote(*_):
            commit = self.old if state["tag"] == self.old_tag else self.new
            return state["tag"], commit

        with ExitStack() as stack:
            for name, function in [("public_pypi", lambda _: files), ("remote_tag", remote),
                                   ("release_info", info), ("download_assets", download),
                                   ("move_tag", move), ("upload", upload), ("set_notes", set_notes),
                                   ("run", local_run)]:
                stack.enter_context(patch.object(replacement, name, side_effect=function))
            if fail_manifest or concurrent_tag:
                with self.assertRaises((RuntimeError, ValueError)):
                    replacement.publish("1.7.0", self.old, self.new, notes, self.root)
            else:
                replacement.publish("1.7.0", self.old, self.new, notes, self.root)
        reports = list((self.root / "dist/replacement-backups").glob("*/report.json"))
        self.assertEqual(len(reports), 1)
        return state, events, json.loads(reports[0].read_text(encoding="utf-8")), original

    def test_publication_backs_up_then_updates_payloads_before_manifests(self):
        state, events, report, _ = self.publication()
        self.assertEqual([event[1] for event in events if event[0] == "upload"], replacement.ASSETS)
        self.assertEqual(events[0], ("tag", self.old_tag, report["replacementTagObject"]))
        self.assertEqual(report["status"], "published")
        self.assertEqual(report["pypiSourceCommit"], self.old)
        self.assertEqual(report["clientCommit"], self.new)
        self.assertEqual(state["notes"], "new notes")

    def test_partial_upload_restores_all_original_assets_notes_and_exact_tag_object(self):
        state, events, report, original = self.publication(fail_manifest=True)
        self.assertEqual(state, {"tag": self.old_tag, "assets": original, "notes": "old notes"})
        self.assertEqual(report["status"], "restored")
        self.assertEqual(events[-1], ("tag", report["replacementTagObject"], self.old_tag))

    def test_concurrent_tag_change_refuses_upload_and_does_not_force_rollback(self):
        state, events, report, original = self.publication(concurrent_tag=True)
        self.assertEqual(state["assets"], original)
        self.assertEqual(state["tag"], "f" * 40)
        self.assertEqual(len(events), 1)
        self.assertEqual(report["status"], "restore_failed")

    def test_tag_moved_during_ownership_download_prevents_all_rollback_uploads(self):
        state, events, report, _ = self.publication(fail_manifest=True, external_change="tag_during_ownership")
        self.assertEqual(state["tag"], "f" * 40)
        self.assertEqual(len([event for event in events if event[0] == "upload"]), 3)
        self.assertEqual(len([event for event in events if event[0] == "tag"]), 1)
        self.assertEqual(report["status"], "restore_failed")

    def test_rollback_preserves_unknown_assets_or_notes_even_when_tag_is_unchanged(self):
        for change in ["asset", "notes"]:
            with self.subTest(change=change):
                # Each publication writes a separate backup; make assertions relative to this attempt.
                before = set((self.root / "dist/replacement-backups").glob("*/report.json"))
                state, events, report, _ = self.publication(fail_manifest=True, external_change=change)
                self.assertEqual(report["status"], "restore_failed")
                self.assertEqual(len([event for event in events if event[0] == "tag"]), 1)
                self.assertEqual(len([event for event in events if event[0] == "upload"]), 3)
                if change == "asset":
                    self.assertEqual(state["assets"][replacement.ASSETS[0]], b"another operator's upload")
                else:
                    self.assertEqual(state["notes"], "another operator's notes")
                # Separate fixture runs are unnecessary; remove only this test's own backup report from counting.
                for path in set((self.root / "dist/replacement-backups").glob("*/report.json")) - before:
                    path.rename(path.with_name("checked-report.json"))


if __name__ == "__main__":
    unittest.main()
