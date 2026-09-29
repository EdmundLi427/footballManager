# Football Manager Reorganization Summary

**Date**: 2026-09-29  
**Status**: ✅ Complete

## What Changed

The repo has been reorganized from a loosely-structured codebase into a production-shaped monorepo with four independent, clearly-documented services.

## Key Changes

### 1. **Service Renaming & Restructuring**
- `files/` → `data-service/` (with src-layout packaging)
- `dataIngestion/` → `etl/` (with organized data directories)
- Root `app.py`, `chatbot.py`, `templates/` → `webapp/` (new service)
- `azure/` → stays in place (updated)

### 2. **Root Organization**
- **`.gitignore`** — comprehensive exclusion of secrets, data, and junk files
- **`README.md`** — architecture overview, data flow diagram, module table, quick-start guide
- **`CLAUDE.md`** — updated with 4 service modules and new guidance
- **`pyproject.toml`** — converted to uv workspace root (members: `data-service`, `webapp`)
- **`uv.lock`** — regenerated with workspace resolution

### 3. **Secrets & Environment**
- `.env` files remain untracked (gitignored)
- `.env.example` templates added to all Python services: `azure/`, `data-service/`, `webapp/`
- No committed credentials or virtualenvs

### 4. **Python Packaging**
- **`data-service/`** — proper src-layout (`src/data_service/`), exports `list_tables`, `describe_table`, `table_history`, `query`
- **`webapp/`** — depends on `data-service` as a workspace member (no `sys.path` hacks)
- Both are `uv` workspace members with aligned Python >=3.10 requirement

### 5. **Test Organization**
- Tests moved into `tests/` subdirectories per service:
  - `data-service/tests/` — `test_server.py`, `test_tables.py`
  - `webapp/tests/` — `test_app.py` (placeholder)
  - `etl/tests/` — `test_clean.py` (validation tests)
  - `azure/` — `npm test` (stub)

### 6. **ETL Data Layout**
- Raw CSVs: `etl/data/raw/`
- Cleaned CSVs: `etl/data/clean/`
- All `.csv` files gitignored (large, regenerated)

### 7. **Code Fixes**
- **Azure `newsTrigger.js`** — now throws errors instead of swallowing them (invocation properly fails)
- **Azure `package.json`** — removed dead `azure@0.10.6` dependency
- **Webapp `chatbot.py`** — removed `sys.path.insert()` hack; imports from `data_service` normally

### 8. **CI/CD**
- **`.github/workflows/`** — four workflows per service:
  - `azure.yml` — npm ci + npm test on Azure changes
  - `data-service.yml` — uv sync + ruff format/check + pytest on data-service changes
  - `webapp.yml` — uv sync + ruff format/check + pytest on webapp changes (includes data-service as dep)
  - `etl.yml` — python etl/tests/test_clean.py on etl changes

### 9. **Documentation**
- **Service READMEs** added:
  - `data-service/README.md` — setup, configuration, MCP tools
  - `etl/README.md` — notebooks, data layout, testing
  - `webapp/README.md` — routes, env vars, architecture
  - (Azure README exists as CLAUDE.md section; no separate file needed)

## What You Get

✅ **Purpose-named directories** — any engineer can immediately see what each service does  
✅ **Single root README** — architecture, data flow, quick-start all in one place  
✅ **Consistent scaffolding** — each service has tests/, .env.example, a README, a pyproject.toml/package.json  
✅ **Clean secrets handling** — .env gitignored, .env.example templates tracked, no hardcoded credentials  
✅ **Proper Python packaging** — data-service installable, webapp depends on it via workspace, no sys.path hacks  
✅ **Production-ready CI** — GitHub Actions workflows per service, format/lint/test on push  
✅ **No dead code/deps** — removed unused Azure SDK, swallowed errors now thrown, organized test locations  

## Before First Commit

Recommended next steps:
1. Set real values in local `.env` files (for local testing)
2. Review `.github/workflows/*.yml` for any org-specific customizations (e.g., runners, Python versions)
3. Add real integration/unit tests to `azure/` (currently a stub)
4. Consider adding a `CONTRIBUTING.md` for onboarding guidance
5. Optionally: create a top-level `Makefile` or `justfile` for common dev commands (e.g., `make test-all`, `make format`)

## File Inventory

```
footballManager/
├── .gitignore                      ← comprehensive secrets/data exclusion
├── README.md                       ← architecture overview
├── CLAUDE.md                       ← updated module docs
├── pyproject.toml                  ← uv workspace root
├── uv.lock                         ← workspace lockfile
├── .github/workflows/
│   ├── azure.yml
│   ├── data-service.yml
│   ├── webapp.yml
│   └── etl.yml
├── azure/
│   ├── .env.example                ← new
│   ├── package.json                ← dead dep removed, error handling fixed
│   ├── src/functions/newsTrigger.js
│   └── ...
├── data-service/                   ← renamed from files/
│   ├── .env.example                ← new
│   ├── pyproject.toml              ← updated for workspace + src-layout
│   ├── src/data_service/
│   │   ├── __init__.py             ← new
│   │   └── server.py
│   ├── tests/
│   │   ├── test_server.py
│   │   └── test_tables.py
│   ├── README.md
│   ├── tables.example.json
│   └── check_features.py
├── etl/                            ← renamed from dataIngestion/
│   ├── data/
│   │   ├── raw/*.csv               ← gitignored
│   │   └── clean/*.csv             ← gitignored
│   ├── tests/
│   │   └── test_clean.py
│   ├── *.ipynb                     ← 4 notebooks
│   └── README.md                   ← new
└── webapp/                         ← new (from root files)
    ├── .env.example                ← new
    ├── pyproject.toml              ← new
    ├── app.py
    ├── chatbot.py                  ← sys.path hack removed
    ├── templates/index.html
    ├── tests/
    │   └── test_app.py
    └── README.md                   ← new
```

---

**Next**: Read `README.md` for the full guide, or `CLAUDE.md` for module-specific details.
