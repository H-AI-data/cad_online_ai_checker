"""STEP -> private meshes/spec -> aligned family renders -> public parts.pdf."""
import argparse
import json
import hashlib
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pymupdf

from dimension_density import scan_medium_dimension_density
from step_export_status import (INVALID_EXPORTED_STEP_EVENT,
                                INVALID_EXPORTED_STEP_EXIT_CODE)
from source_drawings import prepare_source_drawings


def run_family_step_export(command: list[str], *, work: Path, output: Path,
                           step_dir: Path, source_step: Path) -> bool:
    """Return False and withdraw this assembly when a part STEP reimports invalid."""
    result = subprocess.run(command, capture_output=True, text=True)
    if result.stdout:
        print(result.stdout, end='' if result.stdout.endswith('\n') else '\n', flush=True)
    if result.stderr:
        print(result.stderr, end='' if result.stderr.endswith('\n') else '\n',
              file=sys.stderr, flush=True)
    if result.returncode == 0:
        return True
    if result.returncode != INVALID_EXPORTED_STEP_EXIT_CODE:
        raise subprocess.CalledProcessError(result.returncode, command)
    events = []
    for line in result.stdout.splitlines():
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get('event') == INVALID_EXPORTED_STEP_EVENT:
            events.append(event)
    if len(events) != 1:
        raise subprocess.CalledProcessError(result.returncode, command)
    output.unlink(missing_ok=True)
    for path in step_dir.glob('family_*.step'):
        path.unlink()
    if step_dir.is_dir() and not any(step_dir.iterdir()):
        step_dir.rmdir()
    report = {
        'status': 'skipped_assembly',
        'reason': INVALID_EXPORTED_STEP_EVENT,
        'source_step': str(source_step.resolve()),
        'source_sha256': hashlib.sha256(source_step.read_bytes()).hexdigest(),
        **events[0],
    }
    report_path = work/'skipped_assembly.json'
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps({'status': 'skipped_assembly',
                      'report': str(report_path),
                      'family': report['family']}), flush=True)
    return False


def load_screen_selection(path: Path, case_name: str, spec_path: Path,
                          source_step: Path) -> list[str]:
    """Bind screened family IDs to this exact spec and source STEP."""
    selection = json.loads(path.read_text())
    if not isinstance(selection, dict) or selection.get('schema') != 'cad-complexity-screen/step-selection-v1':
        raise ValueError('Unsupported complexity-screen selection format')
    records = selection.get('cases')
    if not isinstance(records, list) or any(not isinstance(case, dict) for case in records):
        raise ValueError('Screen selection cases must be records')
    matches = [case for case in records
               if case.get('case') == case_name]
    if len(matches) != 1:
        raise ValueError(f'Expected exactly one screen selection for {case_name}')
    record = matches[0]
    if record.get('part_spec_sha256') != hashlib.sha256(spec_path.read_bytes()).hexdigest():
        raise ValueError(f'{case_name}: screened part-spec does not match this build')
    if record.get('source_step_sha256') != hashlib.sha256(source_step.read_bytes()).hexdigest():
        raise ValueError(f'{case_name}: screened source STEP does not match this build')
    families = record.get('families')
    if (not isinstance(families, list) or any(not isinstance(fid, str) for fid in families)
            or len(families) != len(set(families))):
        raise ValueError('Screened families must be a unique family-ID array')
    known = {family['id'] for family in json.loads(spec_path.read_text())['families']}
    if set(families) - known:
        raise ValueError(f'Unknown screened families: {sorted(set(families)-known)}')
    return families


def reviewed_override_path(source_step: Path, filename: str,
                           output: Path | None = None) -> Path:
    """Find a source-bound decision beside the STEP or in its output task."""
    source_path = source_step.parent / filename
    if source_path.is_file() or output is None:
        return source_path
    for parent in output.resolve().parents:
        if re.fullmatch(r'cad_[A-Za-z0-9_]+', parent.name):
            task_path = parent / 'tests' / 'reference' / filename
            if task_path.is_file():
                return task_path
            break
    return source_path


