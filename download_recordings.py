import argparse
import csv
import datetime
import hashlib
import json
import os
import re
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

import list_recordings

dotenv_path = Path('.env')
load_dotenv(dotenv_path=dotenv_path)

DEFAULT_DOWNLOAD_DIR = "Downloaded-Recordings/"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV_PATH = SCRIPT_DIR.parent / "recordings.csv"


def parse_download_dirs(value):
    if not value:
        return [DEFAULT_DOWNLOAD_DIR]
    dirs = [part.strip() for part in value.split(',') if part.strip()]
    return dirs or [DEFAULT_DOWNLOAD_DIR]


def get_download_dirs_from_env():
    return parse_download_dirs(os.getenv('DOWNLOAD_DIRS', ''))


def get_csv_path_from_env():
    configured = os.getenv('RECORDINGS_CSV', '').strip()
    if configured:
        return Path(configured)
    return DEFAULT_CSV_PATH


def disk_index_for_account(host_email, disk_count):
    digest = hashlib.sha256(host_email.lower().encode('utf-8')).hexdigest()
    return int(digest[:8], 16) % disk_count


def safe_account_dir(host_email):
    safe = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '_', host_email or 'unknown')
    return safe.strip() or 'unknown'


def resolve_download_dir(host_email, download_dirs, disk_index=None):
    if disk_index is not None:
        base_dir = download_dirs[disk_index]
    else:
        base_dir = download_dirs[disk_index_for_account(host_email, len(download_dirs))]
    return os.path.join(base_dir, safe_account_dir(host_email))


def buildRecordingFileName(topic, timeRecorded, originalFileName, recordingId, download_dir):
    _, ext = os.path.splitext(originalFileName)
    if not ext:
        ext = '.mp4'

    if not topic and not timeRecorded:
        return originalFileName

    safeTopic = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', topic or 'recording')
    safeTopic = re.sub(r'\s+', ' ', safeTopic).strip()[:120] or 'recording'

    timestamp = ''
    if timeRecorded:
        try:
            dt = datetime.datetime.fromisoformat(timeRecorded.replace('Z', '+00:00'))
            timestamp = dt.strftime('%Y-%m-%d_%H-%M-%S')
        except ValueError:
            timestamp = re.sub(r'[<>:"/\\|?*]', '', timeRecorded)[:19]

    parts = [safeTopic]
    if timestamp:
        parts.append(timestamp)
    baseName = '_'.join(parts)
    fileName = baseName + ext

    targetPath = os.path.join(download_dir, fileName)
    if os.path.exists(targetPath):
        fileName = f"{baseName}_{recordingId[:8]}{ext}"

    return fileName


def refresh_headers_if_needed(response, headers):
    if response.status_code != 401:
        return headers

    if not os.getenv('client_id') or not os.getenv('client_secret') or not os.getenv('refresh_token'):
        print("Token expired and OAuth credentials are missing from .env.")
        return headers

    print("Token expired, refreshing...")
    new_token_response = list_recordings.token_refresh()
    if new_token_response.status_code != 200:
        print("Unable to refresh token.")
        print(new_token_response.text)
        return headers

    new_token = json.loads(new_token_response.text)['access_token']
    updated_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer " + new_token,
    }
    with open(SCRIPT_DIR / 'token.json', 'w') as update_token:
        json.dump({"token": new_token}, update_token)
    return updated_headers


def should_process_row(host_email, disk_index, disk_count):
    if disk_index is None:
        return True
    return disk_index_for_account(host_email, disk_count) == disk_index


def load_recording_rows(csv_path):
    rows = []
    with open(csv_path, 'r', newline='') as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            if not row or row[0] == 'recordingId':
                continue
            rows.append(row)
    return rows


def summarize_distribution(rows, download_dirs):
    counts = {download_dir: 0 for download_dir in download_dirs}
    accounts = {download_dir: set() for download_dir in download_dirs}
    for row in rows:
        host_email = row[1]
        download_dir = download_dirs[disk_index_for_account(host_email, len(download_dirs))]
        counts[download_dir] += 1
        accounts[download_dir].add(host_email)
    print("Distribuicao planejada por disco:")
    for download_dir in download_dirs:
        print(f"  {download_dir}: {counts[download_dir]} gravacoes, {len(accounts[download_dir])} contas")


