#!/usr/bin/env python3
"""Batch re-scrape all current LCK/LPL players concurrently."""

import os, re, html, asyncio, aiohttp, sys
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

LCK_TEAMS = {
    'BNK FEARX', 'DN SOOPers', 'Dplus Kia', 'Gen.G',
    'HANJIN BRION', 'Hanwha Life', 'Hanwha Life Esports',
    'DRX', 'Kiwoom DRX', 'KT Rolster', 'NS RedForce',
    'Nongshim RedForce', 'T1',
}

LPL_TEAMS = {
    "Anyone's Legend", "aL", "Bilibili Gaming", "EDward Gaming",
    "Invictus Gaming", "JD Gaming", "LGD Gaming", "LNG Esports",
    "Ninjas in Pyjamas", "Team WE", "WE", "TT Gaming",
    "ThunderTalk Gaming", "Top Esports", "Weibo Gaming",
}

mn_map = {'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
          'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12}


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
    if 'lec' in lower or 'europe' in lower: return 'LEC'
    if 'lcs' in lower or 'america' in lower: return 'LCS'
    if 'pcs' in lower or 'taiwan' in lower: return 'PCS'
    if 'vcs' in lower or 'vietnam' in lower: return 'VCS'
    if 'cblol' in lower or 'brazil' in lower: return 'CBLOL'
    if 'ljl' in lower or 'japan' in lower: return 'LJL'
    if 'lco' in lower or 'oceania' in lower: return 'LCO'
    if 'lla' in lower: return 'LLA'
    if 'tcl' in lower or 'turkey' in lower: return 'TCL'
    return 'Unknown'


def extract_career_from_html(text: str) -> list[dict]:
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

    for table_html in tables:
        rows = re.findall(r'<tr>(.*?)</tr>', table_html, re.DOTALL)
        for row_html in rows:
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
            if len(tds) < 4:
                continue

            team_html = tds[1]
            team_clean = html.unescape(re.sub(r'<[^>]+>', '', team_html)).strip()
            team_name = team_clean.replace('\u2060', '').strip()
            if not team_name:
                continue
            if team_name.upper() == team_name and len(team_name) < 5 and team_name not in ('T1',):
                continue

            role = ''

            start_raw = html.unescape(re.sub(r'<[^>]+>', ' ', tds[3] if len(tds) > 3 else '')).strip()
            end_raw = html.unescape(re.sub(r'<[^>]+>', ' ', tds[4] if len(tds) > 4 else '')).strip()
            start_clean = re.sub(r'\[\s*\d+\s*\]', '', start_raw).strip()
            end_clean = re.sub(r'\[\s*\d+\s*\]', '', end_raw).strip()

            def parse_date(s):
                s = s.strip()
                ym = re.search(r'(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)\s+(\d{4})', s, re.IGNORECASE)
                if ym:
                    return int(ym.group(2)), mn_map.get(ym.group(1).lower())
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
                'team': team_name, 'role': role,
                'start_year': start_year, 'end_year': end_year,
                'start_month': start_month, 'end_month': end_month,
            })

    return careers


async def rescrape_one(driver, session: aiohttp.ClientSession, name: str, sem: asyncio.Semaphore):
    async with sem:
        try:
            async with session.get(
                FANDOM_API,
                params={'action': 'parse', 'page': name, 'prop': 'text', 'format': 'json'},
                headers=HEADERS,
            ) as resp:
                if resp.status != 200:
                    return f'{name}: HTTP {resp.status}'
                data = await resp.json()
                parsed = data.get('parse', {}).get('text', {}).get('*', '')

            if not parsed:
                return f'{name}: empty page'

            careers = extract_career_from_html(parsed)
            if not careers:
                return f'{name}: 0 careers (skipped)'

            async with driver.session(database=DB_CONFIG['database']) as s:
                r = await s.run('MATCH (p:Player {name: $name}) RETURN p.fandom_id AS fid', name=name)
                row = await r.data()
                if not row:
                    return f'{name}: not in DB'
                fid = row[0]['fid']

                await s.run(
                    'MATCH (p:Player {fandom_id: $fid})-[r:PLAYED_FOR]->() DELETE r',
                    fid=fid,
                )

                inserted = 0
                for c in careers:
                    region = detect_region(c['team'])
                    await s.run(
                        'MERGE (t:Team {name: $name, year: $year}) SET t.region = $region',
                        name=c['team'], year=c['start_year'], region=region,
                    )
                    if c['end_year'] and c['end_year'] != c['start_year']:
                        await s.run(
                            'MERGE (t:Team {name: $name, year: $year}) SET t.region = $region',
                            name=c['team'], year=c['end_year'], region=region,
                        )

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
                            role=c.get('role') or '',
                            sm=sm, em=em,
                        )
                        inserted += 1

                return f'{name}: {len(careers)} entries ({inserted} edges)'
        except Exception as e:
            return f'{name}: ERROR {e}'


async def main():
    driver = AsyncGraphDatabase.driver(DB_CONFIG['uri'], auth=(DB_CONFIG['user'], DB_CONFIG['password']))
    await driver.verify_connectivity()

    all_teams = sorted(LCK_TEAMS | LPL_TEAMS)
    print(f'Querying players for {len(all_teams)} teams...')

    async with driver.session(database=DB_CONFIG['database']) as s:
        r = await s.run(
            '''
            MATCH (t:Team)<-[r:PLAYED_FOR]-(p:Player)
            WHERE t.name IN $teams
            WITH t, p, max(t.year) AS max_year
            MATCH (t)<-[r2:PLAYED_FOR]-(p) WHERE t.year = max_year
            RETURN DISTINCT p.name AS name ORDER BY p.name
            ''',
            teams=all_teams,
        )
        records = await r.data()
        names = [rec['name'] for rec in records]

    print(f'Found {len(names)} players to rescrape')

    sem = asyncio.Semaphore(10)
    connector = aiohttp.TCPConnector(limit=20, limit_per_host=10)

    async with aiohttp.ClientSession(connector=connector, headers=HEADERS) as session:
        tasks = [rescrape_one(driver, session, n, sem) for n in names]
        for coro in asyncio.as_completed(tasks):
            msg = await coro
            print(msg)

    await driver.close()


if __name__ == '__main__':
    asyncio.run(main())
