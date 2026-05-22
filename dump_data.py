#!/usr/bin/env python3
"""Export Neo4j data to static JSON files for GitHub Pages.

Usage:
  python dump_data.py

Generates files under data/ which are consumed by the static index.html.
Run this whenever the database is updated, then commit & push.
"""

import os, json, datetime
from collections import defaultdict
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv()

DB_CONFIG = {
    'uri': os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
    'user': os.getenv('NEO4J_USERNAME') or os.getenv('NEO4J_USER', 'neo4j'),
    'password': os.getenv('NEO4J_PASSWORD', 'change_me'),
    'database': os.getenv('NEO4J_DATABASE', 'player'),
}

CURRENT_YEAR = datetime.date.today().year
DATA_DIR = os.path.join(os.path.dirname(__file__), 'data')

SEASONS = {
    2011: {'name': 'Season 1', 'period': 'Jul 2010 – Aug 2011', 'era': 'named', 'splits': []},
    2012: {'name': 'Season 2', 'period': 'Nov 2011 – Oct 2012', 'era': 'named', 'splits': []},
    2013: {'name': 'Season 3', 'period': 'Feb – Oct 2013', 'era': 'named', 'splits': []},
    2014: {'name': '2014 Season', 'period': 'Jan – Oct 2014', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2015: {'name': '2015 Season', 'period': 'Jan – Oct 2015', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2016: {'name': '2016 Season', 'period': 'Jan – Oct 2016', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2017: {'name': '2017 Season', 'period': 'Jan – Nov 2017', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2018: {'name': '2018 Season', 'period': 'Jan – Nov 2018', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2019: {'name': '2019 Season', 'period': 'Jan – Nov 2019', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2020: {'name': '2020 Season', 'period': 'Jan – Oct 2020', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2021: {'name': '2021 Season', 'period': 'Jan – Nov 2021', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2022: {'name': '2022 Season', 'period': 'Jan – Nov 2022', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2023: {'name': '2023 Season', 'period': 'Jan – Nov 2023', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2024: {'name': '2024 Season', 'period': 'Jan – Nov 2024', 'era': 'two-split', 'splits': ['Spring Split', 'Summer Split']},
    2025: {'name': '2025 Season', 'period': 'Jan – Nov 2025 (3-split format)', 'era': 'three-split',
           'splits': ['Split 1 (LCK Cup / LEC Winter / LCS Lock-In) → First Stand',
                      'Split 2 (LCK Road to MSI / LEC Spring / LCS Spring) → MSI',
                      'Split 3 (LCK Playoffs / LEC Summer / LCS Summer) → Worlds']},
    2026: {'name': '2026 Season', 'period': 'Jan – Nov 2026 (3-split format)', 'era': 'three-split',
           'splits': ['Split 1 (LCK Cup / LEC Versus / LCS Lock-In) → First Stand',
                      'Split 2 (LCK Road to MSI / LEC Spring / LCS Spring) → MSI',
                      'Split 3 (LCK Playoffs / LEC Summer / LCS Summer) → Worlds']},
}

PRESENT_ENTRY = {'name': 'Active Players', 'period': 'Currently active rosters', 'era': 'present', 'splits': []}

BAD_KEYWORDS = ['match-fixing', 'investigation', 'controversy', 'scandal', 'tournament',
                'gaming', 'entus', 'rolster', 'dragonx', 'tigers', 'freecs',
                'redforce', 'brave', 'sandbox', 'brion', 'fearx', 'soopers']


def get_season_info(year):
    if year == 0:
        return dict(PRESENT_ENTRY)
    info = SEASONS.get(year)
    if info:
        return dict(info)
    return {'name': f'{year} Season', 'period': f'Jan – Nov {year}', 'era': 'unknown', 'splits': []}


def main():
    os.makedirs(DATA_DIR, exist_ok=True)
    driver = GraphDatabase.driver(DB_CONFIG['uri'], auth=(DB_CONFIG['user'], DB_CONFIG['password']))

    data = {}
    with driver.session(database=DB_CONFIG['database']) as session:
        data['players'] = session.run(
            'MATCH (p:Player) RETURN p.name AS name, p.role AS role, '
            'p.nationality AS nationality, p.birth_date AS birth_date, p.status AS status '
            'ORDER BY p.name'
        ).data()

        data['team_years'] = session.run(
            'MATCH (t:Team) RETURN t.name AS name, t.region AS region, t.year AS year '
            'ORDER BY t.name, t.year'
        ).data()

        data['careers'] = session.run(
            'MATCH (p:Player)-[r:PLAYED_FOR]->(t:Team) '
            'RETURN p.name AS player, t.name AS team, t.region AS region, '
            'r.role AS role, t.year AS year, '
            'r.start_month AS start_month, r.end_month AS end_month'
        ).data()

        data['renames'] = session.run(
            'MATCH (old:Team)-[r:REBRANDED_TO]->(new:Team) '
            'RETURN DISTINCT old.name AS old_name, new.name AS new_name, r.year AS year'
        ).data()

        data['subteams'] = session.run(
            'MATCH (sub:Team)-[:SUBTEAM_OF]->(parent:Team) '
            'RETURN DISTINCT sub.name AS subteam, parent.name AS parent'
        ).data()

    driver.close()

    players = data['players']
    team_years = data['team_years']
    careers = data['careers']
    renames = data['renames']
    subteams = data['subteams']

    # ── Team metadata ──
    team_meta = {}
    for ty in team_years:
        n = ty['name']
        if n not in team_meta:
            team_meta[n] = {'region': ty['region'], 'start_year': ty['year'], 'end_year': ty['year']}
        else:
            if ty['year'] < team_meta[n]['start_year']:
                team_meta[n]['start_year'] = ty['year']
            if ty['year'] > team_meta[n]['end_year']:
                team_meta[n]['end_year'] = ty['year']

    # ── Seasons ──
    years_in_db = sorted(set(ty['year'] for ty in team_years if ty['year'] >= 2011))
    seasons = [{'year': y, **get_season_info(y)} for y in years_in_db]
    seasons.append({'year': 0, **PRESENT_ENTRY})

    # ── Stats ──
    player_count = len(players)
    unique_team_names = len(set(ty['name'] for ty in team_years))
    career_count = len(careers)

    role_dist = defaultdict(int)
    for p in players:
        if p.get('role'):
            role_dist[p['role']] += 1
    role_distribution = [{'role': r, 'cnt': c} for r, c in
                         sorted(role_dist.items(), key=lambda x: -x[1])]

    region_team_counts = defaultdict(set)
    for ty in team_years:
        if ty.get('region'):
            region_team_counts[ty['region']].add(ty['name'])
    region_distribution = [{'region': r, 'cnt': len(region_team_counts[r])}
                          for r in sorted(region_team_counts.keys(),
                                         key=lambda r: -len(region_team_counts[r]))]

    stats = {
        'player_count': player_count,
        'team_count': unique_team_names,
        'career_count': career_count,
        'role_distribution': role_distribution,
        'region_distribution': region_distribution,
    }

    # ── Lookup lists ──
    all_regions = sorted(set(ty['region'] for ty in team_years if ty.get('region')))
    all_roles = sorted(set(p['role'] for p in players if p.get('role')))
    all_team_names = sorted(set(ty['name'] for ty in team_years))

    # Player names with filtering
    team_name_set = {n.lower() for n in all_team_names}
    all_player_names = []
    for p in players:
        n = p['name']
        if len(n) >= 50:
            continue
        if n.lower() in team_name_set:
            continue
        if any(k in n.lower() for k in BAD_KEYWORDS):
            continue
        if not any(c['player'] == n for c in careers):
            continue
        all_player_names.append(n)

    # Player names by region (from careers)
    players_by_region = defaultdict(set)
    for c in careers:
        if c['player'] in all_player_names and c.get('region'):
            players_by_region[c['region']].add(c['player'])

    # ── Rename / subteam lookups ──
    subteam_parents = sorted(set(s['parent'] for s in subteams))

    # ── Build rebrand chains ──
    fwd, rev = {}, {}
    fwd_year, rev_year = {}, {}
    for r in renames:
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
    rename_teams_set = set()
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
            rename_teams_set.add(name)

    # ── Pre-compute rename chains for each team ──
    rename_chains = {}
    for team in all_team_names:
        cur = team
        while cur in rev:
            cur = rev[cur]
        start = cur
        cur = start
        lineage = []
        while cur in fwd:
            new_name = fwd[cur]
            yr = fwd_year.get(cur)
            lineage.append([cur, yr])
            cur = new_name
        lineage.append([cur, None])
        chain_names = {n for n, _ in lineage}
        if team in chain_names and len(lineage) > 1:
            rename_chains[team] = lineage

    # ── Pre-compute team rosters ──
    mn_map = {1: 'Jan', 2: 'Feb', 3: 'Mar', 4: 'Apr', 5: 'May', 6: 'Jun',
              7: 'Jul', 8: 'Aug', 9: 'Sep', 10: 'Oct', 11: 'Nov', 12: 'Dec'}

    team_rosters = {}
    for team_name in all_team_names:
        team_careers = sorted(
            [c for c in careers if c['team'] == team_name],
            key=lambda x: (x['player'], x['year'])
        )
        if not team_careers:
            continue

        grouped = []
        for c in team_careers:
            stint_role = c.get('role') or 'Unknown'
            cur = {
                'player': c['player'],
                'role': stint_role,
                'start_year': c['year'],
                'end_year': c['year'],
                'start_month': c.get('start_month'),
                'end_month': c.get('end_month'),
            }
            if grouped and grouped[-1]['player'] == cur['player'] and \
               grouped[-1]['role'] == cur['role'] and \
               grouped[-1]['end_year'] + 1 >= c['year']:
                grouped[-1]['end_year'] = c['year']
                if cur['end_month'] is not None:
                    grouped[-1]['end_month'] = cur['end_month']
            else:
                grouped.append(cur)

        for g in grouped:
            sy, sm = g['start_year'], g.get('start_month')
            ey, em = g['end_year'], g.get('end_month')
            start = f"{mn_map[sm]} {sy}" if sm else str(sy)
            end = 'Present' if ey >= CURRENT_YEAR else (f"{mn_map[em]} {ey}" if em else str(ey))
            g['duration'] = f"{start} - {end}"

        grouped.sort(key=lambda g: (g['start_year'], g.get('start_month') or 0, g.get('role') or ''))
        team_rosters[team_name] = grouped

    # ── Player info map ──
    player_info = {p['name']: {
        'role': p.get('role'),
        'nationality': p.get('nationality'),
        'birth_date': p.get('birth_date'),
        'status': p.get('status'),
    } for p in players}

    # ── Write files ──

    index_data = {
        'stats': stats,
        'regions': all_regions,
        'roles': all_roles,
        'team_names': all_team_names,
        'player_names': all_player_names,
        'players_by_region': {r: sorted(players_by_region[r]) for r in all_regions},
        'rename_teams': sorted(rename_teams_set),
        'subteam_teams': subteam_parents,
        'seasons': seasons,
        'chain_display': chain_display,
        'team_to_root': team_to_root,
    }

    with open(os.path.join(DATA_DIR, 'index.json'), 'w') as f:
        json.dump(index_data, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, 'players.json'), 'w') as f:
        json.dump(players, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, 'careers.json'), 'w') as f:
        json.dump(careers, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, 'renames.json'), 'w') as f:
        json.dump(renames, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, 'subteams.json'), 'w') as f:
        json.dump(subteams, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, 'team_meta.json'), 'w') as f:
        json.dump(team_meta, f, indent=2, ensure_ascii=False)

    with open(os.path.join(DATA_DIR, 'rename_chains.json'), 'w') as f:
        json.dump(rename_chains, f, indent=2, ensure_ascii=False)

    team_rosters_list = [[k, v] for k, v in team_rosters.items()]
    with open(os.path.join(DATA_DIR, 'team_rosters.json'), 'w') as f:
        json.dump(team_rosters_list, f, indent=2, ensure_ascii=False)

    print(f"Exported: {player_count} players, {unique_team_names} teams, {career_count} careers")
    print(f"Output: {DATA_DIR}/")


if __name__ == '__main__':
    main()
