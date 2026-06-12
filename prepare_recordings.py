#!/usr/bin/env python3
"""
Pre-processa recordings.csv: remove gravacoes ja presentes nos discos e divide por disco.

Saidas:
  recordings_YYYYMMDD.csv
  recordings_YYYYMMDD_disco0.csv, _disco1.csv, _disco2.csv
  prepare_report.json
"""
import argparse
import datetime
import json
import os
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

import recording_common as rc
from recording_paths import (
    find_existing_recording_file,
    is_usable_file,
    legacy_basenames_for_row,
    parse_hash_suffix_from_basename,
    parse_recording_id_from_basename,
)

load_dotenv(Path('.env'))

SCRIPT_DIR = Path(__file__).resolve().parent


def write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
    os.replace(tmp_path, path)


def build_csv_indexes(rows):
    id_to_row = {}
    account_to_rows = defaultdict(list)
    account_legacy_index = defaultdict(dict)

    for row in rows:
        recording_id = row[0]
        host_email = row[1]
        topic = row[2] if len(row) > 2 else ''
        time_recorded = row[3] if len(row) > 3 else ''

        id_to_row[recording_id] = row
        account_to_rows[host_email].append(row)

        for basename in legacy_basenames_for_row(recording_id, topic, time_recorded):
            account_legacy_index[host_email][basename] = recording_id

    account_prefix_index = defaultdict(lambda: defaultdict(list))
    for host_email, account_rows in account_to_rows.items():
        for row in account_rows:
            account_prefix_index[host_email][row[0][:8].lower()].append(row[0])

    return id_to_row, account_to_rows, account_legacy_index, account_prefix_index


def scan_disk_files(download_dirs):
    files = []
    for base_dir in download_dirs:
        if not os.path.isdir(base_dir):
            continue
        for account_name in os.listdir(base_dir):
            account_dir = os.path.join(base_dir, account_name)
            if not os.path.isdir(account_dir):
                continue
            for entry in os.listdir(account_dir):
                if not entry.lower().endswith('.mp4') or entry.endswith('.part'):
                    continue
                full_path = os.path.join(account_dir, entry)
                if is_usable_file(full_path):
                    files.append({
                        'path': full_path,
                        'basename': entry,
                        'account_dir': account_dir,
                        'account_name': account_name,
                        'size': os.path.getsize(full_path),
                    })
    return files


def resolve_account_email(file_info, account_to_rows):
    account_name = file_info['account_name']
    if account_name in account_to_rows:
        return account_name
    for host_email in account_to_rows:
        if rc.safe_account_dir(host_email) == account_name:
            return host_email
    return account_name


def match_file_to_recording_id(file_info, account_email, indexes):
    _, account_legacy_index, account_prefix_index = indexes[1], indexes[2], indexes[3]
    basename = file_info['basename']

    direct_id = parse_recording_id_from_basename(basename)
    if direct_id:
        return direct_id, 'canonical_name'

    hash_prefix = parse_hash_suffix_from_basename(basename)
    if hash_prefix:
        candidates = account_prefix_index[account_email].get(hash_prefix, [])
        if len(candidates) == 1:
            return candidates[0], 'hash_suffix'
        if len(candidates) > 1:
            return None, f'ambiguous_hash_suffix:{hash_prefix}'

    legacy_match = account_legacy_index[account_email].get(basename)
    if legacy_match:
        return legacy_match, 'legacy_basename'

    return None, 'unmatched'


def collect_downloaded_ids(rows, download_dirs):
    indexes = build_csv_indexes(rows)
    id_to_row = indexes[0]
    downloaded_ids = set()
    manual_review = []
    unmatched_on_disk = []

    for file_info in scan_disk_files(download_dirs):
        account_email = resolve_account_email(file_info, indexes[1])
        recording_id, match_method = match_file_to_recording_id(file_info, account_email, indexes)

        if match_method.startswith('ambiguous_hash_suffix'):
            manual_review.append({
                'reason': match_method,
                'path': file_info['path'],
                'account': account_email,
            })
            continue

        if recording_id and recording_id in id_to_row:
            downloaded_ids.add(recording_id)
        else:
            unmatched_on_disk.append({
                'path': file_info['path'],
                'account': account_email,
                'match_method': match_method,
            })

    for recording_id, row in id_to_row.items():
        if recording_id in downloaded_ids:
            continue
        host_email = row[1]
        topic = row[2] if len(row) > 2 else ''
        time_recorded = row[3] if len(row) > 3 else ''
        account_dir = rc.resolve_download_dir(host_email, download_dirs)
        existing = find_existing_recording_file(
            recording_id,
            account_dir,
            topic=topic,
            time_recorded=time_recorded,
        )
        if existing:
            downloaded_ids.add(recording_id)

    return downloaded_ids, manual_review, unmatched_on_disk