def load_reviewed_step_overrides(source_step: Path, spec_path: Path,
                                 output: Path | None = None) -> list[str]:
    """Read source-bound, human-reviewed STEP tier decisions beside the input STEP.

    The normal geometry screen still runs. This manifest records a later review
    of missing drawing coverage and is deliberately data, not family-specific
    drawing code. A fresh default build therefore reproduces the reviewed tier.
    """
    path = reviewed_override_path(source_step, 'step_tier_overrides.json', output)
    if not path.is_file():
        return []
    record = json.loads(path.read_text())
    if (not isinstance(record, dict)
            or record.get('schema') != 'harbor/step-tier-overrides-v1'):
        raise ValueError(f'Unsupported STEP tier override: {path}')
    if record.get('source_step_sha256') != hashlib.sha256(source_step.read_bytes()).hexdigest():
        raise ValueError(f'STEP tier override source mismatch: {path}')
    if record.get('part_spec_sha256') != hashlib.sha256(spec_path.read_bytes()).hexdigest():
        raise ValueError(f'STEP tier override part-spec mismatch: {path}')
    selected = record.get('families')
    if (not isinstance(selected, list) or len(selected) != len(set(selected))
            or any(not isinstance(item, str) for item in selected)):
        raise ValueError(f'STEP tier override needs unique family IDs: {path}')
    known = {item['id'] for item in json.loads(spec_path.read_text())['families']}
    if set(selected) - known:
        raise ValueError(f'STEP tier override has unknown families: {path}')
    return selected


def load_reviewed_medium_overrides(source_step: Path, spec_path: Path,
                                   output: Path | None = None) -> list[str]:
    """Read source-bound families whose drawings need a detailed dimension page."""
    path = reviewed_override_path(source_step, 'medium_tier_overrides.json', output)
    if not path.is_file():
        return []
    record = json.loads(path.read_text())
    if (not isinstance(record, dict)
            or record.get('schema') != 'harbor/medium-tier-overrides-v1'):
        raise ValueError(f'Unsupported medium tier override: {path}')
    if record.get('source_step_sha256') != hashlib.sha256(source_step.read_bytes()).hexdigest():
        raise ValueError(f'Medium tier override source mismatch: {path}')
    if record.get('part_spec_sha256') != hashlib.sha256(spec_path.read_bytes()).hexdigest():
        raise ValueError(f'Medium tier override part-spec mismatch: {path}')
    selected = record.get('families')
    if (not isinstance(selected, list) or len(selected) != len(set(selected))
            or any(not isinstance(item, str) for item in selected)):
        raise ValueError(f'Medium tier override needs unique family IDs: {path}')
    known = {item['id'] for item in json.loads(spec_path.read_text())['families']}
    if set(selected) - known:
        raise ValueError(f'Medium tier override has unknown families: {path}')
    return selected


def infer_case_name(output: Path) -> str | None:
    """Use the nearest cad_* directory when writing into a Harbor case."""
    for parent in output.resolve().parents:
        if re.fullmatch(r'cad_[A-Za-z0-9_]+', parent.name):
            return parent.name
    return None


def check_public_pdf_wording(path: Path) -> None:
    """Reject internal renderer names and superseded copy in public output."""
    with pymupdf.open(path) as document:
        for page in document:
            wording = page.get_text().upper()
            if "BLENDER" in wording or "DIFFICULT PART | STEP +" in wording:
                raise ValueError(
                    f"Public PDF has superseded wording on page {page.number + 1}: {path}")


