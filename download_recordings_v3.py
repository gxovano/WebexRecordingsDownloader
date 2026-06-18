import argparse
import json
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import quote

import requests
from dotenv import load_dotenv

import recording_common as rc
from download_recordings_v2 import (
    DownloadState,
    parse_content_length,
    print_disk_status,
    refresh_headers_if_needed,
    resolve_recording_paths,
    stream_recording_file,
    wait_for_disk_space,
)

dotenv_path = Path('.env')
load_dotenv(dotenv_path=dotenv_path)

SCRIPT_DIR = rc.SCRIPT_DIR
DEFAULT_REPORT_FILE = SCRIPT_DIR / "download_availability_report.json"
DEFAULT_THREADS = 4
DETAIL_RESPONSE_PREVIEW_CHARS = 2000
DETAIL_RETRY_ATTEMPTS = 3
DOWNLOAD_RETRY_ATTEMPTS = 3


@dataclass
class CheckedRecording:
    row: list
    recording_id: str
    host_email: str
    account_dir: str
    topic: str = ''
    time_recorded: str = ''
    download_link: str = ''
    status_code: int = 0
    reason: str = ''
    metadata: dict = None

    def to_report_entry(self):
        entry = {
            "recordingId": self.recording_id,
            "hostEmail": self.host_email,
            "topic": self.topic,
            "timeRecorded": self.time_recorded,
            "statusCode": self.status_code,
            "reason": self.reason,
        }
        if self.metadata:
            entry["metadata"] = self.metadata
        return entry


def get_download_dir_from_env():
    configured = os.getenv('DOWNLOAD_DIR', '').strip()
    if configured:
        return configured
    download_dirs = rc.get_download_dirs_from_env()
    return download_dirs[0] if download_dirs else rc.DEFAULT_DOWNLOAD_DIR


def get_download_threads_from_env():
    configured = os.getenv('DOWNLOAD_THREADS', '').strip()
    if configured:
        return max(1, int(configured))
    return DEFAULT_THREADS


def read_token_headers():
    token_path = SCRIPT_DIR / 'token.json'
    with open(token_path, 'r', encoding='utf-8') as openfile:
        token = json.load(openfile)
        bearer = token["token"]

    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": "Bearer " + str(bearer),
    }


def summarize_api_response(data):
    summary = {}
    for key in ("id", "topic", "hostEmail", "siteUrl", "status", "format", "sizeBytes", "downloadUrl", "playbackUrl"):
        value = data.get(key)
        if value not in (None, ''):
            summary[key] = value
    links = data.get("temporaryDirectDownloadLinks")
    summary["hasTemporaryDirectDownloadLinks"] = bool(links)
    return summary


def response_preview(response):
    text = response.text or ''
    if len(text) > DETAIL_RESPONSE_PREVIEW_CHARS:
        return text[:DETAIL_RESPONSE_PREVIEW_CHARS] + "...[truncated]"
    return text


def parse_retry_after(headers, default_seconds=60):
    raw = headers.get("retry-after") or headers.get("Retry-After")
    try:
        return max(1, int(raw))
    except (TypeError, ValueError):
        return default_seconds


def get_recording_details(recording_id, host_email, headers):
    encoded_email = quote(host_email)
    url = f'https://webexapis.com/v1/recordings/{recording_id}?hostEmail={encoded_email}'
    updated_headers = headers
    for attempt in range(1, DETAIL_RETRY_ATTEMPTS + 1):
        response = requests.get(url, headers=updated_headers)
        refreshed_headers = refresh_headers_if_needed(response, updated_headers)
        if response.status_code == 401 and refreshed_headers != updated_headers:
            updated_headers = refreshed_headers
            response = requests.get(url, headers=updated_headers)

        if response.status_code == 429 and attempt < DETAIL_RETRY_ATTEMPTS:
            wait_seconds = parse_retry_after(response.headers)
            print(f"Rate limited ao consultar {recording_id}. Aguardando {wait_seconds}s.")
            time.sleep(wait_seconds)
            continue

        return response, updated_headers
    return response, updated_headers


