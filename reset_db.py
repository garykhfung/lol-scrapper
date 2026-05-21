"""
Reset Script — Deletes all nodes and relationships from the Neo4j database.
Use this before a clean re-scrape to a fresh/cloud database.

Usage:
    python reset_db.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

DRIVER_CONFIG = {
    "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    "user": os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j"),
    "password": os.getenv("NEO4J_PASSWORD", "change_me"),
    "database": os.getenv("NEO4J_DATABASE", "player"),
}


async def main():
    from neo4j import AsyncGraphDatabase

    driver = AsyncGraphDatabase.driver(
        DRIVER_CONFIG["uri"],
        auth=(DRIVER_CONFIG["user"], DRIVER_CONFIG["password"]),
    )
    await driver.verify_connectivity()
    print('Connected to Neo4j')

    async with driver.session(database=DRIVER_CONFIG["database"]) as session:
        # Get counts first
        result = await session.run('MATCH (n) RETURN count(n) AS cnt')
        rec = await result.single()
        print(f'Current nodes: {rec["cnt"] if rec else 0}')

        # Delete all relationships and nodes
        await session.run('MATCH ()-[r]->() DELETE r')
        print('All relationships deleted.')
        await session.run('MATCH (n) DELETE n')
        print('All nodes deleted.')

        # Verify
        result = await session.run('MATCH (n) RETURN count(n) AS cnt')
        rec = await result.single()
        print(f'Remaining nodes: {rec["cnt"] if rec else 0}')

    await driver.close()
    print('Database reset complete!')


if __name__ == '__main__':
    confirm = input('This will DELETE ALL DATA in your Neo4j database. Continue? (y/N): ')
    if confirm.lower() == 'y':
        asyncio.run(main())
    else:
        print('Cancelled.')
