"""
Recovery: re-scrape career data for players whose PLAYED_FOR edges were lost
during migration. Also removes non-player junk pages from the DB.
"""

import os
import re
import sys
import asyncio
import aiohttp
from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase

sys.path.insert(0, os.path.dirname(__file__))
from lck_scraper import (
    get_parsed_text, upsert_player, upsert_career,
    extract_player_info_from_parse, extract_career_from_parsed_html,
    detect_region, normalize_role,
    DB_CONFIG, CURRENT_YEAR,
)

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))


NON_PLAYER_PATTERNS = [
    r'\d{4}\s+\w+.*\b(?:Scouting\s*Grounds|Tournament\s*Results|Season\b)',
    r'\b(?:investigations?|First\s*Stand|Gaming\s*League)\b',
    r'\bADC\s*Test\b',
    r'\b(?:Tournament\s*Results)\b',
]


def is_non_player(name: str) -> bool:
    for pat in NON_PLAYER_PATTERNS:
        if re.search(pat, name, re.IGNORECASE):
            return True
    return False


async def recover_missing_players(driver, session: aiohttp.ClientSession):
    async with driver.session(database=DB_CONFIG['database']) as neo_session:
        missing = await neo_session.run("""
            MATCH (p:Player)
            WHERE NOT EXISTS { (p)-[:PLAYED_FOR]->(:Team) }
            RETURN p.name AS name, p.fandom_id AS fid, p.role AS role
            ORDER BY p.name
        """)
        players = await missing.data()

    print(f'Total missing players: {len(players)}')

    real_players = [p for p in players if not is_non_player(p['name'])]
    junk_players = [p for p in players if is_non_player(p['name'])]
    print(f'Real players to recover: {len(real_players)}')
    print(f'Junk pages to delete: {len(junk_players)}')

    # Show junk
    if junk_players:
        print('\nJunk pages:')
        for p in junk_players[:15]:
            print(f'  {p["fid"]:10s} {p["name"]}')
        if len(junk_players) > 15:
            print(f'  ... and {len(junk_players) - 15} more')

    # Delete junk player nodes
    junk_ids = [p['fid'] for p in junk_players]
    if junk_ids:
        async with driver.session(database=DB_CONFIG['database']) as neo_session:
            result = await neo_session.run(
                "MATCH (p:Player) WHERE p.fandom_id IN $ids DELETE p RETURN count(*) AS c",
                ids=junk_ids,
            )
            r = await result.single()
            print(f'\nDeleted {r["c"]} junk player nodes')

    # Recover real players in batches
    print(f'\nRecovering {len(real_players)} real players...')
    recovered = 0
    failed = 0
    no_career = 0

    sem = asyncio.Semaphore(5)

    async def recover_one(p):
        nonlocal recovered, failed, no_career
        async with sem:
            page_title = p['name'].replace(' ', '_')
            text = await get_parsed_text(session, page_title)
            if not text:
                failed += 1
                return

            info = extract_player_info_from_parse(text, p['name'])
            careers = extract_career_from_parsed_html(text)

            if not careers:
                no_career += 1
                return

            player_role = info.get('role') or p['role'] or 'Unknown'
            is_retired = info.get('is_retired', False)
            has_current = any(c.get('end_year') is None for c in careers)
            status = 'Retired' if (is_retired or not has_current) else 'Active'

            if is_retired and careers:
                last = careers[-1]
                if last.get('end_year') is None:
                    last['end_year'] = info.get('retired_year')

            await upsert_player(driver, {
                'fandom_id': p['fid'],
                'name': p['name'],
                'real_name': info.get('real_name') or '',
                'role': player_role,
                'nationality': info.get('nationality') or '',
                'birth_date': info.get('birth_date') or '',
                'status': status,
            })

            for c in careers:
                is_current = c.get('end_year') is None
                stint_role = c.get('role') or player_role
                region = detect_region(c['team'])
                await upsert_career(
                    driver, p['fid'], c['team'], stint_role,
                    c['start_year'], c.get('start_month'),
                    c['end_year'], c.get('end_month'),
                    is_current, region,
                )

            recovered += 1

    for i in range(0, len(real_players), 10):
        batch = real_players[i:i + 10]
        tasks = [asyncio.create_task(recover_one(p)) for p in batch]
        await asyncio.gather(*tasks)
        print(f'  [{i + len(batch)}/{len(real_players)}] Recovered: {recovered}, No career: {no_career}, Failed: {failed}')
        await asyncio.sleep(1)

    print(f'\nDone! Recovered {recovered}, no career data: {no_career}, failed: {failed}')


async def main():
    driver = AsyncGraphDatabase.driver(
        DB_CONFIG['uri'],
        auth=(DB_CONFIG['user'], DB_CONFIG['password']),
    )
    try:
        async with aiohttp.ClientSession() as session:
            await recover_missing_players(driver, session)
    finally:
        await driver.close()


if __name__ == '__main__':
    asyncio.run(main())
