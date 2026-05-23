#!/usr/bin/env python3
"""Re-scrape a single player's page to refresh their career data in Neo4j."""

import os, re, html, asyncio, aiohttp
from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase

load_dotenv()

DB_CONFIG = {
    'uri': os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
    'user': os.getenv('NEO4J_USERNAME') or os.getenv('NEO4J_USER', 'neo4j'),
    'password': os.getenv('NEO4J_PASSWORD', 'change_me'),
    'database': os.getenv('NEO4J_DATABASE', 'player'),
}

FANDOM_API = 'https://lol.fandom.com/api.php'
HEADERS = {'User-Agent': 'LCK-Career-Scraper/1.0'}

NATIONALITY_MAP = {
    'South Korean': 'KR', 'Korean': 'KR', 'Korean South': 'KR',
    'Chinese': 'CN', 'Mainland Chinese': 'CN', 'Taiwanese': 'TW',
    'American': 'US', 'Canadian': 'CA',
    'Swedish': 'SE', 'Danish': 'DK', 'French': 'FR', 'German': 'DE',
    'British': 'GB', 'Spanish': 'ES', 'Japanese': 'JP',
    'Brazilian': 'BR', 'Australian': 'AU', 'Thai': 'TH',
    'Vietnamese': 'VN', 'Polish': 'PL', 'Dutch': 'NL',
    'Turkish': 'TR', 'Mexican': 'MX', 'Argentine': 'AR', 'Chilean': 'CL',
    'Russian': 'RU', 'Ukrainian': 'UA',
    'Malaysian': 'MY', 'Singaporean': 'SG', 'Filipino': 'PH',
    'New Zealander': 'NZ', 'Hong Kong': 'HK', 'Mongolian': 'MN',
}

REGION_MAP = {
    'kr': 'LCK', 'korea': 'LCK', 'south korea': 'LCK',
    'cn': 'LPL', 'china': 'LPL',
    'na': 'LCS', 'north america': 'LCS',
    'eu': 'LEC', 'europe': 'LEC',
    'tw': 'PCS', 'hk': 'PCS', 'mo': 'PCS',
    'vn': 'VCS', 'vietnam': 'VCS',
    'br': 'CBLOL', 'brazil': 'CBLOL',
    'jp': 'LJL', 'japan': 'LJL',
    'oce': 'LCO', 'australia': 'LCO',
    'la': 'LLA', 'latam': 'LLA',
    'tr': 'TCL', 'turkey': 'TCL',
    'mena': 'LJL',
}


def detect_region(team_name: str) -> str:
    lower = team_name.lower()
    if any(x in lower for x in ['t1', 'korea', 'hanwha', 'gen.g', 'dplus', 'kt ', 'drx',
                                 'fearx', 'freecs', 'soopers', 'brion', 'sandbox',
                                 'redforce', 'nongshim', 'kwangdong']):
        return 'LCK'
    if any(x in lower for x in ['lpl', 'china', 'bilibili', 'edward', 'invictus',
                                 'jd gaming', 'lgd', 'lng', 'ninjas in pyjamas',
                                 'team we', 'thundertalk', 'top esports', 'weibo',
                                 'anyone\'s legend', 'rare atom', 'oh my god',
                                 'royal never', 'funplus', 'rng', 'tt gaming']):
        return 'LPL'
    if 'lec' in lower or 'europe' in lower:
        return 'LEC'
    if 'lcs' in lower or 'america' in lower:
        return 'LCS'
    if 'pcs' in lower or 'taiwan' in lower:
        return 'PCS'
    if 'vcs' in lower or 'vietnam' in lower:
        return 'VCS'
    if 'cblol' in lower or 'brazil' in lower:
        return 'CBLOL'
    if 'ljl' in lower or 'japan' in lower:
        return 'LJL'
    if 'lco' in lower or 'oceania' in lower:
        return 'LCO'
    if 'lla' in lower:
        return 'LLA'
    if 'tcl' in lower or 'turkey' in lower:
        return 'TCL'
    return 'Unknown'


