import datetime
import os
import re

RECORDING_ID_RE = re.compile(r'^[0-9a-fA-F]{32}$')
HASH_SUFFIX_RE = re.compile(r'_([0-9a-fA-F]{8})\.mp4$', re.IGNORECASE)


def is_usable_file(path):
    return bool(path) and os.path.isfile(path) and os.path.getsize(path) > 0


def recording_file_extension(original_file_name):
    _, ext = os.path.splitext(original_file_name or '')
    return ext or '.mp4'


def canonical_recording_path(recording_id, account_dir, original_file_name='.mp4'):
    ext = recording_file_extension(original_file_name)
    return os.path.join(account_dir, f"{recording_id}{ext}")


def expected_legacy_basename(topic, time_recorded, ext='.mp4', recording_id=None, with_hash=False):
    if not topic and not time_recorded:
        return None

    safe_topic = re.sub(r'[<>:"/\\|?*\x00-\x1f]', '', topic or 'recording')
    safe_topic = re.sub(r'\s+', ' ', safe_topic).strip()[:120] or 'recording'

    timestamp = ''
    if time_recorded:
        try:
            dt = datetime.datetime.fromisoformat(time_recorded.replace('Z', '+00:00'))
            timestamp = dt.strftime('%Y-%m-%d_%H-%M-%S')
        except ValueError:
            timestamp = re.sub(r'[<>:"/\\|?*]', '', time_recorded)[:19]

    parts = [safe_topic]
    if timestamp:
        parts.append(timestamp)
    base_name = '_'.join(parts)
    if with_hash and recording_id:
        return f"{base_name}_{recording_id[:8]}{ext}"
    return f"{base_name}{ext}"


def legacy_basenames_for_row(recording_id, topic, time_recorded, ext='.mp4'):
    basenames = set()
    plain = expected_legacy_basename(topic, time_recorded, ext=ext)
    if plain:
        basenames.add(plain)
    hashed = expected_legacy_basename(
        topic, time_recorded, ext=ext, recording_id=recording_id, with_hash=True
    )
    if hashed:
        basenames.add(hashed)
    return basenames


def parse_recording_id_from_basename(basename):
    stem, _ = os.path.splitext(basename)
    if RECORDING_ID_RE.match(stem):
        return stem.lower()
    return None


def parse_hash_suffix_from_basename(basename):
    match = HASH_SUFFIX_RE.search(basename)
    if not match:
        return None
    return match.group(1).lower()


def find_hash_suffix_file(account_dir, recording_id):
    prefix = recording_id[:8].lower()
    if not os.path.isdir(account_dir):
        return None
    for entry in os.listdir(account_dir):
        if entry.lower().endswith(f'_{prefix}.mp4') and is_usable_file(os.path.join(account_dir, entry)):
            return os.path.join(account_dir, entry)
    return None


def find_existing_recording_file(
    recording_id,
    account_dir,
    topic='',
    time_recorded='',
    original_file_name='.mp4',
    state_data=None,
):
    state_data = state_data or {}
    completed = state_data.get('completed', {})

    stored_path = completed.get(recording_id, {}).get('path', '')
    if is_usable_file(stored_path):
        return stored_path

    canonical = canonical_recording_path(recording_id, account_dir, original_file_name)
    if is_usable_file(canonical):
        return canonical

    ext = recording_file_extension(original_file_name)
    for basename in legacy_basenames_for_row(recording_id, topic, time_recorded, ext=ext):
        legacy_path = os.path.join(account_dir, basename)
        if is_usable_file(legacy_path):
            return legacy_path

    hash_path = find_hash_suffix_file(account_dir, recording_id)
    if hash_path:
        return hash_path

    return None
