#!/usr/bin/env python3
"""Measure explicitly identified scientific figure lines into traceable evidence."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))
import tempfile
from figure_algorithms.figure_dependencies import inspect_dependencies


def bounded(path, limit=64 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('input exceeds figure byte budget')
    return raw


def write(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    path.write_text(json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + '\n')


def family_file(root, name):
    path = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('figure evidence path escapes input directory')
    return path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-contract', type=Path, help='Exact JSON receipt previously emitted by preflight')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('preflight'); check.add_argument('--pdf', action='store_true'); check.add_argument('--output', type=Path)
    schema = sub.add_parser('schema'); schema.add_argument('--output', type=Path, required=True)
    inspect = sub.add_parser('inspect'); inspect.add_argument('--input', type=Path, required=True); inspect.add_argument('--page', type=int); inspect.add_argument('--output', type=Path, required=True)
    digitize = sub.add_parser('digitize'); digitize.add_argument('--input', type=Path, required=True); digitize.add_argument('--request', type=Path, required=True); digitize.add_argument('--output-dir', type=Path, required=True)
    normal = sub.add_parser('normalize'); normal.add_argument('--input-dir', type=Path, required=True); normal.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    source = bounded(args.input) if getattr(args, 'input', None) else None
    pdf = bool(getattr(args, 'pdf', False)) or source is not None and source.startswith(b'%PDF-')
    contract = json.loads(args.dependency_contract.read_bytes()) if args.dependency_contract else None
    versions = inspect_dependencies(pdf=pdf, contract=contract)
    if args.command == 'preflight':
        if args.output: write(args.output, versions)
        else: print(json.dumps(versions, sort_keys=True))
        return
    from figure_algorithms.common import canonical_json
    from figure_algorithms.figure_digitization_contract import FigureDigitizationRequest, materialize_figure_request
    from figure_algorithms.figure_evidence import FigureEvidenceManifest, FigureEvidenceValidationReport
    from figure_algorithms.figure_evidence_validation import build_figure_evidence_validation_report
    if args.command == 'schema':
        write(args.output, {key: model.model_json_schema() for key, model in (
            ('digitization_request', FigureDigitizationRequest), ('manifest', FigureEvidenceManifest), ('validation_report', FigureEvidenceValidationReport))})
        return
    if args.command == 'inspect':
        from figure_algorithms.figure_source import inspect_figure_source_bytes
        if pdf: media = 'application/pdf'
        else:
            import io
            from PIL import Image
            with Image.open(io.BytesIO(source)) as image: media = Image.MIME[image.format]
        images = inspect_figure_source_bytes(source, media_type=media, page=args.page)
        write(args.output, {'source_sha256': hashlib.sha256(source).hexdigest(), 'dependencies': versions,
            'images': [image.public_metadata(local_path='') for image in images]})
        return
    if args.command == 'digitize':
        from figure_algorithms.figure_digitization import build_digitized_figure_bundle
        request = materialize_figure_request(json.loads(bounded(args.request)), source)
        raw_request = canonical_json(request)
        parsed = FigureDigitizationRequest.model_validate_json(raw_request, strict=True)
        # Unknown identities/calibration remain unavailable; no measured family is published.
        parsed.require_ready()
        files, report = build_digitized_figure_bundle(source, raw_request)
        if args.output_dir.exists():
            raise ValueError('output directory already exists; choose a new evidence destination')
        args.output_dir.parent.mkdir(parents=True, exist_ok=True)
        stage = Path(tempfile.mkdtemp(prefix='.figure-evidence-', dir=args.output_dir.parent))
        try:
            for name, (raw, media) in files.items():
                target = family_file(stage, name); target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)
            (stage / 'request.json').write_bytes(raw_request)
            write(stage / 'dependencies.json', versions)
            stage.rename(args.output_dir)
        finally:
            if stage.exists(): shutil.rmtree(stage)
        print(json.dumps({'integrity_status': report['integrity_status'], 'source_status': report['source_status']}, sort_keys=True))
        return
    from figure_algorithms.figure_evidence_normalizer import normalize_figure_evidence
    root = args.input_dir
    manifest_raw = bounded(family_file(root, 'figure_manifest/evidence.json'))
    manifest = FigureEvidenceManifest.model_validate_json(manifest_raw, strict=True)
    sibling_files = {}
    for item in manifest.provenance.output_artifacts:
        sibling_files[item.data_item] = (bounded(family_file(root, item.data_item)), item.media_type)
    verified = build_figure_evidence_validation_report(manifest_data_item='figure_manifest/evidence.json', manifest_content=manifest_raw, sibling_files=sibling_files)
    stored = FigureEvidenceValidationReport.model_validate_json(bounded(family_file(root, 'validation_reports/validation_report.json')), strict=True)
    if canonical_json(verified) != canonical_json(stored.model_dump(mode='json')):
        raise ValueError('stored validation report does not reproduce from the exact figure files')
    tables = {f'curve_table__{panel.panel_key}__{series.series_key}': sibling_files[series.data_item][0]
        for panel in manifest.panels for series in panel.series}
    bundle, audit = normalize_figure_evidence(manifest_content=manifest_raw, validation_report_content=canonical_json(stored.model_dump(mode='json')), curve_tables=tables)
    write(args.output, bundle); write(str(args.output) + '.audit.json', {'series': [item.as_dict() for item in audit]})
    write(str(args.output) + '.dependencies.json', versions)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, ImportError) as error:
        print(f'scientific-paper-evidence: {error}', file=sys.stderr)
        raise SystemExit(2)
