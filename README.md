# MapCidade CS Hub

Anonymized, from-scratch reconstruction of a real internal tool's architecture, built for a portfolio. Fictional data only ("MapCidade") — no real company, city, or infrastructure is represented here.

## English

### Problem

Customer success teams need a single view of account health across ticketing, usage, journeys and contracts.

### Solution

FastAPI assembles seeded SQLite data, live-style sources and snapshots into account views with a weighted health score and coverage checks.

### Stack

Python 3.11+, FastAPI, SQLite, pytest

### How to run

Create and activate a Python virtual environment before installing dependencies.

```sh
python -m pip install -r requirements-dev.txt
python -m pytest
python -m ruff check .
python -m uvicorn app.main:app --port 4701
```

### Demo

Open http://127.0.0.1:4701 and sign in as `ana.lima` with `demo-password`. Explore accounts, hours, resident compliance and lineage.

### What I learned

Missing measurements stay unknown. The score renormalizes weights around the dimensions it can measure. The demo uses a fake directory and synthetic sources, and the advisor is disabled by default.

## Português

### Problema

Equipes de sucesso do cliente precisam reunir chamados, uso, jornadas e contratos para acompanhar a saúde das contas.

### Solução

A API FastAPI combina dados sintéticos em SQLite, fontes simuladas e snapshots em uma visão de contas com pontuação ponderada e cobertura de dados.

### Stack

Python 3.11+, FastAPI, SQLite, pytest

### Como rodar

Execute os comandos da seção em inglês a partir da raiz do projeto.

### Demo

Abra http://127.0.0.1:4701 e use `ana.lima` com `demo-password`. Explore contas, horas, conformidade de residentes e linhagem.

### O que aprendi

Medições ausentes permanecem desconhecidas. A pontuação redistribui o peso entre as dimensões mensuráveis. O demo usa um diretório simulado e fontes sintéticas, com o assistente desabilitado por padrão.

