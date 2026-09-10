from __future__ import annotations
import json, os, re, subprocess, sys, tempfile, time, uuid
from pathlib import Path
from typing import Any
from core import STACK_ROOT, VERSION, classify, classify_task, context_caps, git_root, load_json, repo_state, required_gates, save_json, shasum
from skills import select_skills


BENCHMARK_TASKS: list[dict[str, Any]] = [
    {'id': 'auth-bugfix', 'task': 'Fix incorrect 401 responses in the login rate limiter',
     'files': ['src/auth/rate_limiter.py', 'src/auth/middleware.py'], 'changed_lines': 40},
    {'id': 'payments-migration', 'task': 'Migrate the payments schema to add a refunds table',
     'files': ['migrations/0032_add_refunds.sql', 'src/payments/repository.py'], 'changed_lines': 120},
    {'id': 'ui-copy', 'task': 'Update the empty-state copy on the dashboard',
     'files': ['src/components/Dashboard/EmptyState.tsx'], 'changed_lines': 6},
    {'id': 'billing-integration', 'task': 'Add retry handling to the billing webhook worker',
     'files': ['src/workers/billing_webhook.py', 'src/services/queue.py'], 'changed_lines': 85},
    {'id': 'onboarding-design', 'task': 'Implement the new onboarding flow from Figma',
     'files': ['src/onboarding/Wizard.tsx', 'src/onboarding/steps/Welcome.tsx'], 'changed_lines': 210,
     'figma': 'https://figma.com/file/example/onboarding'},
    {'id': 'search-tickets', 'task': 'Break down the search revamp epic into tickets',
     'files': [], 'changed_lines': 0},
    {'id': 'sdk-docs', 'task': 'Upgrade the SDK using its official documentation for this API version',
     'files': ['pyproject.toml', 'src/client.py'], 'changed_lines': 55},
    {'id': 'service-observability', 'task': 'Add OpenTelemetry tracing and SLO alerting to the worker',
     'files': ['src/workers/events.py', 'src/telemetry.py'], 'changed_lines': 75},
]


BENCHMARK_CORPUS_DIR=STACK_ROOT/'templates/benchmarks/pipeline'


def run_benchmark(state:Path)->dict:
    results:list[dict[str,Any]]=[]
    for fixture in BENCHMARK_TASKS:
        scope={'files':fixture['files'],'file_count':len(fixture['files']),'changed_lines':fixture['changed_lines']}
        row={'id':fixture['id'],'task':fixture['task'],'profiles':{}}
        for profile in ('fast','standard','strict'):
            risk=classify(scope,profile); caps=context_caps(profile)
            skills=select_skills(state,fixture['task'],profile,fixture.get('figma'))
            row['profiles'][profile]={'risk':risk['risk'],'security':risk['security'],
                'task_type':classify_task(fixture['task'],fixture.get('figma')),'skills':skills,
                'context_chars':caps['context_chars'],'usage_tokens':caps['usage_tokens']}
        results.append(row)
    return {'fixtures':len(results),'results':results}


def load_pipeline_corpus(corpus_dir:Path)->list[dict]:
    return [json.loads(p.read_text()) for p in sorted(corpus_dir.glob('*.json'))]


def corpus_digest(scenarios:list[dict])->str:
    return shasum(json.dumps(scenarios,sort_keys=True))[:16]


def _bench_gate_script(spec:dict)->str:
    """A tiny synthetic validator script: prints the scripted verdict, exits nonzero on FAIL."""
    verdict={'status':spec.get('status','PASS'),'evidence':spec.get('evidence',['synthetic evidence'])}
    if spec.get('findings'): verdict['findings']=spec['findings']
    if spec.get('usage'): verdict['usage']=spec['usage']
    if spec.get('summary_markdown'): verdict['summary_markdown']=spec['summary_markdown']
    lines=[f'print({json.dumps(verdict)!r})']
    if spec.get('mutate'): lines.insert(0,"open('bench-mutation.txt','w').write('x')")
    if verdict['status']!='PASS': lines.append('import sys; sys.exit(1)')
    return '\n'.join(lines)


