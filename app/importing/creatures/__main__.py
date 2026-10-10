"""Explicit offline catalog CLI; no Lua, production discovery, or resource writes."""
import argparse
import json
import subprocess
from pathlib import Path

from app.db.session import make_engine
from .archive import build_catalog
from .importer import import_catalog


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', required=True, type=Path)
    parser.add_argument('--repository', required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--database-url', required=True)
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--activate', action='store_true')
    args = parser.parse_args()
    if args.dry_run and args.activate:
        parser.error('--dry-run and --activate are mutually exclusive')
    # Git is read-only. Do not label a dirty working tree as a committed revision.
    command = ['git', '-C', str(args.root)]
    head = subprocess.check_output([*command, 'rev-parse', 'HEAD'], text=True).strip()
    if head != args.revision:
        parser.error('--revision must be the full checked-out commit hash')
    if subprocess.check_output([*command, 'status', '--porcelain', '--untracked-files=all'], text=True).strip():
        parser.error('source checkout must be clean, including untracked files')
    catalog = build_catalog(args.root, repository=args.repository, revision=args.revision)
    tracked = set(subprocess.check_output([*command, 'ls-files', '-z'], text=True).split('\0'))
    if set(catalog.files) - tracked:
        parser.error('all reachable source files must be tracked in the explicit revision')
    if (subprocess.check_output([*command, 'rev-parse', 'HEAD'], text=True).strip() != head or
        subprocess.check_output([*command, 'status', '--porcelain', '--untracked-files=all'], text=True).strip()):
        parser.error('source checkout changed during parsing')
    engine = make_engine(args.database_url)
    try:
        with engine.begin() as connection:
            if args.dry_run:
                connection.exec_driver_sql('SET TRANSACTION READ ONLY')
            result = import_catalog(connection, catalog, dry_run=args.dry_run, activate=args.activate)
        print(json.dumps(result, indent=2, default=str))
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
