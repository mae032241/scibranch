"""Read-only behavior card over one research-loop instance; public facts only.

Reads original jobs, meters, sealed outputs, observations and the control ledger.
It writes nothing, takes no lock and never reads hidden or official scores.
"""
from pathlib import Path
import argparse,json

MAX_NODES=64
MAX_ROWS=32
HIDDEN=('official','hidden','private_score','reward')
try:from scidiscovery.research_loop.role_share import DEFAULT as SHARE
except ImportError:SHARE=dict(version=1,min_w_share=.65,max_thinking_share=.35,thinking_node_target_tokens=250_000)


def read(path):
    path=Path(path)
    if path.is_symlink() or not path.is_file():return None
    return json.loads(path.read_text(encoding='utf-8'))


def records(root,step):
    first=root/'native-evidence'/f'{step:03d}'
    rows=[first] if (first/'native-intent.json').is_file() else []
    more=first/'continuations'
    if more.is_dir():rows+=[path for path in sorted(more.iterdir()) if (path/'native-intent.json').is_file()]
    return rows


def meter(rows):
    total=dict(tokens=0,cached=0,unknown=False)
    for row in rows:
        value=read(row/'usage.json')
        if value is None:total['unknown']=True;continue
        total['tokens']+=value['input_tokens']+value['output_tokens'];total['cached']+=value.get('cached_input_tokens',0)
    return total


def seconds(rows):
    if not rows:return None
    report=(read(rows[-1]/'native-outcome.json') or {}).get('budget_slice') or {}
    value=report.get('advance_seconds')
    return round(value,1) if type(value) in (int,float) else None


def candidates(root):
    """Public completed runs with the step that first observed them."""
    found={}
    for path in sorted((root/'host-history/observations').glob('*.json')):
        step=int(path.stem)
        for row in (read(path) or {}).get('candidates',[]):
            if any(word in key for key in row for word in HIDDEN):raise ValueError('Non-public score field in observations')
            if row.get('status')!='ok' or type(row.get('metric')) not in (int,float) or row.get('direction') not in ('max','min'):continue
            found.setdefault(row['id'],dict(id=row['id'],metric=row['metric'],direction=row['direction'],key=row.get('evaluation_key'),step=step))
    return found


def candidate_fact(pool,candidate,before=None):
    """The named candidate's observed facts, without comparing it to another."""
    row=pool.get(candidate)
    return dict(row) if row is not None and (before is None or row['step']<=before) else None


def delivered(root):
    outcome=None;stop=None
    for path in sorted((root/'control-ledger').glob('*.json')):
        event=read(path) or {}
        if event.get('kind')=='closeout_begin':stop=event.get('stop_reason')
        if event.get('kind')=='action_outcome' and (event.get('result') or {}).get('status')=='confirmed':outcome=event['result']['candidate_id']
    return outcome,stop


def review_reason(root,step):
    value=read(root/'native-evidence'/f'{step:03d}'/'result-identity.json') or {}
    review=read(root/'host-runtime/experiment-control'/f'{step:03d}'/'review.json') or {}
    return review.get('reason'),value.get('control_outcome') or {}


def replay(nodes,jobs,settings,limits):
    """Counterfactual frozen-share classification of the original sequence; only the first refusal is meaningful."""
    used=dict(H=0,A=0,E=0);rows=[];authorized=settings.get('max_tokens') or 0
    for previous,node in zip(nodes,nodes[1:]):
        for key in used:used[key]+=previous['tokens'] if previous['role']==key else 0
        role,job=node['role'],jobs[node['step']]
        if role not in ('H','A'):continue
        required=(job.get('correction_request') is not None or previous['role']=='W' and role=='A'
                  or previous['role']=='A' and previous['next_action']=='audit' and role=='A' and 'candidate_audit' in job)
        thinking=used['H']+used['A']
        reason=(None if required else 'role_repeat_without_evidence' if previous['role']==role
                else 'role_share_thinking_reached' if thinking>=limits['max_thinking_share']*authorized
                else 'role_share_w_floor_reached' if thinking+used['E']>=(1-limits['min_w_share'])*authorized else None)
        rows.append(dict(step=node['step'],role=role,dispatch='required' if required else 'optional',thinking_before=thinking,refused=reason))
    first=next((row for row in rows if row['refused']),None)
    return dict(policy=limits,authorized_tokens=authorized,first_refusal=first,
        avoidable_after_first_refusal_tokens=sum(row['tokens'] for row in nodes if first and row['step']>=first['step'] and row['role'] in ('H','A')),
        dispatches=rows[:MAX_ROWS])


