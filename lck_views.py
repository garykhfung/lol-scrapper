"""
LCK Historical Database Viewer
Supports three views:
  1. Year View    - List all LCK teams for a given year and their players with durations
  2. Player View  - All teams a player played for with durations and roles
  3. Team View    - All players who played for a team, with durations and roles
"""

import sys
import asyncio
from neo4j import AsyncGraphDatabase
from dotenv import load_dotenv
import os

# Fix Unicode encoding on Windows
if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)

load_dotenv()

DB_CONFIG = {
    "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    "user": os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j"),
    "password": os.getenv("NEO4J_PASSWORD", "change_me"),
    "database": os.getenv("NEO4J_DATABASE", "player"),
}


# ── View 1: Year View ─────────────────────────────────────────────────────
# Shows all LCK teams in a given year and their rosters (players who were
# active at that team during that year), including tenure duration.

MONTH_NAMES = {1:'Jan',2:'Feb',3:'Mar',4:'Apr',5:'May',6:'Jun',
               7:'Jul',8:'Aug',9:'Sep',10:'Oct',11:'Nov',12:'Dec'}

def fmt_date(year, month):
    if year is None:
        return 'Present'
    if month:
        return f'{MONTH_NAMES.get(month, "")} {year}'
    return str(year)


YEAR_VIEW_QUERY = """
MATCH (p:Player)-[r:PLAYED_FOR]->(t:Team)
WHERE r.start_year <= $max_year
  AND (r.end_year IS NULL OR r.end_year >= $min_year)
  AND ($region IS NULL OR t.region = $region)
RETURN t.name AS team,
       t.region AS region,
       p.name AS player,
       p.role AS role,
       p.status AS status,
       r.start_year AS joined_year,
       r.start_month AS joined_month,
       r.end_year AS left_year,
       r.end_month AS left_month,
       CASE
         WHEN r.end_year IS NULL THEN 'Present'
         ELSE toString(r.end_year - r.start_year) + ' yr(s)'
       END AS duration
ORDER BY t.name, p.role, p.name
"""


# ── View 2: Player View ───────────────────────────────────────────────────
# Shows every team a player played for, with role and tenure duration.

PLAYER_VIEW_QUERY = """
MATCH (p:Player {name: $player_name})-[r:PLAYED_FOR]->(t:Team)
WHERE $region IS NULL OR t.region = $region
RETURN t.name AS team,
       t.region AS region,
       r.role AS role,
       r.start_year AS joined_year,
       r.start_month AS joined_month,
       r.end_year AS left_year,
       r.end_month AS left_month,
       CASE
         WHEN r.end_year IS NULL THEN 'Present'
         ELSE toString(r.end_year - r.start_year) + ' yr(s)'
       END AS duration
ORDER BY r.start_year
"""


# ── View 3: Team View ─────────────────────────────────────────────────────
# Shows every player who played for a team, with role and tenure duration.

TEAM_VIEW_QUERY = """
MATCH (p:Player)-[r:PLAYED_FOR]->(t:Team)
WHERE t.name IN $team_names
  AND ($region IS NULL OR t.region = $region)
RETURN t.name AS team,
       p.name AS player,
       p.role AS role,
       p.status AS status,
       r.start_year AS joined_year,
       r.start_month AS joined_month,
       r.end_year AS left_year,
       r.end_month AS left_month,
       CASE
         WHEN r.end_year IS NULL THEN 'Present'
         ELSE toString(r.end_year - r.start_year) + ' yr(s)'
       END AS duration
ORDER BY t.name, r.start_year
"""


# ── Helper: print formatted results ───────────────────────────────────────

def print_table(headers, rows):
    col_widths = [len(h) for h in headers]
    for row in rows:
        vals = [str(row.get(h, '')) for h in headers]
        for i, v in enumerate(vals):
            col_widths[i] = max(col_widths[i], len(v))

    fmt = ' | '.join(f'{{:<{w}}}' for w in col_widths)
    sep = '-+-'.join('-' * w for w in col_widths)

    print(fmt.format(*headers))
    print(sep)
    for row in rows:
        vals = [str(row.get(h, '')) for h in headers]
        print(fmt.format(*vals))


def print_section(title):
    print()
    print('=' * 70)
    print(f'  {title}')
    print('=' * 70)


# ── Run queries ───────────────────────────────────────────────────────────

