"""Local job assistant entry points; no experiment or grading commands."""
import argparse
import json
import os
import sys
from pathlib import Path

from .boss_cdp import BossCDP
from .config import api_key, load_project_env
from .engine import Engine
from .onboarding import DeferredReasoner
from .providers import Snapshot, load_jobs
from .reasoning import CloudReasoner
from .store import Store
from .tencent import TencentCareers


def print_json(value):
    print(json.dumps(value, ensure_ascii=False, indent=2))


def main():
    load_project_env()
    parser = argparse.ArgumentParser(description='Local conversational job assistant')
    parser.add_argument('--db', default='workspace/jobs.sqlite')
    sub = parser.add_subparsers(dest='cmd', required=True)
    sub.add_parser('doctor', help='Check local model configuration without API calls')
    imp = sub.add_parser('import', help='Import your own job JSON')
    imp.add_argument('path')
    exp = sub.add_parser('export', help='Export jobs stored in this workspace')
    exp.add_argument('path')
    for name in ('chat', 'serve'):
        p = sub.add_parser(name)
        p.add_argument('--source', choices=['boss-cdp', 'tencent', 'snapshot'], default='boss-cdp')
        p.add_argument('--mode', choices=['cloud'], default='cloud', help=argparse.SUPPRESS)
        p.add_argument('--jobs', help='Your own job JSON; required for snapshot source')
        p.add_argument('--session', default='personal')
        p.add_argument('--seconds-limit', type=int)
        p.add_argument('--token-limit', type=int)
        if name == 'chat':
            p.add_argument('message')
        else:
            p.add_argument('--port', type=int, default=7860)
    args = parser.parse_args()
    store = Store(args.db)
    if args.cmd == 'doctor':
        print_json({'model': os.getenv('JOB_AGENT_MODEL', 'deepseek-flash'),
                    'model_configured': bool(api_key(os.getenv('JOB_AGENT_BASE_URL', 'https://api.deepseek.com'))),
                    'stored_jobs': len(store.jobs()), 'note': 'No model request or login check performed'})
        return
    if args.cmd == 'import':
        jobs = load_jobs(args.path)
        for job in jobs:
            store.save_job(job)
        print_json({'imported': len(jobs)})
        return
    if args.cmd == 'export':
        path = Path(args.path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps([j.model_dump(mode='json') for j in store.jobs()], ensure_ascii=False, indent=2))
        print_json({'path': str(path), 'jobs': len(store.jobs())})
        return
    if args.source == 'snapshot':
        if not args.jobs:
            parser.error('--source snapshot requires --jobs /path/to/jobs.json')
        provider = Snapshot(load_jobs(args.jobs))
    else:
        provider = BossCDP() if args.source == 'boss-cdp' else TencentCareers()
    tokens = args.token_limit if args.token_limit is not None else (150000 if args.source == 'boss-cdp' else 30000)
    seconds = args.seconds_limit if args.seconds_limit is not None else (900 if args.source == 'boss-cdp' else 180)
    if tokens <= 0 or seconds <= 0:
        parser.error('--token-limit and --seconds-limit must be positive')
    reasoner = DeferredReasoner(store, tokens) if args.cmd == 'serve' else CloudReasoner(store, tokens)
    engine = Engine(store, provider, reasoner, seconds_limit=seconds)
    if args.cmd == 'chat':
        rid, state = engine.start(args.session, args.message)
        cp = engine.run(rid, state)
        print_json({'run_id': rid, 'status': store.run(rid)['status'],
                    'answer': cp.answer if cp else None, 'usage': reasoner.budget_store.usage(rid)})
        return
    from .ui import WorkspaceUI
    from .ui_style import CSS, theme
    restore = not any(v == '--source' or v.startswith('--source=') for v in sys.argv)
    WorkspaceUI(engine, args.session, restore_preferences=restore).build().launch(
        server_name='127.0.0.1', server_port=args.port, share=False, css=CSS, theme=theme(),
        footer_links=[], max_file_size='10mb',
        blocked_paths=[str(Path('.env').resolve()), str(Path('workspace/secrets').resolve())])


if __name__ == '__main__':
    main()
