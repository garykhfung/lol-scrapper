"""
Status Migration Script
Sets Player.status based on PLAYED_FOR relationship data:
  - If any relationship has end_year IS NULL → Active
  - Otherwise → Retired
"""

import asyncio
import os
import sys
from dotenv import load_dotenv

if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)

load_dotenv()

RETIRE_DETECT_ROLES = {
    'streamer', 'analyst', 'caster', 'host', 'manager',
    'owner', 'founder', 'content creator', 'journalist',
    'interviewer', 'commentator', 'co-streamer',
}


async def main():
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
        auth=(os.getenv('NEO4J_USERNAME') or os.getenv('NEO4J_USER', 'neo4j'), os.getenv('NEO4J_PASSWORD', 'change_me')),
    )
    await driver.verify_connectivity()
    print('Connected to Neo4j')

    async with driver.session(database=os.getenv('NEO4J_DATABASE', 'player')) as session:
        # Read all players with their career end_years
        result = await session.run("""
            MATCH (p:Player)
            OPTIONAL MATCH (p)-[r:PLAYED_FOR]->()
            WITH p, collect(r.end_year) AS end_years, collect(r.role) AS rel_roles
            RETURN p.name AS name, p.fandom_id AS fandom_id, p.role AS role, end_years, rel_roles
        """)
        players = await result.data()
        print(f'Found {len(players)} players to process')

        updated = 0
        for p in players:
            end_years = p['end_years']
            # Remove None values from the list
            has_null = any(yr is None for yr in end_years)
            if has_null:
                status = 'Active'
            else:
                status = 'Retired'

            await session.run(
                'MATCH (p:Player {fandom_id: $fid}) SET p.status = $status',
                fid=p['fandom_id'], status=status,
            )
            updated += 1
            print(f'  {p["name"]:<30} -> {status}')

    await driver.close()
    print(f'\nDone! Updated {updated} players with status.')


if __name__ == '__main__':
    asyncio.run(main())
