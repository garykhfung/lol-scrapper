#!/usr/bin/env python3
"""Fix players with missing role/nationality by re-fetching their fandom page."""

import os, re, html, asyncio, aiohttp
from collections import Counter
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


def extract_info(text):
    info = {'role': '', 'nationality': ''}
    clean = html.unescape(text)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'[\u2060\u200b\u200c\u200d]', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()
    intro_end = clean.find('\n\n')
    if intro_end == -1:
        intro_end = clean.find('\n')
    if intro_end == -1:
        intro_end = 2000
    intro = clean[:intro_end]
    for nation, code in NATIONALITY_MAP.items():
        if nation.lower() in clean.lower():
            info['nationality'] = code
            break
    role_pats = [
        (r'\btop\s+laner', 'top'), (r'\bjungle\s+laner', 'jungle'),
        (r'\bjg\s+laner', 'jungle'), (r'\bplays?\s+jungle', 'jungle'),
        (r'\bmiddle\s+laner', 'mid'), (r'\bmid\s+laner', 'mid'),
        (r'\bplays?\s+mid', 'mid'), (r'\botc\s+laner', 'mid'),
        (r'\bot\s+laner', 'mid'), (r'\botc', 'mid'),
        (r'\bbot\s+laner', 'adc'), (r'\bad\s+laner', 'adc'),
        (r'\bad\s+carries?\b', 'adc'), (r'\bplays?\s+adc', 'adc'),
        (r'\bplays?\s+bot', 'adc'), (r'\bsupport\s+laner', 'support'),
        (r'\bplays?\s+support', 'support'),
        (r'\bhead\s+coach\b', 'head coach'), (r'\bassistant\s+coach\b', 'assistant coach'),
        (r'\bcoach\b', 'coach'), (r'\banalyst\b', 'analyst'),
        (r'\bmanager\b', 'manager'), (r'\bowner\b', 'owner'),
        (r'\bstreamer\b', 'streamer'), (r'\bcontent\s+creator\b', 'content creator'),
        (r'\bcaster\b', 'caster'), (r'\bhost\b', 'host'),
        (r'\bsubstitute\b', 'substitute'), (r'\binactive\b', 'inactive'),
    ]
    for pattern, role in role_pats:
        if re.search(pattern, intro, re.IGNORECASE):
            info['role'] = role
            break
    return info


async def main():
    driver = AsyncGraphDatabase.driver(DB_CONFIG['uri'], auth=(DB_CONFIG['user'], DB_CONFIG['password']))
    await driver.verify_connectivity()

    async with driver.session(database=DB_CONFIG['database']) as s:
        result = await s.run(
            'MATCH (p:Player) WHERE p.nationality IS NULL OR p.role IS NULL '
            'RETURN p.name AS name, p.fandom_id AS fid '
            'ORDER BY p.name')
        rows = await result.data()

    print(f'Players missing data: {len(rows)}')
    if not rows:
        await driver.close()
        return

    async with aiohttp.ClientSession() as session:
        for i, row in enumerate(rows):
            name, fid = row['name'], row['fid']
            try:
                async with session.get(FANDOM_API, params={'action': 'parse', 'page': name, 'prop': 'text', 'format': 'json'}, headers=HEADERS) as resp:
                    if resp.status != 200:
                        print(f'  [{i+1}/{len(rows)}] {name}: HTTP {resp.status}')
                        await asyncio.sleep(0.3)
                        continue
                    data = await resp.json()
                    parsed = data.get('parse', {}).get('text', {}).get('*', '') if isinstance(data.get('parse', {}).get('text'), dict) else ''

                if not parsed:
                    print(f'  [{i+1}/{len(rows)}] {name}: no text')
                    await asyncio.sleep(0.3)
                    continue

                info = extract_info(parsed)

                # Override role with most common career stint role if available
                async with driver.session(database=DB_CONFIG['database']) as s:
                    result = await s.run(
                        'MATCH (p:Player {fandom_id: $fid})-[r:PLAYED_FOR]->(:Team) '
                        'RETURN r.role AS role', fid=fid)
                    career_rows = await result.data()
                    career_roles = [r['role'] for r in career_rows if r['role']]
                    if career_roles:
                        info['role'] = Counter(career_roles).most_common(1)[0][0]

                if not info['role']:
                    info['role'] = 'Unknown'

                async with driver.session(database=DB_CONFIG['database']) as s:
                    await s.run(
                        'MATCH (p:Player {fandom_id: $fid}) '
                        'SET p.role = $role, p.nationality = $nat',
                        fid=fid, role=info['role'], nat=info['nationality'] or None)

                role_str = info.get('role', '-')
                nat_str = info.get('nationality', '-')
                print(f'  [{i+1}/{len(rows)}] {name}: role={role_str} nats={nat_str}')

            except Exception as e:
                print(f'  [{i+1}/{len(rows)}] {name}: error: {e}')

            await asyncio.sleep(0.3)

    await driver.close()
    print(f'\nDone. Processed {len(rows)} players.')


async def fix_specific(driver, session, name):
    """Fix a single player by name."""
    async with driver.session(database=DB_CONFIG['database']) as s:
        result = await s.run('MATCH (p:Player {name: $name}) RETURN p.fandom_id AS fid', name=name)
        row = await result.data()
        if not row:
            print(f'{name}: not found in DB')
            return
        fid = row[0]['fid']

    async with session.get(FANDOM_API, params={'action': 'parse', 'page': name, 'prop': 'text', 'format': 'json'}, headers=HEADERS) as resp:
        if resp.status != 200:
            print(f'{name}: HTTP {resp.status}')
            return
        data = await resp.json()
        parsed = data.get('parse', {}).get('text', {}).get('*', '')

    if not parsed:
        print(f'{name}: no page text')
        return

    info = extract_info(parsed)

    async with driver.session(database=DB_CONFIG['database']) as s:
        result = await s.run(
            'MATCH (p:Player {fandom_id: $fid})-[r:PLAYED_FOR]->(:Team) '
            'RETURN r.role AS role', fid=fid)
        rows = await result.data()
        roles = [r['role'] for r in rows if r['role']]
        if roles:
            info['role'] = Counter(roles).most_common(1)[0][0]

    if not info['role']:
        info['role'] = 'Unknown'

    async with driver.session(database=DB_CONFIG['database']) as s:
        await s.run(
            'MATCH (p:Player {fandom_id: $fid}) '
            'SET p.role = $role, p.nationality = $nat',
            fid=fid, role=info['role'], nat=info['nationality'] or None)

    print(f'{name}: role={info["role"]} nats={info["nationality"]}')


if __name__ == '__main__':
    import sys
    if len(sys.argv) > 1:
        name = sys.argv[1]
        async def run_single():
            driver = AsyncGraphDatabase.driver(DB_CONFIG['uri'], auth=(DB_CONFIG['user'], DB_CONFIG['password']))
            await driver.verify_connectivity()
            async with aiohttp.ClientSession() as session:
                await fix_specific(driver, session, name)
            await driver.close()
        asyncio.run(run_single())
    else:
        asyncio.run(main())