async def year_view(driver, season, region=None):
    tag = region if region else 'ALL'
    s = int(season)
    if s == 1:
        max_year = 2011
        min_year = 2010
        yr_label = '2010 – 2011'
    else:
        yr = s + 2010
        max_year = yr
        min_year = yr
        yr_label = str(yr)
    print_section(f'SEASON {s} — {yr_label} ({tag}) Teams & Rosters')
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run(YEAR_VIEW_QUERY, max_year=max_year, min_year=min_year, region=region)
        records = await result.data()

    if not records:
        print(f'  No data found for year {year} in region "{tag}".')
        return

    # Build rebrand chain maps to combine rebranded teams
    chain_display, team_to_root = await build_rebrand_chains(driver)

    # Group by chain root or individual team name
    groups = {}
    for rec in records:
        team = rec['team']
        root = team_to_root.get(team, team)
        if root not in groups:
            groups[root] = {'display': chain_display.get(root, root), 'players': []}
        groups[root]['players'].append(rec)

    for root in sorted(groups, key=lambda r: groups[r]['display']):
        info = groups[root]
        players = info['players']
        region = players[0].get('region', '')
        rtag = f' ({region})' if region else ''
        print(f'\n  Team: {info["display"]}{rtag}  ({len(players)} player(s))')
        print(f'  {"Player":<30} {"Role":<14} {"Status":<10} {"Joined":<14} {"Left":<14} {"Duration"}')
        print(f'  {"-"*30} {"-"*14} {"-"*10} {"-"*14} {"-"*14} {"-"*20}')
        for p in sorted(players, key=lambda x: x['role']):
            joined = fmt_date(p['joined_year'], p.get('joined_month'))
            left = fmt_date(p['left_year'], p.get('left_month'))
            dur = p['duration'] or '-'
            st = p.get('status') or '-'
            print(f'  {p["player"]:<30} {p["role"]:<14} {st:<10} {joined:<14} {left:<14} {dur}')


async def player_view(driver, player_name, region=None):
    tag = region if region else 'ALL'
    print_section(f'PLAYER VIEW — {player_name} Career History ({tag})')
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run(PLAYER_VIEW_QUERY, player_name=player_name, region=region)
        records = await result.data()

    if not records:
        print(f'  Player "{player_name}" not found.')
        return

    # Get player info
    async with driver.session(database=DB_CONFIG['database']) as session:
        info = await session.run(
            'MATCH (p:Player {name: $name}) RETURN p.role AS role, p.nationality AS nationality, p.birth_date AS birth_date, p.status AS status',
            name=player_name,
        )
        rec = await info.single()

    if rec:
        status_str = rec.get('status') or 'Unknown'
        print(f'  Role: {rec["role"]} | Nationality: {rec["nationality"]} | Birth: {rec["birth_date"]} | Status: {status_str}')

    print()
    print(f'  {"Team (Region)":<32} {"Role":<12} {"Joined":<14} {"Left":<14} {"Duration"}')
    print(f'  {"-"*32} {"-"*12} {"-"*14} {"-"*14} {"-"*20}')
    for rec in records:
        joined = fmt_date(rec['joined_year'], rec.get('joined_month'))
        left = fmt_date(rec['left_year'], rec.get('left_month'))
        dur = rec['duration'] or '-'
        team_label = rec['team']
        if rec.get('region'):
            team_label += f' ({rec["region"]})'
        print(f'  {team_label:<32} {rec["role"]:<12} {joined:<14} {left:<14} {dur}')


async def get_rebranded_chain(driver, team_name):
    """Get the full rebrand chain for a team (forward + backward through REBRANDED_TO).
    Returns (lineage, all_names) where lineage is [(name, year|None)] ordered
    from oldest to newest, and all_names is the set of all names in the chain.
    """
    async with driver.session(database=DB_CONFIG['database']) as session:
        all_rels = await session.run("""
            MATCH (old:Team)-[r:REBRANDED_TO]->(new:Team)
            RETURN old.name AS old_name, new.name AS new_name, r.year AS year
        """)
        rels = await all_rels.data()

    if not rels:
        return [], {team_name}

    fwd = {r['old_name']: (r['new_name'], r.get('year')) for r in rels}
    rev = {r['new_name']: r['old_name'] for r in rels}

    # Walk backward to find the earliest ancestor
    cur = team_name
    while cur in rev:
        cur = rev[cur]
    start = cur

    # Walk forward from the earliest ancestor to build lineage
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


