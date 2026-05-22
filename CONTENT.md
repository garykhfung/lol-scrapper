# LCK Player Career Database

A Neo4j graph database storing professional League of Legends player career data with historical relationships spanning 2010-2026.

## Files

| File | Purpose |
|------|---------|
| `lck_scraper.py` | Scrapes lol.fandom.com → Neo4j |
| `lck_views.py` | Interactive CLI viewer |
| `migrate_roles.py` | Legacy role normalization (bot→adc, etc.) |
| `reset_db.py` | Delete all data for clean re-scrape |

### `lck_scraper.py` - Data Collector
Scrapes player data from lol.fandom.com via MediaWiki API and stores it in Neo4j.

**Features:**
- Crawls all major regions: LCK, LPL, LEC, LCS, PCS, VCS, CBLOL, LLA, TCL, LJL, LCO
- Extracts player info (name, role, nationality, retirement status) from page intros
- Parses Team History tables to build career timelines
- Stores data as Player-PLAYED_FOR-Team relationships with duration metadata
- Detects 30+ roles (in-game, coaching, analyst, management, broadcast, content creation)
- Detects challenger/academy teams → `LCK CL` / `LPL CL` regions
- Detects retirement from page text (`is a retired League of Legends esports player`)
- Creates `REBRANDED_TO` relationships by searching fandom for all "Team has renamed" / "Roster has joined a new organization" pages

**Run:**
```
python lck_scraper.py
```

### `lck_views.py` - Database Viewer
Interactive CLI for querying the career database with three view modes.

**Commands (interactive mode - `python lck_views.py`):**

| Command | Example | Description |
|---------|---------|-------------|
| `[REGION] year <YEAR>` | `LCK year 2019` | Teams for that year with players, roles, and tenure (optionally filtered by region) |
| `[REGION] player <NAME>` | `LPL player Faker` | All teams a player played for (optionally filtered by region) |
| `[REGION] team <NAME>` | `team T1` | All players for a team (optionally filtered by region) |
| `rename <NAME>` | `rename T1` | View a team's rename/rebrand chain |
| `subteams <NAME>` | `subteams T1` | View a team's sub-teams (challenger/academy) |
| `stats` | `stats` | Database statistics |
| `years` | `years` | List available years |
| `teams` | `teams` | List all teams |
| `players` | `players` | List all players |
| `roles` | `roles` | List all player roles |
| `regions` | `regions` | List all team regions |

**Single command mode:**
```
python lck_views.py year 2024
python lck_views.py player Caps
python lck_views.py team Gen.G
python lck_views.py rename T1
python lck_views.py LCK year 2019
python lck_views.py LPL player Faker
```

### `migrate_roles.py` - Role Migration
Normalizes roles to standard values: bot→adc, jng→jungle, sup→support, m→mid, t→top.

**Run:**
```
python migrate_roles.py
```

## Database Schema

```
(Team) -[BELONGS_TO]-> (Region)
(Player) -[PLAYED_FOR {start_year, end_year, role, is_current}]-> (Team)
(Team) -[REBRANDED_TO {year}]-> (Team)   # Same organization, name changed over time
```

**Player properties:**
- `fandom_id` - Unique MediaWiki page ID
- `name` - Player name
- `real_name` - Real name
- `role` - Position (top, jungle, mid, adc, support, coach, streamer, analyst, etc.)
- `nationality` - Country code (KR, CN, US, etc.)
- `birth_date` - Birth date
- `status` - Active or Retired (auto-detected from page text)

**PLAYED_FOR relationship properties:**
- `start_year` - Year player joined
- `end_year` - Year player left (NULL = still active)
- `role` - Role at that team
- `is_current` - Boolean flag

**REBRANDED_TO relationship properties:**
- `year` - Year of team rebranding (extracted from rename page text)

**Team properties:**
- `name` - Team name
- `region` - Region it belongs
- `start_year` - Year team started
- `end_year` - Year team ended (NULL = still active)

## Database Statistics

