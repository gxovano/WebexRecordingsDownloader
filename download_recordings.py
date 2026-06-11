import argparse
import csv
import datetime
import fcntl
import hashlib
import json
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path

import requests
from dotenv import load_dotenv

import list_recordings

dotenv_path = Path('.env')
load_dotenv(dotenv_path=dotenv_path)

DEFAULT_DOWNLOAD_DIR = "Downloaded-Recordings/"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV_PATH = SCRIPT_DIR.parent / "recordings.csv"
DEFAULT_STATE_FILE = SCRIPT_DIR / "download_state.json"
STATE_VERSION = 1


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


def get_state_file_from_env():
    configured = os.getenv('DOWNLOAD_STATE', '').strip()
    if configured:
        return Path(configured)
    return DEFAULT_STATE_FILE


def get_workers_per_disk_from_env():
    configured = os.getenv('WORKERS_PER_DISK', '').strip()
    if configured:
        return max(1, int(configured))
    return 1


def empty_state():
    return {"version": STATE_VERSION, "completed": {}, "failed": {}}


class DownloadState:
    def __init__(self, path):
        self.path = Path(path)

    @contextmanager
    def _locked(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, 'a+', encoding='utf-8') as handle:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            try:
                handle.seek(0)
                content = handle.read()
                data = json.loads(content) if content.strip() else empty_state()
                yield data
                handle.seek(0)
                handle.truncate()
                json.dump(data, handle, indent=2, ensure_ascii=False)
                handle.write('\n')
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)

    def load(self):
        if not self.path.exists():
            return empty_state()
        with open(self.path, encoding='utf-8') as handle:
            return json.load(handle)

    def is_completed(self, recording_id):
        with self._locked() as data:
            return recording_id in data["completed"]

    def mark_completed(self, recording_id, target_path):
        with self._locked() as data:
            data["completed"][recording_id] = {
                "path": str(target_path),
                "completed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }
            data["failed"].pop(recording_id, None)

    def mark_failed(self, recording_id, error):
        with self._locked() as data:
            data["failed"][recording_id] = {
                "error": str(error),
                "failed_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
            }

    def remove_completed(self, recording_id):
        with self._locked() as data:
            data["completed"].pop(recording_id, None)


def disk_index_for_account(host_email, disk_count):
    digest = hashlib.sha256(host_email.lower().encode('utf-8')).hexdigest()
    return int(digest[:8], 16) % disk_count


def worker_index_for_account(host_email, workers_per_disk):
    digest = hashlib.sha256(host_email.lower().encode('utf-8')).hexdigest()
    return int(digest[8:16], 16) % workers_per_disk


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


def should_process_row(host_email, disk_index, disk_count, worker_index=None, workers_per_disk=1):
    if disk_index is not None and disk_index_for_account(host_email, disk_count) != disk_index:
        return False
    if worker_index is not None and worker_index_for_account(host_email, workers_per_disk) != worker_index:
        return False
    return True


def load_recording_rows(csv_path):
    rows = []
    with open(csv_path, 'r', newline='') as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            if not row or row[0] == 'recordingId':
                continue
            rows.append(row)
    return rows


def summarize_distribution(rows, download_dirs, workers_per_disk=1):
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
    if workers_per_disk > 1:
        print(f"Workers por disco: {workers_per_disk}")


def show_download_status(rows, download_dirs, state_path):
    state = DownloadState(state_path).load()
    completed_ids = set(state.get("completed", {}))
    failed_ids = set(state.get("failed", {}))
    all_ids = {row[0] for row in rows}
    pending_ids = all_ids - completed_ids

    print(f"Arquivo de estado: {state_path}")
    print(f"Total no CSV: {len(all_ids)}")
    print(f"Concluidas: {len(completed_ids & all_ids)}")
    print(f"Pendentes: {len(pending_ids)}")
    print(f"Falhas registradas: {len(failed_ids & all_ids)}")

    print("Pendentes por disco:")
    pending_by_disk = {download_dir: 0 for download_dir in download_dirs}
    for row in rows:
        recording_id = row[0]
        if recording_id in completed_ids:
            continue
        host_email = row[1]
        download_dir = download_dirs[disk_index_for_account(host_email, len(download_dirs))]
        pending_by_disk[download_dir] += 1
    for download_dir in download_dirs:
        print(f"  {download_dir}: {pending_by_disk[download_dir]}")

    stale_completed = completed_ids - all_ids
    if stale_completed:
        print(f"Concluidas no estado mas ausentes do CSV atual: {len(stale_completed)}")


def save_recording_file(content, target_path):
    part_path = f"{target_path}.part"
    if os.path.exists(part_path):
        os.remove(part_path)
    with open(part_path, 'wb') as file:
        file.write(content)
    os.replace(part_path, target_path)


def getDownloadLinks(
    headers,
    csv_path=None,
    download_dirs=None,
    disk_index=None,
    worker_index=None,
    workers_per_disk=1,
    skip_existing=True,
    state_path=None,
):
    csv_path = Path(csv_path) if csv_path else get_csv_path_from_env()
    download_dirs = download_dirs or get_download_dirs_from_env()
    state_path = Path(state_path) if state_path else get_state_file_from_env()
    state = DownloadState(state_path)

    if not csv_path.exists():
        raise FileNotFoundError(f"Arquivo CSV nao encontrado: {csv_path}")

    for download_dir in download_dirs:
        os.makedirs(download_dir, exist_ok=True)

    rows = load_recording_rows(csv_path)
    summarize_distribution(rows, download_dirs, workers_per_disk)

    if disk_index is not None:
        print(f"Processando disco {disk_index + 1}/{len(download_dirs)}: {download_dirs[disk_index]}")
    if worker_index is not None:
        print(f"Worker {worker_index + 1}/{workers_per_disk} deste disco")

    for row in rows:
        recording_id = row[0]
        host_email_raw = row[1]
        if not should_process_row(
            host_email_raw,
            disk_index,
            len(download_dirs),
            worker_index,
            workers_per_disk,
        ):
            continue

        if skip_existing and state.is_completed(recording_id):
            print(f"Ja concluida no estado, pulando: {recording_id}")
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
            error = "Nao foi possivel autenticar para obter o link de download."
            print(error)
            state.mark_failed(recording_id, error)
            continue

        download_link = json.loads(result.text)
        if not topic:
            topic = download_link.get('topic', '')
        if not time_recorded:
            time_recorded = download_link.get('timeRecorded', '')

        links = download_link.get('temporaryDirectDownloadLinks', {})
        recording_download_link = links.get('recordingDownloadLink')
        if recording_download_link is None:
            error = "Link de download indisponivel para esta gravacao."
            print(error)
            state.mark_failed(recording_id, error)
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
                    print(f"Arquivo ja existe, registrando como concluida: {target_path}")
                    state.mark_completed(recording_id, target_path)
                    continue
                print(f"Filename: {save_as}")
                save_recording_file(recording.content, target_path)
                state.mark_completed(recording_id, target_path)
                print(f"{save_as} saved!")
            elif recording.status_code == 429:
                retry_after = recording.headers.get("retry-after") or recording.headers.get("Retry-After")
                print(f"Rate limited. Waiting {retry_after} seconds.")
                time.sleep(int(retry_after))
            else:
                error = f"Download falhou com status {recording.status_code}"
                print("Unable to download, something went wrong!")
                print(f"Status Code: {recording.status_code}")
                state.mark_failed(recording_id, error)
        except Exception as e:
            print(e)
            state.mark_failed(recording_id, e)


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
        '--worker-index',
        type=int,
        help='Processa apenas um subconjunto de contas do disco (--workers-per-disk define o total).',
    )
    parser.add_argument(
        '--workers-per-disk',
        type=int,
        default=get_workers_per_disk_from_env(),
        help='Numero de workers paralelos por disco (padrao: 1 ou WORKERS_PER_DISK).',
    )
    parser.add_argument(
        '--state-file',
        default=str(get_state_file_from_env()),
        help='Arquivo JSON com gravacoes concluidas e falhas (padrao: download_state.json ou DOWNLOAD_STATE).',
    )
    parser.add_argument(
        '--show-distribution',
        action='store_true',
        help='Mostra como as contas seriam distribuidas e encerra.',
    )
    parser.add_argument(
        '--show-status',
        action='store_true',
        help='Mostra gravacoes concluidas, pendentes e falhas com base no arquivo de estado.',
    )
    parser.add_argument(
        '--no-skip-existing',
        action='store_true',
        help='Baixa novamente mesmo se a gravacao ja estiver concluida no estado ou no disco.',
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    download_dirs = parse_download_dirs(args.dirs)
    csv_path = Path(args.csv)
    workers_per_disk = max(1, args.workers_per_disk)

    if args.disk_index is not None and not 0 <= args.disk_index < len(download_dirs):
        parser.error(f"--disk-index deve estar entre 0 e {len(download_dirs) - 1}")

    if args.worker_index is not None and not 0 <= args.worker_index < workers_per_disk:
        parser.error(f"--worker-index deve estar entre 0 e {workers_per_disk - 1}")

    if args.show_distribution:
        rows = load_recording_rows(csv_path)
        summarize_distribution(rows, download_dirs, workers_per_disk)
        return

    if args.show_status:
        if not csv_path.exists():
            parser.error(f"Arquivo CSV nao encontrado: {csv_path}")
        rows = load_recording_rows(csv_path)
        show_download_status(rows, download_dirs, args.state_file)
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
        worker_index=args.worker_index,
        workers_per_disk=workers_per_disk,
        skip_existing=not args.no_skip_existing,
        state_path=args.state_file,
    )


if __name__ == '__main__':
    main()
