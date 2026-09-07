#!/usr/bin/env python3
from __future__ import annotations
import argparse
import os
import sys

# Resolve sibling modules the same way whether this file is run as a script
# (`python ai_stack/cli.py`, bin/ai, the subprocess re-execs) or imported via the
# installed `ai` entry point (`ai_stack.cli:main`), where ai_stack/ is a package
# directory and not itself on sys.path.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import core
from benchmark import cmd_benchmark
from core import GATES, VERSION, git_root, repo_state, task_state
from crg import cmd_crg, cmd_impact, cmd_review
from gates import cmd_gate, cmd_pipeline, cmd_ready, cmd_validate, cmd_validators
from learning import cmd_confidence, cmd_failures, cmd_lessons
from lifecycle import cmd_handoff, cmd_planrun, cmd_ticket
from metrics import cmd_metrics
from prompts import cmd_prompt
from repo import cmd_doctor, cmd_init, cmd_optimize, cmd_profile, cmd_rules, cmd_status
from skills import cmd_skill
from tools import cmd_docs, cmd_figma, cmd_graph
from validators import INSTRUCTIONS


def cmd_path(args):
    print(task_state(repo_state(git_root())))


def parser():
    p=argparse.ArgumentParser(prog='ai',description='Zero-footprint AI coding orchestrator')
    p.add_argument('--version',action='version',version=VERSION)
    sp=p.add_subparsers(dest='cmd')
    sp.add_parser('init').set_defaults(func=cmd_init)
    for name in ['run','plan']:
        q=sp.add_parser(name); q.add_argument('task',nargs='?',default=''); q.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); q.add_argument('--base',default='main'); q.add_argument('--figma'); q.add_argument('--no-figma',action='store_true'); q.add_argument('--skill',action='append',default=None,help='Force a skill (repeatable; still capped by profile)'); q.add_argument('--ticket-file',help='Path to pasted ticket content; auto-fills empty acceptance criteria and Figma link')
        q.set_defaults(func=lambda a: cmd_planrun(a, a.cmd=='run'))
    tk=sp.add_parser('ticket'); tks=tk.add_subparsers(dest='ticket_cmd',required=True); tk.set_defaults(func=cmd_ticket)
    tkc=tks.add_parser('check'); tkc.add_argument('--file'); tkc.add_argument('--text'); tkc.add_argument('--json',action='store_true')
    rev=sp.add_parser('review'); rev.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); rev.add_argument('--base',default='main'); rev.add_argument('--refresh',action='store_true'); rev.add_argument('--build',action='store_true'); rev.add_argument('--no-launch',dest='launch',action='store_false',default=True); rev.set_defaults(func=cmd_review)
    imp=sp.add_parser('impact'); imp.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); imp.add_argument('--base',default='main'); imp.add_argument('--refresh',action='store_true'); imp.add_argument('--build',action='store_true'); imp.set_defaults(func=cmd_impact)
    q=sp.add_parser('ready'); q.add_argument('--no-launch',action='store_true',help=argparse.SUPPRESS); q.set_defaults(func=cmd_ready)
    gate=sp.add_parser('gate'); gate.add_argument('name',choices=GATES); gate.add_argument('--timeout',type=int,default=600); gate.add_argument('command',nargs=argparse.REMAINDER); gate.set_defaults(func=cmd_gate)
    pipeline=sp.add_parser('pipeline'); pipeline.add_argument('--dry-run',action='store_true'); pipeline.add_argument('--resume',action='store_true'); pipeline.set_defaults(func=cmd_pipeline)
    validators=sp.add_parser('validators'); vs=validators.add_subparsers(dest='action',required=True); validators.set_defaults(func=cmd_validators)
    vs.add_parser('show')
    vs.add_parser('install')
    validate=sp.add_parser('validate'); validate.add_argument('name',choices=list(INSTRUCTIONS)); validate.set_defaults(func=cmd_validate)
    remove=vs.add_parser('remove'); remove.add_argument('name',choices=GATES)
    setting=vs.add_parser('set'); setting.add_argument('name',choices=GATES)
    setting.add_argument('--adapter',choices=['json','exit-code'],default='json')
    setting.add_argument('--evidence'); setting.add_argument('--timeout',type=int,default=600)
    setting.add_argument('command',nargs=argparse.REMAINDER)
    metrics=sp.add_parser('metrics'); metrics.add_argument('--all-tasks',action='store_true'); metrics.add_argument('--json',action='store_true'); metrics.set_defaults(func=cmd_metrics)
    metrics.add_argument('--by',choices=['day','week','gate','profile','task_type','task']); metrics.add_argument('--since')
    metrics.add_argument('--top',type=int); metrics.add_argument('--format',choices=['text','json','csv'],default='text')
    metrics.add_argument('--budget',type=int)
    mcs=metrics.add_subparsers(dest='metrics_cmd')
    mprune=mcs.add_parser('prune'); mprune.add_argument('--older-than',required=True); mprune.add_argument('--confirm',action='store_true')
    bench=sp.add_parser('benchmark'); bench.add_argument('--json',action='store_true'); bench.set_defaults(func=cmd_benchmark)
    bcs=bench.add_subparsers(dest='benchmark_cmd')
    brun=bcs.add_parser('run'); brun.add_argument('--profile',action='append',choices=['fast','standard','strict'])
    brun.add_argument('--scenario',action='append'); brun.add_argument('--corpus'); brun.add_argument('--json',action='store_true')
    blist=bcs.add_parser('list'); blist.add_argument('--corpus'); blist.add_argument('--json',action='store_true')
    brep=bcs.add_parser('report'); brep.add_argument('run_id',nargs='?'); brep.add_argument('--json',action='store_true')
    bcmp=bcs.add_parser('compare'); bcmp.add_argument('run_a'); bcmp.add_argument('run_b')
    pr=sp.add_parser('profile'); pr.add_argument('--deep',action='store_true'); pr.add_argument('--refresh',action='store_true'); pr.add_argument('--timeout',type=int,default=600); pr.set_defaults(func=cmd_profile)
    conf=sp.add_parser('confidence'); conf.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); conf.add_argument('--base',default='main'); conf.add_argument('--json',action='store_true'); conf.set_defaults(func=cmd_confidence)
    prm=sp.add_parser('prompt'); pcs=prm.add_subparsers(dest='prompt_cmd',required=True); prm.set_defaults(func=cmd_prompt)
    pcs.add_parser('list')
    pshow=pcs.add_parser('show'); pshow.add_argument('name',choices=list(INSTRUCTIONS)); pshow.add_argument('variant',nargs='?',default='a')
    pexp=pcs.add_parser('experiment'); pes=pexp.add_subparsers(dest='experiment_cmd',required=True)
    pstart=pes.add_parser('start'); pstart.add_argument('name',choices=list(INSTRUCTIONS)); pstart.add_argument('--variants',required=True); pstart.add_argument('--min-samples',type=int,default=15)
    pes.add_parser('status'); pes.add_parser('stop')
    prep=pcs.add_parser('report'); prep.add_argument('--json',action='store_true')
    pprom=pcs.add_parser('promote'); pprom.add_argument('name',choices=list(INSTRUCTIONS)); pprom.add_argument('variant'); pprom.add_argument('--confirm',action='store_true')
    prst=pcs.add_parser('reset'); prst.add_argument('name',choices=list(INSTRUCTIONS))
    prb=pcs.add_parser('rollback'); prb.add_argument('name',choices=list(INSTRUCTIONS)); prb.add_argument('--confirm',action='store_true')
    phist=pcs.add_parser('history'); phist.add_argument('--slot',choices=list(INSTRUCTIONS)); phist.add_argument('--json',action='store_true')
    fail=sp.add_parser('failures'); fail.add_argument('--gate'); fail.add_argument('--min',type=int,default=2); fail.add_argument('--json',action='store_true'); fail.set_defaults(func=cmd_failures)
    fcs=fail.add_subparsers(dest='failures_cmd')
    fshow=fcs.add_parser('show'); fshow.add_argument('pattern_id')
    fcs.add_parser('rebuild'); fcs.add_parser('export')
    les=sp.add_parser('lessons'); les.add_argument('--status',choices=['candidate','confirmed','retired','rejected']); les.add_argument('--json',action='store_true'); les.set_defaults(func=cmd_lessons)
    lcs=les.add_subparsers(dest='lessons_cmd')
    lcs.add_parser('derive')
    ladd=lcs.add_parser('add'); ladd.add_argument('text'); ladd.add_argument('--scope'); ladd.add_argument('--gate')
    for name in ('confirm','reject','retire','promote'):
        sub=lcs.add_parser(name); sub.add_argument('lesson_id')
    lprune=lcs.add_parser('prune'); lprune.add_argument('--unseen-days',type=int,default=90)
    sp.add_parser('status').set_defaults(func=cmd_status); sp.add_parser('doctor').set_defaults(func=cmd_doctor); sp.add_parser('path').set_defaults(func=cmd_path); sp.add_parser('optimize').set_defaults(func=cmd_optimize)
    sk=sp.add_parser('skill'); sks=sk.add_subparsers(dest='skill_cmd'); sl=sks.add_parser('list'); sl.add_argument('--task'); sl.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); se=sks.add_parser('explain'); se.add_argument('name'); sen=sks.add_parser('enable'); sen.add_argument('name'); sdis=sks.add_parser('disable'); sdis.add_argument('name'); sd=sks.add_parser('dry-run'); sd.add_argument('name'); sd.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); sk.set_defaults(func=cmd_skill)
    ho=sp.add_parser('handoff'); ho.add_argument('task',nargs='?',default=''); ho.add_argument('--profile',choices=['fast','standard','strict'],default='standard'); ho.add_argument('--base',default='main'); ho.add_argument('--state'); ho.add_argument('--evidence',action='append'); ho.add_argument('--next'); ho.set_defaults(func=cmd_handoff)
    r=sp.add_parser('rules'); rs=r.add_subparsers(dest='rules_cmd'); rs.add_parser('list'); a=rs.add_parser('add'); a.add_argument('rule'); a.add_argument('--scope',default='**'); rm=rs.add_parser('remove'); rm.add_argument('index',type=int); r.set_defaults(func=cmd_rules)
    d=sp.add_parser('docs'); ds=d.add_subparsers(dest='docs_cmd',required=True); ds.add_parser('doctor'); setup=ds.add_parser('setup'); setup.add_argument('--mcp',action='store_true'); setup.add_argument('--universal',action='store_true'); ds.add_parser('detect'); l=ds.add_parser('library'); l.add_argument('name'); l.add_argument('query',nargs='?',default=''); q=ds.add_parser('query'); q.add_argument('library'); q.add_argument('query'); q.add_argument('--refresh',action='store_true'); d.set_defaults(func=cmd_docs)
    f=sp.add_parser('figma'); fs=f.add_subparsers(dest='figma_cmd',required=True); fs.add_parser('doctor'); fsetup=fs.add_parser('setup'); fsetup.add_argument('--claude-only',action='store_true'); fsetup.add_argument('--codex-only',action='store_true'); f.set_defaults(func=cmd_figma)
    c=sp.add_parser('crg'); cs=c.add_subparsers(dest='crg_cmd',required=True); cs.add_parser('doctor'); cs.add_parser('build'); st=cs.add_parser('status'); up=cs.add_parser('update'); up.add_argument('--base',default='main'); up.add_argument('--brief',action='store_true',default=True); de=cs.add_parser('detect'); de.add_argument('--base',default='main'); de.add_argument('--brief',action='store_true',default=True); c.set_defaults(func=cmd_crg)
    g=sp.add_parser('graph'); gs=g.add_subparsers(dest='graph_cmd',required=True); gs.add_parser('doctor'); gs.add_parser('build'); gs.add_parser('sync'); q=gs.add_parser('query'); q.add_argument('query'); pa=gs.add_parser('path'); pa.add_argument('start'); pa.add_argument('end'); e=gs.add_parser('explain'); e.add_argument('node'); g.set_defaults(func=cmd_graph)
    for command in sp.choices.values():
        command.add_argument('--task-id',help='Task identity; defaults to AI_TASK_ID or current branch/worktree')
    return p


def main():
    p=parser(); args=p.parse_args()
    core.TASK_ID=getattr(args,'task_id',None)
    if not args.cmd: p.print_help(); return
    args.func(args)


if __name__=='__main__':
    main()