def build_unavailable(recording, response, reason, metadata=None):
    return CheckedRecording(
        row=recording.row,
        recording_id=recording.recording_id,
        host_email=recording.host_email,
        account_dir=recording.account_dir,
        topic=recording.topic,
        time_recorded=recording.time_recorded,
        status_code=response.status_code if response is not None else 0,
        reason=reason,
        metadata=metadata or {},
    )


def precheck_recordings(rows, headers, download_dir, state, skip_existing=True):
    available = []
    unavailable = []
    already_completed = []

    for index, row in enumerate(rows, start=1):
        recording_id = row[0]
        host_email = row[1]
        topic = row[2] if len(row) > 2 else ''
        time_recorded = row[3] if len(row) > 3 else ''
        account_dir = rc.resolve_download_dir(host_email, [download_dir], disk_index=0)
        os.makedirs(account_dir, exist_ok=True)

        base_recording = CheckedRecording(
            row=row,
            recording_id=recording_id,
            host_email=host_email,
            account_dir=account_dir,
            topic=topic,
            time_recorded=time_recorded,
        )

        if skip_existing and state.is_completed(recording_id):
            print(f"[{index}/{len(rows)}] Ja concluida no estado, pulando: {recording_id}")
            already_completed.append(base_recording)
            continue

        if skip_existing:
            existing_path, _ = resolve_recording_paths(
                recording_id,
                f"{recording_id}.mp4",
                account_dir,
                state.load(),
                topic=topic,
                time_recorded=time_recorded,
            )
            if existing_path:
                print(f"[{index}/{len(rows)}] Arquivo ja existe, registrando como concluida: {existing_path}")
                state.mark_completed(recording_id, existing_path)
                already_completed.append(base_recording)
                continue

        print(f"[{index}/{len(rows)}] Verificando link: {recording_id}, HostEmail: {host_email}")
        try:
            response, headers = get_recording_details(recording_id, host_email, headers)
        except Exception as exc:
            reason = f"Erro ao consultar detalhes da gravacao: {exc}"
            print(reason)
            state.mark_failed(recording_id, reason)
            unavailable.append(build_unavailable(base_recording, None, reason))
            continue

        try:
            details = response.json()
        except ValueError:
            reason = f"Resposta da API nao e JSON valido (status {response.status_code})"
            metadata = {"responsePreview": response_preview(response)}
            print(reason)
            state.mark_failed(recording_id, reason)
            unavailable.append(build_unavailable(base_recording, response, reason, metadata))
            continue

        metadata = summarize_api_response(details)
        if not topic:
            topic = details.get('topic', '')
        if not time_recorded:
            time_recorded = details.get('timeRecorded', '')
        base_recording.topic = topic
        base_recording.time_recorded = time_recorded
        base_recording.metadata = metadata
        base_recording.status_code = response.status_code

        if response.status_code != 200:
            reason = f"API retornou status {response.status_code} ao obter detalhes da gravacao."
            metadata["responsePreview"] = response_preview(response)
            print(reason)
            state.mark_failed(recording_id, reason)
            unavailable.append(build_unavailable(base_recording, response, reason, metadata))
            continue

        links = details.get('temporaryDirectDownloadLinks') or {}
        recording_download_link = links.get('recordingDownloadLink')
        if not recording_download_link:
            reason = "Link direto indisponivel: resposta 200 sem temporaryDirectDownloadLinks.recordingDownloadLink."
            print(reason)
            state.mark_failed(recording_id, reason)
            unavailable.append(build_unavailable(base_recording, response, reason, metadata))
            continue

        base_recording.download_link = recording_download_link
        base_recording.reason = "Disponivel para download."
        available.append(base_recording)

    return available, unavailable, already_completed, headers


def resolve_file_name(recording_id, response):
    content_disposition = response.headers.get('Content-Disposition', '')
    if "''" in content_disposition:
        return content_disposition.split("''", 1)[1]
    return f"{recording_id}.mp4"


