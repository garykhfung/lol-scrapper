"""
Schema migration: Team nodes become per-year nodes.

Uses bulk Cypher UNWIND for performance.
"""

import os
import datetime
from dotenv import load_dotenv
from neo4j import GraphDatabase

load_dotenv(os.path.join(os.path.dirname(__file__), '.env'))

DB_CONFIG = {
    'uri': os.getenv('NEO4J_URI', 'bolt://localhost:7687'),
    'user': os.getenv('NEO4J_USERNAME') or os.getenv('NEO4J_USER', 'neo4j'),
    'password': os.getenv('NEO4J_PASSWORD', 'change_me'),
    'database': os.getenv('NEO4J_DATABASE', 'player'),
}

CURRENT_YEAR = datetime.date.today().year


def main():
    driver = GraphDatabase.driver(
        DB_CONFIG['uri'],
        auth=(DB_CONFIG['user'], DB_CONFIG['password']),
    )

    with driver.session(database=DB_CONFIG['database']) as session:
        # ── Step 0: Stats ────────────────────────────────────────────────
        old_team = session.run("MATCH (t:Team) RETURN count(t) AS c").single()['c']
        old_player = session.run("MATCH (p:Player) RETURN count(p) AS c").single()['c']
        old_pf = session.run("MATCH ()-[r:PLAYED_FOR]->() RETURN count(r) AS c").single()['c']
        old_re = session.run("MATCH ()-[r:REBRANDED_TO]->() RETURN count(r) AS c").single()['c']
        print(f'Pre: {old_team} teams, {old_player} players, {old_pf} PLAYED_FOR, {old_re} REBRANDED_TO')

        # ── Step 1: Relabel old Team → LegacyTeam ────────────────────────
        print('[1/7] Relabelling :Team → :LegacyTeam ...')
        session.run("MATCH (t:Team) REMOVE t:Team SET t:LegacyTeam")
        session.run("MATCH (r:Region) REMOVE r:Region SET r:LegacyRegion")
        c = session.run("MATCH (t:LegacyTeam) RETURN count(t) AS c").single()['c']
        print(f'  {c} nodes relabelled')

        # ── Step 2: Bulk-create per-year Team nodes ──────────────────────
        print('[2/7] Creating per-year Team nodes ...')
        session.run("""
            MATCH (t:LegacyTeam)
            OPTIONAL MATCH ()-[r:PLAYED_FOR]->(t)
            WITH t, min(r.start_year) AS min_yr,
                 max(CASE WHEN r.end_year IS NULL THEN $cur ELSE r.end_year END) AS max_yr
            WHERE min_yr IS NOT NULL
            WITH t, min_yr, max_yr
            UNWIND range(min_yr, max_yr) AS yr
            MERGE (n:Team {name: t.name, year: yr})
            SET n.region = coalesce(t.region, 'Unknown')
            RETURN count(DISTINCT n) AS c
        """, cur=CURRENT_YEAR)
        c = session.run("MATCH (t:Team) RETURN count(t) AS c").single()['c']
        print(f'  {c} year nodes created')

        # Teams without PLAYED_FOR edges — create a current-year node
        session.run("""
            MATCH (t:LegacyTeam)
            WHERE NOT EXISTS { ()-[:PLAYED_FOR]->(t) }
            MERGE (n:Team {name: t.name, year: $cur})
            SET n.region = coalesce(t.region, 'Unknown')
        """, cur=CURRENT_YEAR)

        # ── Step 3: Bulk NEXT_SEASON ─────────────────────────────────────
        print('[3/7] Creating NEXT_SEASON relationships ...')
        result = session.run("""
            MATCH (a:Team)
            MATCH (b:Team {name: a.name, year: a.year + 1})
            MERGE (a)-[:NEXT_SEASON]->(b)
            RETURN count(*) AS c
        """)
        c = result.single()['c']
        print(f'  {c} NEXT_SEASON edges')

        # ── Step 4: Bulk REBRANDED_TO ───────────────────────────────────
        print('[4/7] Migrating REBRANDED_TO ...')
        # Connect old-name's year node at rebrand year to new-name's year node at yr+1
        result = session.run("""
            MATCH (old:LegacyTeam)-[r:REBRANDED_TO]->(new:LegacyTeam)
            WHERE r.year IS NOT NULL
            WITH old, new, r.year AS yr
            MATCH (old_node:Team {name: old.name, year: yr})
            OPTIONAL MATCH (new_node:Team {name: new.name, year: yr + 1})
            WITH old_node, new_node, yr
            WHERE new_node IS NOT NULL
            MERGE (old_node)-[:REBRANDED_TO {year: yr}]->(new_node)
            RETURN count(*) AS c
        """)
        c = result.single()['c']

        # Fallback: connect to same year node
        result = session.run("""
            MATCH (old:LegacyTeam)-[r:REBRANDED_TO]->(new:LegacyTeam)
            WHERE r.year IS NOT NULL
            WITH old, new, r.year AS yr
            MATCH (old_node:Team {name: old.name, year: yr})
            OPTIONAL MATCH (new_node:Team {name: new.name, year: yr})
            WITH old_node, new_node, yr
            WHERE new_node IS NOT NULL
            AND NOT EXISTS { (old_node)-[:REBRANDED_TO]->(new_node) }
            MERGE (old_node)-[:REBRANDED_TO {year: yr}]->(new_node)
            RETURN count(*) AS c
        """)
        c2 = result.single()['c']
        print(f'  {c + c2} REBRANDED_TO edges')

        # ── Step 5: Bulk PLAYED_FOR (one per year played) ────────────────
        print('[5/7] Migrating PLAYED_FOR to per-year edges ...')
        # Create per-year edges in batches using UNWIND
        result = session.run("""
            MATCH (p:Player)-[r:PLAYED_FOR]->(t:LegacyTeam)
            WHERE r.start_year IS NOT NULL
            WITH p, r, t,
                 r.start_year AS sy,
                 coalesce(r.end_year, $cur) AS ey
            UNWIND range(sy, ey) AS yr
            MATCH (tn:Team {name: t.name, year: yr})
            MERGE (p)-[pf:PLAYED_FOR]->(tn)
            SET pf.role = coalesce(pf.role, r.role),
                pf.start_month = CASE
                    WHEN pf.start_month IS NULL AND yr = sy THEN r.start_month
                    ELSE pf.start_month
                END
            RETURN count(DISTINCT pf) AS c
        """, cur=CURRENT_YEAR)
        c = result.single()['c']

        # Delete old PLAYED_FOR edges
        session.run("""
            MATCH (p:Player)-[r:PLAYED_FOR]->(t:LegacyTeam)
            DELETE r
        """)
        print(f'  {c} per-year PLAYED_FOR edges created (old ones deleted)')

        # ── Step 6: Region nodes + BELONGS_TO ────────────────────────────
        print('[6/7] Creating Region nodes and BELONGS_TO ...')
        session.run("""
            MATCH (t:Team) WHERE t.region IS NOT NULL AND t.region <> ''
            MERGE (r:Region {name: t.region})
            MERGE (t)-[:BELONGS_TO]->(r)
        """)
        rc = session.run("MATCH (r:Region) RETURN count(r) AS c").single()['c']
        bc = session.run("MATCH ()-[r:BELONGS_TO]->(:Region) RETURN count(r) AS c").single()['c']
        print(f'  {rc} Region nodes, {bc} BELONGS_TO edges')

        # ── Step 7: Verify ───────────────────────────────────────────────
        print('[7/7] Verification ...')
        nt = session.run("MATCH (t:Team) RETURN count(t) AS c").single()['c']
        npf = session.run("MATCH ()-[r:PLAYED_FOR]->(:Team) RETURN count(r) AS c").single()['c']
        lt = session.run("MATCH (t:LegacyTeam) RETURN count(t) AS c").single()['c']
        ns = session.run("MATCH ()-[r:NEXT_SEASON]->() RETURN count(r) AS c").single()['c']
        nr = session.run("MATCH ()-[r:REBRANDED_TO]->(:Team) RETURN count(r) AS c").single()['c']
        print(f'  Team year nodes: {nt}')
        print(f'  LegacyTeam (preserved): {lt}')
        print(f'  PLAYED_FOR edges: {npf}')
        print(f'  NEXT_SEASON edges: {ns}')
        print(f'  REBRANDED_TO edges: {nr}')

        # Samples
        print('\n  DN SOOPers:')
        for r in session.run("MATCH (t:Team {name:'DN SOOPers'}) RETURN t.year AS yr ORDER BY yr"):
            print(f'    year={r["yr"]}')

        print('\n  Rebrand chain:')
        for r in session.run("""
            MATCH (old:Team)-[:REBRANDED_TO]->(new:Team)
            WHERE old.name IN ['Rebels Anarchy','Afreeca Freecs','Kwangdong Freecs','DN Freecs']
            RETURN old.name + '_' + toString(old.year) AS f,
                   new.name + '_' + toString(new.year) AS t
        """):
            print(f'    {r["f"]:35s} -> {r["t"]}')

        print('\n  NEXT_SEASON: T1')
        for r in session.run("""
            MATCH (t:Team {name:'T1'})-[:NEXT_SEASON]->(n:Team)
            RETURN t.year AS f, n.year AS t ORDER BY t.year LIMIT 5
        """):
            print(f'    {r["f"]} -> {r["t"]}')

        print('\n  Faker per-year:')
        for r in session.run("""
            MATCH (p:Player {name:'Faker'})-[pf:PLAYED_FOR]->(t:Team)
            RETURN t.name + '_' + toString(t.year) AS team, pf.role AS role
            ORDER BY t.year LIMIT 5
        """):
            print(f'    {r["team"]:20s} role={r["role"]}')

        print('\nDone!')

    driver.close()


if __name__ == '__main__':
    main()