async def build_rebrand_chains(driver):
    """Build maps of all rebrand chains in the database.
    Collects ALL ancestors per endpoint (handling many-to-one redirects)
    and sorts chronologically by team start_year.
    Returns (chain_display, team_to_root) where:
      chain_display[endpoint_name] -> display string like 'MiraGe Gaming -> DAMWON -> DWG KIA -> Dplus KIA'
      team_to_root[any_team_name] -> endpoint_name (final name in chain)
    Teams not in any chain are not included.
    """
    async with driver.session(database=DB_CONFIG['database']) as session:
        all_rels = await session.run("""
            MATCH (old:Team)-[r:REBRANDED_TO]->(new:Team)
            RETURN old.name AS old_name, new.name AS new_name
        """)
        rels = await all_rels.data()
        years = await session.run("""
            MATCH (t:Team) RETURN t.name AS name, t.start_year AS start_year, t.end_year AS end_year
        """)
        team_period = {r['name']: (r['start_year'], r['end_year']) for r in await years.data()}

    if not rels:
        return {}, {}

    from collections import defaultdict
    rev_all = defaultdict(list)
    for r in rels:
        if r['old_name'] != r['new_name']:
            rev_all[r['new_name']].append(r['old_name'])

    all_old = {r['old_name'] for r in rels}
    all_new = {r['new_name'] for r in rels}
    # Endpoints: teams that are targets but never sources (final name in each chain)
    endpoints = all_new - all_old

    chain_display = {}
    team_to_root = {}

    def sort_key(name):
        sy, ey = team_period.get(name, (None, None))
        return (sy or 9999, ey or 9999)

    for ep in endpoints:
        # BFS backward to find all ancestors
        ancestors = set()
        queue = [ep]
        while queue:
            cur = queue.pop(0)
            for pred in rev_all.get(cur, []):
                if pred not in ancestors:
                    ancestors.add(pred)
                    queue.append(pred)

        # Combine and sort chronologically: earliest start first, earliest end last
        all_names = [ep] + list(ancestors)
        all_names.sort(key=sort_key)

        display = ' -> '.join(all_names)
        for name in all_names:
            team_to_root[name] = ep
        chain_display[ep] = display

    return chain_display, team_to_root


async def team_view(driver, team_name, region=None):
    tag = region if region else 'ALL'
    print_section(f'TEAM VIEW — {team_name} Player Roster History ({tag})')

    # Fetch team info
    async with driver.session(database=DB_CONFIG['database']) as session:
        team_info = await session.run(
            'MATCH (t:Team {name: $name}) RETURN t.region AS region, t.start_year AS start_year, t.end_year AS end_year',
            name=team_name,
        )
        info = await team_info.single()

    if not info:
        print(f'  Team "{team_name}" not found.')
        return

    team_region = info.get('region') or 'Unknown'
    start = info.get('start_year')
    end = info.get('end_year')
    lifecycle = f'{start}' if start else '?'
    if end is None and start:
        lifecycle += '-present'
    elif end:
        lifecycle += f'-{end}'
    print(f'  Region: {team_region}  |  Active: {lifecycle}')

    # Get rebrand chain to show related teams
    lineage, all_team_names = await get_rebranded_chain(driver, team_name)

    if lineage and len(lineage) > 1:
        print(f'  Rebrand lineage:', end='')
        for name, year in lineage:
            arrow = ' -> ' if year is not None else ''
            yr_tag = f' ({year})' if year else ''
            print(f' {name}{yr_tag}{arrow}', end='')
        print()

    # Query players from all related teams
    team_names_list = sorted(all_team_names)
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run(TEAM_VIEW_QUERY, team_names=team_names_list, region=region)
        records = await result.data()

    if not records:
        print(f'  No player records found for "{team_name}".')
        return

    # Group by team
    by_team = {}
    for rec in records:
        tn = rec['team']
        by_team.setdefault(tn, []).append(rec)

    total_active = sum(1 for r in records if r.get('status') == 'Active')
    total_players = len(records)
    role_counts = {}
    for r in records:
        role = r['role'] or 'Unknown'
        role_counts[role] = role_counts.get(role, 0) + 1

    print(f'  Total players: {total_players}  |  Active: {total_active}  |  Retired: {total_players - total_active}')
    print(f'  Roles: {", ".join(f"{k}: {v}" for k, v in sorted(role_counts.items()))}')

    # Print each team section
    for tn in team_names_list:
        if tn not in by_team:
            continue
        players = by_team[tn]
        print(f'\n  --- {tn} ({len(players)} player(s)) ---')
        print(f'  {"Player":<30} {"Role":<14} {"Status":<10} {"Joined":<14} {"Left":<14} {"Duration"}')
        print(f'  {"-"*30} {"-"*14} {"-"*10} {"-"*14} {"-"*14} {"-"*20}')
        for rec in sorted(players, key=lambda r: r['joined_year'] or 0):
            joined = fmt_date(rec['joined_year'], rec.get('joined_month'))
            left = fmt_date(rec['left_year'], rec.get('left_month'))
            dur = rec['duration'] or '-'
            st = rec.get('status') or '-'
            print(f'  {rec["player"]:<30} {rec["role"]:<14} {st:<10} {joined:<14} {left:<14} {dur}')

    # Show sub-teams if any
    async with driver.session(database=DB_CONFIG['database']) as session:
        subs = await session.run("""
            MATCH (sub:Team)-[:SUBTEAM_OF]->(parent:Team {name: $name})
            RETURN sub.name AS name
            ORDER BY sub.name
        """, name=team_name)
        sub_data = await subs.data()
    if sub_data:
        print(f'\n  Sub-teams:')
        for s in sub_data:
            print(f'    - {s["name"]}')


