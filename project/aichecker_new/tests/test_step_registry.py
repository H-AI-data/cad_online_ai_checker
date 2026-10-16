"""Real-file tests; never call the historical scan or write the production registry."""

from collections import Counter, defaultdict
import copy
import fcntl
import hashlib
import importlib.util
import inspect
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).resolve().parents[3] / "step_sha256_dedup.py"


def load_api():
    spec = importlib.util.spec_from_file_location("step_registry_under_test", SCRIPT)
    api = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(api)
    return api


def record(content=b"historical", **extra):
    return dict(dataset="history", task="1", path="/old/reference.step",
                bytes=len(content), sha256=hashlib.sha256(content).hexdigest(), **extra)


def report(files=None):
    files = [record(extension={"keep": True})] if files is None else files
    groups = defaultdict(list)
    tasks = defaultdict(set)
    for item in files:
        groups[item["sha256"]].append(item)
        tasks[item["dataset"]].add(item["task"])
    duplicates = sorted([dict(sha256=sha, count=len(items), files=items)
                         for sha, items in groups.items() if len(items) > 1],
                        key=lambda group: (-group["count"], group["sha256"]))
    return dict(schema_version=1, algorithm="SHA-256", status="complete", errors=[],
                generated_at="2026-10-13T21:28:44+08:00", scope="历史标准库",
                duplicate_definition="SHA-256 原始字节相同",
                source_roots=[dict(dataset="history", path="/old", extra="keep")],
                extension={"preserve": [1, 2]}, files=files, duplicate_groups=duplicates,
                summary=dict(task_counts={k: len(v) for k, v in tasks.items()},
                             file_counts=dict(Counter(f["dataset"] for f in files)),
                             files_scanned=len(files), unique_sha256_count=len(groups),
                             duplicate_group_count=len(duplicates),
                             files_in_duplicate_groups=sum(g["count"] for g in duplicates),
                             redundant_file_count=sum(g["count"]-1 for g in duplicates),
                             error_count=0, extension="preserve"))


