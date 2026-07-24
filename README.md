<p align="center">
  <img src="frontend/public/hugginghack-mark.svg" width="92" alt="HuggingHack terminal-face mark">
</p>

<h1 align="center">HuggingHack</h1>

<p align="center">
  <strong>Bring the Hugging Face Hub home.</strong><br>
  Browse live models, choose exactly which files to keep, and build a clean local library on your PC or NAS.
</p>

<p align="center">
  <img src="https://img.shields.io/badge/React-18-20232A?logo=react&logoColor=61DAFB" alt="React 18">
  <img src="https://img.shields.io/badge/TypeScript-5.7-3178C6?logo=typescript&logoColor=white" alt="TypeScript 5.7">
  <img src="https://img.shields.io/badge/FastAPI-0.116-009688?logo=fastapi&logoColor=white" alt="FastAPI 0.116">
  <img src="https://img.shields.io/badge/Docker-Compose-2496ED?logo=docker&logoColor=white" alt="Docker Compose">
  <img src="https://img.shields.io/badge/self--hosted-NAS%20ready-F59E0B" alt="Self-hosted and NAS ready">
  <a href="https://github.com/tyedalwaves/HuggingHack/actions/workflows/ci.yml"><img src="https://github.com/tyedalwaves/HuggingHack/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
</p>

<p align="center">
  <a href="#features">Features</a> ·
  <a href="#screenshots">Screenshots</a> ·
  <a href="#quick-start-on-this-pc">Quick start</a> ·
  <a href="#move-it-to-the-nas">NAS setup</a> ·
  <a href="#security">Security</a>
</p>

![HuggingHack model catalog showing live model cards, filters, search, and download actions](docs/images/models-catalog.png)

<p align="center"><sub>Live Hub discovery with practical metadata, storage-aware downloads, and no cloud dashboard in the middle.</sub></p>

> [!NOTE]
> HuggingHack is an unofficial, local-first project. It is not affiliated with or endorsed by Hugging Face.

<table>
  <tr>
    <td width="33%" valign="top"><strong>🔎 Discover</strong><br>Search the live model catalog and narrow it by task, format, local app, parameter count, or popularity.</td>
    <td width="33%" valign="top"><strong>🎯 Download precisely</strong><br>Keep a full repository, SafeTensors, one GGUF, metadata only, or your own include and exclude patterns.</td>
    <td width="33%" valign="top"><strong>🏠 Own the library</strong><br>Store models in a plain folder on your disk or NAS and index files you copied there yourself.</td>
  </tr>
</table>

## Features

- Familiar Hub-style model catalog with visual, metadata-driven model cards plus task, format, local-app, parameter, and sort filters
- Live Hugging Face metadata, repository file lists, richly rendered model cards, gated status, likes, and download counts
- On-demand GGUF metadata and tensor inspection with shard position, names, shapes, data types, and parameter totals
- Full repository, SafeTensors, single-GGUF, metadata-only, and custom-pattern download modes
- Background downloads with revision selection, byte progress, speed, cancellation, and history
- Restart recovery: interrupted jobs resume through Hugging Face's local-dir metadata
- Automatic local-library indexing with model size, file count, config metadata, and unsafe serialization warnings
- Built-in local accounts with a first-run owner, HTTP-only sessions, and administrator-created member accounts
- Per-account saved models, private notes, and project or rig collections
- Private or locally shared user repositories with resumable, chunked model-folder uploads
- Optional S3-compatible durable storage with a local working cache, remote browsing, restore, and cache eviction
- Network runtime jobs: transfer models to Ollama or switch a remote vLLM rig through an authenticated manager
- Ownership-verified repository deletion with exact-name confirmation
- Optional read-only `HF_TOKEN` support for private and gated models
- Light/dark themes and responsive desktop/mobile layouts
- One Docker Compose service with persistent model and application-data mounts

## Screenshots