# ── Utility queries ───────────────────────────────────────────────────────

async def available_years(driver):
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run('''
            MATCH ()-[r:PLAYED_FOR]->()
            RETURN DISTINCT r.start_year AS year
            ORDER BY year
        ''')
        records = await result.data()
    return [r['year'] for r in records if r['year'] is not None]


async def available_teams(driver):
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run('''
            MATCH (t:Team)
            RETURN t.name AS name
            ORDER BY name
        ''')
        records = await result.data()
    return [r['name'] for r in records]


async def available_players(driver):
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run('''
            MATCH (p:Player)
            RETURN p.name AS name
            ORDER BY name
        ''')
        records = await result.data()
    return [r['name'] for r in records]


async def available_regions(driver):
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run('''
            MATCH (t:Team)
            RETURN DISTINCT t.region AS region
            ORDER BY region
        ''')
        records = await result.data()
    return [r['region'] for r in records if r['region'] is not None]


async def rename_view(driver, team_name):
    print_section(f'TEAM RENAME HISTORY — {team_name}')
    async with driver.session(database=DB_CONFIG['database']) as session:
        # Get all rename relationships (building chain via query)
        all_rels = await session.run("""
            MATCH (old:Team)-[r:REBRANDED_TO]->(new:Team)
            RETURN old.name AS old_name, new.name AS new_name, r.year AS year
        """)
        rels = await all_rels.data()

    if not rels:
        print(f'  No rename history found for "{team_name}".')
        return

    # Build adjacency: old_name -> (new_name, year)
    fwd = {r['old_name']: (r['new_name'], r.get('year')) for r in rels}
    rev = {r['new_name']: r['old_name'] for r in rels}

    # Find the earliest ancestor by walking backward
    cur = team_name
    while cur in rev:
        cur = rev[cur]
    start = cur

    # Walk forward from start, building lineage
    lineage = []
    cur = start
    while cur in fwd:
        new_name, year = fwd[cur]
        lineage.append((cur, year))
        cur = new_name
    lineage.append((cur, None))

    # If the queried team is not in this chain at all, show nothing
    if team_name not in {n for n, _ in lineage}:
        print(f'  No rename history found for "{team_name}".')
        return

    if len(lineage) <= 1:
        print(f'  No rename history found for "{team_name}".')
        return

    print(f'  Rename chain:')
    for name, year in lineage:
        arrow = ' -> ' if year is not None else ''
        yr_tag = f' ({year})' if year else ''
        print(f'    {name}{yr_tag}{arrow}')
    print(f'\n  ({len(lineage)} name(s) in chain)')


