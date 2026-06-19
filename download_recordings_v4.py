import os
from pathlib import Path

import recording_common as rc
import download_recordings_v3 as v3


def run_without_precheck(
    headers,
    csv_path,
    download_dir,
    state_file,
    report_file,
    min_free_bytes,
    skip_existing=True,
    recheck_unavailable=False,
):
    csv_path = Path(csv_path)
    download_dir = str(download_dir)
    state = v3.DownloadState(state_file)

    if not csv_path.exists():
        raise FileNotFoundError(f"Arquivo CSV nao encontrado: {csv_path}")

    os.makedirs(download_dir, exist_ok=True)
    rows = rc.load_recording_rows(csv_path)
    available = []
    unavailable = []
    already_completed = []
    download_results = []

    print(f"Total no CSV: {len(rows)}")
    print(f"Diretorio base: {download_dir}")
    print(f"Arquivo de estado: {state_file}")
    print("Modo --no-precheck ativo: cada link sera obtido somente no momento do download.")
    v3.print_disk_status(download_dir, min_free_bytes)

    for index, row in enumerate(rows, start=1):
        recording_id = row[0]
        print(f"[{index}/{len(rows)}] Processando gravacao: {recording_id}")

        checked, missing, completed, headers = v3.precheck_recordings(
            [row],
            headers,
            download_dir,
            state,
            skip_existing=skip_existing,
            skip_cached_unavailable=not recheck_unavailable,
        )
        available.extend(checked)
        unavailable.extend(missing)
        already_completed.extend(completed)

        if checked:
            result = v3.download_checked_recording(
                checked[0],
                state,
                min_free_bytes=min_free_bytes,
                skip_existing=skip_existing,
            )
            download_results.append(result)
            print(f"{result['recordingId']}: {result['status']} - {result.get('reason') or result.get('path', '')}")

        summary = v3.build_summary(rows, available, unavailable, already_completed, download_results)
        v3.write_report(report_file, summary, available, unavailable, already_completed, download_results, announce=False)

    summary = v3.build_summary(rows, available, unavailable, already_completed, download_results)
    v3.write_report(report_file, summary, available, unavailable, already_completed, download_results)
    print("Resumo:")
    for key, value in summary.items():
        print(f"  {key}: {value}")


def run(
    headers,
    csv_path,
    download_dir,
    threads,
    state_file,
    report_file,
    min_free_bytes,
    skip_existing=True,
    check_only=False,
    recheck_unavailable=False,
    precheck=True,
):
    if precheck:
        return v3.run(
            headers,
            csv_path=csv_path,
            download_dir=download_dir,
            threads=threads,
            state_file=state_file,
            report_file=report_file,
            min_free_bytes=min_free_bytes,
            skip_existing=skip_existing,
            check_only=check_only,
            recheck_unavailable=recheck_unavailable,
        )

    if check_only:
        raise ValueError("--check-only depende da verificacao previa; remova --no-precheck para usa-lo.")

    if threads != 1:
        print("Aviso: --threads e ignorado quando --no-precheck esta ativo.")

    return run_without_precheck(
        headers,
        csv_path=csv_path,
        download_dir=download_dir,
        state_file=state_file,
        report_file=report_file,
        min_free_bytes=min_free_bytes,
        skip_existing=skip_existing,
        recheck_unavailable=recheck_unavailable,
    )


def build_arg_parser():
    parser = v3.build_arg_parser()
    parser.description = (
        "Baixa gravacoes Webex em um unico disco, com verificacao previa opcional dos links."
    )
    parser.add_argument(
        '--no-precheck',
        action='store_true',
        help='Desabilita a verificacao previa global; cada link e obtido apenas antes do respectivo download.',
    )
    return parser


def main():
    parser = build_arg_parser()
    args = parser.parse_args()
    threads = max(1, args.threads)
    min_free_bytes = max(0, args.min_free_mb) * 1024 * 1024
    headers = v3.read_token_headers()

    try:
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
            recheck_unavailable=args.recheck_unavailable,
            precheck=not args.no_precheck,
        )
    except ValueError as exc:
        parser.error(str(exc))


if __name__ == '__main__':
    main()
