import csv
import hashlib
import os
import re
import shutil
from pathlib import Path

DEFAULT_DOWNLOAD_DIR = "Downloaded-Recordings/"
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_CSV_PATH = SCRIPT_DIR.parent / "recordings.csv"

CSV_COLUMNS = (
    'recordingId',
    'hostEmail',
    'topic',
    'timeRecorded',
    'createTime',
    'durationSeconds',
)


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
    return SCRIPT_DIR / "download_state.json"


def resolve_state_file_path(explicit_path=None, disk_index=None):
    if explicit_path:
        return Path(explicit_path)

    configured = os.getenv('DOWNLOAD_STATE', '').strip()
    if configured:
        if '{disk}' in configured:
            if disk_index is None:
                raise ValueError(
                    "DOWNLOAD_STATE contem {disk}, mas --disk-index nao foi informado."
                )
            return Path(configured.format(disk=disk_index))
        return Path(configured)

    if disk_index is not None:
        return SCRIPT_DIR / f"download_state_disco{disk_index}.json"
    return SCRIPT_DIR / "download_state.json"


def get_min_free_bytes_from_env():
    configured = os.getenv('DISK_MIN_FREE_MB', '5120').strip()
    return max(0, int(configured)) * 1024 * 1024


def get_disk_space_wait_sec_from_env():
    configured = os.getenv('DISK_SPACE_WAIT_SEC', '60').strip()
    return max(1, int(configured))


def disk_usage(path):
    total, used, free = shutil.disk_usage(path)
    return {"total": total, "used": used, "free": free}


def format_bytes(num_bytes):
    value = float(num_bytes)
    for unit in ('B', 'KB', 'MB', 'GB', 'TB'):
        if value < 1024 or unit == 'TB':
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def check_disk_space(path, required_bytes=0, min_free_bytes=None):
    min_free_bytes = min_free_bytes if min_free_bytes is not None else get_min_free_bytes_from_env()
    usage = disk_usage(path)
    needed = min_free_bytes + max(0, required_bytes)
    return usage["free"] >= needed, usage, needed


def get_workers_per_disk_from_env():
    configured = os.getenv('WORKERS_PER_DISK', '').strip()
    if configured:
        return max(1, int(configured))
    return 1


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


def load_recording_rows(csv_path):
    rows = []
    seen_ids = set()
    duplicate_count = 0
    with open(csv_path, 'r', newline='', encoding='utf-8') as csvfile:
        reader = csv.reader(csvfile)
        for row in reader:
            if not row or row[0] == 'recordingId':
                continue
            recording_id = row[0]
            if recording_id in seen_ids:
                duplicate_count += 1
                continue
            seen_ids.add(recording_id)
            rows.append(row)
    if duplicate_count:
        print(f"Aviso: {duplicate_count} entradas duplicadas ignoradas no CSV (mesmo recordingId).")
    return rows


def write_recording_csv(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, 'w', newline='', encoding='utf-8') as handle:
        writer = csv.writer(handle, delimiter=',')
        writer.writerow(CSV_COLUMNS)
        for row in rows:
            writer.writerow(row[:len(CSV_COLUMNS)])
