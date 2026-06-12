#!/usr/bin/env python3
"""
Reconcilia gravacoes ja baixadas nos discos com recordings.csv.

Sequencia operacional recomendada no servidor:
  1. Parar run_parallel_downloads.sh e todos os workers Python
  2. Backup: cp recordings.csv recordings_full_backup_$(date +%Y%m%d_%H%M%S).csv
             cp download_state.json download_state_backup_$(date +%Y%m%d_%H%M%S).json  # se existir
  3. Dry-run:  python reconcile_downloads.py --csv ../recordings.csv --dirs "disco1,disco2,disco3"
  4. Revisar:  reconcile_report.json e reconcile_report.log
  5. Aplicar:  python reconcile_downloads.py --csv ../recordings.csv --dirs "..." --apply
  6. Retomar:  export RECORDINGS_CSV=/caminho/recordings_pending.csv
               ./run_parallel_downloads.sh
"""
import argparse
import csv
import datetime
import json
import os
import shutil
import sys
from collections import defaultdict
from pathlib import Path

from dotenv import load_dotenv

import download_recordings as dr
from list_recordings import CSV_COLUMNS
from recording_paths import (
    canonical_recording_path,
    find_existing_recording_file,
    is_usable_file,
    legacy_basenames_for_row,
    parse_hash_suffix_from_basename,
    parse_recording_id_from_basename,
)

load_dotenv(Path('.env'))

SCRIPT_DIR = Path(__file__).resolve().parent
STATE_VERSION = dr.STATE_VERSION


def write_json_atomic(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + '.tmp')
    with open(tmp_path, 'w', encoding='utf-8') as handle:
        json.dump(data, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
    os.replace(tmp_path, path)


def backup_file(path, label):
    path = Path(path)
    if not path.exists():
        return None
    timestamp = datetime.datetime.now().strftime('%Y%m%d_%H%M%S')
    backup_path = path.parent / f'{label}_backup_{timestamp}{path.suffix}'
    shutil.copy2(path, backup_path)
    return backup_path


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
        if dr.safe_account_dir(host_email) == account_name:
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


def plan_recording_actions(recording_id, file_group, row, download_dirs):
    host_email = row[1]
    topic = row[2] if len(row) > 2 else ''
    time_recorded = row[3] if len(row) > 3 else ''
    account_dir = dr.resolve_download_dir(host_email, download_dirs)
    canonical_path = canonical_recording_path(recording_id, account_dir)

    actions = {
        'recording_id': recording_id,
        'account_dir': account_dir,
        'canonical_path': canonical_path,
        'renames': [],
        'deletes': [],
        'manual_review': [],
        'canonical_source': None,
    }

    canonical_files = [f for f in file_group if f['path'] == canonical_path]
    other_files = [f for f in file_group if f['path'] != canonical_path]

    if canonical_files:
        canonical_file = canonical_files[0]
        actions['canonical_source'] = canonical_file['path']
        canonical_size = canonical_file['size']
        for extra in canonical_files[1:]:
            if extra['size'] == canonical_size:
                actions['deletes'].append(extra['path'])
            else:
                actions['manual_review'].append({
                    'reason': 'multiple_canonical_different_size',
                    'paths': [canonical_file['path'], extra['path']],
                })
        for other in other_files:
            if other['size'] == canonical_size:
                actions['deletes'].append(other['path'])
            else:
                actions['manual_review'].append({
                    'reason': 'legacy_different_size_from_canonical',
                    'canonical': canonical_file['path'],
                    'other': other['path'],
                })
        return actions

    if not file_group:
        return actions

    size_groups = defaultdict(list)
    for file_info in file_group:
        size_groups[file_info['size']].append(file_info)

    if len(size_groups) > 1:
        actions['manual_review'].append({
            'reason': 'duplicate_group_different_sizes',
            'paths': [f['path'] for f in file_group],
        })
        return actions

    source = file_group[0]
    if os.path.exists(canonical_path):
        existing_size = os.path.getsize(canonical_path)
        if existing_size != source['size']:
            actions['manual_review'].append({
                'reason': 'rename_target_exists_different_size',
                'source': source['path'],
                'target': canonical_path,
            })
            return actions
        actions['canonical_source'] = canonical_path
        for file_info in file_group:
            if file_info['path'] != canonical_path:
                actions['deletes'].append(file_info['path'])
        return actions

    actions['renames'].append({'from': source['path'], 'to': canonical_path})
    actions['canonical_source'] = canonical_path
    for file_info in file_group[1:]:
        actions['deletes'].append(file_info['path'])
    return actions


def apply_actions(action_plans, apply_changes, log_lines):
    for plan in action_plans:
        for rename in plan['renames']:
            msg = f"RENAME {rename['from']} -> {rename['to']}"
            log_lines.append(msg)
            if apply_changes:
                os.makedirs(os.path.dirname(rename['to']), exist_ok=True)
                os.replace(rename['from'], rename['to'])
        for delete_path in plan['deletes']:
            msg = f"DELETE {delete_path}"
            log_lines.append(msg)
            if apply_changes:
                os.remove(delete_path)


def write_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle, delimiter=',')
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row[:len(CSV_COLUMNS)])


