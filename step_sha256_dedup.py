#!/usr/bin/env python3
"""Inventory and byte-for-byte deduplicate the current grading reference STEPs."""

import argparse
import fcntl
import hashlib
import json
import math
import os
import re
import stat
import sys
import tempfile
import time
from collections import Counter, defaultdict
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo


DATASETS = [
    ("Ali_cad_250_Out", Path("/Users/yuxiang/Desktop/cad-fix/Ali_cad_250_Out/Ali_cad_250_final_delivery/dataset_clean_all250")),
    ("cad_28", Path("/Users/yuxiang/Desktop/cad-fix/cad_28/dataset")),
]
OUTPUT = Path("/Users/yuxiang/Desktop/step查重.json")


def _stamp(path):
    info = path.lstat()
    if not stat.S_ISREG(info.st_mode):
        raise ValueError(f"必须是普通文件，不能是符号链接：{path}")
    return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)


@contextmanager
def _regular_file(path, flags=os.O_RDONLY):
    """O_NOFOLLOW closes the lstat/open symlink race; NONBLOCK rejects FIFOs promptly."""
    fd = os.open(path, flags | os.O_NOFOLLOW | os.O_NONBLOCK | os.O_CLOEXEC, 0o600)
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            raise ValueError(f"必须是普通文件：{path}")
        with os.fdopen(fd, "rb", closefd=False) as stream:
            yield stream
    finally:
        os.close(fd)


def hash_step(path):
    """Return (SHA-256, bytes), using the historical 1 MiB streaming algorithm."""
    path = Path(path)
    before = _stamp(path)
    digest = hashlib.sha256()
    size = 0
    with _regular_file(path) as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != before[:2]:
            raise ValueError("计算编码前 STEP 文件已被替换")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
            size += len(chunk)
    if _stamp(path) != before or size != before[2]:
        raise ValueError("计算编码时 STEP 文件发生变化")
    return digest.hexdigest(), size


def _statistics(files, old_groups=()):
    groups = defaultdict(list)
    tasks = defaultdict(set)
    for item in files:
        groups[item["sha256"]].append(item)
        tasks[item["dataset"]].add(item["task"])
    extensions = {group["sha256"]: group for group in old_groups}
    duplicates = [
        {**extensions.get(sha, {}), "sha256": sha, "count": len(members), "files": members}
        for sha, members in groups.items() if len(members) > 1
    ]
    duplicates.sort(key=lambda group: (-group["count"], group["sha256"]))
    summary = {
        "task_counts": {dataset: len(names) for dataset, names in tasks.items()},
        "file_counts": dict(Counter(item["dataset"] for item in files)),
        "files_scanned": len(files), "unique_sha256_count": len(groups),
        "duplicate_group_count": len(duplicates),
        "files_in_duplicate_groups": sum(group["count"] for group in duplicates),
        "redundant_file_count": sum(group["count"] - 1 for group in duplicates),
        "error_count": 0,
    }
    return summary, duplicates


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"查重库包含重复 JSON 字段：{key}")
        result[key] = value
    return result


def _invalid_constant(value):
    raise ValueError(f"查重库包含非法 JSON 数值：{value}")


def _valid_sha(value):
    return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) is not None