async def subteam_view(driver, team_name):
    print_section(f'SUB-TEAMS OF — {team_name}')
    async with driver.session(database=DB_CONFIG['database']) as session:
        result = await session.run("""
            MATCH (sub:Team)-[r:SUBTEAM_OF]->(parent:Team {name: $name})
            RETURN sub.name AS subteam
            ORDER BY sub.name
        """, name=team_name)
        subs = await result.data()

    if not subs:
        print(f'  No sub-teams found for "{team_name}".')
        return

    print(f'  Parent: {team_name}')
    for s in subs:
        print(f'    - {s["subteam"]}')
    print(f'\n  ({len(subs)} sub-team(s) total)')


async def show_stats(driver):
    async with driver.session(database=DB_CONFIG['database']) as session:
        print_section('DATABASE STATISTICS')

        player_count = await session.run('MATCH (p:Player) RETURN count(p) AS cnt')
        p = await player_count.single()
        print(f'  Total Players: {p["cnt"]}')

        team_count = await session.run('MATCH (t:Team) RETURN count(t) AS cnt')
        t = await team_count.single()
        print(f'  Total Teams: {t["cnt"]}')

        rel_count = await session.run('MATCH ()-[r:PLAYED_FOR]->() RETURN count(r) AS cnt')
        rl = await rel_count.single()
        print(f'  Career Records: {rl["cnt"]}')

        role_dist = await session.run('MATCH (p:Player) WHERE p.role IS NOT NULL RETURN p.role AS role, count(*) AS cnt ORDER BY cnt DESC')
        roles = await role_dist.data()
        print(f'  Players by Role:')
        for r in roles:
            print(f'    {r["role"]:<12} {r["cnt"]}')


# ── Interactive CLI ───────────────────────────────────────────────────────

KNOWN_REGIONS = {'LCK', 'LPL', 'LEC', 'LCS', 'PCS', 'VCS', 'CBLOL', 'LLA', 'TCL', 'LJL', 'LCO', 'LFL', 'NL', 'OPL', 'ECL', 'WCL', 'SCL', 'LCL'}

async def interactive_mode(driver):
    print()
    print('  Available commands:')
    print('    [REGION] season <N>    - View teams & rosters for a competitive season')
    print('    [REGION] player <NAME> - View a player\'s career history')
    print('    [REGION] team <NAME>   - View a team\'s roster history')
    print('    rename <NAME>          - View a team\'s rename/rebrand chain')
    print('    subteams <NAME>        - View a team\'s sub-teams (challenger/academy)')
    print('    stats                 - Show database statistics')
    print('    seasons               - List available seasons')
    print('    teams                 - List all teams')
    print('    players               - List all players')
    print('    roles                 - List all player roles')
    print('    regions               - List all team regions')
    print('    help                  - Show this help')
    print('  Examples: "LCK season 14", "LPL player Faker", "season 13"')
    print()

    while True:
        try:
            cmd = input('LCK DB> ').strip()
        except (EOFError, KeyboardInterrupt):
            print('\nGoodbye!')
            break

        if not cmd:
            continue

        tokens = cmd.split()
        region = None
        if len(tokens) >= 2 and tokens[0].upper() in KNOWN_REGIONS:
            region = tokens[0].upper()
            tokens = tokens[1:]

        action = tokens[0].lower()
        value = ' '.join(tokens[1:]) if len(tokens) > 1 else ''

        if action in ('quit', 'exit', 'q'):
            print('Goodbye!')
            break

        if action == 'help':
            print('  Available commands:')
            print('    [REGION] season <N>    - View teams & rosters for a competitive season')
            print('    [REGION] player <NAME> - View a player\'s career history')
            print('    [REGION] team <NAME>   - View a team\'s roster history')
            print('    rename <NAME>          - View a team\'s rename/rebrand chain')
            print('    subteams <NAME>        - View a team\'s sub-teams (challenger/academy)')
            print('    stats                 - Show database statistics')
            print('    seasons               - List available seasons')
            print('    teams                 - List all teams')
            print('    players               - List all players')
            print('    roles                 - List all player roles')
            print('    regions               - List all team regions')
            print()

        elif action == 'stats':
            await show_stats(driver)

        elif action == 'seasons':
            yrs = await available_years(driver)
            if yrs:
                seasons = [str(y - 2010) for y in yrs if y >= 2011]
                print(f'  Available seasons: {", ".join(seasons)}')
            else:
                print('  No season data available.')

        elif action == 'teams':
            teams = await available_teams(driver)
            if teams:
                for t in teams:
                    print(f'  - {t}')
            else:
                print('  No teams available.')

        elif action == 'players':
            players = await available_players(driver)
            if players:
                for p in players:
                    print(f'  - {p}')
            else:
                print('  No players available.')

        elif action == 'roles':
            async with driver.session(database=DB_CONFIG['database']) as session:
                result = await session.run('MATCH (p:Player) WHERE p.role IS NOT NULL RETURN DISTINCT p.role AS role ORDER BY role')
                roles = await result.data()
            if roles:
                for r in roles:
                    print(f'  - {r["role"]}')
            else:
                print('  No role data available.')

        elif action == 'regions':
            regions = await available_regions(driver)
            if regions:
                for r in regions:
                    print(f'  - {r}')
            else:
                print('  No region data available.')

        elif action in ('year', 'season'):
            if not value:
                print('  Usage: [REGION] season <N>  (e.g. LCK season 14)')
                continue
            await year_view(driver, value, region=region)

        elif action == 'player':
            if not value:
                print('  Usage: [REGION] player <NAME>  (e.g. LCK player Faker)')
                continue
            await player_view(driver, value, region=region)

        elif action == 'team':
            if not value:
                print('  Usage: [REGION] team <NAME>  (e.g. LCK team T1)')
                continue
            await team_view(driver, value, region=region)

        elif action == 'rename':
            if not value:
                print('  Usage: rename <NAME>  (e.g. rename T1)')
                continue
            await rename_view(driver, value)

        elif action == 'subteams':
            if not value:
                print('  Usage: subteams <NAME>  (e.g. subteams T1)')
                continue
            await subteam_view(driver, value)

        else:
            print(f'  Unknown command: "{action}". Type "help" for options.')


