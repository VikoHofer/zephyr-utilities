"""Publication fidelity, refusal and recovery tests with disposable overlay files."""

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import uuid

from zephyr_utilities.features.devicetree import publication
from zephyr_utilities.features.devicetree.publication import OverlayPublisher


class OverlayPublicationTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.workspace = self.root / "workspace"
        self.application = self.workspace / "app"
        self.application.mkdir(parents=True)
        self.path = self.application / "app.overlay"
        self.base = '/* Temperature °C, letter 𐐀 */\r\n&uart0 { status = "disabled"; };\r\n'.encode()
        self.candidate = self.base.replace(b'"disabled"', b'"okay"')
        self.path.write_bytes(self.base)
        self.path.chmod(0o640)
        self.publisher = OverlayPublisher(self.root / "data" / "editor")
        self.observation = self.publisher.observe(self.workspace, self.path)
        self.operation = uuid.uuid4().hex

    def publish(self, callback=lambda: None, **kwargs):
        return self.publisher.publish(self.workspace, self.path, self.observation,
                                      kwargs.get("candidate", self.candidate),
                                      kwargs.get("operation_id", self.operation), callback)

    def journal(self):
        return self.publisher.directory / (self.operation + ".json")

    def test_save_preserves_exact_bytes_permissions_and_original_with_idempotent_receipt(self):
        result = self.publish()
        self.assertEqual(result["status"], "saved")
        self.assertEqual(self.path.read_bytes(), self.candidate)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o640)
        self.assertNotEqual(self.path.stat().st_ino, self.observation["identity"]["inode"])
        original = Path(result["recovery"]["staged_path"])
        self.assertEqual(original.read_bytes(), self.base)
        self.assertEqual(original.stat().st_ino, self.observation["identity"]["inode"])
        record = json.loads(self.journal().read_text())
        self.assertEqual(record["original"]["text"].encode(), self.base)
        self.assertEqual(record["candidate_text"].encode(), self.candidate)
        self.path.write_bytes(b"/* later external edit */\n")
        self.assertEqual(self.publish(), result, "retry returns a historical receipt without replay")
        self.assertEqual(self.path.read_bytes(), b"/* later external edit */\n")
        self.assertEqual(self.publisher.receipts(self.workspace)[0]["status"], "saved")

    def test_noop_keeps_file_identity_and_wrong_reuse_is_refused(self):
        result = self.publish(candidate=self.base)
        self.assertEqual(result["status"], "unchanged")
        self.assertEqual(self.path.stat().st_ino, self.observation["identity"]["inode"])
        self.assertFalse(self.publisher.directory.exists(), "a no-op does not consume publication storage")
        with self.assertRaisesRegex(ValueError, "different change"):
            self.publish()

    def test_changed_bytes_and_same_bytes_replacement_both_refuse_before_writing(self):
        self.path.write_bytes(b"external")
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.publish()
        replacement = self.application / "replacement"
        replacement.write_bytes(self.base)
        replacement.replace(self.path)
        with self.assertRaisesRegex(ValueError, "changed since review"):
            self.publish()
        self.assertEqual(self.path.read_bytes(), self.base)
        self.assertFalse(self.journal().exists())

    def test_missing_nonregular_symlink_and_hardlink_are_refused(self):
        self.path.unlink()
        with self.assertRaises(FileNotFoundError):
            self.publish()
        target = self.root / "outside.overlay"
        target.write_bytes(self.base)
        self.path.symlink_to(target)
        with self.assertRaises(OSError):
            self.publisher.observe(self.workspace, self.path)
        self.path.unlink()
        os.link(target, self.path)
        with self.assertRaisesRegex(ValueError, "singly linked"):
            self.publisher.observe(self.workspace, self.path)
        self.path.unlink()
        os.mkfifo(self.path)
        with self.assertRaisesRegex(ValueError, "regular"):
            self.publisher.observe(self.workspace, self.path)
        self.assertEqual(target.read_bytes(), self.base)

    def test_read_only_and_unsupported_platform_refuse_without_mutating(self):
        self.path.chmod(0o440)
        self.observation = self.publisher.observe(self.workspace, self.path)
        self.assertFalse(self.observation["writable"])
        with self.assertRaisesRegex(ValueError, "read only"):
            self.publish()
        self.path.chmod(0o640)
        self.observation = self.publisher.observe(self.workspace, self.path)
        with patch.object(publication.sys, "platform", "unsupported"):
            with self.assertRaisesRegex(ValueError, "Linux"):
                self.publish()
        self.assertEqual(self.path.read_bytes(), self.base)

    def test_extended_attributes_are_preserved(self):
        if not hasattr(os, "setxattr"):
            self.skipTest("Host has no extended file attributes")
        try:
            os.setxattr(self.path, "user.test", b"preserve")
        except OSError:
            self.skipTest("Filesystem has no user extended attributes")
        self.observation = self.publisher.observe(self.workspace, self.path)
        self.assertEqual(self.publish()["status"], "saved")
        self.assertEqual(os.getxattr(self.path, "user.test"), b"preserve")
        self.assertEqual(self.path.read_bytes(), self.candidate)

    def test_relocated_or_redirected_parent_refuses(self):
        moved = self.workspace / "moved"
        self.application.rename(moved)
        self.application.mkdir()
        self.path.write_bytes(self.base)
        with self.assertRaisesRegex(ValueError, "directory changed"):
            self.publish()
        self.path.unlink()
        self.application.rmdir()
        self.application.symlink_to(moved, target_is_directory=True)
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual((moved / "app.overlay").read_bytes(), self.base)

    def test_external_change_during_prepare_preserves_destination_and_draft_evidence(self):
        calls = 0

        def revalidate():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.path.write_bytes(b"external before exchange")

        result = self.publish(revalidate)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), b"external before exchange")
        self.assertEqual(Path(result["recovery"]["staged_path"]).read_bytes(), self.candidate)
        self.assertIn("No overlay replacement", result["message"])

    def test_parent_swap_during_prepare_never_writes_redirected_path(self):
        calls = 0
        moved = self.workspace / "moved"

        def revalidate():
            nonlocal calls
            calls += 1
            if calls == 2:
                self.application.rename(moved)
                self.application.mkdir()
                self.path.write_bytes(b"new location")

        result = self.publish(revalidate)
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), b"new location")
        self.assertEqual((moved / "app.overlay").read_bytes(), self.base)

    def test_exchange_race_retains_displaced_external_bytes_without_rollback(self):
        exchange = publication._exchange

        def race(parent, left, right):
            self.path.write_bytes(b"external at exchange")
            exchange(parent, left, right)

        with patch.object(publication, "_exchange", race):
            result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), self.candidate)
        self.assertEqual(Path(result["recovery"]["staged_path"]).read_bytes(), b"external at exchange")
        self.assertIn("may already be visible", result["message"])
        with patch.object(publication, "_exchange", side_effect=AssertionError("must not replay")):
            self.assertEqual(self.publish()["status"], "uncertain")

    def test_external_replacement_after_exchange_is_never_rolled_back(self):
        exchange = publication._exchange

        def race(parent, left, right):
            exchange(parent, left, right)
            self.path.write_bytes(b"newer external destination")

        with patch.object(publication, "_exchange", race):
            result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), b"newer external destination")
        self.assertEqual(Path(result["recovery"]["staged_path"]).read_bytes(), self.base)

    def test_held_original_descriptor_write_after_exchange_is_detected(self):
        exchange = publication._exchange
        with self.path.open("r+b") as held:
            def race(parent, left, right):
                exchange(parent, left, right)
                held.seek(0)
                held.write(b"external descriptor")
                held.flush()

            with patch.object(publication, "_exchange", race):
                result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), self.candidate)
        self.assertTrue(Path(result["recovery"]["staged_path"]).read_bytes().startswith(b"external descriptor"))

    def test_prepare_journal_failure_leaves_overlay_unchanged(self):
        with patch.object(self.publisher, "_store", side_effect=OSError("storage unavailable")):
            with self.assertRaisesRegex(OSError, "storage unavailable"):
                self.publish()
        self.assertEqual(self.path.read_bytes(), self.base)

    def test_final_journal_failure_is_uncertain_and_reconciles_without_replay(self):
        store = self.publisher._store

        def fail_final(record, *, create=False):
            if not create:
                raise OSError("storage unavailable")
            store(record, create=create)

        with patch.object(self.publisher, "_store", fail_final):
            result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), self.candidate)
        self.assertEqual(Path(result["recovery"]["staged_path"]).read_bytes(), self.base)
        self.assertEqual(self.publisher.receipts(self.workspace)[0]["status"], "uncertain")
        self.assertEqual(self.publish()["status"], "uncertain")

    def test_failed_exchange_leaves_original_with_recovery(self):
        with patch.object(publication, "_exchange", side_effect=OSError("no exchange support")):
            result = self.publish()
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), self.base)
        self.assertEqual(Path(result["recovery"]["staged_path"]).read_bytes(), self.candidate)

    def test_terminal_record_without_durable_acknowledgement_needs_comparison(self):
        store = self.publisher._store

        def fail_after_saved_record(record, *, create=False):
            if record["status"] == "uncertain":
                raise OSError("fallback storage unavailable")
            store(record, create=create)
            if record["status"] == "saved":
                raise OSError("final directory fsync failed")

        with patch.object(self.publisher, "_store", fail_after_saved_record):
            result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(json.loads(self.journal().read_text())["status"], "saved")
        self.assertEqual(self.publish()["status"], "uncertain")
        restarted = OverlayPublisher(self.publisher.directory.parent)
        self.assertEqual(restarted.receipts(self.workspace)[0]["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), self.candidate)

    def test_restart_exposes_evidence_without_automatically_replaying_a_save(self):
        self.assertEqual(self.publish()["status"], "saved")
        restarted = OverlayPublisher(self.publisher.directory.parent)
        with patch.object(publication, "_exchange", side_effect=AssertionError("must not replay")):
            result = restarted.publish(self.workspace, self.path, self.observation, self.candidate,
                                       self.operation, lambda: None)
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(self.path.read_bytes(), self.candidate)

    def test_attribute_change_at_exchange_cannot_report_success(self):
        exchange = publication._exchange

        def race(parent, left, right):
            os.setxattr(self.path, "user.interference", b"new attribute")
            exchange(parent, left, right)

        with patch.object(publication, "_exchange", race):
            result = self.publish()
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(os.getxattr(result["recovery"]["staged_path"], "user.interference"), b"new attribute")

    def test_stage_flush_failure_preserves_original(self):
        fsync = os.fsync

        def fail_stage(fd):
            info = os.fstat(fd)
            if info.st_size == len(self.candidate):
                raise OSError("stage flush failed")
            fsync(fd)

        with patch.object(publication.os, "fsync", fail_stage):
            result = self.publish()
        self.assertEqual(result["status"], "conflict")
        self.assertEqual(self.path.read_bytes(), self.base)

    def test_participating_writer_locks_use_parent_and_name_across_publishers(self):
        other = OverlayPublisher(self.root / "other-data")
        blocked = []

        def revalidate():
            try:
                other.publish(self.workspace, self.path, self.observation, b"other", uuid.uuid4().hex, lambda: None)
            except ValueError as exc:
                blocked.append(str(exc))

        self.assertEqual(self.publish(revalidate)["status"], "saved")
        self.assertEqual(len(blocked), 2)
        self.assertTrue(all("participating writer" in message for message in blocked))

    def test_unknown_record_version_is_preserved_and_not_replayed(self):
        self.publisher.directory.mkdir(parents=True)
        raw = json.dumps({"schema_version": 99, "id": self.operation}).encode()
        self.journal().write_bytes(raw)
        with self.assertRaisesRegex(ValueError, "unsupported version"):
            self.publish()
        self.assertEqual(self.journal().read_bytes(), raw)
        self.assertEqual(self.path.read_bytes(), self.base)

    def test_storage_symlinks_are_not_followed(self):
        outside = self.root / "outside-data"
        outside.mkdir()
        self.publisher.directory.parent.mkdir(parents=True)
        self.publisher.directory.symlink_to(outside, target_is_directory=True)
        with self.assertRaises(OSError):
            self.publish()
        self.assertEqual(list(outside.iterdir()), [])
        self.assertEqual(self.path.read_bytes(), self.base)

    def test_malformed_known_version_is_reported_and_preserved(self):
        self.publisher.directory.mkdir(parents=True)
        for status in (None, [], {"invalid": True}):
            with self.subTest(status=status):
                raw = json.dumps({"schema_version": 1, "id": self.operation, "status": status}).encode()
                self.journal().write_bytes(raw)
                self.assertEqual(self.publisher.receipts(self.workspace)[0]["status"], "unavailable")
                with self.assertRaisesRegex(ValueError, "malformed"):
                    self.publish()
                self.assertEqual(self.journal().read_bytes(), raw)

    def test_retention_limit_preserves_all_previous_evidence(self):
        self.publisher.directory.mkdir(parents=True)
        (self.publisher.directory / "keep").write_text("recovery")
        with patch.object(publication, "MAX_PUBLICATIONS", 1):
            with self.assertRaisesRegex(ValueError, "storage is full"):
                self.publish()
        self.assertEqual(self.path.read_bytes(), self.base)
        self.assertEqual((self.publisher.directory / "keep").read_text(), "recovery")


if __name__ == "__main__":
    unittest.main()