def run_benchmark_scenario(scenario:dict,profile:str,stack_cli:str)->dict:
    """Run one scenario end-to-end (ai plan -> ai pipeline -> ai ready) in an isolated sandbox.

    HOME/XDG_CONFIG_HOME are redirected into the sandbox and AI_GATE/AI_TASK_DIR are
    stripped, so the child cannot see or write the real user's metrics.jsonl/config —
    a benchmark run must never pollute outcome_stats()/detect_patterns()/variant_stats()
    or a live prompt experiment's sample count.
    """
    with tempfile.TemporaryDirectory(prefix='ai-bench-') as temporary_directory:
        sandbox=Path(temporary_directory); repo=sandbox/'repo'; repo.mkdir()
        home=sandbox/'home'; config=sandbox/'config'; home.mkdir(); config.mkdir()
        env={k:v for k,v in os.environ.items() if k not in ('AI_GATE','AI_TASK_DIR')}
        env.update(HOME=str(home),XDG_CONFIG_HOME=str(config))
        def git(*args):
            subprocess.run(['git',*args],cwd=repo,env=env,check=True,capture_output=True,text=True)
        git('init','-q'); git('config','user.name','bench'); git('config','user.email','bench@example.com')
        git('config','commit.gpgsign','false')
        for rel,content in (scenario.get('base_files') or {'README.md':'# bench\n'}).items():
            path=repo/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)
        git('add','.'); git('commit','-qm','base')
        for rel in scenario.get('deleted_files',[]): (repo/rel).unlink(missing_ok=True)
        for rel,content in scenario.get('change_files',{}).items():
            path=repo/rel; path.parent.mkdir(parents=True,exist_ok=True); path.write_text(content)

        def ai(*args):
            return subprocess.run([sys.executable,stack_cli,*args],cwd=repo,env=env,text=True,capture_output=True)

        plan_args=['plan',scenario['task'],'--profile',profile,'--base','HEAD']
        if scenario.get('figma'): plan_args+=['--figma',scenario['figma']]
        ai(*plan_args)
        task_dir=Path(ai('path').stdout.strip())
        plan_data=load_json(task_dir/'state/current-plan.json',{})
        if scenario.get('acceptance'):
            contract=task_dir/'contracts/current-pr.yml'
            text=re.sub(r'^acceptance:\s*\[\s*\]\s*$','acceptance: '+json.dumps(scenario['acceptance']),
                        contract.read_text(),flags=re.M)
            contract.write_text(text)

        required=required_gates(repo,plan_data) if plan_data else []
        for gate,spec in scenario.get('synthetic',{}).items():
            ai('validators','set',gate,'--',sys.executable,'-c',_bench_gate_script(spec))
        for gate in required:
            if gate not in scenario.get('synthetic',{}):
                ai('validators','set','--adapter','exit-code','--evidence','default synthetic pass',gate,'--','true')

        pipeline_result=ai('pipeline')
        ai('ready')
        readiness=load_json(task_dir/'state/readiness.json',{})
        return {
            'scenario':scenario['id'],'profile':profile,'risk':plan_data.get('risk',{}).get('risk'),
            'required_gates':required,'readiness':readiness.get('status'),
            'failed_gates':readiness.get('failed',[]),'missing_gates':readiness.get('missing_or_stale',[]),
            'pipeline_output':pipeline_result.stdout+pipeline_result.stderr,
        }


def check_benchmark_expectations(result:dict,expect:dict)->list[str]:
    violations=[]
    if 'risk' in expect and result['risk']!=expect['risk']:
        violations.append(f"risk: expected {expect['risk']}, got {result['risk']}")
    if 'required_gates_include' in expect:
        missing=[g for g in expect['required_gates_include'] if g not in result['required_gates']]
        if missing: violations.append(f"required_gates missing: {missing}")
    if 'readiness' in expect and result['readiness']!=expect['readiness']:
        violations.append(f"readiness: expected {expect['readiness']}, got {result['readiness']}")
    if 'failed_gates' in expect and sorted(result['failed_gates'])!=sorted(expect['failed_gates']):
        violations.append(f"failed_gates: expected {expect['failed_gates']}, got {result['failed_gates']}")
    if 'pipeline_message_contains' in expect and expect['pipeline_message_contains'] not in result['pipeline_output']:
        violations.append(f"pipeline output missing: {expect['pipeline_message_contains']!r}")
    return violations


