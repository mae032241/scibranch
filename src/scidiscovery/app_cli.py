"""Small application entry point; scientific commands retain their own CLI."""
from __future__ import annotations

import argparse
import sys

from .research_loop import web_service


def main(argv=None):
    parser = argparse.ArgumentParser(prog='scid')
    commands = parser.add_subparsers(dest='command', required=True)
    web = commands.add_parser('web', help='open the persistent local research workbench')
    web.add_argument('action', choices=('start', 'status', 'stop'), nargs='?', default='start')
    web.add_argument('--foreground', action='store_true', help='serve in this terminal')
    web.add_argument('--workspace', help='initial workspace (default: XDG data directory)')
    web.add_argument('--port', type=int, default=None, help='loopback port (default: an available port)')
    web.add_argument('--state-dir', help='private service state directory')
    args = parser.parse_args(argv)
    if args.port is not None and not 0 <= args.port <= 65535:
        parser.error('--port must be between 0 and 65535')
    if args.action != 'start' and (args.foreground or args.workspace is not None or args.port is not None):
        parser.error('--foreground, --workspace and --port apply only when starting')
    try:
        if args.action == 'status':
            result = web_service.status(state_dir=args.state_dir)
            print(result['url'] if result['status'] == 'running' else 'Web service is stopped.')
        elif args.action == 'stop':
            web_service.stop(state_dir=args.state_dir)
            print('Web service is stopped. Existing research continues independently.')
        else:
            result = web_service.start(workspace=args.workspace, port=args.port,
                state_dir=args.state_dir, foreground=args.foreground)
            if result.get('url'):
                print(result['url'], flush=True)
        return 0
    except (OSError, RuntimeError, ValueError) as error:
        print('scid web: ' + str(error), file=sys.stderr)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