class StepRegistryTests(unittest.TestCase):
    def setUp(self):
        self.api = load_api()
        self.assertTrue(callable(getattr(self.api, "check_and_register", None)),
                        "事务接口 check_and_register 尚未实现")
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.step = self.root / "part.step"
        self.step.write_bytes(b"ISO-10303-21; unique candidate; END-ISO-10303-21;")
        self.registry = self.root / "registry.json"
        self.original = report()
        self.write_report(self.original)

    def write_report(self, data):
        self.registry.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")

    def register(self, path=None, **kwargs):
        path = self.step if path is None else path
        args = dict(module="1", run_id="run-001",
                    expected_sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        args.update(kwargs)
        if "verify_inputs" in kwargs:
            self.assertIn("verify_inputs", inspect.signature(self.api.check_and_register).parameters,
                          "事务接口尚不支持锁内输入快照核验")
        return self.api.check_and_register(path, self.registry, **args)

    def test_input_snapshot_callback_runs_twice_under_the_registry_lock(self):
        calls = []
        def verify_inputs():
            lock = Path(str(self.registry) + ".lock")
            with lock.open("a+b") as other:
                with self.assertRaises(BlockingIOError):
                    fcntl.flock(other.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            calls.append(len(json.loads(self.registry.read_text())["files"]))
        result = self.register(verify_inputs=verify_inputs)
        self.assertEqual(result["status"], "passed")
        self.assertEqual(calls, [1, 1])
        self.assertEqual(len(json.loads(self.registry.read_text())["files"]), 2)

    def test_input_snapshot_callback_failure_at_either_stage_preserves_registry(self):
        original = self.registry.read_bytes()
        for failure_at in (1, 2):
            calls = 0
            def verify_inputs():
                nonlocal calls
                calls += 1
                if calls == failure_at:
                    raise RuntimeError("图纸输入已变化")
            with self.subTest(failure_at=failure_at):
                result = self.register(verify_inputs=verify_inputs)
                self.assertEqual(result["status"], "error")
                self.assertEqual(result["evidence"]["action"], "none")
                self.assertIn("图纸输入已变化", result["reason"])
                self.assertEqual(calls, failure_at)
                self.assertEqual(self.registry.read_bytes(), original)
                self.assertFalse(list(self.root.glob("*.tmp")))

    def test_non_callable_input_snapshot_validator_returns_error(self):
        original = self.registry.read_bytes()
        result = self.register(verify_inputs="not callable")
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["evidence"]["action"], "none")
        self.assertEqual(self.registry.read_bytes(), original)

    def test_input_snapshot_keyboard_interrupt_propagates_at_either_stage(self):
        original = self.registry.read_bytes()
        for failure_at in (1, 2):
            calls = 0
            def verify_inputs():
                nonlocal calls
                calls += 1
                if calls == failure_at:
                    raise KeyboardInterrupt
            with self.subTest(failure_at=failure_at), self.assertRaises(KeyboardInterrupt):
                self.register(verify_inputs=verify_inputs)
            self.assertEqual(self.registry.read_bytes(), original)
            self.assertFalse(list(self.root.glob("*.tmp")))

    def test_hash_streams_real_bytes(self):
        content = bytes(range(256)) * 9000
        self.step.write_bytes(content)
        self.assertEqual(self.api.hash_step(self.step),
                         (hashlib.sha256(content).hexdigest(), len(content)))

    def test_new_registration_preserves_metadata_and_backs_up_exact_bytes(self):
        original_bytes = self.registry.read_bytes()
        result = self.register()
        self.assertEqual((result["status"], result["reason"]), ("passed", "通过"))
        self.assertEqual(set(result["evidence"]),
                         {"action", "sha256", "path", "registry", "matches", "backup"})
        evidence = result["evidence"]
        self.assertEqual(evidence["action"], "registered")
        self.assertEqual(evidence["matches"], [])
        self.assertEqual(Path(evidence["backup"]).read_bytes(), original_bytes)
        updated = json.loads(self.registry.read_text())
        for key in ("generated_at", "scope", "source_roots", "extension", "duplicate_definition"):
            self.assertEqual(updated[key], self.original[key])
        self.assertEqual(updated["files"][:-1], self.original["files"])
        added = updated["files"][-1]
        self.assertEqual(added["dataset"], "aichecker_new")
        self.assertEqual(added["task"], "run-001")
        self.assertEqual(added["module"], "1")
        self.assertTrue(added["registered_at"])
        self.assertEqual(added["sha256"], evidence["sha256"])
        self.assertEqual(updated["summary"]["files_scanned"], 2)
        self.assertEqual(updated["summary"]["extension"], "preserve")
        self.assertEqual(updated["summary"]["task_counts"]["aichecker_new"], 1)
        self.assertTrue(Path(str(self.registry) + ".lock").is_file())
        self.assertIn("aichecker_new", updated.get("registration_scope", ""))
        self.assertIn("完整模块全维度验收由调用方负责", updated.get("registration_scope", ""))
        self.assertIn("本事务不验证 CAD", updated.get("registration_scope", ""))

    def test_duplicate_and_renamed_cross_module_preserve_registry_bytes(self):
        first = self.register()
        original = self.registry.read_bytes()
        renamed = self.root / "renamed.STP"
        renamed.write_bytes(self.step.read_bytes())
        result = self.register(renamed, module="3", run_id="run-002")
        self.assertEqual(result["status"], "failed")
        self.assertIn("重复", result["reason"])
        self.assertEqual(result["evidence"]["action"], "duplicate")
        self.assertIsNone(result["evidence"]["backup"])
        self.assertEqual(result["evidence"]["matches"], json.loads(original)["files"][-1:])
        self.assertEqual(self.registry.read_bytes(), original)
        self.assertEqual(len(list(Path(first["evidence"]["backup"]).parent.iterdir())), 1)

    def test_historical_duplicates_return_all_original_records_without_backup(self):
        self.step.write_bytes(b"historical")
        data = report([record(extension="first"), {**record(), "task": "2"}])
        self.write_report(data)
        original = self.registry.read_bytes()
        result = self.register()
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["evidence"]["matches"], data["files"])
        self.assertEqual(self.registry.read_bytes(), original)
        self.assertFalse(Path(str(self.registry) + ".backups").exists())

    def test_missing_or_malformed_registry_is_never_created_or_replaced(self):
        self.registry.unlink()
        self.assertEqual(self.register()["status"], "error")
        self.assertFalse(self.registry.exists())
        for raw in (b"{", b"[]", b'{"schema_version":1}', b'{"a":1,"a":2}'):
            self.registry.write_bytes(raw)
            self.assertEqual(self.register()["status"], "error")
            self.assertEqual(self.registry.read_bytes(), raw)

    def test_invalid_schema_records_statistics_and_duplicate_groups_fail_closed(self):
        variants = []
        for field, value in (("schema_version", 2), ("schema_version", True),
                             ("algorithm", "sha256"), ("status", "partial"),
                             ("errors", ["failure"]), ("source_roots", None)):
            data = copy.deepcopy(self.original); data[field] = value; variants.append(data)
        for field, value in (("sha256", "a"*63), ("sha256", "A"*64),
                             ("bytes", -1), ("bytes", True), ("path", ""),
                             ("task", 1), ("dataset", None)):
            data = copy.deepcopy(self.original); data["files"][0][field] = value; variants.append(data)
        for field in self.original["summary"]:
            if field == "extension":
                continue
            data = copy.deepcopy(self.original); data["summary"][field] = {}; variants.append(data)
        data = copy.deepcopy(self.original); data["duplicate_groups"] = [dict(sha256="0"*64)]; variants.append(data)
        for data in variants:
            with self.subTest(data=data):
                self.write_report(data)
                original = self.registry.read_bytes()
                self.assertEqual(self.register()["status"], "error")
                self.assertEqual(self.registry.read_bytes(), original)

    def test_expected_hash_module_and_run_id_are_required_and_validated(self):
        original = self.registry.read_bytes()
        for kwargs in (dict(expected_sha256="0"*64), dict(expected_sha256="A"*64),
                       dict(expected_sha256=""), dict(expected_sha256=None),
                       dict(module="4"), dict(module=1), dict(run_id="")):
            with self.subTest(kwargs=kwargs):
                self.assertEqual(self.register(**kwargs)["status"], "error")
                self.assertEqual(self.registry.read_bytes(), original)

    def test_step_registry_and_lock_symlinks_are_rejected(self):
        original = self.registry.read_bytes()
        link = self.root / "alias.step"; link.symlink_to(self.step)
        self.assertEqual(self.register(link)["status"], "error")
        real_registry = self.root / "real.json"
        self.registry.rename(real_registry); self.registry.symlink_to(real_registry)
        self.assertEqual(self.register()["status"], "error")
        self.registry.unlink(); real_registry.rename(self.registry)
        lock = Path(str(self.registry) + ".lock")
        if lock.exists():
            lock.unlink()
        victim = self.root / "untouched"; victim.write_bytes(b"keep")
        lock.symlink_to(victim)
        self.assertEqual(self.register()["status"], "error")
        self.assertEqual(victim.read_bytes(), b"keep")
        self.assertEqual(self.registry.read_bytes(), original)

    def test_non_step_directory_and_fifo_are_rejected_without_blocking(self):
        import os
        nonstep = self.root / "part.txt"; nonstep.write_bytes(b"data")
        for path in (nonstep, self.root / "missing.step", self.root):
            result = self.api.check_and_register(path, self.registry, module="1", run_id="run",
                                                 expected_sha256="0"*64)
            self.assertEqual(result["status"], "error")
        fifo = self.root / "pipe.step"; os.mkfifo(fifo)
        start = time.monotonic()
        result = self.api.check_and_register(fifo, self.registry, module="1", run_id="run",
                                             expected_sha256="0"*64)
        self.assertEqual(result["status"], "error")
        self.assertLess(time.monotonic()-start, 1)

    def test_lock_timeout_is_controlled_and_lock_inode_survives(self):
        lock = Path(str(self.registry) + ".lock")
        with lock.open("a+b") as stream:
            inode = lock.stat().st_ino
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            start = time.monotonic()
            result = self.register(lock_timeout=0.08)
            self.assertEqual(result["status"], "error")
            self.assertIn("锁", result["reason"])
            self.assertLess(time.monotonic()-start, 1)
        self.assertEqual(lock.stat().st_ino, inode)

    def test_replace_failure_preserves_original_bytes_and_backup(self):
        original = self.registry.read_bytes()
        with patch.object(self.api.os, "replace", side_effect=OSError("injected write failure")):
            result = self.register()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["evidence"]["action"], "none")
        self.assertEqual(self.registry.read_bytes(), original)
        self.assertEqual(Path(result["evidence"]["backup"]).read_bytes(), original)
        self.assertFalse(list(self.root.glob("*.tmp")))

    def test_backup_and_temporary_flush_failures_do_not_replace_registry(self):
        original = self.registry.read_bytes()
        real_fsync = self.api.os.fsync
        for failure_at in (1, 3):
            calls = 0
            def fail_fsync(fd):
                nonlocal calls
                calls += 1
                if calls == failure_at:
                    raise OSError("injected fsync failure")
                return real_fsync(fd)
            with self.subTest(failure_at=failure_at), patch.object(self.api.os, "fsync", side_effect=fail_fsync):
                result = self.register()
            self.assertEqual(result["status"], "error")
            self.assertEqual(result["evidence"]["action"], "none")
            self.assertEqual(self.registry.read_bytes(), original)
            self.assertFalse(list(self.root.glob("*.tmp")))

    def test_post_commit_failure_reports_written_state_truthfully(self):
        real_sync = self.api._sync_directory
        def fail_registry_directory(directory):
            if directory == self.registry.parent:
                raise OSError("injected final directory fsync failure")
            return real_sync(directory)
        with patch.object(self.api, "_sync_directory", side_effect=fail_registry_directory):
            result = self.register()
        self.assertEqual(result["status"], "error")
        self.assertEqual(result["evidence"]["action"], "registered")
        self.assertIn("已写入", result["reason"])
        self.assertEqual(len(json.loads(self.registry.read_text())["files"]), 2)

    def test_target_changed_during_hash_does_not_register(self):
        original = self.registry.read_bytes()
        real_hash = self.api.hash_step
        def edit_during_hash(path):
            result = real_hash(path)
            path.write_bytes(b"changed after approval")
            return result
        with patch.object(self.api, "hash_step", side_effect=edit_during_hash):
            result = self.register()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.registry.read_bytes(), original)

    def test_historical_groups_and_their_extensions_survive_new_registration(self):
        data = report([record(), {**record(), "task": "2"}])
        data["duplicate_groups"][0]["extension"] = {"keep": True}
        self.write_report(data)
        self.assertEqual(self.register()["status"], "passed")
        updated = json.loads(self.registry.read_text())
        self.assertEqual(updated["files"][:-1], data["files"])
        self.assertEqual(updated["duplicate_groups"], data["duplicate_groups"])
        self.assertEqual(updated["summary"]["unique_sha256_count"], 2)
        self.assertEqual(updated["summary"]["redundant_file_count"], 1)

    @unittest.skipUnless(SCRIPT.with_name("step查重.json").is_file(), "正式库只读样本不可用")
    def test_production_registry_readonly_copy_keeps_every_historical_record(self):
        source = SCRIPT.with_name("step查重.json")
        source_raw = source.read_bytes()
        original = json.loads(source_raw)
        self.registry.write_bytes(source_raw)
        self.assertEqual(self.register()["status"], "passed")
        updated = json.loads(self.registry.read_text())
        self.assertEqual(updated["files"][:-1], original["files"])
        self.assertEqual(updated["summary"]["files_scanned"], len(original["files"])+1)
        self.assertEqual(updated["summary"]["unique_sha256_count"], original["summary"]["unique_sha256_count"]+1)
        self.assertEqual(updated["duplicate_groups"], original["duplicate_groups"])
        self.assertEqual(source.read_bytes(), source_raw)

    def test_external_registry_edit_is_not_overwritten(self):
        real_hash = self.api.hash_step
        changed = self.registry.read_bytes() + b" "
        def edit_during_hash(path):
            value = real_hash(path)
            self.registry.write_bytes(changed)
            return value
        with patch.object(self.api, "hash_step", side_effect=edit_during_hash):
            result = self.register()
        self.assertEqual(result["status"], "error")
        self.assertEqual(self.registry.read_bytes(), changed)

    def test_keyboard_interrupt_propagates_and_does_not_write(self):
        original = self.registry.read_bytes()
        with patch.object(self.api, "hash_step", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.register()
        self.assertEqual(self.registry.read_bytes(), original)

    def cli_args(self, path=None):
        path = self.step if path is None else path
        return [sys.executable, str(SCRIPT), "--check-and-register", str(path),
                "--registry", str(self.registry), "--module", "2", "--run-id", "cli-run",
                "--expected-sha256", hashlib.sha256(path.read_bytes()).hexdigest()]

    def test_cli_json_and_exit_codes(self):
        for expected in (0, 1):
            result = subprocess.run(self.cli_args(), capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, expected, result.stderr)
            self.assertEqual(json.loads(result.stdout)["status"], "passed" if expected == 0 else "failed")
        self.registry.write_bytes(b"broken")
        result = subprocess.run(self.cli_args(), capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_incomplete_cli_never_falls_through_to_historical_scan(self):
        result = subprocess.run([sys.executable, str(SCRIPT), "--check-and-register", str(self.step)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertEqual(json.loads(result.stdout)["status"], "error")

    def test_no_argument_dispatch_preserves_legacy_entry_without_running_scan(self):
        with patch.object(self.api, "main", return_value=None) as legacy:
            self.assertIsNone(self.api.cli([]))
            legacy.assert_called_once_with()

    def test_concurrent_same_candidate_registers_exactly_once(self):
        processes = [subprocess.Popen(self.cli_args(), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for _ in range(2)]
        answers = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertIn(process.returncode, (0, 1), stderr)
            answers.append(json.loads(stdout)["status"])
        self.assertCountEqual(answers, ["passed", "failed"])
        self.assertEqual(len(json.loads(self.registry.read_text())["files"]), 2)

    def test_concurrent_distinct_candidates_both_survive_with_unique_backups(self):
        second = self.root / "second.stp"; second.write_bytes(b"second unique STEP")
        processes = [subprocess.Popen(self.cli_args(path), stdout=subprocess.PIPE,
                                      stderr=subprocess.PIPE, text=True) for path in (self.step, second)]
        backups = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=15)
            self.assertEqual(process.returncode, 0, stderr)
            backups.append(json.loads(stdout)["evidence"]["backup"])
        self.assertEqual(len(set(backups)), 2)
        data = json.loads(self.registry.read_text())
        self.assertEqual(len(data["files"]), 3)
        self.assertEqual(data["summary"]["files_scanned"], 3)
        self.assertEqual(data["summary"]["task_counts"]["aichecker_new"], 1)


if __name__ == "__main__":
    unittest.main()