| Metric | Count |
|--------|-------|
| Players | 612 |
| Teams | 78 |
| Career records | 670 |
| Years covered | 2010-2026 |

**Players by role:**
- Unknown: 190
- top: 92
- mid: 91
- adc: 85
- head coach: 51
- coach: 31
- inactive: 13
- substitute: 12
- streamer: 12
- manager: 7
- owner: 6
- content creator: 5
- assistant coach: 4
- analyst: 4
- general manager: 2
- support: 2
- co-owner: 1
- team manager: 1
- caster: 1
- color caster: 1
- co-streamer: 1

(Scraper now also detects: head coach, assistant coach, strategic coach, analyst, head analyst,
manager, general manager, owner, caster, host, streamer, content creator, substitute, and more.)

**Region detection** now also recognizes challenger/academy teams (e.g. "KT Rolster Challengers"
→ `LCK CL`, "T1 Academy" → `LCK CL`, "FearX Youth" → `LCK CL`).

**Team rename tracking** — `REBRANDED_TO` relationships are created dynamically by
scraping each team's fandom page for "Team has renamed" or "Roster has joined a new
organization" links (e.g. Suning → Weibo Gaming, SK Telecom T1 → T1, CLG → NRG).

### `migrate_status.py` - Status Backfill
One-time migration to set `Player.status` ("Active"/"Retired") on all existing players based on their `PLAYED_FOR` relationships.

**Run:**
```
python migrate_status.py
```

### `reset_db.py` - Database Reset
Deletes all data from Neo4j. Use this before a clean re-scrape to a fresh database.

**Run:**
```
python reset_db.py
```

## Clean Re-scrape for Cloud Database

To start fresh (e.g. pushing to a cloud Neo4j):

1. Update your `.env` with the cloud Neo4j credentials
2. Reset the database (if needed):
   ```
   python reset_db.py
   ```
3. Run the scraper:
   ```
   python lck_scraper.py
   ```

## Configuration

Set environment variables in `.env`:
```
NEO4J_URI=bolt://localhost:7687
NEO4J_USERNAME=neo4j
NEO4J_PASSWORD=your_password
NEO4J_DATABASE=player
```

## Static Site (GitHub Pages)

The repo includes a fully static version of the web frontend that requires **no backend server** and **no database credentials**. All data is pre-exported from Neo4j into JSON files.

### Files

| File | Purpose |
|------|---------|
| `index.html` | Static frontend (served by GitHub Pages) |
| `data/` | Pre-exported JSON files (committed to repo) |
| `dump_data.py` | Exports Neo4j data → `data/` (run locally, no cloud needed) |

### How it works

```
Browser ──> index.html (GitHub Pages)
             ├── data/index.json          ← dropdowns, stats, chain maps
             ├── data/players.json        ← player profiles
             ├── data/careers.json        ← all PLAYED_FOR edges
             ├── data/team_meta.json      ← team region/year ranges
             ├── data/team_rosters.json   ← pre-computed team rosters
             ├── data/renames.json        ← rebrand relationships
             ├── data/rename_chains.json  ← pre-computed rename lineages
             └── data/subteams.json       ← sub-team relationships
```

No credentials, no database connection, no backend. Everything is pre-computed from Neo4j into static JSON files.

### Setup GitHub Pages

1. Go to repo → **Settings → Pages → Source: Deploy from a branch**
2. Branch: `main`, Folder: `/` (root)
3. Save

Your site will be live at `https://garykhfung.github.io/lol-scrapper/`.

### Refresh data workflow

```
python dump_data.py    # export latest from Neo4j → data/
git add -A
git commit -m "Update data"
git push               # GitHub Pages auto-deploys
```

### Notes

- The original Flask backend (`frontend/`) is untouched — run locally with `python3 frontend/app.py`
- `dump_data.py` does not require credentials in the repo (uses your local `.env` file)
- No credentials are ever pushed to GitHub

## Dependencies

```
aiohttp
neo4j
python-dotenv
```

Install: `pip install -r requirements.txt`