def build_completed_state(downloaded_rows, action_plans):
    completed = {}
    plan_by_id = {plan['recording_id']: plan for plan in action_plans}
    now = datetime.datetime.now(datetime.timezone.utc).isoformat()

    for row in downloaded_rows:
        recording_id = row[0]
        plan = plan_by_id.get(recording_id)
        path = plan['canonical_source'] if plan and plan['canonical_source'] else None
        if path and is_usable_file(path):
            completed[recording_id] = {
                'path': str(path),
                'completed_at': now,
            }
    return {'version': STATE_VERSION, 'completed': completed, 'failed': {}, 'in_progress': {}}


def reconcile(csv_path, download_dirs, state_file, output_dir, apply_changes):
    csv_path = Path(csv_path)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    rows = dr.load_recording_rows(csv_path)
    indexes = build_csv_indexes(rows)
    id_to_row = indexes[0]

    disk_files = scan_disk_files(download_dirs)
    matched_files = []
    unmatched_on_disk = []

    groups_by_id = defaultdict(list)
    for file_info in disk_files:
        account_email = resolve_account_email(file_info, indexes[1])
        recording_id, match_method = match_file_to_recording_id(file_info, account_email, indexes)
        file_info['match_method'] = match_method
        file_info['account_email'] = account_email

        if recording_id and recording_id in id_to_row:
            file_info['recording_id'] = recording_id
            matched_files.append(file_info)
            groups_by_id[recording_id].append(file_info)
        else:
            unmatched_on_disk.append({
                'path': file_info['path'],
                'account': account_email,
                'match_method': match_method,
            })

    plan_by_id = {}
    duplicate_groups = 0
    manual_review = []

    for recording_id, file_group in sorted(groups_by_id.items()):
        if len(file_group) > 1:
            duplicate_groups += 1
        row = id_to_row[recording_id]
        plan = plan_recording_actions(recording_id, file_group, row, download_dirs)
        if plan['manual_review']:
            manual_review.extend(plan['manual_review'])
        plan_by_id[recording_id] = plan

    downloaded_ids = set()
    for plan in plan_by_id.values():
        if plan['canonical_source'] and is_usable_file(plan['canonical_source']):
            downloaded_ids.add(plan['recording_id'])

    for recording_id, row in id_to_row.items():
        if recording_id in downloaded_ids:
            continue
        host_email = row[1]
        topic = row[2] if len(row) > 2 else ''
        time_recorded = row[3] if len(row) > 3 else ''
        account_dir = dr.resolve_download_dir(host_email, download_dirs)
        existing = find_existing_recording_file(
            recording_id,
            account_dir,
            topic=topic,
            time_recorded=time_recorded,
        )
        if not existing:
            continue
        downloaded_ids.add(recording_id)
        if recording_id in plan_by_id:
            continue
        groups_by_id[recording_id] = [{
            'path': existing,
            'basename': os.path.basename(existing),
            'account_dir': account_dir,
            'account_name': dr.safe_account_dir(host_email),
            'size': os.path.getsize(existing),
            'match_method': 'find_existing_recording_file',
            'account_email': host_email,
            'recording_id': recording_id,
        }]
        plan = plan_recording_actions(recording_id, groups_by_id[recording_id], row, download_dirs)
        if plan['manual_review']:
            manual_review.extend(plan['manual_review'])
        plan_by_id[recording_id] = plan

    action_plans = list(plan_by_id.values())
    downloaded_rows = [row for row in rows if row[0] in downloaded_ids]
    pending_rows = [row for row in rows if row[0] not in downloaded_ids]

    log_lines = []
    mode = 'APPLY' if apply_changes else 'DRY-RUN'
    log_lines.append(f'=== reconcile_downloads.py [{mode}] {datetime.datetime.now().isoformat()} ===')

    if apply_changes:
        backup_file(csv_path, 'recordings_full')
        if state_file and Path(state_file).exists():
            backup_file(state_file, 'download_state')

    apply_actions(action_plans, apply_changes, log_lines)

    report = {
        'mode': mode,
        'timestamp': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'csv_path': str(csv_path),
        'download_dirs': download_dirs,
        'total_csv_rows': len(rows),
        'downloaded_count': len(downloaded_rows),
        'pending_count': len(pending_rows),
        'files_on_disk': len(disk_files),
        'matched_on_disk': len(matched_files),
        'duplicate_groups': duplicate_groups,
        'unmatched_on_disk_count': len(unmatched_on_disk),
        'unmatched_on_disk': unmatched_on_disk,
        'manual_review_count': len(manual_review),
        'manual_review': manual_review,
        'actions': {
            'renames': sum(len(p['renames']) for p in action_plans),
            'deletes': sum(len(p['deletes']) for p in action_plans),
        },
    }

    report_path = output_dir / 'reconcile_report.json'
    log_path = output_dir / 'reconcile_report.log'

    with open(log_path, 'a', encoding='utf-8') as handle:
        handle.write('\n'.join(log_lines))
        handle.write('\n')
        handle.write(json.dumps(report, indent=2, ensure_ascii=False))
        handle.write('\n')

    write_json_atomic(report_path, report)

    downloaded_csv = output_dir / 'recordings_downloaded.csv'
    pending_csv = output_dir / 'recordings_pending.csv'
    write_csv(downloaded_csv, downloaded_rows)
    write_csv(pending_csv, pending_rows)

    if apply_changes:
        state_data = build_completed_state(downloaded_rows, action_plans)
        write_json_atomic(state_file, state_data)

    print(f"Modo: {mode}")
    print(f"Total no CSV: {report['total_csv_rows']}")
    print(f"Baixadas (reconhecidas): {report['downloaded_count']}")
    print(f"Pendentes: {report['pending_count']}")
    print(f"Arquivos no disco: {report['files_on_disk']}")
    print(f"Grupos duplicados: {report['duplicate_groups']}")
    print(f"Renomes planejados/executados: {report['actions']['renames']}")
    print(f"Remocoes planejadas/executadas: {report['actions']['deletes']}")
    print(f"Arquivos no disco sem match no CSV: {report['unmatched_on_disk_count']}")
    print(f"Casos para revisao manual: {report['manual_review_count']}")
    print(f"Relatorio: {report_path}")
    print(f"Log: {log_path}")
    print(f"CSV baixadas: {downloaded_csv}")
    print(f"CSV pendentes: {pending_csv}")
    if apply_changes:
        print(f"Estado atualizado: {state_file}")
    else:
        print("Nenhuma alteracao no disco. Use --apply para executar renomes/remocoes e atualizar o estado.")

    return report


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description=(
            'Reconcilia gravacoes ja baixadas nos discos com recordings.csv. '
            'Por padrao executa em dry-run (sem alterar arquivos).'
        ),
        epilog=(
            'Sequencia no servidor: (1) parar workers, (2) backup do CSV e state, '
            '(3) dry-run, (4) revisar reconcile_report.json, (5) --apply, '
            '(6) export RECORDINGS_CSV=recordings_pending.csv e retomar downloads.'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        '--csv',
        default=str(dr.get_csv_path_from_env()),
        help='Caminho para recordings.csv completo',
    )
    parser.add_argument(
        '--dirs',
        default=','.join(dr.get_download_dirs_from_env()),
        help='Diretorios base dos discos, separados por virgula',
    )
    parser.add_argument(
        '--state-file',
        default=str(dr.get_state_file_from_env()),
        help='Arquivo download_state.json a ser gerado/atualizado com --apply',
    )
    parser.add_argument(
        '--output-dir',
        default=str(SCRIPT_DIR),
        help='Diretorio para gravar CSVs de saida e relatorios',
    )
    parser.add_argument(
        '--apply',
        action='store_true',
        help='Executa renomes/remocoes e atualiza download_state.json (padrao: dry-run)',
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    download_dirs = dr.parse_download_dirs(args.dirs)

    if not Path(args.csv).exists():
        print(f"Arquivo CSV nao encontrado: {args.csv}", file=sys.stderr)
        sys.exit(1)

    for download_dir in download_dirs:
        if not os.path.isdir(download_dir):
            print(f"Aviso: diretorio nao encontrado (sera ignorado na varredura): {download_dir}")

    reconcile(
        csv_path=args.csv,
        download_dirs=download_dirs,
        state_file=args.state_file,
        output_dir=args.output_dir,
        apply_changes=args.apply,
    )


if __name__ == '__main__':
    main()
