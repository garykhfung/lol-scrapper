"""
Role Migration Script
Normalizes player roles to standard values (bot→adc, jng→jungle, sup→support, m→mid, t→top).

Usage:
    python migrate_roles.py
"""

import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

from lck_scraper import ROLE_NORMALIZATION

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
    print("Connected to Neo4j")

    async with driver.session(database=DRIVER_CONFIG["database"]) as session:
        result = await session.run(
            "MATCH (p:Player) WHERE p.role IS NOT NULL RETURN p.name AS name, p.role AS role"
        )
        players = await result.data()
        print(f"Found {len(players)} players with roles")

        updated = 0
        for p in players:
            old_role = p["role"]
            role_lower = old_role.lower().strip()
            new_role = ROLE_NORMALIZATION.get(role_lower, old_role)
            if new_role != old_role:
                await session.run(
                    "MATCH (p:Player {name: $name}) SET p.role = $role",
                    name=p["name"], role=new_role,
                )
                updated += 1
                print(f"  {p['name']:<35} {old_role:<12} -> {new_role}")

    await driver.close()
    print(f"\nDone! Normalized roles for {updated} players.")


if __name__ == "__main__":
    asyncio.run(main())
