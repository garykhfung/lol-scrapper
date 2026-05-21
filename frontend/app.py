"""
FastAPI web frontend for the LCK Player Career Database.
Provides a clean web UI with all the views from lck_views.py.
"""

import os
import sys
import datetime
from collections import OrderedDict
from dotenv import load_dotenv
from contextlib import asynccontextmanager
from typing import Optional

from fastapi import FastAPI, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from neo4j import AsyncGraphDatabase

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), '..', '.env'))

DB_CONFIG = {
    'uri': os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
    'user': os.getenv('NEO4J_USERNAME') or os.getenv('NEO4J_USER', 'neo4j'),
    'password': os.getenv('NEO4J_PASSWORD', 'change_me'),
    'database': os.getenv('NEO4J_DATABASE', 'player'),
}

driver: Optional[AsyncGraphDatabase] = None

CURRENT_YEAR = datetime.date.today().year

# Proper historical season naming for LoL esports
# Keyed by the competitive year (Worlds year).
SEASONS = {
    2011: {
        'name': 'Season 1',
        'period': 'Jul 2010 – Aug 2011',
        'min_year': 2010,
        'max_year': 2011,
        'era': 'named',
        'splits': [],
    },
    2012: {
        'name': 'Season 2',
        'period': 'Nov 2011 – Oct 2012',
        'min_year': 2011,
        'max_year': 2012,
        'era': 'named',
        'splits': [],
    },
    2013: {
        'name': 'Season 3',
        'period': 'Feb – Oct 2013',
        'min_year': 2013,
        'max_year': 2013,
        'era': 'named',
        'splits': [],
    },
    2014: {
        'name': '2014 Season',
        'period': 'Jan – Oct 2014',
        'min_year': 2014,
        'max_year': 2014,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2015: {
        'name': '2015 Season',
        'period': 'Jan – Oct 2015',
        'min_year': 2015,
        'max_year': 2015,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2016: {
        'name': '2016 Season',
        'period': 'Jan – Oct 2016',
        'min_year': 2016,
        'max_year': 2016,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2017: {
        'name': '2017 Season',
        'period': 'Jan – Nov 2017',
        'min_year': 2017,
        'max_year': 2017,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2018: {
        'name': '2018 Season',
        'period': 'Jan – Nov 2018',
        'min_year': 2018,
        'max_year': 2018,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2019: {
        'name': '2019 Season',
        'period': 'Jan – Nov 2019',
        'min_year': 2019,
        'max_year': 2019,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2020: {
        'name': '2020 Season',
        'period': 'Jan – Oct 2020',
        'min_year': 2020,
        'max_year': 2020,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2021: {
        'name': '2021 Season',
        'period': 'Jan – Nov 2021',
        'min_year': 2021,
        'max_year': 2021,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2022: {
        'name': '2022 Season',
        'period': 'Jan – Nov 2022',
        'min_year': 2022,
        'max_year': 2022,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2023: {
        'name': '2023 Season',
        'period': 'Jan – Nov 2023',
        'min_year': 2023,
        'max_year': 2023,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2024: {
        'name': '2024 Season',
        'period': 'Jan – Nov 2024',
        'min_year': 2024,
        'max_year': 2024,
        'era': 'two-split',
        'splits': ['Spring Split', 'Summer Split'],
    },
    2025: {
        'name': '2025 Season',
        'period': 'Jan – Nov 2025 (3-split format)',
        'min_year': 2025,
        'max_year': 2025,
        'era': 'three-split',
        'splits': ['Split 1 (LCK Cup / LEC Winter / LCS Lock-In) → First Stand',
                    'Split 2 (LCK Road to MSI / LEC Spring / LCS Spring) → MSI',
                    'Split 3 (LCK Playoffs / LEC Summer / LCS Summer) → Worlds'],
    },
    2026: {
        'name': '2026 Season',
        'period': 'Jan – Nov 2026 (3-split format)',
        'min_year': 2026,
        'max_year': 2026,
        'era': 'three-split',
        'splits': ['Split 1 (LCK Cup / LEC Versus / LCS Lock-In) → First Stand',
                    'Split 2 (LCK Road to MSI / LEC Spring / LCS Spring) → MSI',
                    'Split 3 (LCK Playoffs / LEC Summer / LCS Summer) → Worlds'],
    },
}


PRESENT_ENTRY = {
    'name': 'Active Players',
    'period': 'Currently active rosters',
    'min_year': 0,
    'max_year': 9999,
    'era': 'present',
    'splits': [],
}


def get_season_info(year: int) -> dict:
    """Return season info dict for a given year, with fallback defaults."""
    if year == 0:
        return PRESENT_ENTRY
    info = SEASONS.get(year)
    if info:
        return info
    return {
        'name': f'{year} Season',
        'period': f'Jan – Nov {year}',
        'min_year': year,
        'max_year': year,
        'era': 'unknown',
        'splits': [],
    }

BASE_DIR = os.path.dirname(__file__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    global driver
    driver = AsyncGraphDatabase.driver(
        DB_CONFIG['uri'],
        auth=(DB_CONFIG['user'], DB_CONFIG['password']),
    )
    await driver.verify_connectivity()
    print(f'Connected to Neo4j at {DB_CONFIG["uri"]}')
    yield
    await driver.close()
    print('Disconnected from Neo4j')


app = FastAPI(title='LCK Career DB', lifespan=lifespan)
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, 'templates'))


# ── Helper ────────────────────────────────────────────────────────────────────

async def query(cypher: str, **params) -> list:
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run(cypher, **params)
        return await result.data()


# ── Pages ─────────────────────────────────────────────────────────────────────

@app.get('/', response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse(request, 'index.html')


# ── Lookup endpoints ──────────────────────────────────────────────────────────

@app.get('/api/seasons')
async def get_seasons():
    rows = await query('''
        MATCH (t:Team)
        RETURN min(t.year) AS min_yr, max(t.year) AS max_yr
    ''')
    if not rows or rows[0]['min_yr'] is None:
        return [{'year': 0, **PRESENT_ENTRY}]
    min_yr = rows[0]['min_yr']
    max_yr = rows[0]['max_yr']
    years = [y for y in range(min_yr, max_yr + 1) if y >= 2011]
    entries = [{'year': y, **get_season_info(y)} for y in years]
    entries.append({'year': 0, **PRESENT_ENTRY})
    return entries


@app.get('/api/teams')
async def get_teams(region: Optional[str] = Query(None)):
    if region:
        rows = await query('''
            MATCH (t:Team)
            WHERE t.region = $region
            RETURN DISTINCT t.name AS name
            ORDER BY name
        ''', region=region)
    else:
        rows = await query('''
            MATCH (t:Team)
            RETURN DISTINCT t.name AS name
            ORDER BY name
        ''')
    return [r['name'] for r in rows]


@app.get('/api/players')
async def get_players(region: Optional[str] = Query(None), role: Optional[str] = Query(None)):
    region = _region_param(region)
    role = _region_param(role)
    clauses = ["EXISTS((p)-[:PLAYED_FOR]->())"]
    params = {}
    if region:
        clauses.append("t.region = $region")
        params['region'] = region
    if role:
        clauses.append("p.role = $role")
        params['role'] = role
    where = " AND ".join(clauses)
    if region:
        rows = await query(
            f"MATCH (p:Player)-[:PLAYED_FOR]->(t:Team) WHERE {where} "
            "RETURN DISTINCT p.name AS name ORDER BY name", **params)
    else:
        rows = await query(
            f"MATCH (p:Player) WHERE {where} "
            "RETURN DISTINCT p.name AS name ORDER BY name", **params)
    team_rows = await query('''
        MATCH (t:Team)
        RETURN DISTINCT t.name AS name
    ''')
    team_names = {r['name'].lower() for r in team_rows}
    names = [r['name'] for r in rows]
    bad_keywords = ['match-fixing', 'investigation', 'controversy', 'scandal', 'tournament',
                    'gaming', 'entus', 'rolster', 'dragonx', 'tigers', 'freecs',
                    'redforce', 'brave', 'sandbox', 'brion', 'fearx', 'soopers']
    names = [n for n in names
             if len(n) < 50
             and n.lower() not in team_names
             and not any(k in n.lower() for k in bad_keywords)]
    return names


@app.get('/api/roles')
async def get_roles():
    rows = await query('''
        MATCH (p:Player) WHERE p.role IS NOT NULL
        RETURN DISTINCT p.role AS role
        ORDER BY role
    ''')
    return [r['role'] for r in rows]


@app.get('/api/regions')
async def get_regions():
    rows = await query('''
        MATCH (t:Team)
        RETURN DISTINCT t.region AS region
        ORDER BY region
    ''')
    return [r['region'] for r in rows if r['region'] is not None]


# ── View endpoints ────────────────────────────────────────────────────────────

@app.get('/api/stats')
async def get_stats():
    pc = await query("MATCH (p:Player) RETURN count(p) AS cnt")
    tc = await query("MATCH (t:Team) RETURN count(DISTINCT t.name) AS cnt")
    cc = await query("MATCH ()-[r:PLAYED_FOR]->(:Team) RETURN count(r) AS cnt")
    roles = await query("""
        MATCH (p:Player) WHERE p.role IS NOT NULL
        RETURN p.role AS role, count(*) AS cnt
        ORDER BY cnt DESC
    """)
    regions = await query("""
        MATCH (t:Team)
        RETURN t.region AS region, count(DISTINCT t.name) AS cnt
        ORDER BY cnt DESC
    """)
    return {
        'player_count': pc[0]['cnt'],
        'team_count': tc[0]['cnt'],
        'career_count': cc[0]['cnt'],
        'role_distribution': roles,
        'region_distribution': regions,
    }


def _region_param(region: Optional[str]) -> Optional[str]:
    return region if region else None


@app.get('/api/season')
async def get_season(year: int = Query(alias='season'), region: Optional[str] = Query(None)):
    region = _region_param(region)
    info = get_season_info(year)
    if year == 0:
        year = CURRENT_YEAR

    rows = await query(
        'MATCH (p:Player)-[r:PLAYED_FOR]->(t:Team {year: $year}) '
        'WHERE $region IS NULL OR t.region = $region '
        'RETURN t.name AS team, '
        '       t.region AS region, '
        '       p.name AS player, '
        '       p.role AS role, '
        '       p.status AS status, '
        '       r.start_month AS start_month, '
        '       r.end_month AS end_month, '
        '       r.role AS stint_role '
        'ORDER BY t.name, p.role, p.name',
        year=year, region=region,
    )
    if not rows:
        return {'teams': {}, 'total_players': 0, 'season': info}

    # Enrich with edge data: find min/max year per player-team
    team_year_ranges = await query(
        'MATCH (p:Player)-[r:PLAYED_FOR]->(t:Team) '
        'WHERE $region IS NULL OR t.region = $region '
        'WITH p.name AS player, t.name AS team, '
        '     min(t.year) AS joined_year, max(t.year) AS left_year '
        'RETURN player, team, joined_year, left_year',
        region=region,
    )
    tenure_map = {}
    for tr in team_year_ranges:
        tenure_map[(tr['player'], tr['team'])] = (tr['joined_year'], tr['left_year'])

    # Build rebrand chain maps to combine rebranded teams
    chain_display, team_to_root = await _build_rebrand_chains()

    # Group by chain root or individual team name
    groups = {}
    for r in rows:
        team = r['team']
        root = team_to_root.get(team, team)
        key = (root, team)
        if key not in groups:
            display = chain_display.get(root, team) if root in chain_display else team
            groups[key] = {'display': display, 'players': []}

        tenure = tenure_map.get((r['player'], team), (year, year))
        r['joined_year'] = tenure[0]
        r['joined_month'] = r.get('start_month') if tenure[0] == year else None
        r['left_year'] = tenure[1]
        r['left_month'] = r.get('end_month') if tenure[1] == year else None
        r['duration'] = 'Present' if tenure[1] >= CURRENT_YEAR else f"{tenure[1] - tenure[0] + 1} yr(s)"
        groups[key]['players'].append(r)

    for g in groups.values():
        g['players'].sort(key=lambda p: (p['joined_year'], p.get('joined_month') or 0, p.get('role') or ''))

    # Flatten to {display_name: [players]} for frontend compatibility
    result_teams = {}
    for g in groups.values():
        result_teams[g['display']] = g['players']

    return {'teams': result_teams, 'total_players': len(rows), 'season': info}


@app.get('/api/player')
async def get_player(name: str = Query(), region: Optional[str] = Query(None)):
    region = _region_param(region)
    rows = await query(
        'MATCH (p:Player {name: $name})-[r:PLAYED_FOR]->(t:Team) '
        'WHERE $region IS NULL OR t.region = $region '
        'RETURN t.name AS team, t.region AS region, '
        '       r.role AS role, '
        '       r.start_month AS start_month, '
        '       r.end_month AS end_month, '
        '       t.year AS year ',
        name=name, region=region,
    )
    # Group by team with year range
    careers = OrderedDict()
    for r in rows:
        team = r['team']
        if team not in careers:
            careers[team] = {
                'team': team,
                'region': r['region'],
                'role': r['role'],
                'joined_year': r['year'],
                'left_year': r['year'],
                'joined_month': r.get('start_month'),
                'left_month': None,
            }
        c = careers[team]
        if r['year'] < c['joined_year']:
            c['joined_year'] = r['year']
            c['joined_month'] = r.get('start_month')
        if r['year'] > c['left_year']:
            c['left_year'] = r['year']
            c['left_month'] = r.get('end_month')

    career_list = []
    for c in careers.values():
        c['duration'] = 'Present' if c['left_year'] >= CURRENT_YEAR else f"{c['left_year'] - c['joined_year'] + 1} yr(s)"
        career_list.append(c)

    career_list.sort(key=lambda c: (c['joined_year'], c.get('joined_month') or 0, c.get('role') or ''))

    info = await query(
        'MATCH (p:Player {name: $name}) '
        'RETURN p.role AS role, p.nationality AS nationality, '
        '       p.birth_date AS birth_date, p.status AS status',
        name=name,
    )
    return {
        'career': career_list,
        'info': info[0] if info else None,
    }


@app.get('/api/team')
async def get_team(name: str = Query(), region: Optional[str] = Query(None)):
    region = _region_param(region)
    meta = await query(
        'MATCH (t:Team {name: $name}) '
        'RETURN t.region AS region, min(t.year) AS start_year, max(t.year) AS end_year',
        name=name,
    )
    if not meta or meta[0]['region'] is None:
        return {'error': 'Team not found'}

    team_meta = meta[0]

    lineage, all_names = await _get_rebranded_chain(name)

    rows = await query(
        'MATCH (p:Player)-[r:PLAYED_FOR]->(t:Team) '
        'WHERE t.name IN $team_names '
        '  AND ($region IS NULL OR t.region = $region) '
        'RETURN t.name AS team, '
        '       p.name AS player, '
        '       p.role AS role, '
        '       p.status AS status, '
        '       r.start_month AS start_month, '
        '       r.end_month AS end_month, '
        '       r.role AS stint_role, '
        '       t.year AS year '
        'ORDER BY p.name, t.year',
        team_names=list(all_names), region=region,
    )

    # Group into rows per (player, role, consecutive year range)
    by_team = OrderedDict()
    for r in rows:
        team = r['team']
        by_team.setdefault(team, [])

    # Process each team's rows grouped by player, detecting role/gap changes
    for team in by_team:
        team_rows = [r for r in rows if r['team'] == team]
        # Sort: player, then year
        team_rows.sort(key=lambda x: (x['player'], x['year']))
        grouped = []
        for r in team_rows:
            stint_role = r['stint_role'] or r['role']
            cur = {
                'player': r['player'],
                'role': stint_role,
                'status': r['status'],
                'start_year': r['year'],
                'end_year': r['year'],
                'start_month': r.get('start_month'),
                'end_month': r.get('end_month'),
            }
            if grouped and grouped[-1]['player'] == cur['player'] and grouped[-1]['role'] == cur['role'] and grouped[-1]['end_year'] + 1 >= r['year']:
                grouped[-1]['end_year'] = r['year']
                if cur['end_month'] is not None:
                    grouped[-1]['end_month'] = cur['end_month']
            else:
                grouped.append(cur)

        for g in grouped:
            mn = {1:'Jan',2:'Feb',3:'Mar',4:'Apr',5:'May',6:'Jun',7:'Jul',8:'Aug',9:'Sep',10:'Oct',11:'Nov',12:'Dec'}
            sy, sm = g['start_year'], g.get('start_month')
            ey, em = g['end_year'], g.get('end_month')
            start = f"{mn[sm]} {sy}" if sm else str(sy)
            end = 'Present' if ey >= CURRENT_YEAR else (f"{mn[em]} {ey}" if em else str(ey))
            g['duration'] = f"{start} - {end}"

        grouped.sort(key=lambda g: (g['start_year'], g.get('start_month') or 0, g.get('role') or ''))
        by_team[team] = grouped

    return {
        'meta': team_meta,
        'lineage': lineage,
        'by_team': by_team,
        'total_players': len(set(r['player'] for r in rows)),
    }


@app.get('/api/rename-teams')
async def get_rename_teams():
    rows = await query(
        "MATCH (t:Team)-[:REBRANDED_TO]-() "
        "RETURN DISTINCT t.name AS name "
        "ORDER BY name"
    )
    return [r['name'] for r in rows]


@app.get('/api/rename')
async def get_rename(name: str = Query()):
    lineage, _ = await _get_rebranded_chain(name)
    return {
        'name': name,
        'lineage': lineage,
    }


@app.get('/api/subteam-teams')
async def get_subteam_teams():
    rows = await query(
        "MATCH (sub:Team)-[:SUBTEAM_OF]->(parent:Team) "
        "RETURN DISTINCT parent.name AS name "
        "ORDER BY name"
    )
    return [r['name'] for r in rows]


@app.get('/api/subteams')
async def get_subteams(name: str = Query()):
    rows = await query(
        'MATCH (sub:Team)-[:SUBTEAM_OF]->(parent:Team {name: $name}) '
        'RETURN sub.name AS subteam '
        'ORDER BY sub.name',
        name=name,
    )
    return {
        'parent': name,
        'subteams': [r['subteam'] for r in rows],
    }


# ── Shared logic ──────────────────────────────────────────────────────────────

async def _get_rebranded_chain(team_name: str):
    rels = await query(
        'MATCH (old:Team)-[r:REBRANDED_TO]->(new:Team) '
        'RETURN DISTINCT old.name AS old_name, '
        '       new.name AS new_name, r.year AS year'
    )
    if not rels:
        return [], {team_name}

    fwd = {}
    fwd_year = {}
    rev = {}
    rev_year = {}
    for r in rels:
        old, new = r['old_name'], r['new_name']
        yr = r.get('year')
        if old == new:
            continue
        if old not in fwd or (yr is not None and fwd_year.get(old) is None):
            fwd[old] = (new, yr)
            fwd_year[old] = yr
        elif yr is not None and fwd_year.get(old) is not None and yr > fwd_year[old]:
            fwd[old] = (new, yr)
            fwd_year[old] = yr
        if new not in rev or (yr is not None and rev_year.get(new) is None):
            rev[new] = old
            rev_year[new] = yr
        elif yr is not None and rev_year.get(new) is not None and yr > rev_year[new]:
            rev[new] = old
            rev_year[new] = yr

    cur = team_name
    while cur in rev:
        cur = rev[cur]
    start = cur

    lineage = []
    cur = start
    while cur in fwd:
        new_name, year = fwd[cur]
        lineage.append((cur, year))
        cur = new_name
    lineage.append((cur, None))

    chain_names = {n for n, _ in lineage}
    if team_name not in chain_names:
        return [], {team_name}

    return lineage, chain_names


async def _build_rebrand_chains():
    """Build maps of linear rebrand chains from the rename graph.
    Handles skip-level and duplicate rename edges by preferring
    year-annotated connections, then building single linear chain per root.
    Returns (chain_display, team_to_root) where:
      chain_display[endpoint_name] -> display string like 'Afreeca Freecs -> Kwangdong Freecs -> DN Freecs -> DN SOOPers'
      team_to_root[any_team_name] -> endpoint_name (final name in chain)
    """
    rels = await query(
        'MATCH (old:Team)-[r:REBRANDED_TO]->(new:Team) '
        'RETURN DISTINCT old.name AS old_name, new.name AS new_name, r.year AS year'
    )
    if not rels:
        return {}, {}

    years = await query(
        'MATCH (t:Team) RETURN t.name AS name, min(t.year) AS start_year, max(t.year) AS end_year'
    )
    team_period = {r['name']: (r['start_year'], r['end_year']) for r in years}

    from collections import defaultdict
    fwd = {}
    rev = {}
    fwd_year = {}
    rev_year = {}

    for r in rels:
        old, new = r['old_name'], r['new_name']
        yr = r.get('year')
        if old == new:
            continue
        if old not in fwd or (yr is not None and fwd_year.get(old) is None):
            fwd[old] = new
            fwd_year[old] = yr
        elif yr is not None and fwd_year.get(old) is not None and yr > fwd_year[old]:
            fwd[old] = new
            fwd_year[old] = yr
        if new not in rev or (yr is not None and rev_year.get(new) is None):
            rev[new] = old
            rev_year[new] = yr
        elif yr is not None and rev_year.get(new) is not None and yr > rev_year[new]:
            rev[new] = old
            rev_year[new] = yr

    all_old = set(fwd.keys())
    all_new = set(rev.keys())
    roots = all_old - all_new

    chain_display = {}
    team_to_root = {}

    for root in roots:
        lineage = []
        cur = root
        while cur in fwd:
            lineage.append(cur)
            cur = fwd[cur]
        lineage.append(cur)
        display = ' -> '.join(lineage)
        endpoint = lineage[-1]
        chain_display[endpoint] = display
        for name in lineage:
            team_to_root[name] = endpoint

    return chain_display, team_to_root


# ── Main ──────────────────────────────────────────────────────────────────────

if __name__ == '__main__':
    import uvicorn
    uvicorn.run('app:app', host='0.0.0.0', port=8000, reload=True)
