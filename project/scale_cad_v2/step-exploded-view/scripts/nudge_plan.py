"""Move selected parts in the orthographic view plane, without rotating geometry."""
import argparse
import hashlib
import json
import math
from pathlib import Path


def view_offset(direction, right_mm, up_mm):
    if len(direction) != 3 or not all(math.isfinite(x) for x in direction):
        raise ValueError('direction must contain three finite numbers')
    norm = math.sqrt(sum(x*x for x in direction))
    if norm == 0:
        raise ValueError('direction must be nonzero')
    x, y, z = [v/norm for v in direction]
    horizontal = math.hypot(x, y)
    if horizontal < 1e-8:
        raise ValueError('Vertical camera: use an explicit world-space offset to avoid ambiguous screen axes')
    right = [-y/horizontal, x/horizontal, 0]
    up = [-z*x/horizontal, -z*y/horizontal, horizontal]
    return [right_mm*r + up_mm*u for r, u in zip(right, up)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--plan', type=Path, required=True)
    parser.add_argument('--part', action='append', required=True, help='Repeat to move several parts together')
    parser.add_argument('--right-mm', type=float, default=0, help='Positive right; negative left, in view-plane model mm')
    parser.add_argument('--up-mm', type=float, default=0, help='Positive up; negative down, in view-plane model mm')
    parser.add_argument('--relative-to', choices=['current', 'original'], default='current', help='Original replaces the offset; current adds to it')
    args = parser.parse_args()
    if not all(math.isfinite(v) for v in [args.right_mm, args.up_mm]):
        parser.error('Movement must be finite')
    plan = json.loads(args.plan.read_text())
    catalog_path = args.plan.parent/'catalog.json'
    catalog = json.loads(catalog_path.read_text())
    if (plan['catalog_sha256'] != hashlib.sha256(catalog_path.read_bytes()).hexdigest()
            or plan['source_sha256'] != catalog['source_sha256']):
        parser.error('Plan/catalog mismatch; do not apply IDs from another export')
    if set(plan['offsets']) != {p['id'] for p in catalog['parts']}:
        parser.error('Plan must cover every catalog part exactly once')
    ids = list(dict.fromkeys(args.part))
    if any(i not in plan['offsets'] for i in ids):
        parser.error('Unknown part ID')
    delta = view_offset(plan['direction'], args.right_mm, args.up_mm)
    changes = []
    for ident in ids:
        before = plan['offsets'][ident]
        base = before if args.relative_to == 'current' else [0, 0, 0]
        if len(base) != 3 or not all(math.isfinite(v) for v in base):
            parser.error('Existing offset must contain three finite numbers')
        after = [round(a+b, 9) for a, b in zip(base, delta)]
        plan['offsets'][ident] = after
        changes.append(dict(part=ident, before=before, after=after))
    args.plan.write_text(json.dumps(plan, indent=2)+'\n')
    print(json.dumps(changes, indent=2))
    print('Re-render and inspect occlusion. Screen-plane movement is illustrative, not a removal-path check.')


if __name__ == '__main__':
    main()
