# Download de gravações — versão multi-disco (v2)

Este documento descreve o fluxo de produção para baixar gravações Webex em múltiplos discos, com pré-processamento do CSV e distribuição por conta.

O fluxo interativo original (`recordings.py` + `download_recordings.py`) permanece disponível para uso local simples. A versão v2 é voltada ao servidor com três discos e pastas por conta.

## Visão geral

```text
recordings.csv (original)
        │
        ▼
prepare_recordings.py  ──► varre discos, filtra já baixados, divide por disco
        │
        ├── recordings_YYYYMMDD.csv
        ├── recordings_YYYYMMDD_disco0.csv
        ├── recordings_YYYYMMDD_disco1.csv
        ├── recordings_YYYYMMDD_disco2.csv
        └── prepare_report.json
        │
        ▼
download_recordings_v2.py  (um processo por disco, em paralelo)
        │
        ▼
/mnt/discoN/WebexRecordings/{conta}/{recordingId}.mp4
```

## Guia passo a passo

Fluxo completo no servidor, do zero até as gravações nos discos.

### Pré-requisitos

- Python 3.7+ e dependências instaladas (`pip install -r requirements.txt`)
- Três discos montados e acessíveis (ex.: `/mnt/disco1/WebexRecordings`, `/mnt/disco2/...`, `/mnt/disco3/...`)
- Credenciais Webex configuradas:
  - `.env` com `client_id`, `client_secret` e `refresh_token`
  - `token.json` com token de acesso válido (o downloader renova automaticamente em 401)

### Passo 1 — Gerar a lista de gravações

No diretório do projeto, execute o fluxo de listagem (equivalente à opção 1 do menu interativo):

```bash
python recordings.py
# Escolha a opção 1, informe a URL do site Webex e o período em semanas
```

Isso gera ou atualiza `recordings.csv` na pasta pai (`../recordings.csv` por padrão), com as colunas `recordingId`, `hostEmail`, `topic`, etc. O arquivo original **não é alterado** pelos passos seguintes.

### Passo 2 — Revisar configuração (opcional)

Confira se os caminhos dos discos e o CSV de entrada estão corretos. Os padrões do `run_parallel_downloads.sh` já apontam para três discos e `../recordings.csv`. Para sobrescrever:

```bash
export RECORDINGS_CSV="../recordings.csv"
export DOWNLOAD_DIRS="/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings"
```

### Passo 3 — Executar prepare + download em paralelo

A forma mais simples é um único comando:

```bash
cd /caminho/para/WebexRecordingsDownloader
chmod +x run_parallel_downloads.sh   # apenas na primeira vez
./run_parallel_downloads.sh
```

O script faz automaticamente:

1. **Prepare** (`prepare_recordings.py`) — varre os discos, remove do CSV o que já foi baixado e gera `recordings_YYYYMMDD.csv` e um CSV por disco (`_disco0`, `_disco1`, `_disco2`)
2. **Distribuição e status** — exibe quantas gravações vão para cada disco e o espaço livre
3. **Workers** — inicia um processo `download_recordings_v2.py` por disco (e por worker, se `WORKERS_PER_DISK` > 1)
4. **Encerramento** — aguarda todos os workers e imprime o status final

Se não houver gravações pendentes, o script encerra sem iniciar downloads.

### Passo 4 — Acompanhar o progresso

Durante a execução, use:

| Onde olhar | O que mostra |
|------------|--------------|
| Terminal do `run_parallel_downloads.sh` | Workers iniciados, reinícios por OOM, status final |
| `download-disk0-worker0.log` (e disco 1, 2…) | Log detalhado de cada worker |
| `prepare_report.json` | Totais do prepare, pendências por disco, itens em `manual_review` |
| `download_state_discoN.json` | Concluídas, falhas e em andamento por disco |

Para consultar o status sem baixar:

```bash
python download_recordings_v2.py \
  --csv recordings_20250612.csv \
  --dirs "/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings" \
  --show-status
```

Substitua `20250612` pela data dos CSVs gerados (`RECORDINGS_DATE`).

