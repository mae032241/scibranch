"""Frozen, relocated Skill trees work without the repository Python packages."""
import json
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
from PIL import Image, ImageDraw

pytestmark = pytest.mark.process_e2e

ROOT = Path(__file__).resolve().parents[2]


def bundle(tmp_path, name, script):
    target=tmp_path/'frozen'/name
    shutil.copytree(ROOT/'skills'/name,target)
    return target/'scripts'/script


def run(script,*args,cwd):
    return subprocess.run([sys.executable,'-I',str(script),*map(str,args)],cwd=cwd,capture_output=True,text=True,timeout=30)


def test_curve_bundle_cli_scoring_and_plots_survive_relocation(tmp_path):
    script=bundle(tmp_path,'curve-analysis','curve_analysis.py')
    fixture=json.loads((ROOT/'tests/fixtures/curve_calculation_pre_support.json').read_bytes())
    data=tmp_path/'curves.json';data.write_text(fixture['sources']['curve_bundle'])
    spec=tmp_path/'spec.json';spec.write_text(json.dumps(fixture['record']['request']['comparison_spec']))
    output=tmp_path/'metrics.json';plot=tmp_path/'overview.png'
    result=run(script,'score','--bundle',data,'--spec',spec,'--output',output,'--plot',plot,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    assert json.loads(output.read_bytes())['aggregate_status']=='fail'
    assert plot.read_bytes().startswith(b'\x89PNG')
    result=run(script,'diagnose','--bundle',data,'--spec',spec,'--output-dir',tmp_path/'diagnosis',cwd=tmp_path)
    assert result.returncode==0,result.stderr
    assert (tmp_path/'diagnosis/numerics.json').exists()
    schema=tmp_path/'schema.json'
    result=run(script,'schema','--output',schema,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    assert 'comparison_spec' in json.loads(schema.read_bytes())


def test_figure_bundle_cli_rejects_tampered_family_and_unknown_identity(tmp_path):
    script=bundle(tmp_path,'scientific-paper-evidence','figure_evidence.py')
    image=Image.new('RGB',(32,32),'white');draw=ImageDraw.Draw(image)
    draw.line((3,1,8,1),fill='black');draw.line((3,15,28,15),fill='black')
    source=tmp_path/'source.png';image.save(source)
    request={'schema_version':'scidiscovery.curve-figure-digitization-request.v2',
        'figure_key':'synthetic','panel_key':'main','figure':'Local raster','citation':'Algorithm test','source':{},
        'plot_bbox':[3,3,29,29],
        'axis_calibration':{'x':{'scale':'linear','unit':'um','ticks':[[3,0],[28,25]]},
            'y':{'scale':'log10','unit':'cm^-3','ticks':[[3,100],[28,1]]}},
        'series':[{'series_key':'black','label':'black','color':'#000000','binding_source':'legend',
            'visible_label':'black','binding_bbox':[3,0,9,2],'seeds':[[3,15],[28,15]]}]}
    spec=tmp_path/'request.json';spec.write_text(json.dumps(request))
    evidence=tmp_path/'evidence'
    result=run(script,'digitize','--input',source,'--request',spec,'--output-dir',evidence,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    assert (evidence/'audit_overlays/main--identity-fidelity.png').exists()
    output=tmp_path/'normalized.json'
    result=run(script,'normalize','--input-dir',evidence,'--output',output,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    normalized=json.loads(output.read_bytes())
    assert normalized['series'][0]['availability']['status']=='available'
    assert normalized['series'][0]['y_axis']['scale']=='log10'
    table=evidence/'curve_tables/main--black.csv';table.write_bytes(table.read_bytes()+b'\n')
    rejected=tmp_path/'rejected.json'
    result=run(script,'normalize','--input-dir',evidence,'--output',rejected,cwd=tmp_path)
    assert result.returncode!=0 and not rejected.exists()
    # A missing legend color cannot create a qualified numeric curve.
    request['series'][0]['binding_bbox']=[20,0,26,2];spec.write_text(json.dumps(request))
    unresolved=tmp_path/'unresolved'
    result=run(script,'digitize','--input',source,'--request',spec,'--output-dir',unresolved,cwd=tmp_path)
    assert result.returncode==0,result.stderr
    manifest=json.loads((unresolved/'figure_manifest/evidence.json').read_bytes())
    assert manifest['status']=='unresolved'
    result=run(script,'normalize','--input-dir',unresolved,'--output',tmp_path/'unavailable.json',cwd=tmp_path)
    assert result.returncode==0,result.stderr
    assert json.loads((tmp_path/'unavailable.json').read_bytes())['series'][0]['availability']['status']=='unavailable'
