# Download de gravações — versão com pré-checagem (v3)

Esta versão baixa gravações Webex em um único diretório base. Antes de iniciar qualquer download, ela consulta todas as gravações do CSV e separa quais possuem `temporaryDirectDownloadLinks.recordingDownloadLink` disponível.

## Fluxo

```text
recordings.csv
        │
        ▼
pré-checagem de links na API Webex
        │
        ├── indisponíveis ──► download_availability_report.json
        │
        ▼
disponíveis
        │
        ▼
pool com N threads de download
        │
        ▼
Downloaded-Recordings/{conta}/{recordingId}.mp4
```

## Uso básico

```bash
python download_recordings_v3.py \
  --csv ../recordings.csv \
  --dir /mnt/disco1/WebexRecordings \
  --threads 4
```

O script grava:

- `download_state.json`: gravações concluídas, falhas e em andamento.
- `download_availability_report.json`: resumo da pré-checagem, gravações disponíveis, indisponíveis e resultados dos downloads.

## Apenas verificar disponibilidade

Use `--check-only` para gerar o relatório sem baixar arquivos:

```bash
python download_recordings_v3.py \
  --csv ../recordings.csv \
  --dir /mnt/disco1/WebexRecordings \
  --threads 4 \
  --check-only
```

## Flags principais

```text
--csv              CSV de entrada. Padrão: RECORDINGS_CSV ou ../recordings.csv
--dir              Diretório base único. Padrão: DOWNLOAD_DIR, primeiro DOWNLOAD_DIRS ou Downloaded-Recordings/
--threads          Downloads simultâneos. Padrão: DOWNLOAD_THREADS ou 4
--state-file       Estado JSON. Padrão: DOWNLOAD_STATE ou download_state.json
--report-file      Relatório JSON. Padrão: download_availability_report.json
--min-free-mb      Espaço livre mínimo antes/durante downloads. Padrão: DISK_MIN_FREE_MB ou 5120
--no-skip-existing Reprocessa gravações já concluídas no estado ou encontradas no disco
--check-only       Faz apenas a pré-checagem
```

## Como interpretar indisponíveis

No relatório, uma gravação com `statusCode` 200 e motivo `resposta 200 sem temporaryDirectDownloadLinks.recordingDownloadLink` existe na API, mas não possui link direto de download disponível para o token atual. Os metadados `playbackUrl`, `downloadUrl`, `sizeBytes`, `format` e `status`, quando retornados pela API, ajudam a diferenciar restrição de download, gravação sem mídia ou política aplicada no Webex.
