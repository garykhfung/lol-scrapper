# lol-scrapper

A **League of Legends esports player career database** — scrapes professional player data from the [Leaguepedia](https://lol.fandom.com) wiki, stores it in a **Neo4j** graph database, and provides **three interfaces** to explore the data.

![Static site preview](https://img.shields.io/badge/LoL-ESports-blue)

## Features

- **Scrapes** player bios, team histories, renames, and rosters across 20+ regions (LCK, LPL, LEC, LCS, PCS, VCS, CBLOL, LLA, TCL, LJL, LCO and more)
- **Stores** data in a Neo4j graph — `Player`, `Team`, `Region` nodes linked by `PLAYED_FOR`, `NEXT_SEASON`, `REBRANDED_TO`, `BELONGS_TO`, `SUBTEAM_OF` relationships
- **Detects** team rebrands (e.g. Samsung Galaxy → KSV → Gen.G) via fandom notice parsing and redirect resolution
- **Tracks** 30+ roles including players, coaches, management, broadcast talent, and content creators
- **Covers** 2011–2026 with month-level tenure precision

## Three Interfaces

| Interface | How to use |
|-----------|-----------|
| **CLI** | `python lck_views.py` — interactive REPL or single-shot commands (`season 14`, `player Faker`, `team T1`) |
| **Live web** | `python frontend/app.py` — FastAPI server with REST API + browser UI at `http://localhost:8000` |
| **Static site** | Open `index.html` in a browser — pure JS, no server needed. Uses pre-exported JSON files in `data/` |

## Quick Start

### 1. Prerequisites

- Python 3.13+
- [Neo4j](https://neo4j.com/download/) Community Edition running locally (default: `bolt://localhost:7687`)
- `pip install -r requirements.txt`

### 2. Configuration

```bash
cp .env.example .env
```

Edit `.env` with your Neo4j credentials:

```
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your_password
```

### 3. Scrape the Data

```bash
python lck_scraper.py
```

This will fetch ~1,600+ players and build the full graph (~8,600 career records). The scraper is async and handles rate limiting automatically.

### 4. Explore

```bash
# Interactive CLI
python lck_views.py

# Single shot
python lck_views.py player Faker
python lck_views.py team T1
python lck_views.py season 14
python lck_views.py stats

# Web server
python frontend/app.py

# Static export
python dump_data.py
```

## Project Structure

```
├── lck_scraper.py          # Async scraper: fetches & stores player data
├── lck_views.py            # CLI viewer with interactive and command modes
├── dump_data.py            # Exports Neo4j → static JSON for GitHub Pages
├── reset_db.py             # Wipes the Neo4j database
├── index.html              # Static frontend (Chart.js + Tailwind CSS)
├── frontend/
│   ├── app.py              # FastAPI backend + REST API
│   ├── requirements.txt    # FastAPI, Uvicorn, Neo4j driver
│   └── templates/
│       └── index.html      # Jinja2 template for live web UI
├── data/                   # Pre-exported static JSON files
│   ├── index.json          # Master index with stats & lookups
│   ├── players.json        # All player profiles
│   ├── careers.json        # PLAYED_FOR career edges
│   └── ...                 # (team_meta, team_rosters, renames, etc.)
├── requirements.txt        # aiohttp, neo4j, python-dotenv
├── CONTENT.md              # Comprehensive documentation
└── .env.example            # Neo4j connection template
```

## Database Schema

```
(Player: {name, role, nationality, birth_date, is_retired})
    │
    │ [PLAYED_FOR {start_year, end_year, start_month, end_month, role}]
    ▼
(Team: {name, year})
    │
    ├── [NEXT_SEASON] → (Team: {name, year+1})
    ├── [REBRANDED_TO {year}] → (Team)
    ├── [BELONGS_TO] → (Region: {name})
    └── [SUBTEAM_OF] → (Team)
```

## Data Refresh

To re-scrape with the latest data:

```bash
python reset_db.py          # clears all nodes (will ask for confirmation)
python lck_scraper.py       # full re-scrape
python dump_data.py          # regenerate static files
```

## Deployment (GitHub Pages)

The `index.html` + `data/` directory is fully static. Push to a `gh-pages` branch or serve from any static host.

## License

MIT