def _nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def _validate_registry(raw):
    report = json.loads(raw, object_pairs_hook=_json_object, parse_constant=_invalid_constant)
    if (not isinstance(report, dict) or type(report.get("schema_version")) is not int
            or report["schema_version"] != 1 or report.get("algorithm") != "SHA-256"
            or report.get("status") != "complete" or report.get("errors") != []):
        raise ValueError("查重库必须是完整的 schema_version=1、SHA-256、complete 且无错误的库")
    if any(not _nonempty(report.get(key)) for key in ("generated_at", "scope", "duplicate_definition")):
        raise ValueError("查重库缺少必要元数据")
    roots = report.get("source_roots")
    if not isinstance(roots, list) or any(
            not isinstance(root, dict) or not _nonempty(root.get("dataset"))
            or not _nonempty(root.get("path")) for root in roots):
        raise ValueError("查重库 source_roots 无效")
    files = report.get("files")
    if not isinstance(files, list):
        raise ValueError("查重库 files 必须是完整记录列表")
    for item in files:
        if (not isinstance(item, dict)
                or any(not _nonempty(item.get(key)) for key in ("dataset", "task", "path"))
                or type(item.get("bytes")) is not int or item["bytes"] < 0
                or not _valid_sha(item.get("sha256"))):
            raise ValueError("查重库包含无效的文件记录")
    expected_summary, expected_groups = _statistics(files)
    summary = report.get("summary")
    if not isinstance(summary, dict):
        raise ValueError("查重库 summary 缺失")
    for key, expected in expected_summary.items():
        value = summary.get(key)
        if (type(value) is not type(expected) or value != expected
                or isinstance(value, dict) and any(type(n) is not int for n in value.values())):
            raise ValueError(f"查重库统计与 files 不一致：{key}")
    groups = report.get("duplicate_groups")
    if not isinstance(groups, list) or len(groups) != len(expected_groups):
        raise ValueError("查重库 duplicate_groups 与 files 不一致")
    remaining = {group["sha256"]: group for group in expected_groups}
    for group in groups:
        if not isinstance(group, dict) or not _valid_sha(group.get("sha256")):
            raise ValueError("查重库重复组记录无效")
        expected = remaining.pop(group["sha256"], None)
        if (expected is None or type(group.get("count")) is not int
                or any(group.get(key) != expected[key] for key in ("count", "files"))):
            raise ValueError("查重库重复组内容与 files 不一致")
    return report


@contextmanager
def _registry_lock(registry, timeout):
    lock = registry.with_name(registry.name + ".lock")
    with _regular_file(lock, os.O_RDWR | os.O_CREAT) as stream:
        deadline = time.monotonic() + timeout
        while True:
            try:
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("等待查重库锁超时")
                time.sleep(min(0.05, remaining))
        try:
            info = os.fstat(stream.fileno())
            identity = (info.st_dev, info.st_ino)
            if _stamp(lock)[:2] != identity:
                raise ValueError("查重库锁文件被替换")
            yield lock, identity
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)
    # Never unlink the stable lock: other processes may already be waiting on its inode.


def _read_registry(registry):
    before = _stamp(registry)
    with _regular_file(registry) as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read()
    if (opened.st_dev, opened.st_ino) != before[:2] or _stamp(registry) != before:
        raise ValueError("读取时查重库发生变化")
    return raw, before


def _sync_directory(directory):
    fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _verify_input_snapshot(verify_inputs):
    if verify_inputs is not None:
        try:
            verify_inputs()
        except Exception as exc:
            # Ordinary callback failures reject registration; Ctrl+C still propagates.
            raise ValueError(f"输入快照核验失败：{exc}") from exc