def extract_career_from_html(text: str) -> list[dict]:
    """Extract team history from the rendered HTML table."""
    careers = []

    tables = re.findall(
        r'<table[^>]*class="[^"]*player-team-history[^"]*"[^>]*>(.*?)</table>',
        text, re.DOTALL | re.IGNORECASE,
    )
    if not tables:
        tables = re.findall(
            r'<table[^>]*class="[^"]*wikitable[^"]*"[^>]*>(.*?)</table>',
            text, re.DOTALL | re.IGNORECASE,
        )

    table_map = {
        '': '', 'top': 'top', 'top lane': 'top', 'top laner': 'top',
        'jungle': 'jungle', 'jungle lane': 'jungle', 'jungler': 'jungle', 'jg': 'jungle',
        'mid': 'mid', 'mid lane': 'mid', 'mid laner': 'mid', 'middle': 'mid',
        'adc': 'adc', 'bot': 'adc', 'bot lane': 'adc', 'bot laner': 'adc',
        'support': 'support', 'support lane': 'support', 'sup': 'support',
    }

    r_map = {1: 'jan', 2: 'feb', 3: 'mar', 4: 'apr', 5: 'may', 6: 'jun',
             7: 'jul', 8: 'aug', 9: 'sep', 10: 'oct', 11: 'nov', 12: 'dec'}
    mn_map = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
              'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}

    for table_html in tables:
        rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
        for row_html in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
            if len(tds) < 4:
                continue

            # Team name from 2nd td (index 1)
            team_html = tds[1]
            team_clean = html.unescape(re.sub(r'<[^>]+>', '', team_html)).strip()
            team_name = team_clean.replace('\u2060', '').strip()
            if not team_name:
                continue
            if team_name.upper() == team_name and len(team_name) < 5 and team_name not in ('T1',):
                continue

            # Role from row background color or header context (skip - most rows have no sprite)
            role = ''

            # Date parsing: tds[3] = start, tds[4] = end
            start_raw = html.unescape(re.sub(r'<[^>]+>', ' ', tds[3] if len(tds) > 3 else '')).strip()
            end_raw = html.unescape(re.sub(r'<[^>]+>', ' ', tds[4] if len(tds) > 4 else '')).strip()
            start_clean = re.sub(r'\[\s*\d+\s*\]', '', start_raw).strip()
            end_clean = re.sub(r'\[\s*\d+\s*\]', '', end_raw).strip()

            def parse_date(s):
                s = s.strip()
                ym = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})', s, re.IGNORECASE)
                if ym:
                    return int(ym.group(2)), mn_map.get(ym.group(1).lower())
                # Try "YYYY-MM-DD" or "YYYY-MM"
                ym = re.search(r'(\d{4})-(\d{1,2})', s)
                if ym:
                    return int(ym.group(1)), int(ym.group(2))
                y = re.search(r'(\d{4})', s)
                if y:
                    return int(y.group(1)), None
                return None, None

            start_year, start_month = parse_date(start_clean)
            end_year, end_month = None, None
            if 'present' not in end_clean.lower():
                end_year, end_month = parse_date(end_clean)

            if not start_year:
                continue

            careers.append({
                'team': team_name,
                'role': role,
                'start_year': start_year,
                'end_year': end_year,
                'start_month': start_month,
                'end_month': end_month,
            })

    return careers


async def rescrape_player(name: str):
    """Re-scrape a player page and update Neo4j."""
    driver = AsyncGraphDatabase.driver(DB_CONFIG['uri'], auth=(DB_CONFIG['user'], DB_CONFIG['password']))
    await driver.verify_connectivity()

    async with aiohttp.ClientSession() as session:
        # Fetch the page
        async with session.get(FANDOM_API, params={'action': 'parse', 'page': name, 'prop': 'text', 'format': 'json'}, headers=HEADERS) as resp:
            if resp.status != 200:
                print(f'{name}: HTTP {resp.status}')
                await driver.close()
                return
            data = await resp.json()
            parsed = data.get('parse', {}).get('text', {}).get('*', '')

        if not parsed:
            print(f'{name}: no page text returned')
            await driver.close()
            return

        # Extract careers
        careers = extract_career_from_html(parsed)
        print(f'{name}: found {len(careers)} career entries')

        # Get player's fandom_id
        async with driver.session(database=DB_CONFIG['database']) as s:
            r = await s.run('MATCH (p:Player {name: $name}) RETURN p.fandom_id AS fid', name=name)
            row = await r.data()
            if not row:
                print(f'{name}: not found in DB')
                await driver.close()
                return
            fid = row[0]['fid']

        # Delete existing PLAYED_FOR edges for this player
        async with driver.session(database=DB_CONFIG['database']) as s:
            await s.run(
                'MATCH (p:Player {fandom_id: $fid})-[r:PLAYED_FOR]->() DELETE r',
                fid=fid,
            )
            print(f'{name}: deleted existing career edges')

        # Insert new career edges
        def normalize_role(r):
            if not r:
                return r
            m = {'bot': 'adc', 'jng': 'jungle', 'sup': 'support', 'm': 'mid', 't': 'top'}
            return m.get(r.lower().strip(), r)

        for c in careers:
            region = detect_region(c['team'])
            async with driver.session(database=DB_CONFIG['database']) as s:
                # Ensure Team node exists
                await s.run(
                    'MERGE (t:Team {name: $name, year: $year}) '
                    'SET t.region = $region',
                    name=c['team'], year=c['start_year'], region=region,
                )
                if c['end_year'] and c['end_year'] != c['start_year']:
                    await s.run(
                        'MERGE (t:Team {name: $name, year: $year}) '
                        'SET t.region = $region',
                        name=c['team'], year=c['end_year'], region=region,
                    )

                # Create PLAYED_FOR edge
                for y in range(c['start_year'], (c['end_year'] or c['start_year']) + 1):
                    sm = c['start_month'] if y == c['start_year'] else None
                    em = c['end_month'] if c['end_year'] and y == c['end_year'] else None
                    await s.run(
                        'MATCH (p:Player {fandom_id: $fid}) '
                        'MATCH (t:Team {name: $tname, year: $year}) '
                        'MERGE (p)-[r:PLAYED_FOR]->(t) '
                        'SET r.role = $role, '
                        '    r.start_month = CASE WHEN $sm IS NOT NULL THEN $sm ELSE r.start_month END, '
                        '    r.end_month = CASE WHEN $em IS NOT NULL THEN $em ELSE r.end_month END',
                        fid=fid, tname=c['team'], year=y,
                        role=normalize_role(c.get('role') or ''),
                        sm=sm, em=em,
                    )

        print(f'{name}: inserted {len(careers)} career entries')

    await driver.close()


if __name__ == '__main__':
    import sys
    names = sys.argv[1:] if len(sys.argv) > 1 else ['Zeus']
    for n in names:
        print(f'\nRescraping {n}...')
        asyncio.run(rescrape_player(n))