def card(root):
    root=Path(root).resolve();settings=read(root/'settings.json') or {}
    pool=candidates(root);nodes=[];roles={};registered=0;fragments=0;reviews={};recommendations=[]
    observations={int(path.stem):read(path) or {} for path in (root/'host-history/observations').glob('*.json')}
    steps=sorted(int(path.stem) for path in (root/'jobs').glob('*.json'));jobs={}
    for step in steps:
        job=jobs[step]=read(root/'jobs'/f'{step:03d}.json');rows=records(root,step)
        if not rows:continue
        role=('A_closeout' if job.get('purpose')=='closeout' else job['role']);usage=meter(rows)
        science=read(rows[-1]/'science-output.json') or {}
        ending=next((value['reason'] for value in (read(row/'stage-end.json') for row in reversed(rows)) if value),None)
        outcome=read(rows[-1]/'native-outcome.json') or {}
        hint=(read(root/'project/nodes'/f'{step:03d}'/'work/budget-status.json') or {}).get('target_state')
        reason,control=review_reason(root,step)
        calls=observations.get(step,{}).get('managed_calls',[]) if job['role']=='W' else []
        focus=(job.get('search') or {}).get('focus') or {}
        entry=dict(step=step,role=role,tokens=usage['tokens'],cached=usage['cached'],seconds=seconds(rows),native_records=len(rows),
            focus=focus.get('mechanism_ref'),task_action=focus.get('action'),next_action=science.get('next_action'),
            stage_end=ending or (control.get('worker_delivery') or {}).get('reason') or control.get('stage_end_reason') or outcome.get('stop_reason'),
            registered_calls=len(calls),new_public_runs=sum(row['step']==step for row in pool.values()),soft_review=reason,hint_state=hint,
            author_correction=(job.get('author_correction') or {}).get('reason'))
        if usage['unknown']:entry['usage_unknown']=True
        nodes.append(entry)
        bucket=roles.setdefault(role,dict(tokens=0,cached=0,seconds=0.,nodes=0))
        bucket['tokens']+=usage['tokens'];bucket['cached']+=usage['cached'];bucket['nodes']+=1;bucket['seconds']+=entry['seconds'] or 0.
        registered+=len(calls)
        if job['role']=='W':fragments+=len(rows)
        for kind,value in (('review',reason),('hint',hint if hint!='within' else None),('stage_end',ending)):
            if value:reviews[kind+':'+value]=reviews.get(kind+':'+value,0)+1
        if job['role']=='A' and science.get('candidate_recommendation'):
            choice=science['candidate_recommendation']
            recommendations.append(dict(step=step,candidate=choice,closeout=job.get('purpose')=='closeout',
                at_time=candidate_fact(pool,choice,before=step)))
    total=sum(row['tokens'] for row in roles.values()) or 1;authorized=settings.get('max_tokens') or 1
    for row in roles.values():
        row.update(share_of_observed=round(row['tokens']/total,3),share_of_authorized=round(row['tokens']/authorized,3),seconds=round(row['seconds'],1))
    thinking=sum(roles.get(key,{}).get('tokens',0) for key in ('H','A','A_closeout'))
    chosen,stop=delivered(root)
    submitted=[value['actual_submission']['candidate_id'] for _,value in sorted(observations.items()) if (value.get('actual_submission') or {}).get('candidate_id')]
    return dict(scope='public_read_only_behavior_card',instance=root.parent.name if root.name=='instance' else root.name,max_tokens=settings.get('max_tokens'),max_seconds=settings.get('max_seconds'),
        role_budget_policy=settings.get('role_budget_policy'),observed_tokens=total if roles else 0,
        roles=roles,thinking_share_of_observed=round(thinking/total,3),w_share_of_observed=round(roles.get('W',{}).get('tokens',0)/total,3),
        sequence=' '.join(f"{row['role'][0]}{row['step']:03d}" for row in nodes),
        w_stages=sum(row['role']=='W' for row in nodes),w_native_records=fragments,registered_experiments=registered,
        completed_public_runs=len(pool),soft_review_reasons=reviews,
        a_recommendations=recommendations[:MAX_ROWS],a_recommendations_omitted=max(0,len(recommendations)-MAX_ROWS),
        delivered=dict(candidate=chosen,closeout_reason=stop,facts=candidate_fact(pool,chosen)),
        observed_submission_file=dict(candidate=submitted[-1],facts=candidate_fact(pool,submitted[-1]),scope='observed_file_not_delivery_confirmation') if submitted else None,
        default_policy_replay=replay([row for row in nodes if row['role']!='A_closeout'],jobs,settings,settings.get('role_budget_policy') or SHARE),
        nodes=nodes[:MAX_NODES],nodes_omitted=max(0,len(nodes)-MAX_NODES))


def text(value):
    roles=', '.join(f"{key} {row['tokens']/1e6:.2f}M ({row['share_of_observed']:.0%})" for key,row in value['roles'].items())
    recs='; '.join(f"{row['step']}:{row['candidate']} observed facts {row['at_time']}" for row in value['a_recommendations'])
    out=value['delivered']
    return '\n'.join([f"{value['instance']}: {value['observed_tokens']/1e6:.2f}M observed of {value['max_tokens']/1e6:.1f}M; {roles}",
        f"sequence {value['sequence']}; W stages {value['w_stages']}, W native records {value['w_native_records']}, registered experiments {value['registered_experiments']}",
        f"soft reviews {value['soft_review_reasons']}; A recommendations {recs or 'none'}",
        f"frozen-share replay first refusal {(lambda row:row and (row['role']+str(row['step']).zfill(3)+' '+row['refused']))(value['default_policy_replay']['first_refusal']) or 'none'}; "
        f"H/A tokens from that node {value['default_policy_replay']['avoidable_after_first_refusal_tokens']/1e6:.2f}M",
        f"delivered {out['candidate']} observed facts {out['facts']} (closeout {out['closeout_reason']}); observed submission file "
        +(f"{seen['candidate']} observed facts {seen['facts']}" if (seen:=value['observed_submission_file']) else 'none')])


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('instance',type=Path);parser.add_argument('--text',action='store_true')
    args=parser.parse_args();value=card(args.instance)
    print(text(value) if args.text else json.dumps(value,ensure_ascii=False,indent=1))


if __name__=='__main__':main()
