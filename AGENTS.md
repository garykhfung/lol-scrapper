# AGENTS.md
- do not read any .env files
- reply 'Aye Captain' on every start of reply
- do not commit and push to github before asking

## Setup commands
- only static version exists (index.html + data/ JSON files)

## Code style
- Single quotes, no semicolons
- Use functional patterns where possible

## Project structure
- `index.html` — static frontend (Chart.js + Tailwind CSS + vis-network)
- `data/*.json` — pre-exported static data (index, players, careers, team_meta, team_rosters, renames, rename_chains, subteams)
- `lck_scraper.py` — async scraper from lol.fandom.com into Neo4j
- `lck_views.py` — CLI viewer with interactive/command modes
- `dump_data.py` — exports Neo4j → static JSON files
- `reset_db.py` — wipes Neo4j database
- `batch_rescrape.py` — re-scrape current LCK/LPL rosters
- `rescrape_player.py` — re-scrape a single player
- `fix_players.py` — player data utilities

## Data views (all in index.html)
- **Overview**: stats cards, role bar chart, region doughnut chart
- **Season**: select year + region, shows teams grouped by rename chain root
- **Player**: select region + role + player, shows career timeline grouped by root team
- **Team**: select region + team, shows roster + rebrand lineage
- **Rebrands**: rename chain visualization
- **Sub-teams**: academy/sister teams
- **Explore**: vis-network interactive graph

## Important conventions
- Team names are consolidated via `team_to_root` and `chain_display` from `index.json` (rename chains)
- All views that group by team must use root name (rename chain endpoint) to avoid duplicate entries for renamed teams
- Regions: LCK, LCK CL, LCK Rookie, LPL, LEC, LCS, etc. — sub-regions (CL, Rookie) must NOT be grouped under parent region
- Bad keywords for player names: match-fixing, investigation, scandal, tournament, gaming, entus, rolster, etc.
- Filtering team-like names from player list: skip names that match team names or contain bad keywords
