"""Explicitly publish GAP_ANALYSIS.md as GitHub milestones/issues (requires gh auth)."""
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
REPO = 'ramshetty01/Codine-Harness'
MILESTONES = [
 ('Representative tasks','Five realistic repository tasks with independent final grading and reproducible manifests; preserve every attempt.'),
 ('Sandboxed repository execution','Real tests/builds work inside the boundary; access/cancellation/cleanup tests pass; no host fallback.'),
 ('Long-task reliability','A task survives compaction and interruption without losing requirements or replaying actions.'),
 ('Daily terminal workflow','A user can steer work, inspect outputs/diffs, switch/resume sessions, and recover a related change set.'),
 ('Project customization','A trusted skill and extension load/reload through documented interfaces with failure isolation.'),
 ('External integrations','One MCP integration and one SDK/RPC client work with explicit permissions and cost accounting.'),
 ('Isolated parallel work','Two workers run without file conflicts or independent budget resets; cancellation reaches both.'),
 ('Distribution and optional products','Ship surfaces justified by usage; maintain installation, compatibility and recovery checks. Optional product integrations require concrete acceptance scope before implementation.'),
]
GROUPS = {
 1:'Q1 Q2 Q3 Q4 Q5 R1',
 2:'E1 E2 E3 E4 E5 E6 E7 E8',
 3:'C1 C2 C3 C4 C5 C6 A3 A4 A5 Q6 Q7 Q8 Q9',
 4:'A1 A2 R2 R3 R4 R5 R6 R7 R8 U1 U2 U3 U4 U5 S1 S2 S3 S4 M1 M2 M3 M4 M5',
 5:'I1 I2 I3 I4 I5 I6 C7 X1 X2 X3',
 6:'X4 X5 X6 X8 X9 M6 M7 M8',
 7:'P1 P2 P3 P4 S5 S6 S7 A6',
 8:'D1 D2 D3 D4 D5 D6 D7 D8 D9 P5 P6 U6 U7 U8 X7',
}
DEPENDENCIES = {'E1':['E2','E3'],'E3':['E2'],'E4':['E3'],'E5':['E1'],
 'C2':['C1'],'C3':['C2'],'C5':['C1'],'X2':['X1'],'X3':['X1','I3'],
 'X4':['I3'],'X5':['X1','I3'],'X8':['X4'],'X9':['X8'],
 'P1':['P2'],'P3':['P2'],'P4':['P1','P3'],'D4':['X6'],'D2':['D1'],
 'S4':['R4'],'Q4':['Q1','Q2','Q3'],'M8':['M2','M5']}
ACCEPTANCE = {
 'Q3':['Hash the task specification, relevant harness source, execution image and request configuration in evaluation reports.', 'Offline and live reports identify provenance without exposing credentials or reference patches to the model.', 'Tests prove hashes are deterministic, change when relevant inputs change, and ignore credentials/generated state.'],
 'E2':['Stage explicitly selected source, configuration and test files for repository execution.', 'Reject traversal, credential/internal/generated paths, symlinks and special files; enforce byte/file limits.', 'Boundary tests prove only declared content is staged, without mounting the host repository or silently executing on the host.'],
 'C1':['Use a documented model-aware context/token estimate and reserve configured output space in the actual request path.', 'Keep the existing character bound and valid tool/result groups; retain the full archive and opaque reasoning.', 'Offline tests cover boundary fits, output reservation, unknown models and invalid settings without claiming an approximate count is exact.'],
 'D1':['Build an installable wheel with a codine console entry point, declared dependencies and required resources.', 'Expose version and document installation/upgrade/uninstallation while retaining checkout development.', 'Verify a clean isolated install runs --version and --check from outside the checkout with no inference, and does not write user state into site-packages.'],
}


def gh(endpoint, method='GET', data=None):
    command=['gh','api',endpoint,'--method',method]
    if data is not None: command += ['--input','-']
    run=subprocess.run(command,input=json.dumps(data) if data is not None else None,text=True,capture_output=True)
    if run.returncode:
        raise RuntimeError(run.stderr.strip())
    return json.loads(run.stdout) if run.stdout.strip() else None


def pages(endpoint):
    result=[]
    for page in range(1,100):
        separator='&' if '?' in endpoint else '?'
        batch=gh(f'{endpoint}{separator}per_page=100&page={page}')
        result.extend(batch)
        if len(batch)<100: return result
    raise RuntimeError('Unexpectedly large issue list.')