def download_checked_recording(recording, state, min_free_bytes=None, skip_existing=True):
    recording_id = recording.recording_id

    if skip_existing and state.is_completed(recording_id):
        return {"recordingId": recording_id, "status": "skipped", "reason": "Ja concluida no estado."}

    if not state.try_claim(recording_id):
        return {"recordingId": recording_id, "status": "skipped", "reason": "Ja em andamento ou concluida."}

    try:
        for attempt in range(1, DOWNLOAD_RETRY_ATTEMPTS + 1):
            with requests.get(recording.download_link, stream=True) as response:
                if response.status_code == 429 and attempt < DOWNLOAD_RETRY_ATTEMPTS:
                    wait_seconds = parse_retry_after(response.headers)
                    print(f"Rate limited em {recording_id}. Aguardando {wait_seconds}s antes de tentar novamente.")
                    time.sleep(wait_seconds)
                    continue

                if response.status_code != 200:
                    error = f"Download falhou com status {response.status_code}"
                    state.mark_failed(recording_id, error)
                    return {"recordingId": recording_id, "status": "failed", "reason": error}

                content_length = parse_content_length(response.headers)
                if content_length:
                    print(f"{recording_id}: tamanho estimado {rc.format_bytes(content_length)}")
                wait_for_disk_space(recording.account_dir, required_bytes=content_length, min_free_bytes=min_free_bytes)

                file_name = resolve_file_name(recording_id, response)
                existing_path, target_path = resolve_recording_paths(
                    recording_id,
                    file_name,
                    recording.account_dir,
                    state.load(),
                    topic=recording.topic,
                    time_recorded=recording.time_recorded,
                )
                if skip_existing and existing_path:
                    state.mark_completed(recording_id, existing_path)
                    return {
                        "recordingId": recording_id,
                        "status": "skipped",
                        "path": existing_path,
                        "reason": "Arquivo ja existe.",
                    }

                print(f"{recording_id}: salvando em {target_path}")
                stream_recording_file(
                    response,
                    target_path,
                    download_dir=recording.account_dir,
                    min_free_bytes=min_free_bytes,
                )
                state.mark_completed(recording_id, target_path)
                return {"recordingId": recording_id, "status": "downloaded", "path": target_path}

        error = f"Download falhou apos {DOWNLOAD_RETRY_ATTEMPTS} tentativas."
        state.mark_failed(recording_id, error)
        return {"recordingId": recording_id, "status": "failed", "reason": error}
    except OSError as exc:
        state.release_claim(recording_id)
        return {"recordingId": recording_id, "status": "failed", "reason": str(exc)}
    except Exception as exc:
        state.mark_failed(recording_id, exc)
        return {"recordingId": recording_id, "status": "failed", "reason": str(exc)}


def write_report(report_file, summary, available, unavailable, already_completed, download_results=None):
    report_path = Path(report_file)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    download_results = download_results or []
    report = {
        "summary": summary,
        "available": [recording.to_report_entry() for recording in available],
        "unavailable": [recording.to_report_entry() for recording in unavailable],
        "alreadyCompleted": [recording.to_report_entry() for recording in already_completed],
        "downloads": download_results,
    }
    with open(report_path, 'w', encoding='utf-8') as handle:
        json.dump(report, handle, indent=2, ensure_ascii=False)
        handle.write('\n')
    print(f"Relatorio gravado em: {report_path}")


def download_available_recordings(available, threads, state, min_free_bytes=None, skip_existing=True):
    results = []
    if not available:
        return results

    print(f"Iniciando downloads: {len(available)} gravacoes disponiveis, {threads} threads.")
    with ThreadPoolExecutor(max_workers=threads) as executor:
        future_to_recording = {
            executor.submit(
                download_checked_recording,
                recording,
                state,
                min_free_bytes=min_free_bytes,
                skip_existing=skip_existing,
            ): recording
            for recording in available
        }
        for future in as_completed(future_to_recording):
            recording = future_to_recording[future]
            try:
                result = future.result()
            except Exception as exc:
                result = {"recordingId": recording.recording_id, "status": "failed", "reason": str(exc)}
                state.mark_failed(recording.recording_id, exc)
            results.append(result)
            print(f"{result['recordingId']}: {result['status']} - {result.get('reason') or result.get('path', '')}")
    return results