The catalog above is the main workspace. Open any model to inspect its repository, estimate
storage, and choose the exact download mode without leaving the app. Local accounts add
private shortlists, notes, collections, and repositories without turning HuggingHack into a
hosted service.

<table>
  <tr>
    <td width="50%" valign="top">
      <img src="docs/images/account-setup.png" alt="HuggingHack first-run owner account setup">
    </td>
    <td width="50%" valign="top">
      <img src="docs/images/saved-library.png" alt="HuggingHack saved model library with collections and private notes">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>One-time local owner setup with no hosted identity service</sub></td>
    <td align="center"><sub>Per-account model shortlists, collections, and private notes</sub></td>
  </tr>
</table>

![HuggingHack account repositories with private and shared model uploads](docs/images/account-uploads.png)

<p align="center"><sub>Resumable model-folder uploads into private or locally shared repositories on the mounted drive</sub></p>

<table>
  <tr>
    <td width="68%" valign="top">
      <img src="docs/images/model-details-dark.png" alt="HuggingHack dark-theme model details and download options">
    </td>
    <td width="32%" valign="top">
      <img src="docs/images/mobile-catalog.png" alt="HuggingHack responsive mobile model catalog">
    </td>
  </tr>
  <tr>
    <td align="center"><sub>Repository details and file-aware download controls in dark mode</sub></td>
    <td align="center"><sub>The same live catalog on mobile</sub></td>
  </tr>
</table>

## Quick start on this PC

