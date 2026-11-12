#!/usr/bin/env python3
"""Serve one independent Terminal or App Consultant from deployment configuration."""
import argparse
from pathlib import Path
import copy
import uuid
import uvicorn


def log_config():
    """uvicorn's logging with the date and time on every line, so a log or bug report shows when each thing happened.

    Application warnings (the root logger) use the same format instead of Python's bare fallback.
    """
    from uvicorn.config import LOGGING_CONFIG
    config = copy.deepcopy(LOGGING_CONFIG)
    for formatter in config['formatters'].values():
        formatter['fmt'] = '%(asctime)s ' + formatter['fmt']
        formatter['datefmt'] = '%Y-%m-%d %H:%M:%S%z'
    config['root'] = {'handlers': ['default'], 'level': 'WARNING'}
    return config

if __name__ == '__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--kind',choices=['data-terminal','app-consultant'],required=True)
    parser.add_argument('--id',required=True)
    parser.add_argument('--port',type=int,required=True)
    parser.add_argument('--web-dir',help='Captured UI release for this instance; source edits leave other running views unchanged.')
    args=parser.parse_args()
    if not args.web_dir:
        from attas.data_terminal_phemar.instance_assets import capture_ui
        release=Path.home()/'.local/state/finmas/terminal-ui-releases'/args.id/uuid.uuid4().hex
        args.web_dir=str(capture_ui(release))
    from attas.data_terminal_phemar.runtime import create_app
    uvicorn.run(create_app(args.kind,args.id,args.port,web_dir=args.web_dir),host='127.0.0.1',port=args.port,timeout_graceful_shutdown=20,log_config=log_config())