def build_summary(rows, available, unavailable, already_completed, download_results=None):
    download_results = download_results or []
    downloaded = sum(1 for item in download_results if item.get("status") == "downloaded")
    failed_downloads = sum(1 for item in download_results if item.get("status") == "failed")
    skipped_downloads = sum(1 for item in download_results if item.get("status") == "skipped")
    return {
        "total": len(rows),
        "already_completed": len(already_completed),
        "available": len(available),
        "unavailable": len(unavailable),
        "downloaded": downloaded,
        "failed_downloads": failed_downloads,
        "skipped_downloads": skipped_downloads,
    }


def run(headers, csv_path, download_dir, threads, state_file, report_file, min_free_bytes, skip_existing=True, check_only=False):
    csv_path = Path(csv_path)
    download_dir = str(download_dir)
    state = DownloadState(state_file)

    if not csv_path.exists():
        raise FileNotFoundError(f"Arquivo CSV nao encontrado: {csv_path}")

    os.makedirs(download_dir, exist_ok=True)
    rows = rc.load_recording_rows(csv_path)
    print(f"Total no CSV: {len(rows)}")
    print(f"Diretorio base: {download_dir}")
    print(f"Arquivo de estado: {state_file}")
    print_disk_status(download_dir, min_free_bytes)

    available, unavailable, already_completed, headers = precheck_recordings(
        rows,
        headers,
        download_dir,
        state,
        skip_existing=skip_existing,
    )

    download_results = []
    if not check_only:
        download_results = download_available_recordings(
            available,
            threads,
            state,
            min_free_bytes=min_free_bytes,
            skip_existing=skip_existing,
        )
    else:
        print("Modo --check-only ativo: downloads nao serao iniciados.")

    summary = build_summary(rows, available, unavailable, already_completed, download_results)
    write_report(report_file, summary, available, unavailable, already_completed, download_results)
    print("Resumo:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


def build_arg_parser():
    parser = argparse.ArgumentParser(
        description="Baixa gravacoes Webex em um unico disco apos verificar todos os links disponiveis."
    )
    parser.add_argument(
        '--csv',
        default=str(rc.get_csv_path_from_env()),
        help='Caminho para recordings.csv (padrao: ../recordings.csv ou RECORDINGS_CSV).',
    )
    parser.add_argument(
        '--dir',
        default=get_download_dir_from_env(),
        help='Diretorio base unico de download (padrao: DOWNLOAD_DIR, primeiro DOWNLOAD_DIRS ou Downloaded-Recordings/).',
    )
    parser.add_argument(
        '--threads',
        type=int,
        default=get_download_threads_from_env(),
        help='Numero de downloads simultaneos (padrao: 4 ou DOWNLOAD_THREADS).',
    )
    parser.add_argument(
        '--state-file',
        default=str(rc.get_state_file_from_env()),
        help='Arquivo JSON de estado (padrao: download_state.json ou DOWNLOAD_STATE).',
    )
    parser.add_argument(
        '--report-file',
        default=str(DEFAULT_REPORT_FILE),
        help='Arquivo JSON do relatorio de disponibilidade (padrao: download_availability_report.json).',
    )
    parser.add_argument(
        '--min-free-mb',
        type=int,
        default=max(0, rc.get_min_free_bytes_from_env() // (1024 * 1024)),
        help='Espaco livre minimo no disco antes de iniciar downloads (padrao: 5120 ou DISK_MIN_FREE_MB).',
    )
    parser.add_argument(
        '--no-skip-existing',
        action='store_true',
        help='Baixa novamente mesmo se a gravacao ja estiver concluida no estado ou no disco.',
    )
    parser.add_argument(
        '--check-only',
        action='store_true',
        help='Executa apenas a pre-checagem e grava o relatorio, sem baixar arquivos.',
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    threads = max(1, args.threads)
    min_free_bytes = max(0, args.min_free_mb) * 1024 * 1024
    headers = read_token_headers()

    run(
        headers,
        csv_path=args.csv,
        download_dir=args.dir,
        threads=threads,
        state_file=args.state_file,
        report_file=args.report_file,
        min_free_bytes=min_free_bytes,
        skip_existing=not args.no_skip_existing,
        check_only=args.check_only,
    )


if __name__ == '__main__':
    main()