### Passo 5 — Reexecutar ou retomar

**Nova rodada completa** (lista atualizada + prepare + download):

```bash
python recordings.py   # opção 1 — atualizar recordings.csv
./run_parallel_downloads.sh
```

**Só download**, reutilizando CSVs já preparados no mesmo dia:

```bash
RUN_PREPARE=0 RECORDINGS_DATE=20250612 ./run_parallel_downloads.sh
```

**Prepare isolado** (útil para inspecionar pendências antes de baixar):

```bash
python prepare_recordings.py \
  --csv ../recordings.csv \
  --dirs "/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings" \
  --output-dir .
```

Depois, com `RUN_PREPARE=0` e a data correta em `RECORDINGS_DATE`, rode o shell script ou baixe disco a disco (ver seção [Passo 2 — Baixar as gravações](#passo-2--baixar-as-gravações)).

### Passo 6 — Ajustes comuns

```bash
# Mais margem de espaço livre (10 GB) e espera maior entre tentativas
DISK_MIN_FREE_MB=10240 DISK_SPACE_WAIT_SEC=120 ./run_parallel_downloads.sh

# Ajustar workers pela RAM disponível
AUTO_WORKERS=1 ./run_parallel_downloads.sh

# Dois workers por disco (sharding por hash do e-mail)
WORKERS_PER_DISK=2 ./run_parallel_downloads.sh
```

### Checklist rápido

```text
[ ] recordings.csv gerado (recordings.py opção 1)
[ ] .env e token.json configurados
[ ] Discos montados e com espaço livre
[ ] ./run_parallel_downloads.sh executado
[ ] Logs e download_state_discoN.json revisados
[ ] prepare_report.json — conferir manual_review se houver
```

## Arquivos do fluxo v2

| Arquivo | Função |
|---------|--------|
| `prepare_recordings.py` | Pré-processa o CSV: remove gravações já no disco e divide em um arquivo por disco |
| `download_recordings_v2.py` | Baixa gravações para a pasta da conta no disco correto |
| `run_parallel_downloads.sh` | Orquestra prepare + workers paralelos |
| `recording_common.py` | Utilitários compartilhados (distribuição por disco, leitura de CSV) |
| `recording_paths.py` | Resolução de nomes de arquivo no disco (canônico, legado, hash) |
| `download_state_discoN.json` | Estado por disco (concluídas, falhas, em andamento); workers do mesmo disco compartilham o arquivo |

Arquivos **não alterados** por este fluxo:

- `download_recordings.py` — downloader simples (pasta flat `Downloaded-Recordings/`)
- `recordings.py` — menu interativo original

## Estrutura no disco

Cada disco tem diretórios por conta (e-mail sanitizado) e gravações identificadas pelo `recordingId`:

```text
/mnt/disco1/WebexRecordings/
  usuario@empresa.com/
    abc123def456789012345678901234ab.mp4
/mnt/disco2/WebexRecordings/
  outro@empresa.com/
    fedcba987654321098765432109876fe.mp4
/mnt/disco3/WebexRecordings/
  ...
```

O nome canônico é `{recordingId}.mp4`. O sistema também reconhece arquivos com nomes legados (`{tópico}_{data}.mp4` ou com sufixo `_{8 primeiros chars do id}.mp4`) para não baixar duplicatas.

## Distribuição por disco

A conta (`hostEmail`) é atribuída a um disco de forma determinística:

```python
digest = SHA-256(hostEmail em minúsculas)
disk_index = int(digest[:8], 16) % número_de_discos
```

Com três discos, cada conta vai sempre para o mesmo disco. O `prepare_recordings.py` usa a mesma regra ao dividir o CSV.

## Formato do CSV

Colunas esperadas (o `recordings.csv` original **não é modificado**):

```csv
recordingId,hostEmail,topic,timeRecorded,createTime,durationSeconds
```

| Coluna | Obrigatória para download |
|--------|---------------------------|
| `recordingId` | Sim |
| `hostEmail` | Sim |
| `topic` | Não (ajuda a reconhecer nomes legados) |
| `timeRecorded` | Não (idem) |
| `createTime` | Não |
| `durationSeconds` | Não |

## Passo 1 — Preparar os CSVs

Varre os discos, identifica o que já foi baixado e gera os arquivos filtrados.

```bash
python prepare_recordings.py \
  --csv ../recordings.csv \
  --dirs "/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings" \
  --output-dir .
```

### Saídas

| Arquivo | Conteúdo |
|---------|----------|
| `recordings_YYYYMMDD.csv` | Todas as gravações pendentes |
| `recordings_YYYYMMDD_disco0.csv` | Pendentes do disco 0 |
| `recordings_YYYYMMDD_disco1.csv` | Pendentes do disco 1 |
| `recordings_YYYYMMDD_disco2.csv` | Pendentes do disco 2 |
| `prepare_report.json` | Resumo: totais, por disco, casos para revisão manual |

`YYYYMMDD` é a data da execução. Use `--date 20250612` para forçar um sufixo específico.

### Como o filtro detecta gravações já baixadas

Para cada arquivo `.mp4` em `{disco}/{conta}/`:

1. Nome canônico — stem de 32 caracteres hex = `recordingId`
2. Sufixo hash — `_*xxxxxxxx.mp4` cruzado com prefixo do ID no CSV
3. Nome legado — `{tópico}_{timestamp}.mp4` via metadados do CSV

Uma linha do CSV é excluída do arquivo filtrado quando o `recordingId` já existe no disco (em qualquer um dos três), na pasta da conta correspondente.

Casos com hash ambíguo (dois IDs com o mesmo prefixo de 8 caracteres) são listados em `prepare_report.json` em `manual_review` e **não** são marcados como baixados automaticamente.

## Passo 2 — Baixar as gravações

### Opção recomendada: script paralelo

```bash
./run_parallel_downloads.sh
```

Por padrão (`RUN_PREPARE=1`), o script executa o `prepare_recordings.py` e em seguida inicia um worker por disco, cada um com o CSV correspondente.

### Opção manual: um disco por vez

```bash
python download_recordings_v2.py \
  --csv recordings_20250612_disco0.csv \
  --dirs "/mnt/disco1/WebexRecordings,/mnt/disco2/WebexRecordings,/mnt/disco3/WebexRecordings" \
  --disk-index 0
```

Com `--disk-index`, o estado padrão é `download_state_disco0.json` (um arquivo por disco). Workers paralelos no mesmo disco continuam compartilhando esse arquivo via lock.

```bash
# Estado explícito (opcional)
python download_recordings_v2.py \
  --csv recordings_20250612_disco0.csv \
  --dirs "..." \
  --disk-index 0 \
  --state-file meu_estado_disco0.json
```

Repita com `--disk-index 1` e `--disk-index 2` para os outros discos.

### Comportamento do downloader v2

- Salva em `{disco}/{conta}/{recordingId}.mp4`
- Download em streaming (1 MB por chunk) via arquivo temporário `.part`
- Pula gravações já no disco ou marcadas como concluídas no estado do disco
- Monitora espaço livre antes e durante o download; aguarda se estiver abaixo do mínimo
- Lock de arquivo (`fcntl`) para evitar duplicação entre workers do mesmo disco
- Refresh automático do token OAuth em respostas 401
- Espera em caso de rate limit (HTTP 429)

### Flags úteis

```bash
# Ver distribuição planejada sem baixar
python download_recordings_v2.py --csv recordings_20250612.csv --dirs "..." --show-distribution

# Ver status (concluídas, pendentes, falhas)
python download_recordings_v2.py --csv recordings_20250612.csv --dirs "..." --show-status

# Múltiplos workers por disco (shard por hash do e-mail)
python download_recordings_v2.py --csv ... --disk-index 0 --worker-index 0 --workers-per-disk 2
```

## Variáveis de ambiente

| Variável | Padrão | Descrição |
|----------|--------|-----------|
| `RECORDINGS_CSV` | `../recordings.csv` | CSV original de entrada |
| `DOWNLOAD_DIRS` | `/mnt/disco1/...,/mnt/disco2/...,/mnt/disco3/...` | Discos separados por vírgula |
| `DOWNLOAD_STATE` | `download_state_discoN.json` por disco | Arquivo de estado; use `{disk}` no caminho para template por disco |
| `DISK_MIN_FREE_MB` | `5120` | Espaço livre mínimo (MB) antes de iniciar cada download |
| `DISK_SPACE_WAIT_SEC` | `60` | Intervalo de espera quando o disco está abaixo do mínimo |
| `WORKERS_PER_DISK` | `1` | Workers paralelos por disco |
| `RUN_PREPARE` | `1` | Se `1`, executa prepare antes dos downloads |
| `RECORDINGS_DATE` | data de hoje (`YYYYMMDD`) | Sufixo dos CSVs gerados/consumidos |
| `PREPARE_OUTPUT_DIR` | diretório do script | Onde gravar os CSVs filtrados |
| `AUTO_WORKERS` | `0` | Se `1`, ajusta workers pela RAM disponível |
| `MEM_RESERVE_MB` | `2048` | RAM reservada para o sistema |
| `MEM_PER_WORKER_MB` | `400` | Estimativa de RAM por worker |

Credenciais OAuth (refresh de token): `client_id`, `client_secret`, `refresh_token` no arquivo `.env`. Token de acesso em `token.json`.

## Monitoramento de espaço em disco

Antes de cada download, o v2 verifica se o disco de destino tem espaço livre suficiente:

- Reserva mínima configurável (`DISK_MIN_FREE_MB`, padrão 5 GB)
- Se o servidor informar `Content-Length`, o tamanho do arquivo também entra no cálculo
- Durante o streaming, o espaço é revalidado a cada ~100 MB
- Se o espaço for insuficiente, o worker **aguarda** (`DISK_SPACE_WAIT_SEC`, padrão 60 s) e tenta de novo, sem marcar a gravação como falha

```bash
DISK_MIN_FREE_MB=10240 DISK_SPACE_WAIT_SEC=120 ./run_parallel_downloads.sh
```

O `--show-status` também exibe o espaço livre em cada disco.

## Logs e monitoramento

O `run_parallel_downloads.sh` grava um log por worker:

```text
download-disk0-worker0.log
download-disk1-worker0.log
download-disk2-worker0.log
```

O `prepare_report.json` e os arquivos `download_state_discoN.json` permitem acompanhar o progresso sem inspecionar os discos.

## Casos especiais

### CSV vazio após o filtro

Se todas as gravações já estiverem no disco, o prepare gera CSVs apenas com o cabeçalho e imprime um aviso. O `run_parallel_downloads.sh` detecta isso e encerra sem iniciar workers.

### Nomes legados no disco

Gravações baixadas com a versão antiga (`{tópico}_{data}.mp4`) são reconhecidas pelo prepare e pelo v2, desde que `topic` e `timeRecorded` estejam no CSV. Para normalizar nomes no disco, use `reconcile_downloads.py` (ferramenta separada de manutenção).

### Reinício após falha (OOM)

O shell reinicia workers mortos por falta de memória (códigos 137/143) até `MAX_WORKER_RESTARTS` vezes. Ajuste `WORKERS_PER_DISK` ou use `AUTO_WORKERS=1` se necessário.

## Comparação com o fluxo original

| Aspecto | Fluxo original | Fluxo v2 |
|---------|----------------|----------|
| Entrada | `recordings.py` opção 2 | `prepare_recordings.py` + `download_recordings_v2.py` |
| Destino | `Downloaded-Recordings/` (flat) | `{disco}/{conta}/{recordingId}.mp4` |
| Filtro de já baixados | Não | Sim (varredura dos discos) |
| Paralelismo | Sequencial | Um processo por disco (ou mais com workers) |
| Estado | Nenhum | `download_state_discoN.json` (um por disco) |
| Script principal | `download_recordings.py` | `download_recordings_v2.py` |

## Dependências

As mesmas do projeto principal (`requirements.txt`):

```bash
pip install -r requirements.txt
```

Pacotes utilizados: `requests`, `python-dotenv`.
