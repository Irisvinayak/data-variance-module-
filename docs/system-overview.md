Data Variance (Regulatory Variance & NL-Query Assistant) — System Overview

1. Introduction

Data Variance is a standalone AI-assisted module for computing period-over-period variance on RBI/regulatory-return data, designed to simplify manual variance analysis and enable natural-language querying of enterprise reporting data through conversational AI.

The assistant helps users with:
• Finding a regulatory return/report by name
• Manually selecting table, date and period to compute variance
• Performing comparative variance analysis across reporting periods
• Resolving natural-language questions into a variance computation
• Running free-form natural-language database queries
• Enforcing return-level access control per logged-in user

The platform combines FastAPI backend services, LLM inference using Ollama, semantic vector search (FAISS), Oracle database querying, and XML-driven configuration/authorization into a modular enterprise module, deployed alongside an existing .NET host application.

2. User Features

Feature                     Description
Return Lookup               Fuzzy-find a return/report by name
Manual Variance Compute     Select table, date, period; compute diff & % change vs. prior period(s)
Comparative Analysis        Compare reporting periods (vs-current or sequential chaining)
NL Resolve                  Ask a natural-language question; resolves table/column/date automatically
NL→SQL Query                Free-form question answered via LLM-generated SQL (sqlcoder)
Return-Level Authorization  Access limited to returns permitted for the user's department
My Returns (debug)          List return IDs the current login can access

3. Guided Workflow Features

The system supports a deterministic guided (wizard) workflow for the manual path, kept intentionally separate from the NLP path.

Guided Categories
• 📋 Find a return
• 📊 Select table, date & reporting period
• 🔁 Choose comparison mode (vs-current / sequential)
• 📄 View computed variance results

Guided mode avoids ambiguity by collecting inputs step-by-step instead of relying on LLM interpretation. Per project policy, this guided/manual flow and the NLP flow (/variance/nlquery, sqlcoder) are deliberately decoupled and must never be interconnected.

4. System Objectives

The platform is designed to:
• Automate variance computation across regulatory reporting periods
• Provide both a deterministic manual path and a conversational NL path to the same data
• Support guided, step-by-step workflows for non-technical users
• Enable semantic database querying via embeddings + FAISS
• Simplify comparative (period-over-period) analysis
• Enforce return-level access control sourced from existing XML/.NET auth data
• Support enterprise deployment alongside an existing .NET host application
• Keep the NLP layer swappable/optional without affecting the core compute engine

5. High-Level Architecture

React Frontend (embedded via iframe in .NET Repo5.5 host)
       │
       ▼
FastAPI Backend (backend/main.py)
       │
       ├── Return/Report Lookup
       ├── Table Mapping Resolution
       ├── Variance Compute Engine
       ├── NLP Subsystem
       │     ├── Embedding + FAISS Retrieval
       │     ├── Intent Resolver
       │     ├── Date Resolver
       │     └── SQL Generator
       ├── Auth Service (XML-driven)
       └── Logging
               │
               ▼
        Ollama LLM Server
               │
               ├── qwen2.5:7b
               └── sqlcoder-7b-2:Q5_K_M

6. Backend Architecture

Module                    Purpose
main.py                   FastAPI application entry point, all route definitions
service.py                Orchestration: return lookup → table mapping → compute
calculate_variance.py     Core variance math (frequency-aware, Decimal-safe)
report_lookup.py          Fuzzy scored search over Returns.xml
db.py                     Oracle connection pooling (oracledb, thin mode)
xml_loader.py             Defensive XML parsing
auth_service.py / auth_deps.py   XML-driven login + return-access checks (TTL cached)
nlp/                      Embedding, FAISS retrieval, intent/date resolution, SQL generation
logging_config.py         Daily rotating file handler + console logging
models.py                 Pydantic request/response models
config.py                 .env-driven system configuration

7. API Endpoints

Endpoint                              Purpose
GET  /health                          Health monitoring endpoint
GET  /variance/find                   Fuzzy return lookup + list of tables
POST /variance/compute                Manual variance compute (table/date/period)
POST /variance/nlresolve              NL question → resolve table/date via embeddings + LLM → compute
POST /variance/nlquery                Free-form NL → SQL (sqlcoder) → raw execution
GET  /auth/my-returns                 Debug: returns accessible to the logged-in user

8. AI / ML Technologies

Technology              Purpose
Ollama                  LLM inference server (remote proxy)
SentenceTransformers    Embedding generation (BAAI/bge-large-en)
FAISS                   Semantic vector similarity search
Embedding Models        Semantic indexing of tables/columns/row labels
LLM Inference           Intent resolution + SQL generation

9. LLM Models Used

9.1 Intent & Date Resolution Model
Property         Value
Model            qwen2.5:7b
Purpose          Intent + entity resolution, date resolution
Deployment       Via Ollama (remote proxy)
Responsibilities
• Pick best table/column from the authorized shortlist
• Return structured JSON intent
• Resolve relative/absolute date expressions to reporting_date/reporting_period

9.2 SQL Generation Model
Property         Value
Model            sqlcoder-7b-2:Q5_K_M (defog)
Purpose          Free-form Oracle SQL generation for /variance/nlquery
Deployment       Via Ollama (remote proxy)
Responsibilities
• Generate SELECT-only Oracle SQL from natural language
• Apply period-comparison/variance self-join prompt rules
• Handle VERTICAL table and DOM/OVE column conventions
Note: qwen2.5:7b is used for intent/JSON tasks and sqlcoder for SQL generation because
each model is weak at the other's task — this split is a deliberate design decision.