def promote_unannotated_medium(candidates_path: Path, medium_pdf: Path) -> list[str]:
    """Move blank or densely annotated medium parts to STEP.

    The detail builder writes one audit record per medium family. A base page
    containing only the overall envelope must not pass as a detailed drawing
    when the geometry remains uncertified.
    """
    rows = json.loads(candidates_path.read_text())
    audit_path = medium_pdf.with_suffix('.audit.json')
    audit = json.loads(audit_path.read_text())
    row_ids = [row['family'] for row in rows]
    audit_ids = [row['family'] for row in audit]
    if (len(row_ids) != len(set(row_ids))
            or len(audit_ids) != len(set(audit_ids))
            or set(row_ids) != set(audit_ids)):
        raise ValueError('Medium detail audit does not match candidate families')
    density = scan_medium_dimension_density(medium_pdf)
    if set(density) != set(row_ids):
        raise ValueError('Dimension-density screen does not match candidate families')
    medium_pdf.with_name('medium-density-screen.json').write_text(json.dumps(
        density, ensure_ascii=False, indent=2))
    promoted = {
        row['family'] for row in audit
        if (row.get('geometry_status') == 'step_required'
            and not row.get('step_available')
            and row.get('supplement_pages', 0) == 0
            and row.get('additional_projection_pages', 0) == 0)
    }
    promoted.update(family for family, result in density.items()
                    if result['step_required_for_density'])
    if not promoted:
        return []
    with pymupdf.open(medium_pdf) as source:
        output = pymupdf.open()
        seen = set()
        for page in source:
            headings = {match.group(0).lower()
                        for line in page.get_text().splitlines()
                        if (match := re.match(r'^FAMILY_[A-Za-z0-9]+\b', line))}
            if len(headings) != 1:
                raise ValueError('Unidentified medium detail page')
            family = headings.pop()
            if family in promoted:
                seen.add(family)
            else:
                output.insert_pdf(source, from_page=page.number,
                                  to_page=page.number)
        if seen != promoted:
            raise ValueError(f'Missing promoted medium pages: {sorted(promoted-seen)}')
        temporary = medium_pdf.with_name(medium_pdf.stem + '.filtered.pdf')
        temporary.unlink(missing_ok=True)
        if len(output):
            output.save(temporary, garbage=4, deflate=True)
        output.close()
    if temporary.exists():
        os.replace(temporary, medium_pdf)
    else:
        medium_pdf.unlink()
    kept = [row for row in rows if row['family'] not in promoted]
    candidates_path.write_text(json.dumps(kept, ensure_ascii=False, indent=2))
    audit_path.write_text(json.dumps(
        [row for row in audit if row['family'] not in promoted],
        ensure_ascii=False, indent=2))
    return sorted(promoted)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--step', required=True, type=Path)
    parser.add_argument('--work-dir', required=True, type=Path, help='New empty private work directory')
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--blender', help='Host Blender executable; auto-detected by default')
    parser.add_argument('--freecad-python', type=Path,
                        help='FreeCAD-bundled Python for validated single-part STEP export')
    parser.add_argument('--freecad-lib', type=Path,
                        help='FreeCAD library directory for --freecad-python')
    parser.add_argument('--title', default='CAD PARTS')
    parser.add_argument('--mesh-cache', type=Path, help='Optional same-run FreeCAD cache with matching catalog SHA-256')
    parser.add_argument('--resume-after-extract', action='store_true',
                        help='Reuse verified geometry, part-spec, and occurrence STL files in work-dir')
    parser.add_argument('--no-feature-annotations',action='store_true',help='Disable sparse STEP feature dimensions')
    parser.add_argument('--step-files-dir', type=Path,
                        help='Directory of already exported family_XXX.step files to announce in parts.pdf')
    parser.add_argument('--provide-step-families-json', type=Path,
                        help='JSON family-ID list to export as local-coordinate STEP files beside parts.pdf')
    parser.add_argument('--complexity-screen-selection', type=Path,
                        help='Override the default screen with a prior complexity_step_selection.json')
    parser.add_argument('--case-name',
                        help='Optional case name when the output path has no cad_* directory')
    args = parser.parse_args()
    selection_modes = sum(bool(value) for value in
                          (args.provide_step_families_json, args.complexity_screen_selection))
    if selection_modes > 1:
        parser.error('Choose only one STEP-selection mode')
    if selection_modes and (args.step_files_dir or args.no_feature_annotations):
        parser.error('STEP-selection modes require feature annotations and no --step-files-dir')
    if args.case_name and not re.fullmatch(r'cad_[A-Za-z0-9_]+', args.case_name):
        parser.error('--case-name must be a single cad_* directory name')
    auto_screen = not selection_modes and not args.step_files_dir and not args.no_feature_annotations
    case_name = args.case_name or infer_case_name(args.output)
    if args.complexity_screen_selection and not case_name:
        selection = json.loads(args.complexity_screen_selection.read_text())
        records = selection.get('cases', []) if isinstance(selection, dict) else []
        if isinstance(records, list) and len(records) == 1 and isinstance(records[0], dict):
            case_name = records[0].get('case')
        if not case_name:
            parser.error('A multi-case selection needs --case-name or an output path below cad_*')
    if auto_screen and not case_name:
        case_name = 'cad_current'
    blender = shutil.which(args.blender or os.environ.get('BLENDER_BIN', 'blender'))
    if not blender:
        parser.error('Blender not found; set BLENDER_BIN or pass --blender')
    native_python = str(args.freecad_python or os.environ.get('FREECAD_PYTHON', 'python'))
    if not shutil.which(native_python):
        parser.error('FreeCAD Python not found; set FREECAD_PYTHON or pass --freecad-python')
    native_lib = args.freecad_lib or os.environ.get('FREECAD_LIB')
    if not args.step.is_file():
        parser.error('STEP not found')
    freecad_flags = ['--freecad-lib', str(native_lib)] if native_lib else []
    work = args.work_dir.resolve()
    if work.exists() and any(work.iterdir()) and not args.resume_after_extract:
        parser.error('Use an empty work directory to avoid mixing stale meshes and manifests')
    work.mkdir(parents=True, exist_ok=True)
    os.environ['CAD_PARTS_ALIGNMENT_CACHE'] = str(work/'alignment-cache')
    root = Path(__file__).resolve().parent

    def run(script, *flags):
        subprocess.run([sys.executable, str(root/script), *map(str, flags)], check=True)

    if args.resume_after_extract:
        if args.mesh_cache:
            parser.error('--resume-after-extract and --mesh-cache cannot be combined')
        source = work/'geometry/meshes.json.gz'
        catalog = json.loads((work/'geometry/catalog.json').read_text())
        if catalog['source_sha256'] != hashlib.sha256(args.step.read_bytes()).hexdigest():
            parser.error('Cached geometry source checksum mismatch')
        spec = work/'part-spec.json'
        parts = work/'parts'
        if not source.is_file() or not spec.is_file() or not parts.is_dir():
            parser.error('Incomplete cached extraction in work directory')
        expected = json.loads(spec.read_text())['occurrence_count']
        if len(list(parts.glob('part_*.stl'))) != expected:
            parser.error('Cached occurrence STL count mismatch')
    elif args.mesh_cache:
        mesh_cache = args.mesh_cache.resolve()
        catalog = json.loads((mesh_cache.parent/'catalog.json').read_text())
        if catalog['source_sha256'] != hashlib.sha256(args.step.read_bytes()).hexdigest():
            parser.error('Mesh source checksum mismatch')
        (work/'geometry').mkdir()
        shutil.copy2(mesh_cache.parent/'catalog.json',work/'geometry/catalog.json')
        source = work/'geometry/meshes.json.gz'
        shutil.copy2(mesh_cache, source)
    else:
        pipeline = root.parents[1]/'step-exploded-view/scripts/pipeline.py'
        subprocess.run([sys.executable, str(pipeline), 'extract', str(args.step.resolve()),
                        str(work/'geometry'), '--freecad-python',str(native_python),
                        *freecad_flags], check=True)
        source = work/'geometry/meshes.json.gz'
    spec = work/'part-spec.json' 
    parts = work/'parts'
    aligned = work/'aligned'
    renders = work/'renders'
    if not args.resume_after_extract:
        run('extract_part_spec.py', '--step', source, '--output', spec)
        from extract_part_spec import load_occurrence_meshes
        parts.mkdir()
        meshes = load_occurrence_meshes(source)
        for i, mesh in enumerate(meshes):
            mesh.export(parts/f'part_{i:05d}.stl')
    source_drawings = work/'verified_source_drawings.json'
    pdf_family_ids = prepare_source_drawings(args.step.resolve(), spec, source_drawings)
    run('export_aligned_family_meshes.py', '--spec', spec, '--parts-dir', parts,
        '--output-dir', aligned)
    manifest = aligned/'manifest.json'
    families = json.loads(manifest.read_text())['families']

    def render_families(family_ids, batch_name, step_views=False):
        if not family_ids:
            return
        batch_ids = work / f'blender-{batch_name}-ids.json'
        batch_ids.write_text(json.dumps(family_ids))
        command = [blender, '--background', '-t', '4', '--python',
                   str(root/'blender_render_part_depth.py'), '--',
                   '--batch-ids-json', str(batch_ids), '--meshes-dir', str(aligned),
                   '--manifest', str(manifest), '--output-root', str(renders)]
        if step_views:
            command.append('--step-views')
        subprocess.run(command, check=True)

    render_families([family['id'] for family in families
                     if auto_screen or family['show_depth_views']], 'depth')
    args.output.parent.mkdir(parents=True, exist_ok=True)
    drawing_flags = ['--spec', spec, '--parts-dir', parts,
                     '--manifest', manifest, '--renders-dir', renders,
                     '--output', args.output.resolve(),
                     '--product-title', args.title,
                     '--footer-label', f'{args.title} | PARTS | UNITS: mm']
    if auto_screen:
        drawing_flags.append('--all-family-depth-views')
    if args.step_files_dir:
        drawing_flags.extend(['--step-files-dir', args.step_files_dir.resolve()])
    run('generate_parts_pdf_six_views.py', *drawing_flags)
    full_baseline_pdf = work/'full_baseline.pdf'
    shutil.copy2(args.output.resolve(),full_baseline_pdf)
    if not args.no_feature_annotations:
        analytic=work/'analytic';analytic.mkdir(exist_ok=True)
        extractor=root.parents[1]/'cad-dimension-sheets/scripts'
        subprocess.run([str(native_python),str(extractor/'extract_step.py'),
                        '--step',str(args.step.resolve()),'--out',str(analytic),
                        '--catalog',str(source.parent/'catalog.json'),
                        *freecad_flags],check=True)
        feature_plan=work/'feature-annotations.json'
        run('annotate_parts_pdf.py','--prepare','--input',args.output.resolve(),
            '--plan',feature_plan,'--spec',spec,'--geometry',analytic/'manual_geometry.json','--meshes',source,
            '--parts-dir',parts)
        already_annotated = False
        screen_selection = args.complexity_screen_selection
        medium_candidates = None
        baseline_pdf = None
        if auto_screen:
            # The screen reads complete dimensioned pages; keep this baseline
            # private so the final STEP-simplified pages do not become scanner input.
            run('annotate_parts_pdf.py','--input',args.output.resolve(),
                '--output',args.output.resolve(),'--plan',feature_plan)
            already_annotated = True
            scan_root = work/'complexity-screen-input'
            scan_input = scan_root/case_name/'environment/input'
            scan_input.mkdir(parents=True, exist_ok=True)
            shutil.copy2(args.output.resolve(), scan_input/'parts.pdf')
            baseline_pdf = scan_input/'parts.pdf'
            scan_work = work/'complexity-screen'
            scan_work.mkdir(exist_ok=True)
            inventory = work/'complexity-screen-inventory.json'
            inventory.write_text(json.dumps([{'name':case_name,'index':0,
                                              'family_count':len(families),
                                              'parts_workdir':str(work)}]))
            screen_script = root.parents[1]/'cad-complexity-screen/scripts/run.py'
            screen_command = [sys.executable,str(screen_script),'--manifest',str(inventory),
                              '--harbor-dir',str(scan_root),'--work-dir',str(scan_work)]
            subprocess.run(screen_command+['--check-inputs'],check=True)
            subprocess.run(screen_command+['--no-preview', '--skip-visual-watchlist'],check=True)
            screen_selection = scan_work/'complexity_step_selection.json'
        if screen_selection:
            selected_ids = load_screen_selection(screen_selection.resolve(),
                                                 case_name,spec,args.step.resolve())
        elif args.provide_step_families_json:
            selected_ids = json.loads(args.provide_step_families_json.read_text())
            if (not isinstance(selected_ids,list)
                    or any(not isinstance(fid,str) for fid in selected_ids)
                    or len(selected_ids)!=len(set(selected_ids))):
                raise ValueError('Provided STEP families must be a unique family-ID array')
            known = {family['id'] for family in json.loads(spec.read_text())['families']}
            if set(selected_ids)-known:
                raise ValueError(f'Unknown STEP families: {sorted(set(selected_ids)-known)}')
        else:
            selected_ids = []
        if auto_screen:
            complexity_root = root.parents[1]/'cad-complexity-screen/scripts'
            subprocess.run([sys.executable,
                            str(complexity_root/'screen_medium_parts.py'),
                            '--manifest',str(inventory),
                            '--harbor-dir',str(scan_root),
                            '--work-dir',str(scan_work),
                            '--output-dir',str(scan_work)],check=True)
            initial_difficult = work/'initial-difficult-families.json'
            initial_difficult.write_text(json.dumps(sorted(selected_ids),indent=2))
            medium_candidates = work/'medium_candidates.json'
            final_difficult = work/'difficult-families.json'
            subprocess.run([sys.executable,
                            str(complexity_root/'augment_medium_candidates.py'),
                            '--candidates',str(scan_work/'中等难度零件候选.json'),
                            '--features',str(feature_plan),
                            '--spec',str(spec),'--case',case_name,
                            '--existing-difficult-json',str(initial_difficult),
                            '--meshes',str(aligned),
                            '--output',str(medium_candidates),
                            '--difficult-output',str(final_difficult)],check=True)
            selected_ids = json.loads(final_difficult.read_text())
        # A closed source B-rep can occasionally triangulate as an open mesh.
        # Its signed mesh volume is then not a trustworthy part fingerprint.
        # Keep the source-bound drawing/spec, but provide the exact B-rep STEP
        # for every affected family instead of treating its drawing as complete.
        catalog = json.loads((work/'geometry/catalog.json').read_text())
        mesh_volume_deviations = []
        for occurrence in json.loads(spec.read_text())['occurrences']:
            index = occurrence['reference_index']
            brep_volume = abs(float(catalog['parts'][index]['volume_mm3']))
            mesh_volume = abs(float(occurrence['volume_mm3']))
            if abs(brep_volume - mesh_volume) > max(0.05, brep_volume * 0.05):
                # A closed display mesh can still underestimate a highly curved
                # source solid.  A bounded discrepancy is handled like an open
                # mesh: provide the exact source B-rep as a single-part STEP.
                # The export path independently checks source identity, area,
                # extent, and the STEP round trip before the file is published.
                if (occurrence['mechanical_metrics']['watertight'] and
                        abs(brep_volume - mesh_volume) > max(0.05, brep_volume * 0.10)):
                    raise ValueError(f"Watertight mesh volume disagrees with source B-rep: "
                                     f"{occurrence['family_id']} / solid {index}")
                mesh_volume_deviations.append({
                    'family': occurrence['family_id'], 'reference_index': index,
                    'brep_volume_mm3': brep_volume, 'mesh_volume_mm3': mesh_volume,
                })
        (work/'source_mesh_volume_deviations.json').write_text(
            json.dumps(mesh_volume_deviations, indent=2))
        # The source extraction counts triangles for each occurrence.  Above
        # one million, a full drawing can exhaust memory even when the solid
        # itself is valid.  Supply its exact single-part STEP instead.
        super_complex_parts = []
        for occurrence in json.loads(spec.read_text())['occurrences']:
            index = occurrence['reference_index']
            triangles = int(catalog['parts'][index].get(
                'source_triangles', catalog['parts'][index]['triangles']))
            if triangles > 1_000_000:
                super_complex_parts.append({
                    'family': occurrence['family_id'],
                    'reference_index': index,
                    'source_triangles': triangles,
                })
        (work/'super_complex_parts.json').write_text(
            json.dumps(super_complex_parts, indent=2))
        reviewed_step_ids = set(load_reviewed_step_overrides(
            args.step.resolve(), spec, args.output.resolve()))
        reviewed_step_ids.update(row['family'] for row in mesh_volume_deviations)
        reviewed_step_ids.update(row['family'] for row in super_complex_parts)
        if reviewed_step_ids:
            selected_ids = sorted(set(selected_ids) | reviewed_step_ids)
            (work/'difficult-families.json').write_text(json.dumps(selected_ids,indent=2))
            if auto_screen:
                candidate_rows = json.loads(medium_candidates.read_text())
                candidate_rows = [row for row in candidate_rows
                                  if row['family'] not in reviewed_step_ids]
                medium_candidates.write_text(json.dumps(candidate_rows,indent=2))
        if pdf_family_ids:
            selected_ids = sorted(set(selected_ids) - pdf_family_ids)
            (work/'difficult-families.json').write_text(json.dumps(selected_ids, indent=2))
            if auto_screen:
                candidate_rows = json.loads(medium_candidates.read_text())
                medium_candidates.write_text(json.dumps(
                    [row for row in candidate_rows if row['family'] not in pdf_family_ids],
                    indent=2))
        reviewed_medium_ids = load_reviewed_medium_overrides(
            args.step.resolve(), spec, args.output.resolve())
        if reviewed_medium_ids:
            if not auto_screen:
                raise ValueError('Reviewed medium tier requires the automatic four-tier build')
            if set(reviewed_medium_ids) & set(selected_ids):
                raise ValueError('A reviewed medium family is already in the STEP tier')
            candidate_rows = json.loads(medium_candidates.read_text())
            existing = {row['family'] for row in candidate_rows}
            feature_rows = {row['family']: row
                            for row in json.loads(feature_plan.read_text())}
            spec_families = {row['id']: row
                             for row in json.loads(spec.read_text())['families']}
            for family_id in reviewed_medium_ids:
                if family_id in existing:
                    continue
                feature = feature_rows[family_id]
                family = spec_families[family_id]
                candidate_rows.append({
                    'case': case_name, 'family': family_id,
                    'quantity': family['quantity'],
                    'pdf_page': feature['page'] + 1,
                    'size_mm': feature['dimensions'],
                    'tier': '优先',
                    'reasons': ['reviewed_dimension_coverage'],
                })
            candidate_rows.sort(key=lambda row: row['pdf_page'])
            medium_candidates.write_text(json.dumps(candidate_rows, indent=2))
        if selected_ids:
            selected_path = work/'difficult-families.json'
            selected_path.write_text(json.dumps(sorted(selected_ids),indent=2))
            shutil.copy2(feature_plan,work/'feature-annotations-before-step.json')
            step_dir=args.output.resolve().parent/'provided_part_files';step_dir.mkdir(exist_ok=True)
            # An existing task may retain files from an earlier automatic
            # screen.  This run's source-bound selection is authoritative;
            # medium families promoted later are exported again below.
            for stale in step_dir.glob('*.step'):
                if stale.stem not in selected_ids:
                    stale.unlink()
            export_command = [str(native_python),str(root/'export_family_steps.py'),
                              '--step',str(args.step.resolve()),
                              '--spec',str(spec),
                              '--catalog',str(source.parent/'catalog.json'),
                              '--geometry',str(analytic/'manual_geometry.json'),
                              '--features',str(feature_plan),
                              '--families-json',str(selected_path),
                              '--output-dir',str(step_dir),
                              *freecad_flags]
            if not run_family_step_export(
                    export_command, work=work, output=args.output.resolve(),
                    step_dir=step_dir, source_step=args.step):
                return INVALID_EXPORTED_STEP_EXIT_CODE
            expected_steps = {f'{family_id}.step' for family_id in selected_ids}
            actual_steps = {path.name for path in step_dir.glob('*.step')}
            if actual_steps != expected_steps:
                raise ValueError(f'STEP bundle has missing or stale files: '
                                 f'missing={expected_steps - actual_steps}, '
                                 f'extra={actual_steps - expected_steps}')
            missing_step_views = []
            for family_id in selected_ids:
                family_dir = renders/family_id
                render_manifest = family_dir/'render_manifest.json'
                views_ready = False
                if render_manifest.is_file():
                    data = json.loads(render_manifest.read_text())
                    views = data.get('views', {})
                    required = ('upper-oblique', 'rear-oblique', 'top')
                    views_ready = (data.get('view_version') == 'upper-opposite-grid-linework-v3'
                                   and all(views.get(name, {}).get('image')
                                           and (family_dir / views[name]['image']).is_file()
                                           for name in required))
                if not views_ready:
                    missing_step_views.append(family_id)
            render_families(missing_step_views, 'step-views', step_views=True)
            run('generate_parts_pdf_six_views.py', *drawing_flags,
                '--step-files-dir',step_dir,'--difficult-families-json',selected_path)
            run('annotate_parts_pdf.py','--prepare','--input',args.output.resolve(),
                '--plan',feature_plan,'--spec',spec,
                '--geometry',analytic/'manual_geometry.json','--meshes',source,
                '--parts-dir',parts)
            already_annotated = False
        if not already_annotated:
            run('annotate_parts_pdf.py','--input',args.output.resolve(),
                '--output',args.output.resolve(),'--plan',feature_plan)
        if selected_ids:
            run('verify_step_bundle.py','--pdf',args.output.resolve(),
                '--families-json',selected_path,'--step-files-dir',step_dir)
        if auto_screen:
            medium_rows = [row for row in json.loads(medium_candidates.read_text())
                           if row['case'] == case_name]
            medium_pdf = work/'medium-dimensions.pdf'
            if medium_rows:
                missing_medium_views = []
                for row in medium_rows:
                    family_dir = renders/row['family']
                    render_manifest = family_dir/'render_manifest.json'
                    views_ready = False
                    if render_manifest.is_file():
                        data = json.loads(render_manifest.read_text())
                        views = data.get('views', {})
                        required = ('upper-oblique', 'rear-oblique', 'top', 'bottom')
                        views_ready = (data.get('view_version') == 'upper-opposite-grid-linework-v3'
                                       and all(views.get(name, {}).get('image')
                                               and (family_dir / views[name]['image']).is_file()
                                               for name in required))
                    if not views_ready:
                        missing_medium_views.append(row['family'])
                render_families(missing_medium_views, 'medium-views')
                run('build_medium_detail_book.py','--case',case_name,
                    '--input',baseline_pdf,'--candidates',medium_candidates,
                    '--features',work/'feature-annotations-before-step.json'
                    if selected_ids else feature_plan,
                    '--geometry',analytic/'manual_geometry.json',
                    '--meshes',aligned,'--renders',renders,
                    '--step-files-dir',args.output.resolve().parent/'provided_part_files',
                    '--output',medium_pdf)
                promoted = promote_unannotated_medium(medium_candidates, medium_pdf)
                if promoted:
                    before_step_features = work/'feature-annotations-before-step.json'
                    if not before_step_features.is_file():
                        shutil.copy2(feature_plan, before_step_features)
                    selected_ids = sorted(set(selected_ids) | set(promoted))
                    selected_path = work/'difficult-families.json'
                    selected_path.write_text(json.dumps(selected_ids, indent=2))
                    promoted_path = work/'unannotated-medium-step-families.json'
                    promoted_path.write_text(json.dumps(promoted, indent=2))
                    step_dir = args.output.resolve().parent/'provided_part_files'
                    export_command = [
                        str(native_python), str(root/'export_family_steps.py'),
                        '--step', str(args.step.resolve()),
                        '--spec', str(spec),
                        '--catalog', str(source.parent/'catalog.json'),
                        '--geometry', str(analytic/'manual_geometry.json'),
                        '--features', str(before_step_features),
                        '--families-json', str(promoted_path),
                        '--output-dir', str(step_dir),
                        *freecad_flags,
                    ]
                    if not run_family_step_export(
                            export_command, work=work,
                            output=args.output.resolve(), step_dir=step_dir,
                            source_step=args.step):
                        return INVALID_EXPORTED_STEP_EXIT_CODE
                    render_families(promoted, 'unannotated-medium-step-views',
                                    step_views=True)
                    medium_rows = [row for row in json.loads(medium_candidates.read_text())
                                   if row['case'] == case_name]
            stage_output = (work/'four-tier-staging'/case_name/'environment/input/parts.pdf')
            four_tier_base = full_baseline_pdf
            four_tier_features = (work/'feature-annotations-before-step.json'
                                  if selected_ids else feature_plan)
            with pymupdf.open(four_tier_base) as base_doc:
                family_pages = {
                    page.get_text().splitlines()[0].lower(): page.number
                    for page in base_doc
                    if page.get_text().splitlines()
                    and page.get_text().splitlines()[0].lower()
                    in {family['id'] for family in families}
                }
            if set(family_pages) != {family['id'] for family in families}:
                # Some source pages contain two standard three-view families.
                # Four-tier basic/light pages need a separate nine-grid source
                # page for each family, including all four Blender views.
                missing_base_views = []
                for family in families:
                    family_dir = renders/family['id']
                    render_manifest = family_dir/'render_manifest.json'
                    views_ready = False
                    if render_manifest.is_file():
                        data = json.loads(render_manifest.read_text())
                        views = data.get('views', {})
                        required = ('upper-oblique', 'rear-oblique', 'top', 'bottom')
                        views_ready = (data.get('view_version') == 'upper-opposite-grid-linework-v3'
                                       and all(views.get(name, {}).get('image')
                                               and (family_dir / views[name]['image']).is_file()
                                               for name in required))
                    if not views_ready:
                        missing_base_views.append(family['id'])
                render_families(missing_base_views, 'four-tier-base')
                four_tier_base = work/'full_baseline_all_depth.pdf'
                run('generate_parts_pdf_six_views.py', '--spec', spec,
                    '--parts-dir', parts, '--manifest', manifest,
                    '--renders-dir', renders, '--output', four_tier_base,
                    '--product-title', args.title,
                    '--footer-label', f'{args.title} | PARTS | UNITS: mm',
                    '--all-family-depth-views')
                with pymupdf.open(four_tier_base) as base_doc:
                    family_pages = {
                        page.get_text().splitlines()[0].lower(): page.number
                        for page in base_doc
                        if page.get_text().splitlines()
                        and page.get_text().splitlines()[0].lower()
                        in {family['id'] for family in families}
                    }
                if set(family_pages) != {family['id'] for family in families}:
                    raise ValueError('All-depth base is missing family pages')
                source_features = json.loads(four_tier_features.read_text())
                four_tier_features = work/'four-tier-feature-annotations.json'
                adapted = [{**feature, 'page': family_pages[feature['family']],
                            'layout': 'nine-grid'}
                           for feature in source_features]
                four_tier_features.write_text(json.dumps(adapted))
            four_tier_flags = ['--case',case_name,'--title',args.title,'--spec',spec,
                               '--features',four_tier_features,
                               '--manifest',manifest,'--meshes',aligned,
                               '--candidates',medium_candidates,
                               '--step-files-dir',args.output.resolve().parent/'provided_part_files',
                               '--base',four_tier_base,'--renders',renders,
                               '--output',stage_output]
            if pdf_family_ids:
                four_tier_flags.extend(['--source-drawings-json', source_drawings])
            if medium_rows:
                four_tier_flags.extend(['--medium',medium_pdf])
            run('build_four_tier_parts.py',*four_tier_flags)
            if selected_ids:
                run('verify_step_bundle.py','--pdf',stage_output,
                    '--families-json',work/'difficult-families.json',
                    '--step-files-dir',stage_output.parent/'provided_part_files')
            check_public_pdf_wording(stage_output)
            run('audit_four_tier_output.py', '--pdf', stage_output,
                '--manifest', stage_output.parent.parent.parent/'author_work/four_tier_audit.json',
                '--step-files-dir', stage_output.parent/'provided_part_files',
                '--report', work/'four_tier_layout_audit.json')
            staged_pdf = args.output.resolve().with_name(args.output.name+'.four-tier-tmp')
            shutil.copy2(stage_output,staged_pdf)
            os.replace(staged_pdf,args.output.resolve())
        else:
            check_public_pdf_wording(args.output.resolve())
    args.output.chmod(0o644)
    print(f'Public PDF: {args.output.resolve()}')
    print(f'Private intermediates (do not publish): {work}')


if __name__ == '__main__':
    raise SystemExit(main())