1. Install and start Docker Desktop.
2. Double-click **Start HuggingHack.bat**.
3. Open [http://localhost:7860](http://localhost:7860).

The first launch builds the container. Later launches reuse the image unless the project changes.
On the first browser visit, HuggingHack asks you to create the owner account. Use a unique
password of at least 12 characters. The owner can add local member accounts from **Settings**.

Command-line equivalent:

```powershell
Copy-Item .env.example .env
docker compose up --build -d
```

Stop it with **Stop HuggingHack.bat** or:

```powershell
docker compose down
```

Models and the default SQLite database are persistent and are not removed by
`docker compose down`.

## Use PostgreSQL

SQLite remains the zero-configuration default. For a multi-user deployment or an external
database service, set `DATABASE_URL` to a PostgreSQL connection URL:

```dotenv
DATABASE_URL=postgresql://hugginghack:password@database-host:5432/hugginghack
```

HuggingHack creates and upgrades its tables at startup. PostgreSQL credentials stay on the
server and are not returned by the API.

An optional Compose overlay runs PostgreSQL 17 beside HuggingHack. Add a long URL-safe
password to `.env`, then start both services:

```dotenv
POSTGRES_PASSWORD=replace-with-a-long-random-password
```

```powershell
docker compose -f docker-compose.yml -f docker-compose.postgres.yml up --build -d
```

The overlay stores PostgreSQL data in the `postgres-data` named volume and waits for the
database health check before starting HuggingHack. Back it up separately from `./data`.
Switching `DATABASE_URL` does not copy an existing SQLite installation into PostgreSQL.

## Accounts, saved models, and uploads

Accounts are local to this HuggingHack installation—there is no hosted identity service and
no account data leaves the server. Each member gets a separate saved-model library, private
notes, collections, and download history. Members can rotate their own password from
**Settings**; doing so revokes their other active sessions.

Use the heart on a Hub model to save it without downloading. The **Saved** workspace can
organize those models into multiple collections, such as a project shortlist or a target rig.

The **Uploads** workspace creates repositories under the signed-in owner name:

```text
models/
  your-username/
    your-repository/
      .hugginghack.json
      config.json
      model.safetensors
      ...
```

Choose a model folder in the browser and HuggingHack sends each file in bounded chunks.
Interrupted uploads keep their progress and resume from the server's confirmed offset.
Uploaded repositories are private by default; their owner can share them with every local
account. Model files stay in the model mount rather than in the metadata database.

To preserve the original trusted-LAN behavior, set `ACCOUNTS_ENABLED=false`. This creates a
single local compatibility identity and skips sign-in. Do not use that mode on an untrusted
network.

## Choose the model folder

Edit `.env` and set `MODEL_STORAGE_PATH` to the host folder that should contain models:

```dotenv
MODEL_STORAGE_PATH=./models
```

The container sees this folder as `/models`. Managed repositories are stored in a plain hierarchy:

```text
models/
  organization/
    repository/
      .hugginghack.json
      config.json
      model.safetensors
      ...
```

That layout is portable and works with vLLM, llama.cpp, Ollama import workflows, Transformers, Diffusers, and other tools that accept a local repository path.

## Use S3-compatible model storage

Set `MODEL_STORAGE_BACKEND=s3` to keep complete managed repositories in AWS S3 or an
S3-compatible service such as MinIO or Ceph. `/models` remains a local working cache because
vLLM, llama.cpp, and similar runtimes require filesystem paths.

```dotenv
MODEL_STORAGE_BACKEND=s3
MODEL_STORAGE_PATH=./models
S3_BUCKET=my-model-bucket
S3_PREFIX=models
S3_REGION=us-east-1
AWS_ACCESS_KEY_ID=replace-me
AWS_SECRET_ACCESS_KEY=replace-me
```

For MinIO or another custom endpoint:

```dotenv
S3_ENDPOINT_URL=http://minio:9000
S3_ADDRESSING_STYLE=path
S3_USE_SSL=false
```

HuggingHack also supports boto3's normal credential chain, including attached IAM roles, so
static keys are optional on AWS. Credentials stay server-side and are never returned by the API.
Downloads and finalized browser uploads sync automatically. The manifest is published last, so
partially transferred repositories are not indexed as complete. From the Local library you can
remove a local cache copy while keeping its durable S3 copy, then restore it when an inference
runtime needs the files.

The bucket identity needs `s3:ListBucket` on the bucket and `s3:GetObject`,
`s3:PutObject`, and `s3:DeleteObject` on the configured prefix.
Keep the metadata database backed up too: private upload manifests fail closed unless their
matching ownership metadata is present.

## Send models to Ollama or vLLM

HuggingHack can dispatch a cached model to another inference device on the same network.
Destinations are configured server-side so endpoints and credentials never have to be entered
in the browser. The owner can then choose **Local library → model → Send to runtime**, while
automation can use the same API.

Add one or both target types to `.env` on a single line:

```dotenv
RUNTIME_TARGETS_JSON=[{"id":"ollama-rig","name":"Ollama GPU","kind":"ollama","base_url":"http://192.168.0.36:11434","keep_alive":"15m"},{"id":"vllm-rig","name":"vLLM GPU","kind":"vllm","base_url":"http://192.168.0.35:8090","remote_model_root":"/mnt/nas/models","token_env":"VLLM_AGENT_TOKEN"}]
RUNTIME_WORKERS=2
VLLM_AGENT_TOKEN=replace-with-the-same-long-random-secret-used-on-the-agent
```

The two adapters deliberately handle storage differently:

- **Ollama** uses its native blob and create APIs. HuggingHack hashes each required file, skips
  blobs the remote server already has, transfers missing data over HTTP, creates the Ollama
  model, and preloads it for the configured `keep_alive`. A repository needs one selected GGUF
  or a root-level SafeTensors model supported by Ollama.
- **vLLM** reads the existing NAS files instead of copying them. Mount the HuggingHack model
  folder on the vLLM device, then set `remote_model_root` to that device's mount path. vLLM
  fixes its base model at process startup, so the authenticated agent stops the process it
  manages and starts `vllm serve` with the selected model. Active inference requests will be
  interrupted during a switch.

S3-only models must be restored to the local cache before either adapter can use them.

### Run the vLLM agent

On the vLLM device, mount the same model share and run the small manager included in this
repository. The token is mandatory and must match the environment variable forwarded to the
HuggingHack container:

```bash
export VLLM_AGENT_TOKEN='replace-with-a-long-random-secret'
export VLLM_AGENT_MODEL_ROOT=/mnt/nas/models
export VLLM_AGENT_VLLM_PORT=8000
export VLLM_AGENT_EXTRA_ARGS_JSON='["--gpu-memory-utilization","0.9"]'
python -m uvicorn app.vllm_agent:app \
  --app-dir backend \
  --host 0.0.0.0 \
  --port 8090
```

The agent never accepts a shell command or arbitrary model path. It only starts `vllm serve`
for a directory inside `VLLM_AGENT_MODEL_ROOT`, with additional vLLM arguments fixed by the
agent administrator through `VLLM_AGENT_EXTRA_ARGS_JSON`. Do not run a separate vLLM server on
the configured vLLM port; the agent owns that process.

### Runtime API

Set `RUNTIME_API_TOKEN` to enable bearer-token automation scoped to runtime targets, loads, and
job history:

```dotenv
RUNTIME_API_TOKEN=replace-with-another-long-random-secret
```

Queue a load:

```bash
curl -X POST http://NAS-IP:7860/api/runtimes/ollama-rig/load \
  -H "Authorization: Bearer $RUNTIME_API_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"repo_id":"bartowski/Qwen2.5-7B-Instruct-GGUF","runtime_model_name":"qwen-local","source_file":"Qwen2.5-7B-Instruct-Q4_K_M.gguf"}'
```

The response is a persistent asynchronous job. Read it at
`GET /api/runtime-jobs/{job_id}`, list history at `GET /api/runtime-jobs`, and discover
configured destinations at `GET /api/runtimes`. The same endpoints also accept the owner's
normal browser session and CSRF token. Interactive API documentation is available at
`http://NAS-IP:7860/api/docs`.

## Move it to the NAS

Copy the entire `HuggingHack` directory to your NAS, then change only `MODEL_STORAGE_PATH` in `.env`.

The host model folder and the project's `data` folder must exist before the container starts. Synology Container Manager does not always create missing bind-mount sources. For a project stored at `/volume1/docker/HuggingHack`, create them in File Station or over SSH:

```bash
mkdir -p /volume1/docker/HuggingHack/models
mkdir -p /volume1/docker/HuggingHack/data
```

Then set `MODEL_STORAGE_PATH=/volume1/docker/HuggingHack/models`. If you choose another model location, create that exact path first.

Common examples:

```dotenv
# Synology
MODEL_STORAGE_PATH=/volume1/AI/models

# TrueNAS
MODEL_STORAGE_PATH=/mnt/tank/ai/models

# QNAP
MODEL_STORAGE_PATH=/share/Container/models
```

If your NAS enforces Unix ownership, set its user and group IDs:

```dotenv
PUID=1026
PGID=100
```

Find them over SSH with `id your-nas-user`. Then launch from the project directory:

```bash
docker compose up --build -d
```

Open `http://NAS-IP:7860` from another computer on the LAN.

## Gated and private models

1. Sign in at Hugging Face and accept the repository's license or access terms in your browser.
2. Create a read-only user access token.
3. Put it in `.env`:

```dotenv
HF_TOKEN=hf_your_read_token
```

4. Restart the service:

```bash
docker compose up -d
```

The token is read only by the backend container. It is never returned by the API or sent to the browser.

## File filtering

The model drawer offers five download modes:

- **Full repository** downloads every file in the selected revision.
- **SafeTensors** selects safe weights plus configuration and tokenizer files.
- **One GGUF** lets you choose a specific quantization from the repository file list.
- **Metadata only** fetches configuration, tokenizer, and documentation files without weights.
- **Custom** accepts comma-separated include and exclude patterns.

Custom pattern examples:

- Include only SafeTensors and config files: `*.safetensors, *.json, tokenizer*`
- Download one GGUF quantization: `*Q4_K_M.gguf, *.json, tokenizer*`
- Exclude legacy PyTorch weights: `*.bin, *.pt, *.pth`

Patterns use Hugging Face's official `snapshot_download` filtering.

## GGUF metadata and tensors

Repositories containing GGUF files get a **GGUF** tab in the model drawer. Select a file or
shard to inspect its metadata, tensor names, shapes, data types, quantization breakdown, and
parameter count without downloading the model weights.

HuggingHack reads only bounded byte ranges from the selected file, caches the result for the
browser session, and leaves every other shard untouched until you select it. Private and gated
repositories use the backend's `HF_TOKEN`; the token is never exposed to the browser.

## Cancel and resume

Active downloads have a **Cancel download** action. Cancellation stops the isolated download worker, keeps already transferred files and Hugging Face local-directory metadata, and marks the job as cancelled in history. Starting the same repository again can reuse those partial files instead of discarding the completed work.

## Manually added models

Copy a model folder anywhere within the first few directory levels of the mounted model folder, then choose **Local library → Scan folder**. HuggingHack recognizes common configs and weight extensions such as:

- `config.json`, `model_index.json`, `tokenizer.json`
- `.safetensors`, `.gguf`, `.onnx`, `.bin`, `.pt`, `.pth`, and `.ckpt`

Manually copied models are indexed but never modified.

## Security

- HuggingHack downloads files but does not execute repository code, import model modules, or deserialize weights.
- Model cards are rendered as sanitized Markdown with safe HTML, readable code, tables, lists, and math; embedded scripts, forms, and frames are discarded.
- Pickle-compatible formats can execute code when loaded by other applications. Prefer SafeTensors or GGUF and only load models from publishers you trust.
- Passwords are salted and hashed with `scrypt`; sessions use hashed random tokens in HTTP-only, SameSite cookies and state-changing requests require a per-session CSRF token.
- Built-in accounts protect application data, but public exposure still requires HTTPS. Put HuggingHack behind a TLS reverse proxy such as Caddy, Traefik, or Nginx Proxy Manager and set `SECURE_COOKIES=true`.
- Upload paths are confined to repositories owned by the signed-in account. Repository deletion verifies ownership and requires the exact repository name.
- Runtime dispatch is administrator-only in the UI. Optional bearer access is limited to runtime endpoints; use long random tokens and firewall Ollama and the vLLM agent to trusted LAN clients.
- The vLLM agent rejects paths outside its configured model root and launches a fixed argument vector without a shell.
- Use a read-only Hugging Face token.

## Development

Backend:

```powershell
py -3.13 -m venv .venv
.venv\Scripts\pip install -r backend\requirements.txt
$env:MODEL_STORAGE="$PWD\models"
$env:DATA_DIR="$PWD\data"
.venv\Scripts\uvicorn app.main:app --app-dir backend --reload --port 7860
```

Python 3.12 or 3.13 is recommended for local development. The Docker image uses Python 3.12, so Python is not required on the NAS.

Frontend:

```powershell
Set-Location frontend
npm install
npm run dev
```

The Vite development server proxies `/api` to port 7860.
Frontend development and production builds require Node.js 22 or newer. The lockfile is maintained with npm 10.9.8.

Tests and build:

```powershell
$env:PYTHONPATH="$PWD\backend"
pytest backend\tests
Set-Location frontend
npm test
npm run build
```

## Data ownership and backups

- Models: the host path configured by `MODEL_STORAGE_PATH`
- S3 mode: durable model objects in `S3_BUCKET` and working copies in `MODEL_STORAGE_PATH`
- Accounts, sessions, saved collections, repository ownership, download history, and local
  index: `./data/hugginghack.sqlite3` by default, or the database named by `DATABASE_URL`
- Hub metadata cache: `./data/hub-cache`

Back up the models folder and metadata database together. Keep backing up `data` for the Hub
cache and for SQLite deployments. The model index can be rebuilt from model files, but the
database preserves accounts, saved-model organization, ownership, and download history.
Store backups securely because it contains password hashes and active session hashes.