def cmd_benchmark(args):
    state=repo_state(git_root())
    sub=getattr(args,'benchmark_cmd',None)
    if sub=='run':
        corpus_dir=Path(args.corpus) if args.corpus else BENCHMARK_CORPUS_DIR
        scenarios=load_pipeline_corpus(corpus_dir)
        if args.scenario: scenarios=[s for s in scenarios if s['id'] in args.scenario]
        digest=corpus_digest(scenarios)
        stack_cli=str(Path(__file__).with_name('cli.py'))
        cases=[]
        for scenario in scenarios:
            for profile in (args.profile or scenario.get('profiles',['standard'])):
                result=run_benchmark_scenario(scenario,profile,stack_cli)
                result['expectation_failures']=check_benchmark_expectations(result,scenario.get('expect',{}))
                cases.append(result)
        run_id='bench_'+uuid.uuid4().hex[:12]
        summary={'run_id':run_id,'ts':time.time(),'stack_version':VERSION,'corpus_digest':digest,
                 'scenarios':len(scenarios),'cases':len(cases),
                 'cases_passed':sum(1 for c in cases if not c['expectation_failures']),
                 'cases_failed':sum(1 for c in cases if c['expectation_failures'])}
        bench_dir=state/'benchmarks'; bench_dir.mkdir(exist_ok=True)
        save_json(bench_dir/f'{run_id}.json',{**summary,'case_results':cases})
        with (bench_dir/'index.jsonl').open('a') as f: f.write(json.dumps(summary,sort_keys=True)+"\n")
        if args.json: print(json.dumps({**summary,'case_results':cases},indent=2)); return
        print(f"Benchmark run {run_id}: {summary['cases_passed']}/{summary['cases']} case(s) passed (corpus {digest})")
        for c in cases:
            print(f"  [{'ok' if not c['expectation_failures'] else 'FAIL'}] {c['scenario']} ({c['profile']}) readiness={c['readiness']}")
            for v in c['expectation_failures']: print(f"      - {v}")
        if summary['cases_failed']: raise SystemExit(1)
        return
    if sub=='list':
        corpus_dir=Path(args.corpus) if args.corpus else BENCHMARK_CORPUS_DIR
        scenarios=load_pipeline_corpus(corpus_dir)
        digest=corpus_digest(scenarios)
        if args.json: print(json.dumps({'corpus_digest':digest,'scenarios':scenarios},indent=2)); return
        print(f"Corpus digest: {digest}")
        for s in scenarios: print(f"  {s['id']:32} {s['task']}")
        return
    if sub=='report':
        bench_dir=state/'benchmarks'
        if args.run_id:
            record=load_json(bench_dir/f'{args.run_id}.json',None)
            if not record: raise SystemExit(f'Unknown benchmark run: {args.run_id}')
        else:
            index=bench_dir/'index.jsonl'
            rows=[json.loads(line) for line in index.read_text().splitlines()] if index.exists() else []
            if not rows: raise SystemExit('No benchmark runs recorded yet.')
            record=load_json(bench_dir/f"{rows[-1]['run_id']}.json",{})
        if args.json: print(json.dumps(record,indent=2)); return
        print(f"{record['run_id']}: {record['cases_passed']}/{record['cases']} passed (corpus {record['corpus_digest']})")
        for c in record['case_results']:
            print(f"  [{'ok' if not c['expectation_failures'] else 'FAIL'}] {c['scenario']} ({c['profile']}) readiness={c['readiness']}")
        return
    if sub=='compare':
        bench_dir=state/'benchmarks'
        a=load_json(bench_dir/f'{args.run_a}.json',None); b=load_json(bench_dir/f'{args.run_b}.json',None)
        if not a or not b: raise SystemExit('Unknown benchmark run id(s).')
        if a['corpus_digest']!=b['corpus_digest']:
            raise SystemExit(f"Cannot compare: corpus digest differs ({a['corpus_digest']} vs {b['corpus_digest']}).")
        comparison={'run_a':args.run_a,'run_b':args.run_b,
                    'cases_passed':[a['cases_passed'],b['cases_passed']],
                    'cases_failed':[a['cases_failed'],b['cases_failed']]}
        print(json.dumps(comparison,indent=2)); return
    # default (no subcommand): today's routing comparison, unchanged
    report=run_benchmark(state)
    if args.json: print(json.dumps(report,indent=2)); return
    for row in report['results']:
        print(f"\n{row['id']}: {row['task']}")
        for profile,data in row['profiles'].items():
            print(f"  {profile:8} risk={data['risk']:6} security={str(data['security']):5} "
                  f"skills={','.join(data['skills']) or '(none)'} "
                  f"context_chars={data['context_chars']} usage_tokens={data['usage_tokens']}")