def check_and_register(path, registry, *, module, run_id, expected_sha256, lock_timeout=10,
                       verify_inputs=None):
    """Register a previously approved STEP, atomically rejecting identical raw bytes.

    The caller owns the grading gate. This function rechecks the approved digest,
    validates every historical record, then serializes read/hash/check/backup/replace.
    Optional verify_inputs() must raise if any approved input has changed; it runs
    after acquiring the registry lock and again immediately before commit checks.
    """
    evidence = dict(action="none", sha256=None, path=str(path), registry=str(registry),
                    matches=[], backup=None)
    temporary = None
    committed = False
    replace_started = False
    try:
        path, registry = Path(path).absolute(), Path(registry).absolute()
        evidence.update(path=str(path), registry=str(registry))
        if module not in ("1", "2", "3") or not _nonempty(run_id):
            raise ValueError("模块必须为 1、2、3，且 run_id 不能为空")
        if not _valid_sha(expected_sha256):
            raise ValueError("expected_sha256 必须是 64 位小写 SHA-256 编码")
        if verify_inputs is not None and not callable(verify_inputs):
            raise ValueError("verify_inputs 必须是可调用的输入快照核验函数")
        if (isinstance(lock_timeout, bool) or not isinstance(lock_timeout, (int, float))
                or not math.isfinite(lock_timeout) or lock_timeout < 0):
            raise ValueError("锁超时必须是非负有限秒数")
        if path.suffix.lower() not in (".step", ".stp"):
            raise ValueError("入库目标必须是 STEP/STP 文件")
        _stamp(path)
        _stamp(registry)  # Missing/corrupt registries must never turn into empty ones.
        with _registry_lock(registry, lock_timeout) as (lock, lock_identity):
            _verify_input_snapshot(verify_inputs)
            raw, original_stamp = _read_registry(registry)
            report = _validate_registry(raw)
            target_stamp = _stamp(path)
            digest, size = hash_step(path)
            evidence["sha256"] = digest
            if digest != expected_sha256 or _stamp(path) != target_stamp:
                raise ValueError("STEP 编码与评测时记录不一致，文件可能已被替换，拒绝入库")
            matches = [item for item in report["files"] if item["sha256"] == digest]
            if matches:
                evidence.update(action="duplicate", matches=matches)
                return dict(status="failed", reason="STEP 编码与历史记录重复，拒绝入库", evidence=evidence)
            now = datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds")
            report["files"].append(dict(dataset="aichecker_new", task=run_id, module=module,
                                        registered_at=now, path=str(path), bytes=size, sha256=digest))
            summary, groups = _statistics(report["files"], report["duplicate_groups"])
            report["summary"].update(summary)
            report.update(duplicate_groups=groups, updated_at=now)
            report.setdefault("registration_scope", "在原历史数据集基础上，追加 aichecker_new 调用方提交的 STEP 编码；按文件原始字节 SHA-256 查重。完整模块全维度验收由调用方负责，本事务不验证 CAD。")
            payload = (json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n").encode("utf-8")
            backup_dir = registry.with_name(registry.name + ".backups")
            backup_dir.mkdir(exist_ok=True)
            if backup_dir.is_symlink() or not backup_dir.is_dir():
                raise ValueError("备份目录必须是真实目录，不能是符号链接")
            backup_fd, backup_name = tempfile.mkstemp(prefix=registry.name + ".", suffix=".bak", dir=backup_dir)
            with os.fdopen(backup_fd, "wb") as stream:
                stream.write(raw)
                stream.flush()
                os.fsync(stream.fileno())
            evidence["backup"] = backup_name
            _sync_directory(backup_dir)
            temp_fd, temp_name = tempfile.mkstemp(prefix=registry.name + ".", suffix=".tmp", dir=registry.parent)
            temporary = Path(temp_name)
            with os.fdopen(temp_fd, "wb") as stream:
                os.fchmod(stream.fileno(), stat.S_IMODE(registry.stat().st_mode))
                stream.write(payload)
                stream.flush()
                os.fsync(stream.fileno())
            _verify_input_snapshot(verify_inputs)
            latest_raw, latest_stamp = _read_registry(registry)
            if latest_raw != raw or latest_stamp != original_stamp:
                raise ValueError("查重库被外部修改，已取消本次写入")
            if _stamp(path) != target_stamp or _stamp(lock)[:2] != lock_identity:
                raise ValueError("提交前 STEP 或查重库锁发生变化，已取消本次写入")
            replace_started = True
            os.replace(temporary, registry)
            committed = True
            evidence["action"] = "registered"
            temporary = None
            _sync_directory(registry.parent)
            return dict(status="passed", reason="通过", evidence=evidence)
    except (OSError, ValueError, TypeError, OverflowError) as exc:
        if committed:
            reason = f"查重库已写入，但提交后的持久化或锁释放失败，请核对：{exc}"
        elif replace_started:
            reason = f"查重库替换阶段发生错误，可能已写入，请核对库和备份：{exc}"
        else:
            reason = f"查重入库失败，未执行库替换：{exc}"
        return dict(status="error", reason=reason, evidence=evidence)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass  # Cleanup must not turn an I/O failure into a passing result.


def main():
    files = []
    errors = []
    groups = defaultdict(list)
    task_counts = Counter()
    for dataset, root in DATASETS:
        if not root.is_dir():
            errors.append({"dataset": dataset, "path": str(root), "error": "数据集目录不存在"})
            continue
        tasks = sorted((p for p in root.iterdir() if p.is_dir()),
                       key=lambda p: (0, int(p.name)) if p.name.isdigit() else (1, p.name))
        task_counts[dataset] = len(tasks)
        for task in tasks:
            reference = task / "tests/reference/reference.step"
            try:
                grader = task / "tests/grader.py"
                source = grader
                if "a.tests/'reference/reference.step'" not in grader.read_text(encoding="utf-8"):
                    source = task / "tests/run_verifier.py"
                if "reference/reference.step" not in source.read_text(encoding="utf-8"):
                    raise ValueError("无法确认判分脚本使用的标准 STEP 路径")
                digest, size = hash_step(reference)
                record = {
                    "dataset": dataset,
                    "task": task.name,
                    "path": str(reference),
                    "bytes": size,
                    "sha256": digest,
                }
                files.append(record)
                groups[record["sha256"]].append(record)
            except (OSError, ValueError) as exc:
                errors.append({"dataset": dataset, "task": task.name,
                               "path": str(reference), "error": str(exc)})

    duplicates = [
        {"sha256": sha, "count": len(members), "files": members}
        for sha, members in groups.items() if len(members) > 1
    ]
    duplicates.sort(key=lambda group: (-group["count"], group["sha256"]))
    summary = {
        "task_counts": dict(task_counts),
        "file_counts": dict(Counter(f["dataset"] for f in files)),
        "files_scanned": len(files),
        "unique_sha256_count": len(groups),
        "duplicate_group_count": len(duplicates),
        "files_in_duplicate_groups": sum(g["count"] for g in duplicates),
        "redundant_file_count": sum(g["count"] - 1 for g in duplicates),
        "error_count": len(errors),
    }
    report = {
        "schema_version": 1,
        "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(timespec="seconds"),
        "algorithm": "SHA-256",
        "scope": "当前两个 dataset 目录中，判分入口读取的 tests/reference/reference.step；每题一份",
        "duplicate_definition": "按原始文件内容计算 SHA-256；文件名、后缀和路径不参与哈希。用于文件内容查重，不用于几何等价判断。",
        "source_roots": [{"dataset": label, "path": str(root)} for label, root in DATASETS],
        "status": "complete" if not errors else "partial",
        "summary": summary,
        "duplicate_groups": duplicates,
        "files": files,
        "errors": errors,
    }
    temporary = OUTPUT.with_name(OUTPUT.name + ".tmp")
    temporary.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(OUTPUT)
    print(json.dumps({"output": str(OUTPUT), "status": report["status"], **summary}, ensure_ascii=False))
    if errors:
        raise SystemExit(1)


class _ArgumentParser(argparse.ArgumentParser):
    def error(self, message):
        raise ValueError(message)


def cli(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv:
        return main()  # Preserve the explicitly invoked, no-argument historical scan.
    parser = _ArgumentParser(description=__doc__)
    parser.add_argument("--check-and-register", required=True, metavar="STEP")
    parser.add_argument("--registry", required=True, metavar="JSON")
    parser.add_argument("--module", required=True, choices=("1", "2", "3"))
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--expected-sha256", required=True)
    try:
        args = parser.parse_args(argv)
        result = check_and_register(args.check_and_register, args.registry, module=args.module,
                                    run_id=args.run_id, expected_sha256=args.expected_sha256)
    except ValueError as exc:
        result = dict(status="error", reason=f"命令参数错误：{exc}",
                      evidence=dict(action="none", sha256=None, path=None, registry=None,
                                    matches=[], backup=None))
    print(json.dumps(result, ensure_ascii=False))
    return {"passed": 0, "failed": 1, "error": 2}[result["status"]]


if __name__ == "__main__":
    raise SystemExit(cli())
