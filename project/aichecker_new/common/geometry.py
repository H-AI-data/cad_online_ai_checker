"""Bounded STEP geometry jobs and complete-link family membership."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

from vendor.mechanical_families import same_mechanical_family

MAX_FILE_BYTES = 256 * 1024 * 1024


def analyze_step(path: Path, output: Path, timeout=300) -> dict:
    path, output = Path(path).resolve(), Path(output).resolve()
    result = {'kind': 'unknown', 'complete': False, 'families': [], 'occurrence_count': 0,
              'images': [], 'warnings': [], 'source_file': str(path), 'source_sha256': None}
    try:
        if not path.is_file() or path.stat().st_size > MAX_FILE_BYTES:
            result['warnings'].append('STEP 必须是普通文件，且大小不能超过256MB安全限制')
            return result
        digest = hashlib.sha256()
        with path.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                digest.update(block)
        result['source_sha256'] = digest.hexdigest()
        output.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='geometry-', dir=output) as job:
            destination = Path(job) / 'result.json'
            process = subprocess.run(
                [sys.executable, '-m', 'common.geometry_worker', str(path), str(output), str(destination)],
                cwd=Path(__file__).resolve().parents[1], capture_output=True, timeout=timeout,
            )
            if process.returncode or not destination.is_file():
                result['warnings'].append('STEP 几何子进程失败或没有返回可验证结果')
                return result
            parsed = json.loads(destination.read_text(encoding='utf-8'))
            if parsed.get('source_sha256') != result['source_sha256']:
                result['warnings'].append('STEP 文件在分析期间发生变化')
                return result
            return parsed
    except subprocess.TimeoutExpired:
        result['warnings'].append('STEP 几何分析超时，需人工复核')
    except (OSError, ValueError, TypeError) as exc:
        result['warnings'].append(f'STEP 几何分析失败：{type(exc).__name__}: {exc}')
    return result


def match_families(assembly: dict, parts: list[dict]) -> dict:
    result = {'matched_family_ids': [], 'matches': [], 'foreign': [], 'ambiguous': [], 'complete': bool(assembly.get('complete'))}
    targets = assembly.get('families', [])
    if not targets:
        result['complete'] = False
    if any(not family.get('members_metrics') and family.get('quantity', 1) > 1 for family in targets):
        result['complete'] = False
        result['ambiguous'].append({'reason': 'missing_target_members_metrics'})
        return result
    for part in parts:
        if not part.get('complete') or not part.get('families'):
            result['complete'] = False
        for source in part.get('families', []):
            item = {'source_file': part.get('source_file', ''), 'family_id': source['id']}
            candidates = []
            try:
                if not source.get('members_metrics') and source.get('quantity', 1) > 1:
                    raise ValueError('missing_source_members_metrics')
                left = source.get('members_metrics') or [source['metrics']]
                for target in targets:
                    right = target.get('members_metrics') or [target['metrics']]
                    if all(same_mechanical_family(a, b) for a in left for b in right):
                        candidates.append(target['id'])
            except (KeyError, TypeError, ValueError):
                result['complete'] = False
                result['ambiguous'].append({**item, 'reason': 'missing_or_invalid_metrics'})
                continue
            if len(candidates) == 1:
                result['matches'].append({**item, 'assembly_family_id': candidates[0]})
                if candidates[0] not in result['matched_family_ids']:
                    result['matched_family_ids'].append(candidates[0])
            elif len(candidates) > 1:
                result['ambiguous'].append({**item, 'candidate_family_ids': candidates})
                result['complete'] = False
            else:
                result['foreign'].append(item)
    return result