def getDownloadLinks(headers, csv_path=None, download_dirs=None, disk_index=None, skip_existing=True):
    csv_path = Path(csv_path) if csv_path else get_csv_path_from_env()
    download_dirs = download_dirs or get_download_dirs_from_env()

    if not csv_path.exists():
        raise FileNotFoundError(f"Arquivo CSV nao encontrado: {csv_path}")

    for download_dir in download_dirs:
        os.makedirs(download_dir, exist_ok=True)

    rows = load_recording_rows(csv_path)
    summarize_distribution(rows, download_dirs)

    if disk_index is not None:
        print(f"Processando apenas o disco {disk_index + 1}/{len(download_dirs)}: {download_dirs[disk_index]}")

    for row in rows:
        recording_id = row[0]
        host_email_raw = row[1]
        if not should_process_row(host_email_raw, disk_index, len(download_dirs)):
            continue

        host_email = host_email_raw.replace('@', '%40').replace('+', '%2B')
        topic = row[2] if len(row) > 2 else ''
        time_recorded = row[3] if len(row) > 3 else ''
        account_dir = resolve_download_dir(host_email_raw, download_dirs, disk_index)
        os.makedirs(account_dir, exist_ok=True)

        print(f"RecordingId: {recording_id}, HostEmail: {host_email}, Destino: {account_dir}")
        url = f'https://webexapis.com/v1/recordings/{recording_id}?hostEmail={host_email}'
        result = requests.get(url, headers=headers)
        headers = refresh_headers_if_needed(result, headers)
        if result.status_code == 401:
            print("Nao foi possivel autenticar para obter o link de download.")
            continue

        download_link = json.loads(result.text)
        if not topic:
            topic = download_link.get('topic', '')
        if not time_recorded:
            time_recorded = download_link.get('timeRecorded', '')

        links = download_link.get('temporaryDirectDownloadLinks', {})
        recording_download_link = links.get('recordingDownloadLink')
        if recording_download_link is None:
            print("Link de download indisponivel para esta gravacao.")
            continue

        try:
            recording = requests.get(recording_download_link)
            if recording.status_code == 200:
                content_disposition = recording.headers.get('Content-Disposition', '')
                if "''" in content_disposition:
                    file_name = content_disposition.split("''")[1]
                else:
                    file_name = f"{recording_id}.mp4"
                save_as = buildRecordingFileName(topic, time_recorded, file_name, recording_id, account_dir)
                target_path = os.path.join(account_dir, save_as)
                if skip_existing and os.path.exists(target_path):
                    print(f"Ja existe, pulando: {target_path}")
                    continue
                print(f"Filename: {save_as}")
                with open(target_path, 'wb') as file:
                    file.write(recording.content)
                print(f"{save_as} saved!")
            elif recording.status_code == 429:
                retry_after = recording.headers.get("retry-after") or recording.headers.get("Retry-After")
                print(f"Rate limited. Waiting {retry_after} seconds.")
                time.sleep(int(retry_after))
            else:
                print("Unable to download, something went wrong!")
                print(f"Status Code: {recording.status_code}")
        except Exception as e:
            print(e)


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Baixa gravacoes Webex listadas em recordings.csv, distribuindo por conta entre varios discos."
    )
    parser.add_argument(
        '--csv',
        default=str(get_csv_path_from_env()),
        help='Caminho para recordings.csv (padrao: ../recordings.csv ou RECORDINGS_CSV)',
    )
    parser.add_argument(
        '--dirs',
        default=','.join(get_download_dirs_from_env()),
        help='Diretorios base separados por virgula (padrao: DOWNLOAD_DIRS ou Downloaded-Recordings/)',
    )
    parser.add_argument(
        '--disk-index',
        type=int,
        help='Processa apenas um dos discos informados em --dirs (0, 1, 2, ...), util para workers em paralelo.',
    )
    parser.add_argument(
        '--show-distribution',
        action='store_true',
        help='Mostra como as contas seriam distribuidas e encerra.',
    )
    parser.add_argument(
        '--no-skip-existing',
        action='store_true',
        help='Baixa novamente mesmo se o arquivo ja existir.',
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    download_dirs = parse_download_dirs(args.dirs)
    csv_path = Path(args.csv)

    if args.disk_index is not None and not 0 <= args.disk_index < len(download_dirs):
        parser.error(f"--disk-index deve estar entre 0 e {len(download_dirs) - 1}")

    if args.show_distribution:
        rows = load_recording_rows(csv_path)
        summarize_distribution(rows, download_dirs)
        return

    token_path = SCRIPT_DIR / 'token.json'
    with open(token_path, 'r') as openfile:
        token = json.load(openfile)
        bearer = token["token"]

    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer " + str(bearer),
    }

    getDownloadLinks(
        headers,
        csv_path=csv_path,
        download_dirs=download_dirs,
        disk_index=args.disk_index,
        skip_existing=not args.no_skip_existing,
    )


if __name__ == '__main__':
    main()