def main():
    if '--publish' not in sys.argv:
        raise SystemExit('Use --publish to explicitly create GitHub milestones/issues.')
    if gh('user')['login'] != 'ramshetty01':
        raise SystemExit('Active GitHub account must be ramshetty01.')
    rows=[]
    for line in (ROOT/'docs/GAP_ANALYSIS.md').read_text().splitlines():
        parts=[part.strip() for part in line.strip('|').split('|')]
        if len(parts)>=3 and re.fullmatch(r'[A-Z]\d+',parts[0]) and parts[1] in ('P0','P1','P2','P3'):
            rows.append({'id':parts[0],'priority':parts[1],'scope':parts[-1],
                         'current':parts[2] if len(parts)==4 else None})
    membership={ident:group for group,ids in GROUPS.items() for ident in ids.split()}
    if {row['id'] for row in rows} != membership.keys():
        raise RuntimeError('Every backlog item must belong to exactly one milestone.')
    milestones={row['title']:row for row in pages(f'repos/{REPO}/milestones?state=all')}
    assigned={}
    for number,(title,gate) in enumerate(MILESTONES,1):
        title=f'M{number}: {title}'
        item=milestones.get(title) or gh(f'repos/{REPO}/milestones','POST',{'title':title,'description':gate+'\n\nTrack scope in docs/GAP_ANALYSIS.md. Close only after the acceptance gate is verified.'})
        assigned[number]=item
        print('Milestone:',title,flush=True)
    existing={row['title'].split(']')[0].lstrip('['):row for row in pages(f'repos/{REPO}/issues?state=all') if row['title'].startswith('[') and 'pull_request' not in row}
    index=[]
    for row in rows:
        ident=row['id']; group=membership[ident]
        criteria=ACCEPTANCE.get(ident,[f"Deliver the documented behavior: {row['scope']}", 'Add meaningful acceptance/regression coverage for normal operation and relevant failure/boundary cases.', 'Document the user-facing interface, effective limits and recovery behavior; preserve existing permissions, archives and budget controls.'])
        body=f"## Scope\n\n{row['scope']}\n\n"
        if row['current']: body+=f"Current state: {row['current']}\n\n"
        body+=f"Roadmap ID: **{ident}** · Priority: **{row['priority']}** · Milestone: **M{group}**\n\n"
        body+='Source: [Codine gap analysis](https://github.com/'+REPO+'/blob/main/docs/GAP_ANALYSIS.md).\n\n## Acceptance criteria\n\n'
        body+='\n'.join('- [ ] '+criterion for criterion in criteria)
        body+='\n\n## Dependencies and verification\n\n'
        dependencies=DEPENDENCIES.get(ident,[])
        body+=('Depends on roadmap items: '+', '.join(dependencies)+'.' if dependencies else 'No hard issue dependency identified; coordinate interfaces with related milestone work.')
        body+='\n\nRecord the implementation commit(s), exact checks, observed outcomes and remaining limitations before closing. Offline checks first; live requests must use existing bounded evaluation controls. Preserve unrelated user changes and never commit credentials or local reports.\n'
        title=f"[{ident}] {row['scope']}"[:240]
        item=existing.get(ident)
        if item is None:
            item=gh(f'repos/{REPO}/issues','POST',{'title':title,'body':body,'milestone':assigned[group]['number'],'assignees':['ramshetty01']})
        index.append({**row,'milestone':group,'number':item['number'],'url':item['html_url']})
        print(f"{ident}: #{item['number']}",flush=True)
        cache=ROOT/'.codine/roadmap-sync.json'
        cache.parent.mkdir(parents=True,exist_ok=True)
        cache.write_text(json.dumps(index,indent=2)+'\n')
    lines=['# GitHub implementation backlog','',f'Repository: https://github.com/{REPO}', '', 'Created and assigned through the `ramshetty01` account. Issue closure requires implementation evidence. Milestones remain open until their acceptance gates pass.','', '| Roadmap | Priority | Milestone | Issue |','|---|---|---|---|']
    lines += [f"| {r['id']} | {r['priority']} | M{r['milestone']} | [#{r['number']}: {r['scope']}]({r['url']}) |" for r in index]
    lines+=['','## First parallel batch','','- Q3: evaluation provenance (main checkout).','- E2: controlled repository staging (isolated worktree).','- C1: model-aware context budgets (isolated worktree).','- D1: installable package and CLI (isolated worktree).','','D1 is pulled forward because reproducible installation supports the other milestones. Four workstreams means the coordinator plus three isolated implementation agents. Later work is selected by dependency readiness, not by closing incomplete issues.','']
    (ROOT/'docs/ISSUES.md').write_text('\n'.join(lines))
    print(f'Published {len(index)} issues across {len(assigned)} milestones.',flush=True)


if __name__=='__main__': main()