def split_rows_by_disk(pending_rows, download_dirs):
    by_disk = {index: [] for index in range(len(download_dirs))}
    for row in pending_rows:
        host_email = row[1]
        disk_index = rc.disk_index_for_account(host_email, len(download_dirs))
        by_disk[disk_index].append(row)
    return by_disk


def prepare(csv_path, download_dirs, output_dir, date_stamp=None):
    csv_path = Path(csv_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if date_stamp is None:
        date_stamp = datetime.datetime.now().strftime('%Y%m%d')

    rows = rc.load_recording_rows(csv_path)
    downloaded_ids, manual_review, unmatched_on_disk = collect_downloaded_ids(rows, download_dirs)

    pending_rows = [row for row in rows if row[0] not in downloaded_ids]
    downloaded_rows = [row for row in rows if row[0] in downloaded_ids]
    by_disk = split_rows_by_disk(pending_rows, download_dirs)

    dated_csv = output_dir / f'recordings_{date_stamp}.csv'
    rc.write_recording_csv(dated_csv, pending_rows)

    disk_csv_paths = {}
    for disk_index in range(len(download_dirs)):
        disk_csv = output_dir / f'recordings_{date_stamp}_disco{disk_index}.csv'
        rc.write_recording_csv(disk_csv, by_disk[disk_index])
        disk_csv_paths[disk_index] = str(disk_csv)

    per_disk_counts = {
        str(disk_index): len(by_disk[disk_index]) for disk_index in range(len(download_dirs))
    }

    report = {
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'date_stamp': date_stamp,
        'csv_path': str(csv_path),
        'download_dirs': download_dirs,
        'total_csv_rows': len(rows),
        'downloaded_count': len(downloaded_rows),
        'pending_count': len(pending_rows),
        'per_disk_pending': per_disk_counts,
        'outputs': {
            'pending_csv': str(dated_csv),
            'disk_csvs': disk_csv_paths,
        },
        'manual_review_count': len(manual_review),
        'manual_review': manual_review,
        'unmatched_on_disk_count': len(unmatched_on_disk),
        'unmatched_on_disk': unmatched_on_disk,
    }

    report_path = output_dir / 'prepare_report.json'
    write_json_atomic(report_path, report)

    print(f"Data: {date_stamp}")
    print(f"Total no CSV: {report['total_csv_rows']}")
    print(f"Ja baixadas (filtradas): {report['downloaded_count']}")
    print(f"Pendentes: {report['pending_count']}")
    for disk_index, download_dir in enumerate(download_dirs):
        print(f"  Disco {disk_index} ({download_dir}): {per_disk_counts[str(disk_index)]} pendentes")
    print(f"CSV pendentes: {dated_csv}")
    for disk_index, path in disk_csv_paths.items():
        print(f"CSV disco {disk_index}: {path}")
    print(f"Relatorio: {report_path}")
    if manual_review:
        print(f"Aviso: {len(manual_review)} caso(s) para revisao manual (hash ambiguo)")
    if not pending_rows:
        print("Aviso: nenhuma gravacao pendente apos o filtro.")

    return report


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            'Filtra recordings.csv removendo gravacoes ja presentes nos discos '
            'e divide o resultado em um CSV por disco.'
        ),
    )
    parser.add_argument(
        '--csv',
        default=str(rc.get_csv_path_from_env()),
        help='Caminho para recordings.csv original',
    )
    parser.add_argument(
        '--dirs',
        default=','.join(rc.get_download_dirs_from_env()),
        help='Diretorios base dos discos, separados por virgula',
    )
    parser.add_argument(
        '--output-dir',
        default=str(SCRIPT_DIR),
        help='Diretorio para gravar CSVs de saida e prepare_report.json',
    )
    parser.add_argument(
        '--date',
        help='Sufixo de data para os arquivos de saida (padrao: YYYYMMDD de hoje)',
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    download_dirs = rc.parse_download_dirs(args.dirs)

    if not Path(args.csv).exists():
        print(f"Arquivo CSV nao encontrado: {args.csv}", file=sys.stderr)
        sys.exit(1)

    if len(download_dirs) < 1:
        print("Informe ao menos um diretorio em --dirs.", file=sys.stderr)
        sys.exit(1)

    for download_dir in download_dirs:
        if not os.path.isdir(download_dir):
            print(f"Aviso: diretorio nao encontrado (sera ignorado na varredura): {download_dir}")

    prepare(
        csv_path=args.csv,
        download_dirs=download_dirs,
        output_dir=args.output_dir,
        date_stamp=args.date,
    )


if __name__ == '__main__':
    main()