# ── Single-run mode ───────────────────────────────────────────────────────

async def run_single(driver, view_type, value, region=None):
    view_type = view_type.lower()
    if view_type in ('year', 'season'):
        await year_view(driver, value, region=region)
    elif view_type == 'player':
        await player_view(driver, value, region=region)
    elif view_type == 'team':
        await team_view(driver, value, region=region)
    elif view_type == 'rename':
        await rename_view(driver, value)
    elif view_type == 'subteams':
        await subteam_view(driver, value)
    elif view_type == 'stats':
        await show_stats(driver)
    else:
        print(f'Unknown view: {view_type}')
        print('Valid views: season, player, team, rename, subteams, stats')


# ── Main ──────────────────────────────────────────────────────────────────

async def main():
    print('Connecting to Neo4j...')
    driver = AsyncGraphDatabase.driver(
        DB_CONFIG['uri'],
        auth=(DB_CONFIG['user'], DB_CONFIG['password']),
    )
    try:
        await driver.verify_connectivity()
        print('Connected!\n')

        # Parse command-line arguments (supports region-first syntax)
        # e.g.: lck_views.py year 2019        → view=year, value=2019
        #       lck_views.py LCK year 2019    → region=LCK, view=year, value=2019
        argv = sys.argv[1:]
        region = None
        if len(argv) >= 2 and argv[0].upper() in KNOWN_REGIONS:
            region = argv[0].upper()
            argv = argv[1:]

        if len(argv) >= 2:
            await run_single(driver, argv[0], argv[1], region=region)
        elif len(argv) == 1 and argv[0] in ('stats', 'years', 'seasons', 'teams', 'players', 'roles', 'regions'):
            if argv[0] == 'stats':
                await show_stats(driver)
            elif argv[0] == 'years':
                yrs = await available_years(driver)
                print(f'  Available years: {", ".join(str(y) for y in yrs)}')
            elif argv[0] == 'teams':
                teams = await available_teams(driver)
                for t in teams:
                    print(f'  - {t}')
            elif argv[0] == 'players':
                players = await available_players(driver)
                for p in players:
                    print(f'  - {p}')
            elif argv[0] == 'roles':
                async with driver.session(database=DB_CONFIG['database']) as session:
                    result = await session.run('MATCH (p:Player) WHERE p.role IS NOT NULL RETURN DISTINCT p.role AS role ORDER BY role')
                    roles = await result.data()
                for r in roles:
                    print(f'  - {r["role"]}')
            elif argv[0] == 'regions':
                regions = await available_regions(driver)
                for r in regions:
                    print(f'  - {r}')
        else:
            await interactive_mode(driver)
    finally:
        await driver.close()


if __name__ == '__main__':
    asyncio.run(main())
