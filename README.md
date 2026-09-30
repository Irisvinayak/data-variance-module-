# Data Variance Module

Period-over-period variance reporting for iDEAL regulatory returns, with a
natural-language query layer over the returns database.

The same codebase serves **two** iDEAL host applications — 5.5 (ASP.NET MVC,
single-tenant) and 6.0 (React + .NET API, multi-tenant). `VERSION` in the root
`.env` is the single switch; everything host-specific lives behind a
`HostProfile` in [backend/hosts/](backend/hosts/).

## Layout

```
.
├── backend/                  FastAPI application (the Python package)
│   ├── auth/                 login resolution, allowed-return checks
│   ├── config/               settings + per-request context
│   ├── data/                 variance engine, Oracle access, XML lookups
│   ├── hosts/                HostProfile per iDEAL version (5.5 / 6.0)
│   ├── nlp/                  NL query -> SQL (FAISS + BM25 retrieval)
│   ├── logging_config.py
│   └── main.py               route definitions
├── frontend/                 React + Vite single-page app
│   ├── public/               copied verbatim into dist/ (incl. IIS web.config)
│   └── src/
├── artifacts/                GENERATED — not edited by hand
│   └── nlp-index/
│       ├── ideal-55/         FAISS/BM25 index for the 5.5 (CIMS) database
│       └── ideal-60/         FAISS/BM25 index for the 6.0 (QCB) database
├── config/
│   └── env/                  .env templates for side-by-side deployments
├── data/                     static reference data
│   ├── mappings/             excel_tablemapping.json
│   └── schema/               Oracle DDL extracts
├── docs/                     integration plan, system overview, sample queries
├── scripts/                  build + evaluation tooling (not imported by the app)
├── tests/                    pytest suite
├── dev_server.py             uvicorn launcher (application entrypoint)
└── requirements.txt
```

`artifacts/nlp-index/` holds files produced by an **external** embedding build
tool and dropped in wholesale; the application only ever reads them.

## Setup

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

Copy the environment template you need and fill it in — see
[backend/config/settings.py](backend/config/settings.py) for every key:

```powershell
copy config\env\.env.55.example .env     # or .env.60.example
```

## Running

Backend (port follows `VERSION`: 5.5 → 8004, 6.0 → 8003):

```powershell
python dev_server.py
$env:DEV_SERVER_RELOAD=1; python dev_server.py   # with auto-reload
```

Frontend:

```powershell
cd frontend
npm install
npm run dev          # dev server, proxies to the backend port from the root .env
npm run build:55     # production bundle for the 5.5 IIS site
npm run build:60     # production bundle for the 6.0 IIS site
```

### Running 5.5 and 6.0 side by side

Start two backend processes, each with a real OS environment variable
`DV_ENV_FILE` pointing at its own overlay file (copied from `config/env/`).
The overlay is loaded after the root `.env` and only needs to carry the keys
that differ per instance. Details in
[backend/config/settings.py](backend/config/settings.py).

## Tests

```powershell
pytest
```

## Documentation

Start with the overview, then follow it into whichever reference you need.

| | |
|---|---|
| [docs/system-overview.md](docs/system-overview.md) | One-page orientation — read this first |
| [docs/architecture.md](docs/architecture.md) | Layers, request flow, the 5.5/6.0 HostProfile seam, concurrency model |
| [docs/functionality.md](docs/functionality.md) | What each feature does, in domain terms |
| [docs/models.md](docs/models.md) | Data models and API contracts, with real payloads |
| [docs/logging.md](docs/logging.md) | Log streams, levels, the AI audit trail, what the logs contain |
| [docs/integration-plan.md](docs/integration-plan.md) | Historical record of the 5.5/6.0 integration: verified differences, bugs found, open questions |
| [docs/samples/](docs/samples/) | Example NL and user queries |