9.3 (Not currently implemented)
No dedicated enterprise-analytics/cloud-hosted reasoning model is used; all SQL generation
and reasoning run through the two local/proxied models above.

9.4 (Not currently implemented)
No AI-based error-explanation model exists in this module. There is no XBRL/XML
validation-error pipeline in Data Variance — this module works purely with tabular
Oracle return data.

10. Speech-to-Text Pipeline

Not implemented in this module. All input is text-based (manual wizard selections or
typed natural-language queries).

11. Intent Detection System

Variance/Report Intents
• find_return
• compute_variance
• resolve_nl_query

NL→SQL Intent
• nl_query (free-form, routed to sqlcoder)

Application DB Q&A Intents
Not implemented — this module has no user/department/role conversational Q&A layer;
authorization data is read directly from XML, not exposed as a chat intent.

12. Semantic Search Architecture

The system supports semantic retrieval using:
• SentenceTransformer embeddings (BAAI/bge-large-en)
• FAISS vector indexes
• Table metadata indexing
• Column metadata indexing
• Row-label metadata indexing

Indexed Components
Index               Purpose
Table Index         Table-level semantic retrieval
Column Index        Column-level semantic retrieval
Row Label Index     Row-label-level semantic retrieval

Retrieval fuses all three indices (RRF) with lexical tie-breaking, then filters results
to the user's authorized returns before the LLM ever sees table names.

13. XML/XBRL Processing

The platform performs (return/report configuration only — no XBRL instance parsing):
• Return metadata lookup (Returns.xml, NonXBRLReturns.xml)
• Table mapping resolution (TableMapping.xml)
• User/department/role authorization lookup (XML_User.xml, XML_Dept.xml, XML_RoleAccess.xml)
• Defensive XML parsing (repair of malformed declarations)

Not implemented: XBRL instance parsing, error XML extraction, validation error
categorization, or AI-based XBRL error explanation — these are out of scope for this module.

14. Performance Optimizations

The system implements:
• Oracle connection pooling (min/max pool size, direct-connect fallback)
• TTL-cached authorization lookups (default 3600s)
• Prebuilt, preloaded FAISS indices (no runtime index build)
• Async FastAPI request handling

Not yet implemented: LLM warmup on startup, SentenceTransformer preload, Ollama
keep-alive tuning.

15. Security Features

Feature                     Purpose
XML-driven Auth             require_login / require_return_access validate against XML_User.xml / XML_Dept.xml
NL→SQL Validation           validate_sql() rejects non-SELECT statements and DML/DDL keywords
Pre-auth Shortlist Filtering  Authorized-return filter applied before the LLM sees schema
CORS Protection              Configurable allowed origins via DV_CORS_ORIGINS
Structured Logging           Daily rotating logs for auditability
Exception Handling           Safe error responses on invalid input/no-data/access-denied

Known gaps: hardcoded Oracle default credentials in config.py (overridable via .env);
no explicit path-traversal stripping on table-mapping path joins; /auth/my-returns
debug endpoint flagged for prod restriction/removal.

16. Logging & Monitoring

The platform includes:
• Daily rotating file handler (logs/YYYY-MM-DD.log) + console output
• INFO-level boundary events (API requests, LLM calls, auth decisions)
• DEBUG-level per-row/per-candidate internals
• WARNING/ERROR for access-denied, no-data, invalid-input, exceptions
• GET /health endpoint for infra monitoring

17. Tech Stack

Backend
Technology      Usage
Python          Core programming language
FastAPI         Backend framework
oracledb        Oracle DB access (thin mode, pooled)
Pydantic        Request/response validation
Uvicorn         ASGI server

AI / ML Stack
Technology              Usage
Ollama                  LLM serving (remote proxy)
qwen2.5:7b              Intent + date resolution
sqlcoder-7b-2           SQL generation
SentenceTransformers    Embeddings
FAISS                   Vector retrieval

Database / Search
Technology      Usage
Oracle DB       Regulatory return data
FAISS           Semantic search
XML Storage     Config/auth data (returns, table mapping, users, departments, roles)

Frontend
Technology      Usage
React (Vite)    Frontend UI, embedded in .NET host iframe
JavaScript      Client-side logic
recharts        Charting
flatpickr       Date picker

18. Deployment Architecture

React Frontend (built to dist/, IIS-hosted via web.config)
       │
       ▼
FastAPI Backend (uvicorn / dev_server.py)
       │
       ├── Ollama Server (remote proxy)
       ├── FAISS Vector Store (prebuilt)
       ├── Oracle Database
       └── .NET Host App (Repo5.5) — passes loginId/uid, hosts iframe

No Docker/containerization is used — deployment is Windows/IIS-based, colocated with
the .NET host application.

19. Conclusion

Data Variance is a focused enterprise module that combines a deterministic manual
variance-compute wizard with a decoupled NLP layer (embedding retrieval, intent/date
resolution, and optional raw SQL generation) into a single regulatory variance
analysis system.

The architecture emphasizes:
• Modularity — standalone module, swappable NLP layer
• Strict separation between manual compute and NL query paths
• Defense-in-depth authorization (return-level filtering before the LLM, plus SQL validation)
• Deterministic workflows for non-technical users
• Deployment integration with an existing .NET/IIS enterprise environment

The current implementation supports manual variance computation and NL-driven
variance/SQL querying against Oracle regulatory data.

Future development could extend the module to support:
• Speech-to-text input for the NL query path
• LLM/FAISS warmup and keep-alive optimizations
• Formal explanation of variance drivers (business-friendly narrative summaries)
• Path-traversal hardening on table-mapping resolution
