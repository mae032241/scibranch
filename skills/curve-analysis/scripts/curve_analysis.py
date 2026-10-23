#!/usr/bin/env python3
"""Deterministic curve normalization, comparison and residual plots."""
from __future__ import annotations
import argparse
import importlib.metadata
import json
from pathlib import Path
import sys
sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parent))


def preflight(*, plots=False, contract=None):
    packages = ["pydantic"] + (["Pillow"] if plots else [])
    versions = {"python_packages": {}}
    for package in packages:
        try:
            versions["python_packages"][package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError as error:
            raise RuntimeError(f"missing dependency: {package}; see this Skill's requirements.txt") from error
    if versions["python_packages"]["pydantic"].split('.')[0] != '2':
        raise RuntimeError("Pydantic major version 2 is required")
    if plots and int(versions["python_packages"]["Pillow"].split('.')[0]) < 10:
        raise RuntimeError("Pillow version 10 or newer is required")
    if contract is not None and contract != versions:
        raise RuntimeError("runtime dependencies differ from the supplied exact version contract")
    return versions


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if hasattr(value, 'model_dump'):
        value = value.model_dump(mode='json')
    path.write_text(json.dumps(value, allow_nan=False, sort_keys=True, indent=2) + '\n')


def bounded(path, limit=16 * 1024 * 1024):
    with Path(path).open('rb') as stream:
        raw = stream.read(limit + 1)
    if len(raw) > limit:
        raise ValueError('input_byte_limit')
    return raw


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--dependency-contract', type=Path, help='Exact JSON receipt previously emitted by preflight')
    sub = parser.add_subparsers(dest='command', required=True)
    check = sub.add_parser('preflight'); check.add_argument('--plots', action='store_true'); check.add_argument('--output', type=Path)
    schema = sub.add_parser('schema'); schema.add_argument('--output', type=Path, required=True)
    normal = sub.add_parser('normalize')
    normal.add_argument('--format', choices=('csv', 'sprocess-log', 'plx'), required=True)
    normal.add_argument('--input', type=Path, required=True); normal.add_argument('--spec', type=Path, required=True); normal.add_argument('--output', type=Path, required=True)
    for command in ('score', 'diagnose'):
        job = sub.add_parser(command)
        job.add_argument('--bundle', type=Path, required=True); job.add_argument('--spec', type=Path, required=True)
        if command == 'score':
            job.add_argument('--output', type=Path, required=True); job.add_argument('--plot', type=Path)
        else:
            job.add_argument('--output-dir', type=Path, required=True); job.add_argument('--comparison-key')
    args = parser.parse_args()
    contract = json.loads(args.dependency_contract.read_bytes()) if args.dependency_contract else None
    versions = preflight(plots=args.command == 'diagnose' or bool(getattr(args, 'plot', None)) or bool(getattr(args, 'plots', False)), contract=contract)
    if args.command == 'preflight':
        if args.output: write(args.output, versions)
        else: print(json.dumps(versions, sort_keys=True))
        return
    from curve_algorithms.common import canonical_json
    from curve_algorithms.schema import CurveBundle, CurveComparisonSpec, evaluate_curve_consistency, validate_curve_comparison_declaration_contract
    from curve_algorithms.sources import CSVSource, csv_series, MAX_POINTS
    from curve_algorithms.curve_normalizer import SProcessLogSourceSpec, normalize_sprocess_log
    from curve_algorithms.plx_normalizer import SProcessPLXSourceSpec, normalize_sprocess_plx
    if args.command == 'schema':
        write(args.output, {name: model.model_json_schema() for name, model in (
            ('curve_bundle', CurveBundle), ('comparison_spec', CurveComparisonSpec),
            ('csv_source', CSVSource), ('sprocess_log_source', SProcessLogSourceSpec), ('plx_source', SProcessPLXSourceSpec))})
        return
    if args.command == 'normalize':
        import hashlib
        raw, specification = bounded(args.input), bounded(args.spec)
        if args.format == 'csv':
            mapping = CSVSource.model_validate_json(specification, strict=True)
            bundle = CurveBundle(source_profile='analysis_inputs', source_digests=(hashlib.sha256(raw).hexdigest(),), series=(csv_series(mapping, raw),))
            audit = {'input_sha256': hashlib.sha256(raw).hexdigest(), 'input_bytes': len(raw), 'point_count': len(bundle.series[0].points)}
        elif args.format == 'sprocess-log':
            bundle, audit = normalize_sprocess_log(raw, SProcessLogSourceSpec.model_validate_json(specification, strict=True))
        else:
            bundle, audit = normalize_sprocess_plx(raw, SProcessPLXSourceSpec.model_validate_json(specification, strict=True), source_name=args.input.name)
        write(args.output, bundle); write(str(args.output) + '.audit.json', audit)
        write(str(args.output) + '.dependencies.json', versions)
        return
    bundle = CurveBundle.model_validate_json(bounded(args.bundle), strict=True)
    spec = CurveComparisonSpec.model_validate_json(bounded(args.spec), strict=True)
    validate_curve_comparison_declaration_contract(spec)
    if sum(len(item.points) for item in bundle.series) > MAX_POINTS:
        raise ValueError('source_point_limit')
    if len(spec.comparisons) > 16 or any(len(c.operators) > 16 or c.evaluation_points > 4096 for c in spec.comparisons):
        raise ValueError('comparison_work_limit')
    by_key = {s.series_key: s for s in bundle.series}
    work = sum(c.evaluation_points * len(c.operators) for c in spec.comparisons)
    interpolation_work = sum(c.evaluation_points * len(c.operators) * sum(len(by_key[k].points) for k in (c.reference_series,c.candidate_series) if k in by_key) for c in spec.comparisons)
    if work > 65536 or interpolation_work > 2_000_000:
        raise ValueError('comparison_work_limit')
    report = evaluate_curve_consistency(bundle, spec)
    if args.command == 'score':
        write(args.output, report); write(str(args.output) + '.dependencies.json', versions)
        if args.plot:
            from curve_algorithms.plotter import render_curve_comparison_overview
            args.plot.parent.mkdir(parents=True, exist_ok=True)
            args.plot.write_bytes(render_curve_comparison_overview(bundle, spec, report).content)
    else:
        from curve_algorithms.analysis import compute_curve_error, render_curve_error
        numerics = compute_curve_error(bundle, spec, report, comparison_key=args.comparison_key)
        write(args.output_dir / 'numerics.json', numerics)
        artifacts = render_curve_error(numerics)
        write(args.output_dir / 'report.json', artifacts.report)
        write(args.output_dir / 'dependencies.json', versions)
        for name, raw in artifacts.plots:
            target = args.output_dir / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(raw)


if __name__ == '__main__':
    try:
        main()
    except (ValueError, RuntimeError, OSError, ImportError) as error:
        print(f'curve-analysis: {error}', file=sys.stderr)
        raise SystemExit(2)
