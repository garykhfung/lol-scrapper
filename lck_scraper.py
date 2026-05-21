"""
Pro Player Career Data Extractor
Scrapes lol.fandom.com via MediaWiki API and stores player career data in Neo4j.
Crawls ALL major leagues: LCK, LPL, LEC, LCS, PCS, VCS, CBLOL, LLA, TCL, LJL, LCO.
Uses parse API (more reliable than extract API for disambiguation pages).
"""

import html
import json
import os
import re
import sys
import aiohttp
import asyncio
from dotenv import load_dotenv
from neo4j import AsyncGraphDatabase, AsyncDriver
from typing import Optional


if sys.stdout.encoding.lower() != "utf-8":
    sys.stdout = open(sys.stdout.fileno(), mode="w", encoding="utf-8", buffering=1)


# ── Configuration ──────────────────────────────────────────────────────────
load_dotenv()

DB_CONFIG = {
    "uri": os.getenv("NEO4J_URI", "bolt://localhost:7687"),
    "user": os.getenv("NEO4J_USERNAME") or os.getenv("NEO4J_USER", "neo4j"),
    "password": os.getenv("NEO4J_PASSWORD", "change_me"),
    "database": os.getenv("NEO4J_DATABASE", "player"),
}

CURRENT_YEAR = __import__('datetime').date.today().year

FANDOM_API = "https://lol.fandom.com/api.php"
SESSION_HEADERS = {
    "User-Agent": "LCK-Career-Scraper/1.0 (educational project; contact owner for commercial use)",
}

# All player categories across every region
ALL_PLAYER_CATEGORIES = [
    "List_of_LCK_pro_players",
    "List_of_LPL_pro_players",
    "List_of_LEC_pro_players",
    "List_of_LCS_pro_players",
    "List_of_PCS_pro_players",
    "List_of_VCS_pro_players",
    "List_of_CBLOL_pro_players",
    "List_of_LLA_pro_players",
    "List_of_TCL_pro_players",
    "List_of_LJL_pro_players",
    "List_of_LCO_pro_players",
    "List_of_LFL_pro_players",
    "List_of_NL_Cup_pro_players",
    "List_of_deL_pro_players",
    "List_of_lSol_pro_players",
    "List_of_LCL_pro_players",
    "List_of_WCL_pro_players",
    "List_of_SCL_pro_players",
    "List_of_OPL_pro_players",
    "List_of_ECL_pro_players",
    "List_of_League_of_Legends_pro_athletes",
]

# Search queries for player discovery
SEARCH_QUERIES = [
    "League of Legends professional player",
    "LCK player",
    "LPL player",
    "LEC player",
    "LCS player",
    "PCS player",
    "VCS player",
    "CBLOL player",
    "LLA player",
    "TCL player",
    "LJL player",
    "professional gamer",
    "esports player League of Legends",
    "League of Legends player",
    "pro player League",
    "T1 player",
    "Gen.G player",
    "DRX player",
    "G2 player",
    "Fnatic player",
    "TSM player",
    "Cloud9 player",
    "EDG player",
    "JDG player",
    "RNG player",
    "top laner League of Legends",
    "jungle League of Legends",
    "mid laner League of Legends",
    "adc League of Legends",
    "support League of Legends",
]


# ── MediaWiki API helpers ─────────────────────────────────────────────────

async def fandom_get(session: aiohttp.ClientSession, params: dict, retries: int = 3) -> dict:
    """Execute a MediaWiki API GET request with retry logic."""
    params["format"] = "json"
    params["action"] = "query"
    for attempt in range(retries):
        async with session.get(FANDOM_API, params=params, headers=SESSION_HEADERS) as resp:
            if resp.status == 200:
                try:
                    return await resp.json()
                except aiohttp.ContentTypeError:
                    if attempt < retries - 1:
                        await asyncio.sleep(2 ** attempt)
                        continue
                    raise
            elif resp.status == 502:
                if attempt < retries - 1:
                    wait = 2 ** attempt
                    await asyncio.sleep(wait)
                    continue
                raise aiohttp.ContentTypeError(
                    resp.request_info, resp.history,
                    status=resp.status,
                    message='Attempt %d/3 failed: HTTP %d' % (retries, resp.status),
                )
            else:
                raise aiohttp.ContentTypeError(
                    resp.request_info, resp.history,
                    status=resp.status,
                    message='Unexpected HTTP %d' % resp.status,
                )
    raise RuntimeError("fandom_get retry exhausted without result")


async def search_players(session: aiohttp.ClientSession, query: str) -> list[dict]:
    """Search Fandom for pages matching a query."""
    data = await fandom_get(session, {"list": "search", "srsearch": query, "srlimit": 50, "srfulltext": "1"})
    search_results = data.get("query", {}).get("search", [])
    # Filter out non-player pages
    skip_patterns = ["Season/", "Statistics", "Playoffs", "Rounds", "Groups", "Spring Season",
                     "Summer Season", "Winter Season", "Promotion", "Championship", "All-Star",
                     "Rift Rivals", "MSI", "Worlds", "Mid Season", "Challenger",
                     "roster", "Roster", "transfer", "Transfer"]
    return [r for r in search_results if r.get("title", "") and not any(pat in r.get("title", "") for pat in skip_patterns)]


async def get_page_ids(session: aiohttp.ClientSession, titles: list[str]) -> dict[str, str]:
    """Resolve page titles to page IDs."""
    # Batch titles to avoid URL length limits (~50 per request)
    all_ids = {}
    for i in range(0, len(titles), 50):
        batch = titles[i:i + 50]
        joined = "|".join(batch)
        data = await fandom_get(session, {"titles": joined, "prop": "pageprops"})
        pages = data.get("query", {}).get("pages", {})
        for k, v in pages.items():
            if "missing" not in v:
                all_ids[str(v["title"])] = str(v["pageid"])
        await asyncio.sleep(0.2)
    return all_ids


async def get_parsed_text(session: aiohttp.ClientSession, title: str) -> str:
    """Get rendered page text via action=parse API (includes Team History table).
    Returns empty string on failure (non-200, non-JSON response)."""
    try:
        async with session.get(
            "https://lol.fandom.com/api.php",
            params={"action": "parse", "page": title, "prop": "text", "format": "json"},
            headers=SESSION_HEADERS,
        ) as resp:
            if resp.status != 200:
                return ""
            data = await resp.json()
        parse_data = data.get("parse", {})
        text_data = parse_data.get("text", {})
        if isinstance(text_data, dict):
            return text_data.get("*", "")
        return text_data if isinstance(text_data, str) else ""
    except Exception:
        return ""


async def get_category_members(session: aiohttp.ClientSession, category: str) -> list[str]:
    """Get all pages in a category (handles pagination)."""
    members = []
    cmcontinue = None
    while True:
        params = {
            "list": "categorymembers",
            "cmtitle": "Category:%s" % category,
            "cmlimit": "max",
        }
        if cmcontinue:
            params["cmcontinue"] = cmcontinue
        data = await fandom_get(session, params)
        members.extend(m.get("title", "") for m in data.get("query", {}).get("categorymembers", []))
        cmcontinue = data.get("query-continue", {}).get("categorymembers", {}).get("cmcontinue")
        if not cmcontinue:
            break
        await asyncio.sleep(0.3)
    return members


# ── Career data extraction ────────────────────────────────────────────────

def normalize_team_name(name: str) -> str:
    """Strip wiki markup and extra text from team names."""
    name = re.sub(r"\[+[^\]]+\]+", "", name)
    name = re.sub(r"\|+", "|", name)
    name = name.strip()
    name = re.sub(r"\s*\(\d{4}[–-]\d{4}?\)", "", name).strip()
    return name


ROLE_NORMALIZATION = {
    "bot": "adc",
    "jng": "jungle",
    "sup": "support",
    "m": "mid",
    "t": "top",
}


def normalize_role(role: str) -> str:
    if not role:
        return role
    role_lower = role.lower().strip()
    return ROLE_NORMALIZATION.get(role_lower, role)


MONTH_MAP = {
    'jan': 1, 'feb': 2, 'mar': 3, 'apr': 4, 'may': 5, 'jun': 6,
    'jul': 7, 'aug': 8, 'sep': 9, 'oct': 10, 'nov': 11, 'dec': 12,
}


def parse_month_from_str(date_str: str) -> Optional[int]:
    cleaned = date_str.lstrip('≈')
    m = re.match(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?', cleaned, re.IGNORECASE)
    if m:
        return MONTH_MAP.get(m.group(1).lower()[:3])
    return None


def extract_player_info_from_parse(text: str, page_title: str) -> dict:
    """Extract player name, role, nationality from parsed page text."""
    info = {"real_name": "", "role": "", "nationality": "", "birth_date": ""}

    # Clean text
    clean = html.unescape(text)
    clean = re.sub(r'<[^>]+>', ' ', clean)
    clean = re.sub(r'[\u2060\u200b\u200c\u200d]', '', clean)
    clean = re.sub(r'\s+', ' ', clean).strip()

    # Only use the first paragraph (intro) for role detection
    # This avoids matching section headers like "Top", "Jungle", etc.
    intro_end = clean.find('\n\n')
    if intro_end == -1:
        intro_end = clean.find('\n')
    if intro_end == -1:
        intro_end = 2000
    intro = clean[:intro_end]

    # Nationality detection (search full text)
    nationality_map = {
        "South Korean": "KR", "Korean": "KR", "Korean South": "KR",
        "Chinese": "CN", "Mainland Chinese": "CN", "Taiwanese": "TW",
        "American": "US", "American (United States)": "US", "Canadian": "CA",
        "Swedish": "SE", "Danish": "DK", "French": "FR", "German": "DE",
        "British": "GB", "Spanish": "ES", "Japanese": "JP",
        "Brazilian": "BR", "Australian": "AU", "Thai": "TH",
    }
    for nation, code in nationality_map.items():
        if nation.lower() in clean.lower():
            info["nationality"] = code
            break

    # Role detection - only from intro paragraph
    # Order matters: more specific patterns must come before generic ones
    role_patterns = [
        # In-game roles
        (r'\btop\s+laner', 'top'),
        (r'\bjungle\s+laner', 'jungle'),
        (r'\bjg\s+laner', 'jungle'),
        (r'\bplays?\s+jungle', 'jungle'),
        (r'\bmiddle\s+laner', 'mid'),
        (r'\bmid\s+laner', 'mid'),
        (r'\bplays?\s+mid', 'mid'),
        (r'\bplays?\s+middle', 'mid'),
        (r'\botc\s+laner', 'mid'),
        (r'\bot\s+laner', 'mid'),
        (r'\botc', 'mid'),
        (r'\b(bot\s+laner|plays?\s+bot)', 'adc'),
        (r'\bad\s+laner', 'adc'),
        (r'\bad\s+carries?\b', 'adc'),
        (r'\bplays?\s+adc', 'adc'),
        (r'\bsupport\s+laner', 'support'),
        (r'\bsupport\s+lane', 'support'),
        (r'\bplays?\s+support', 'support'),
        (r'\bsup\s+laner', 'support'),
        (r'\bsupport\s+player', 'support'),
        (r'\badc\s+player', 'adc'),
        # Coaching staff (specific before generic)
        (r'\bhead\s+coach\b', 'head coach'),
        (r'\binterim\s+head\s+coach\b', 'interim head coach'),
        (r'\bassistant\s+coach\b', 'assistant coach'),
        (r'\bstrategic\s+coach\b', 'strategic coach'),
        (r'\bperformance\s+coach\b', 'performance coach'),
        (r'\btwo-?way\s+coach\b', 'two-way coach'),
        (r'\bcoach\b', 'coach'),
        # Analyst roles
        (r'\bhead\s+analyst\b', 'head analyst'),
        (r'\bdata\s+analyst\b', 'data analyst'),
        (r'\binternal\s+analyst\b', 'internal analyst'),
        (r'\bremote\s+analyst\b', 'remote analyst'),
        (r'\bdesk\s+analyst\b', 'desk analyst'),
        (r'\banalyst\b', 'analyst'),
        # Management
        (r'\bgeneral\s+manager\b', 'general manager'),
        (r'\bhead\s+manager\b', 'head manager'),
        (r'\bteam\s+manager\b', 'team manager'),
        (r'\bmanager\b', 'manager'),
        (r'\bco-?owner\b', 'co-owner'),
        (r'\bowner\b', 'owner'),
        (r'\bfounder\b', 'founder'),
        # Broadcast / talent
        (r'\bcolor\s+caster\b', 'color caster'),
        (r'\bcaster\b', 'caster'),
        (r'\bcommentator\b', 'commentator'),
        (r'\bhost\b', 'host'),
        (r'\binterviewer\b', 'interviewer'),
        (r'\bjournalist\b', 'journalist'),
        # Content creation
        (r'\bcontent\s+creator\b', 'content creator'),
        (r'\bco-?streamer\b', 'co-streamer'),
        (r'\bstreamer\b', 'streamer'),
        # Special statuses
        (r'\bsubstitute\b', 'substitute'),
        (r'\btrainee\b', 'trainee'),
        (r'\binactive\b', 'inactive'),
        # Bare-word last-resort patterns (after all more specific patterns)
        (r'\bsupport\b', 'support'),
        (r'\badc\b', 'adc'),
        (r'\bmid\b', 'mid'),
        (r'\bjungle\b', 'jungle'),
        (r'\btop\b', 'top'),
    ]
    for pattern, role in role_patterns:
        if re.search(pattern, intro, re.IGNORECASE):
            info["role"] = role
            break

    # If no role found in intro, try full text (but skip section headers)
    if not info["role"]:
        role_desc = re.search(
            r'((?:top|jungle|mid|adc|support|coach|analyst|manager|streamer)\s+(?:laner|player|position))',
            intro, re.IGNORECASE
        )
        if role_desc:
            role_word = role_desc.group(1).split()[0].lower()
            role_map = {
                'top': 'top', 'jungle': 'jungle', 'mid': 'mid',
                'adc': 'adc', 'support': 'support', 'coach': 'coach',
                'analyst': 'analyst', 'manager': 'manager', 'streamer': 'streamer',
            }
            info["role"] = role_map.get(role_word)

    # Retirement detection (scan full text)
    info["is_retired"] = False
    info["retired_year"] = None
    if re.search(r'\bretired\s+(?:League\s+of\s+Legends\s+)?esports\s+player', clean, re.IGNORECASE):
        info["is_retired"] = True
        yr_match = re.search(r'(?:retired\s+(?:in\s+|since\s+))?(\d{4})', clean)
        if yr_match:
            info["retired_year"] = int(yr_match.group(1))

    return info


# Team name patterns for career extraction, grouped by region
REGION_TEAM_PATTERNS: list[tuple[str, list[str]]] = [
    ("LCK", [
        r'\bT1\b\s?(?:Esports)?', r'\bGen(?:\.G|G)\b', r'\bDRX\b', r'\bDplus\s?(?:Kia)?',
        r'\bHanwha\s?(?:Life)?', r'\bKT\b(?:\s+Rolster)?', r'\bNongshim\s?(?:RedForce|Fresh)?',
        r'\bNS\b(?:\s+RedForce)?', r'\bKDF\b', r'\bOK\b(?:\s+Brave)?',
        r'\bLiiv\s?(?:SANDBOX)?', r'\bBRION\b', r'\bFear(?:X)?(?:\s?(?:Academy|Youth))?',
        r'\bBNK\b(?:\s+FEARX|FearX)?', r'\bDN\b(?:\s+SOOPers)?', r'\bDK\b\s?(?:Challengers)?',
        r'(?:SK\s?Telecom\s?T1|SKT1?)', r'\bKiwoom\s?DRX',
        r'\bHANJIN\s?BRION', r'\bHLE\b',
        r'\bCJ\s?Entus', r'\bLongzhu(?:\s?Gaming)?', r'\bKingzone(?:\s?DragonX)?',
        r'\bJin\s?Air(?:\s?Green\s?Wings)?', r'\bROX\s?(?:Tigers)?', r'\bAfreeca\s?(?:Freecs)?',
        r'\bGriffin\b', r'\bDAMWON(?:\s?Gaming)?', r'\bDWG\b', r'\bSandbox(?:\s?Gaming)?',
        r'\bKSV\s?eSports', r'\bMVP\b', r'\bKwangdong\s?(?:Freecs)?',
    ]),
    ("LPL", [
        r'\b(?:EDG|Edward|Edward Gaming)\b', r'\b(?:BLG|Bilibili|Bilibili Gaming)\b',
        r'\b(?:JDG|Jingdong|JingDong Gaming)\b', r'\b(?:WE|World Elite)\b',
        r'\b(?:IG|Invictus Gaming)\b', r'\b(?:FPX|FunPlus Phoenix)\b',
        r'\b(?:RNG|Royal Never Give Up)\b', r'\b(?:LNG|Luoding)\b', r'\b(?:WBG|Weibo Gaming)\b',
        r'\b(?:TT|Tesla Tech)\b', r'\b(?:AL|Anyone Legend)\b', r'\b(?:UP|Ultra Power)\b',
        r'\b(?:OMG|Oh My God)\b', r'\b(?:RA|Rare Atom)\b', r'\b(?:LGD|Logi Gaming)\b',
        r'\b(?:TES|Top Esports)\b',
    ]),
    ("LEC", [
        r'\b(?:G2|G2 Esports)\b', r'\b(?:FNC|Fnatic)\b', r'\b(?:MAD|Mad Lions)\b', r'\b(?:RGE|Rogue)\b',
        r'\b(?:TH|Team Heretics)\b', r'\b(?:BDS|Team BDS)\b', r'\b(?:AUR|Aurora Esports)\b',
        r'\bSK\s?Gaming\b', r'\b(?:XL|Excel)\b',
        r'\b(?:VIT|Team Vitality)\b', r'\b(?:GIA|GIANTX)\b',
    ]),
    ("LCS", [
        r'\b(?:C9|Cloud9)\b', r'\b(?:TL|Team Liquid)\b', r'\b(?:C100|100 Thieves)\b', r'\b(?:TSM|TSM)\b',
        r'\b(?:EG|Evil Geniuses)\b', r'\b(?:FLY|FlyQuest)\b', r'\b(?:DIG|Dignitas)\b',
        r'\b(?:CLG|Counter Logic Gaming)\b', r'\b(?:IMT|Immortals)\b', r'\b(?:SG|Shopify Gaming)\b',
        r'\b(?:NRG|NRG)\b',
    ]),
    ("PCS", [
        r'\b(?:PSG|Paris Saint-Germain)\b', r'\b(?:TW|Tabe Warriors)\b', r'\b(?:CTB|Crazy Braves)\b',
        r'\b(?:AHQ|AHQ)\b', r'\b(?:FNC|Flash Wolves)\b', r'\b(?:GAM|GIGABYTE Marines)\b',
        r'\b(?:SGB|Sunpayus Gaming)\b', r'\b(?:DFM|Defuse Advanced)\b',
    ]),
    ("VCS", [
        r'\b(?:GAM|GAM Esports)\b', r'\b(?:VIE|Viettel Esports)\b', r'\b(?:TBG|Toan Good Gaming)\b',
        r'\b(?:FLC|FPT Legion)\b', r'\b(?:PNG|Pengo Gaming)\b', r'\b(?:HCL|HCM City Phoenix)\b',
    ]),
    ("CBLOL", [
        r'\b(?:PAW|paiN Gaming)\b', r'\b(?:RED|RedCanaries)\b', r'\b(?:INTZ|INTZ)\b',
    ]),
    ("LLA", [
        r'\b(?:LEV|Leviatán)\b', r'\b(?:ITZ|Isurus)\b', r'\b(?:R7|70percent Esports)\b', r'\b(?:INF|Infamous)\b',
    ]),
    ("TCL", [
        r'\b(?:S07|Samprix)\b', r'\b(?:GTK|GTK)\b', r'\b(?:GSM|GamerStreet)\b',
    ]),
    ("LJL", [
        r'\b(?:DFM|Defuse Advanced)\b', r'\b(?:FLM|Flame)\b', r'\b(?:VP|V3 Esports)\b', r'\b(?:RAM|RAMS)\b',
    ]),
    ("LCO", [
        r'\bChiefs\s?(?:Esports\s?Club)?\b', r'\bPentanet\.?GG\b', r'\bOrder\b',
        r'\bMammoth\b', r'\bDire\s?Wolves\b', r'\bLegacy\s?Esports\b',
        r'\bGround\s?Zero\s?Gaming\b', r'\bGravitas\b', r'\bFury\b',
    ]),
    ("EMEA", [
        r'\bKC\b', r'\bKarmine\b', r'\bXPERION\b', r'\bUnicorns?\s+of\s+Love\b',
        r'\bMovistar\b', r'\bKOI\b', r'\bGiants?\b', r'\bBIG\b', r'\bEintracht\b',
        r'\bSpandau\b', r'\bMOUZ\b', r'\bmousesports\b',
        r'\bAGO\b', r'\bRogue\b', r'\bSplyce\b', r'\bSchalke\b',
        r'\bOrigen\b', r'\bH2K\b', r'\bUoL\b',
    ]),
]

# Derive flat list from region-grouped patterns (preserves original TEAM_PATTERNS behavior)
TEAM_PATTERNS = [pat for _, patterns in REGION_TEAM_PATTERNS for pat in patterns]


CHALLENGER_RE = re.compile(r'(?:Challengers?|Academy|Youth|CL)\b', re.IGNORECASE)
ROOKIES_RE = re.compile(r'(?:Rookies|Scholars)\b', re.IGNORECASE)



def detect_region(team_name: str) -> str:
    """Return region code for a team name based on known patterns.
    Detects challenger/academy teams → ' CL', rookie/scholar teams → ' Rookies'."""
    for region, patterns in REGION_TEAM_PATTERNS:
        for pat in patterns:
            if re.search(pat, team_name, re.IGNORECASE):
                if ROOKIES_RE.search(team_name):
                    return region + ' Rookies'
                if CHALLENGER_RE.search(team_name):
                    return region + ' CL'
                return region
    if ROOKIES_RE.search(team_name):
        return "Unknown Rookies"
    if CHALLENGER_RE.search(team_name):
        return "Unknown CL"
    return "Unknown"


ROLE_MAP = {
    'top laner': 'top', 'jungler': 'jungle', 'mid laner': 'mid',
    'bot laner': 'adc', 'support': 'support',
    'top': 'top', 'jungle': 'jungle', 'mid': 'mid',
    'adc': 'adc', 'bot': 'adc',
}


def extract_career_from_parsed_html(text: str) -> list[dict]:
    """Extract career data from the Team History table in parsed (rendered) HTML.
    Parses the raw HTML table rows directly to get team, role, and dates."""
    careers = []
    seen = set()

    table_pat = re.compile(
        r'<table[^>]*class="[^"]*player-team-history[^"]*"[^>]*>.*?</table>',
        re.DOTALL | re.IGNORECASE,
    )
    tables = table_pat.findall(text)
    if not tables:
        return careers

    # Use the LAST table (detailed one, if multiple)
    table_html = tables[-1]

    row_pat = re.compile(r'<tr>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
    all_rows = row_pat.findall(table_html)

    for row_html in all_rows:
        if '<th' in row_html:
            continue

        # Extract entire <td...>...</td> elements with their attributes
        td_tags = re.findall(r'(<td[^>]*>.*?</td>)', row_html, re.DOTALL)
        if len(td_tags) < 5:
            continue

        # --- Team name (from data-sort-value on the 2nd td) ---
        team_match = re.search(r'data-sort-value="([^"]*)"', td_tags[1], re.IGNORECASE)
        if not team_match:
            continue
        team_name = normalize_team_name(team_match.group(1).strip())

        # --- Role (from title attribute in the 3rd td) ---
        role = None
        role_match = re.search(
            r'<span[^>]*title="([^"]*)"[^>]*class="[^"]*sprite role-sprite[^"]*"',
            td_tags[2], re.DOTALL | re.IGNORECASE,
        )
        if role_match:
            raw = role_match.group(1).strip()
            lower = raw.lower()
            role = ROLE_MAP.get(lower, raw)

        # --- Start date (from toggle spans in the 4th td) ---
        # --- End date (from toggle spans or <i>Present</i> in the 5th td) ---
        start_spans = re.findall(
            r'<span[^>]*class="ofl-toggle-2-1[^"]*"[^>]*>([^<]*)</span>',
            td_tags[3],
        )
        end_spans = re.findall(
            r'<span[^>]*class="ofl-toggle-2-1[^"]*"[^>]*>([^<]*)</span>',
            td_tags[4],
        )

        start_str = start_spans[0].strip() if start_spans else None
        end_str = None
        if end_spans:
            end_str = end_spans[0].strip()
        elif '<i>Present</i>' in td_tags[4]:
            end_str = 'Present'

        if not start_str:
            continue

        # Parse start date: e.g. "≈Apr 2015" or "Dec 2016"
        month_pat = r'≈?(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{4})'
        sm = re.match(month_pat, start_str, re.IGNORECASE)
        if not sm:
            continue
        start_month = MONTH_MAP.get(sm.group(1).lower()[:3])
        start_year = int(sm.group(2))

        end_year = None
        end_month = None
        if end_str and end_str.lower() != 'present':
            em = re.match(month_pat, end_str, re.IGNORECASE)
            if em:
                end_month = MONTH_MAP.get(em.group(1).lower()[:3])
                end_year = int(em.group(2))

        # Dedup identical (team, start_year, start_month) entries
        key = (team_name, start_year, start_month)
        if key in seen:
            continue
        seen.add(key)

        if end_year is not None and end_year < start_year:
            continue

        careers.append({
            "team": team_name,
            "role": role,
            "start_year": start_year,
            "start_month": start_month,
            "end_year": end_year,
            "end_month": end_month,
        })

    return careers


# ── Team renames (REBRANDED_TO) ────────────────────────────────────────────
# Detected by searching fandom for all pages containing
# "Team has renamed" or "Roster has joined a new organization" links,
# then matching old/new names against existing DB teams.

RENAME_LINK_RE = re.compile(
    r'<a\s+href="/wiki/([^"]+)"[^>]*>(?:Team\s+has\s+renamed\.?|Roster\s+has\s+joined\s+a\s+new\s+organization\.?)</a>',
    re.IGNORECASE,
)


def extract_rename_year(text: str, match) -> Optional[int]:
    """Extract the year from text near a rename notice match."""
    start = max(0, match.start() - 300)
    end = min(len(text), match.end() + 300)
    context = text[start:end]
    years = re.findall(r'\b(20[0-9]{2})\b', context)
    if years:
        return int(years[0])
    return None


def clean_wiki_name(raw: str) -> str:
    """Normalize a fandom wiki page title to a clean team name."""
    name = raw.replace('_', ' ')
    for suffix in ['(LEC_Team)', '(LCK_Team)', '(LCS_Team)', '(LPL_Team)',
                   '(LLA_Team)', '(PCS_Team)', '(VCS_Team)', '(CBLOL_Team)',
                   '(LJL_Team)', '(TCL_Team)', '(LCO_Team)', '(LFL_Team)']:
        name = name.replace(suffix, '')
    return name.strip()


async def search_rename_pages(session: aiohttp.ClientSession) -> set[str]:
    """Search fandom for all pages with rename/reorg notices."""
    results = set()
    for query in ['"Team has renamed"', '"Roster has joined a new organization"']:
        params = {
            'action': 'query',
            'list': 'search',
            'srsearch': query,
            'srlimit': 200,
            'format': 'json',
        }
        data = await fandom_get(session, params)
        for r in data.get('query', {}).get('search', []):
            results.add(r['title'])
    return results


async def find_redirects_to(session, team_name: str) -> list[str]:
    """Find fandom pages that redirect to a given team name."""
    params = {
        'action': 'query',
        'prop': 'redirects',
        'titles': team_name,
        'rdlimit': 200,
        'format': 'json',
    }
    data = await fandom_get(session, params)
    pages = data.get('query', {}).get('pages', {})
    for page_id, page_data in pages.items():
        if 'redirects' in page_data:
            return [r['title'] for r in page_data['redirects']]
    return []


async def resolve_redirect_title(session: aiohttp.ClientSession, title: str) -> str:
    """Resolve a fandom page title to its canonical form, following redirects."""
    params = {
        'action': 'query',
        'titles': title,
        'redirects': 1,
        'format': 'json',
    }
    data = await fandom_get(session, params)
    redirects = data.get('query', {}).get('redirects', [])
    if redirects:
        # The last entry in the redirects array is the final target
        return redirects[-1]['to']
    pages = data.get('query', {}).get('pages', {})
    for page_id, page_data in pages.items():
        if 'missing' not in page_data:
            return page_data.get('title', title)
    return title


async def process_rename_page(session, driver, old_title: str, existing: set, semaphore, created_counter: list):
    """Fetch a page, check for rename notice, create REBRANDED_TO if matches DB team.
    Resolves fandom redirects for both old and new names.
    Also handles redirect-only renames (no explicit rename notice on the target page).
    """
    async with semaphore:
        # Resolve redirects to get canonical page title
        resolved_old = await resolve_redirect_title(session, old_title)
        page_to_fetch = resolved_old if resolved_old != old_title else old_title
        text = await get_parsed_text(session, page_to_fetch)
        if not text:
            return
        match = RENAME_LINK_RE.search(text)

        # Try case-insensitive match if exact match fails
        def find_match(name: str) -> Optional[str]:
            if name in existing:
                return name
            name_lower = name.lower().strip()
            for ex in existing:
                if ex.lower().strip() == name_lower:
                    return ex
            return None

        if match:
            new_name = clean_wiki_name(match.group(1))
            # Resolve redirects for the new name too
            resolved_new = await resolve_redirect_title(session, new_name)
            new_name = clean_wiki_name(resolved_new if resolved_new != new_name else new_name)
            rename_year = extract_rename_year(text, match)

            # Use resolved old title for DB matching (page_to_fetch is the canonical form)
            old_for_db = page_to_fetch
            new_for_db = new_name

            old_match = find_match(old_for_db)
            new_match = find_match(new_for_db)

            if not old_match and not new_match:
                return

            # Use the matched DB names if found, otherwise create new team nodes
            old_name = old_match or old_for_db
            new_name_final = new_match or new_for_db

            async with driver.session(database=DB_CONFIG["database"]) as db_session:
                if old_name not in existing:
                    yr = rename_year if rename_year else CURRENT_YEAR
                    await db_session.run(
                        "MERGE (t:Team {name: $name, year: $yr}) SET t.region = 'Unknown'",
                        name=old_name, yr=yr,
                    )
                    existing.add(old_name)
                if new_name_final not in existing:
                    yr = (rename_year + 1) if rename_year else CURRENT_YEAR
                    await db_session.run(
                        "MERGE (t:Team {name: $name, year: $yr}) SET t.region = 'Unknown'",
                        name=new_name_final, yr=yr,
                    )
                    existing.add(new_name_final)
                if rename_year:
                    # Connect old team's last year before rename to new team's first year
                    await db_session.run("""
                        MATCH (old:Team {name: $old_name, year: $yr})
                        MATCH (new:Team {name: $new_name, year: $yr + 1})
                        MERGE (old)-[r:REBRANDED_TO]->(new)
                        SET r.year = $yr
                    """, old_name=old_name, new_name=new_name_final, yr=rename_year)
                else:
                    # Connect max year of old to min year of new
                    await db_session.run("""
                        MATCH (old:Team {name: $old_name})
                        MATCH (new:Team {name: $new_name})
                        WITH old, new ORDER BY old.year DESC, new.year ASC
                        WITH old, new LIMIT 1
                        MERGE (old)-[r:REBRANDED_TO]->(new)
                    """, old_name=old_name, new_name=new_name_final)
                created_counter[0] += 1
                yr_tag = f' ({rename_year})' if rename_year else ''
                # Show if redirect resolution changed the name
                resolved_tag = f' (resolved: {old_for_db})' if old_for_db != old_title else ''
                print(f'    {old_name} -> {new_name_final}{yr_tag}{resolved_tag}')
        elif resolved_old != old_title:
            # No rename notice found, but the candidate page redirects to another page.
            # Treat this as a redirect-based rename if both names exist in DB.
            old_candidate = clean_wiki_name(old_title)
            new_candidate = clean_wiki_name(resolved_old)

            # Skip self-references (e.g. a page redirecting to its own canonical variant)
            if old_candidate == new_candidate:
                return

            old_match = find_match(old_candidate)
            new_match = find_match(new_candidate)

            if not old_match or not new_match:
                return

            old_name = old_match
            new_name_final = new_match

            async with driver.session(database=DB_CONFIG["database"]) as db_session:
                await db_session.run("""
                    MATCH (old:Team {name: $old_name})
                    MATCH (new:Team {name: $new_name})
                    WITH old, new ORDER BY old.year DESC, new.year ASC
                    WITH old, new LIMIT 1
                    MERGE (old)-[r:REBRANDED_TO]->(new)
                """, old_name=old_name, new_name=new_name_final)
                created_counter[0] += 1
                print(f'    {old_name} -> {new_name_final} (redirect)')


async def create_rename_relations(driver, session: aiohttp.ClientSession):
    """Search fandom for rename/reorg notices, match against DB teams,
    and create REBRANDED_TO relationships.
    Uses three approaches:
      1. Search API for pages containing "Team has renamed" / "Roster has joined..."
      2. For each DB team, find redirect pages — these may contain rename notices,
         or may simply redirect (redirect-only renames handled in process_rename_page).
      3. Directly check each existing DB team's page for rename notices.
    """
    # Get existing team names from DB
    async with driver.session(database=DB_CONFIG["database"]) as db_session:
        result = await db_session.run("MATCH (t:Team) RETURN DISTINCT t.name AS name")
        existing = set(r["name"] for r in await result.data())

    # Find candidate pages to check: approach 1 (search API)
    print("  Approach 1: Searching fandom for rename/reorg phrases...")
    candidates = await search_rename_pages(session)
    print(f"    Found {len(candidates)} pages via search")

    # Approach 2: For each DB team, find redirects that might have rename notices
    # (catches redirect pages not indexed by search, e.g. SKT T1 → T1)
    print("  Approach 2: Checking redirects to DB teams...")
    found_via_redirect = 0
    sem_redirect = asyncio.Semaphore(10)
    async def check_redirects(team_name):
        async with sem_redirect:
            redirects = await find_redirects_to(session, team_name)
        local_count = 0
        for r in redirects:
            if r not in candidates:
                candidates.add(r)
                local_count += 1
        return local_count
    results = await asyncio.gather(*[check_redirects(t) for t in sorted(existing)])
    found_via_redirect = sum(results)
    print(f"    Found {found_via_redirect} additional pages via redirects")

    # Approach 3: Add each existing team name directly as a candidate
    # This ensures ALL DB teams are checked for rename notices
    for t in sorted(existing):
        candidates.add(t)
    print(f"  Total candidate pages: {len(candidates)}")

    candidates_sorted = sorted(candidates)
    created_counter = [0]
    sem = asyncio.Semaphore(10)

    # Process all candidates concurrently
    tasks = [
        asyncio.create_task(
            process_rename_page(session, driver, title, existing, sem, created_counter)
        )
        for title in candidates_sorted
    ]
    await asyncio.gather(*tasks)
    print(f"  Created {created_counter[0]} REBRANDED_TO relationships.")


async def create_belongs_to_relations(driver):
    """Create Region nodes and BELONGS_TO relationships for all teams."""
    async with driver.session(database=DB_CONFIG["database"]) as session:
        result = await session.run(
            "MATCH (t:Team) WHERE t.region IS NOT NULL RETURN t.name AS name, t.region AS region"
        )
        teams = await result.data()

    # Group by region for efficient creation
    by_region = {}
    for team in teams:
        by_region.setdefault(team["region"], []).append(team["name"])

    created = 0
    for region_name, team_names in by_region.items():
        async with driver.session(database=DB_CONFIG["database"]) as db_session:
            for team_name in set(team_names):
                result = await db_session.run("""
                    MATCH (t:Team {name: $team_name, year: $cur})
                    MERGE (r:Region {name: $region_name})
                    MERGE (t)-[:BELONGS_TO]->(r)
                    RETURN count(t) AS c
                """, team_name=team_name, region_name=region_name, cur=CURRENT_YEAR)
                row = await result.single()
                if row:
                    created += row['c']

    print(f"  Created/verified {created} BELONGS_TO relationships.")


# ── Neo4j Database helpers ────────────────────────────────────────────────

async def create_indexes(driver: AsyncDriver):
    """Create constraints and indexes in Neo4j."""
    async with driver.session(database=DB_CONFIG["database"]) as session:
        await session.run("""
            CREATE CONSTRAINT player_fandom_id IF NOT EXISTS
            FOR (p:Player) REQUIRE p.fandom_id IS UNIQUE
        """)
        await session.run("""
            CREATE INDEX team_name IF NOT EXISTS
            FOR (t:Team) ON (t.name)
        """)
        await session.run("""
            CREATE INDEX player_name IF NOT EXISTS
            FOR (p:Player) ON (p.name)
        """)


async def upsert_player(driver, player_data: dict):
    """Insert or update a player node."""
    async with driver.session(database=DB_CONFIG["database"]) as session:
        await session.run("""
            MERGE (p:Player {fandom_id: $fandom_id})
            SET p.name = $name,
                p.real_name = $real_name,
                p.role = $role,
                p.nationality = $nationality,
                p.birth_date = $birth_date,
                p.status = $status
        """, {
            "fandom_id": player_data.get("fandom_id"),
            "name": player_data["name"],
            "real_name": player_data.get("real_name"),
            "role": normalize_role(player_data.get("role")),
            "nationality": player_data.get("nationality"),
            "birth_date": player_data.get("birth_date"),
            "status": player_data.get("status", "Active"),
        })


async def upsert_career(driver, player_fandom_id: str, team_name: str, role: Optional[str],
                        start_year: Optional[int], start_month: Optional[int],
                        end_year: Optional[int], end_month: Optional[int],
                        is_current: bool, region: str = "Unknown"):
    """Create or update per-year PLAYED_FOR relationships.
    Creates :Team {name, year} nodes for each year in the stint range,
    links consecutive years with :NEXT_SEASON, and creates a PLAYED_FOR edge
    from the player to each year node with the stint's role.
    Caps the player's range at the team's existing lifespan to avoid
    extending defunct/rebranded teams past their last active year.
    """
    if start_year is None:
        return

    end = end_year if end_year is not None else CURRENT_YEAR

    async with driver.session(database=DB_CONFIG["database"]) as session:
        # Find team's existing year range
        result = await session.run(
            "MATCH (t:Team {name: $name}) RETURN min(t.year) AS min_yr, max(t.year) AS max_yr",
            name=team_name,
        )
        row = await result.single()
        team_min = row['min_yr'] if row else None
        team_max = row['max_yr'] if row else None

        if team_min is None:
            # New team — create year nodes for the full stint range
            await session.run("""
                UNWIND range($sy, $ey) AS yr
                MERGE (t:Team {name: $team, year: yr})
                SET t.region = $region
                WITH t, yr ORDER BY yr
                WITH collect(t) AS nodes
                UNWIND range(0, size(nodes) - 2) AS i
                WITH nodes[i] AS a, nodes[i + 1] AS b
                MERGE (a)-[:NEXT_SEASON]->(b)
            """, team=team_name, region=region, sy=start_year, ey=end)
        else:
            # Existing team — cap player range to team's known lifespan
            end = min(end, team_max)
            if start_year > end:
                return

        # Create per-year PLAYED_FOR edges to existing year nodes
        if start_year <= end:
            await session.run("""
                MATCH (p:Player {fandom_id: $fid})
                UNWIND range($sy, $ey) AS yr
                MATCH (t:Team {name: $team, year: yr})
                MERGE (p)-[r:PLAYED_FOR]->(t)
                SET r.role = $role,
                    r.start_month = CASE
                        WHEN yr = $sy THEN $sm
                        ELSE r.start_month
                    END,
                    r.end_month = CASE
                        WHEN yr = $ey THEN $em
                        ELSE r.end_month
                    END
            """, fid=player_fandom_id, team=team_name, role=normalize_role(role) if role else role,
                 sy=start_year, ey=end, sm=start_month, em=end_month)


# ── Main scraping logic ──────────────────────────────────────────────────

def is_likely_player_page(title: str) -> bool:
    """Check if a page title looks like a player page."""
    skip_patterns = ["Season/", "Statistics", "Playoffs", "Rounds", "Groups",
                     "Spring Season", "Summer Season", "Winter Season",
                     "Promotion", "Championship", "All-Star", "Rift Rivals",
                     "MSI", "Worlds", "Mid Season", "Challenger",
                     "roster", "Roster", "transfer", "Transfer"]
    return not any(pat in title for pat in skip_patterns)


async def gather_all_candidates(session: aiohttp.ClientSession) -> list[str]:
    """Gather all unique player page titles from categories and search."""
    all_pages = set()

    # Step 1: Fetch from all regional player categories
    print("[1/4] Fetching player categories from all regions...")
    for cat in ALL_PLAYER_CATEGORIES:
        print("  - %s ..." % cat, end=" ", flush=True)
        try:
            members = await get_category_members(session, cat)
            player_pages = [m for m in members if is_likely_player_page(m)]
            print("%d/%d pages" % (len(player_pages), len(members)))
            all_pages.update(player_pages)
        except Exception as e:
            print("error: %s" % e)
        await asyncio.sleep(0.3)

    # Step 2: Search for players using multiple queries
    print("\n[2/4] Searching for additional players...")
    for query in SEARCH_QUERIES:
        print('  Searching: "%s" ...' % query, end=" ", flush=True)
        try:
            results = await search_players(session, query)
            titles = [r.get("title", "") for r in results]
            new_titles = [t for t in titles if t not in all_pages]
            print("%d new" % len(new_titles))
            all_pages.update(new_titles)
        except Exception as e:
            print("error: %s" % e)
        await asyncio.sleep(0.5)

    all_pages_list = list(all_pages)
    print("\n  Total unique player pages discovered: %d" % len(all_pages_list))
    return all_pages_list


async def process_career_page(driver, session: aiohttp.ClientSession, player_name: str, fandom_id: str, existing_role: str):
    """Fetch and process a single player page."""
    try:
        parsed = await get_parsed_text(session, player_name)
        if not parsed:
            return player_name, 0, None

        # Extract player info from parse text
        player_info = extract_player_info_from_parse(parsed, player_name)
        is_retired = player_info.get("is_retired", False)
        retired_year = player_info.get("retired_year")

        # Extract careers with per-stint roles
        careers = extract_career_from_parsed_html(parsed)

        # Compute player-level role from the most common competitive role in careers
        competitive_roles = [c.get("role") for c in careers if c.get("role") and c["role"].lower() not in ("streamer", "caster", "analyst", "coach", "manager", "host", "owner", "founder", "content creator", "journalist", "interviewer", "commentator")]
        if competitive_roles:
            player_role = max(set(competitive_roles), key=competitive_roles.count)
        else:
            player_role = player_info.get("role") or existing_role or "Unknown"

        # Determine player status
        has_current = any(c.get("end_year") is None for c in careers)
        if is_retired or not has_current:
            status = "Retired"
        else:
            status = "Active"

        # If retired but the last career has no end_year, apply retirement year
        if is_retired and careers:
            last_career = careers[-1]
            if last_career.get("end_year") is None:
                last_career["end_year"] = retired_year

        # Save/update player
        await upsert_player(driver, {
            "fandom_id": fandom_id,
            "name": player_name,
            "real_name": player_info.get("real_name"),
            "role": normalize_role(player_role),
            "nationality": player_info.get("nationality"),
            "birth_date": player_info.get("birth_date"),
            "status": status,
        })

        # Save careers with per-stint roles
        for c in careers:
            is_current = c.get("end_year") is None
            region = detect_region(c["team"])
            stint_role = c.get("role") or player_role
            await upsert_career(
                driver, fandom_id, c["team"], stint_role,
                c["start_year"], c.get("start_month"),
                c["end_year"], c.get("end_month"),
                is_current, region,
            )

        return player_name, len(careers), player_role
    except Exception as e:
        return player_name, -1, str(e)


async def process_players_careers(driver, session: aiohttp.ClientSession, players: list[dict], max_workers: int = 8):
    """Process careers for all players with concurrent workers."""
    print("[3/5] Processing careers...")

    semaphore = asyncio.Semaphore(max_workers)
    success = 0
    failed = 0
    no_career = 0

    async def worker(player):
        async with semaphore:
            return await process_career_page(
                driver, session,
                player["name"], player["fandom_id"],
                player.get("role"),
            )

    for i in range(0, len(players), 20):
        batch = players[i:i + 20]
        tasks = [asyncio.create_task(worker(p)) for p in batch]
        results = await asyncio.gather(*tasks)

        for name, result, extra in results:
            if result > 0:
                success += 1
            elif result == 0:
                no_career += 1
            else:
                failed += 1

        print("  [%d/%d] Success: %d, No career: %d, Failed: %d" % (
            i + len(batch), len(players), success, no_career, failed))
        await asyncio.sleep(0.5)





# ── Current roster scraping ───────────────────────────────────────────────

ROSTER_MONTH_MAP = {
    'Jan': 1, 'Feb': 2, 'Mar': 3, 'Apr': 4, 'May': 5, 'Jun': 6,
    'Jul': 7, 'Aug': 8, 'Sep': 9, 'Oct': 10, 'Nov': 11, 'Dec': 12,
}

ROSTER_ROLE_MAP = {
    'top laner': 'top', 'jungler': 'jungle', 'mid laner': 'mid',
    'bot laner': 'adc', 'support': 'support',
    'sub/top': 'top', 'sub/jungle': 'jungle', 'sub/mid': 'mid',
    'sub/bot': 'adc', 'sub/support': 'support', 'sub': 'sub',
    'coach': 'coach', 'head coach': 'head coach',
    'assistant coach': 'assistant coach',
    'analyst': 'analyst',
}


async def scrape_current_rosters(driver, session: aiohttp.ClientSession):
    """Scrape current rosters for all active teams from their fandom pages.
    Parses the 'Active' roster table and creates/updates Player + PLAYED_FOR.
    """
    async with driver.session(database=DB_CONFIG['database']) as neo_session:
        teams = await neo_session.run("""
            MATCH (t:Team)
            WITH t.name AS name, max(t.year) AS last_year
            WHERE last_year >= $cur - 1
              AND NOT EXISTS((t)-[:REBRANDED_TO]->())
            RETURN name
            ORDER BY name
        """, cur=CURRENT_YEAR)
        team_names = [r['name'] for r in await teams.data()]

    if not team_names:
        print('  No active teams found.')
        return

    created_players = 0
    created_rosters = 0
    no_roster = 0
    failed = 0

    for i, team_name in enumerate(team_names):
        print(f'  [{i+1}/{len(team_names)}] {team_name}...', end=' ', flush=True)
        page_title = team_name.replace(' ', '_')
        try:
            text = await asyncio.wait_for(
                get_parsed_text(session, page_title), timeout=15,
            )
            if not text:
                print('no page')
                no_roster += 1
                continue
        except Exception:
            print('fail')
            failed += 1
            continue

        # Find the Active roster table (class="team-members-current")
        idx = text.find('team-members-current')
        if idx < 0:
            print('no roster table')
            no_roster += 1
            continue

        table_start = text.rfind('<table', 0, idx)
        table_end = text.find('</table>', idx)
        if table_start < 0 or table_end < 0:
            print('no table bounds')
            no_roster += 1
            continue
        table_html = text[table_start:table_end + len('</table>')]

        row_pat = re.compile(r'<tr>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
        rows = row_pat.findall(table_html)
        roster_count = 0

        for row_html in rows:
            if '<th' in row_html:
                continue

            # Player name (display text inside <a>)
            player_match = re.search(
                r'<a[^>]*href="/wiki/([^"]+)"[^>]*>([^<]+)</a>', row_html,
            )
            if not player_match:
                continue
            player_page_title = player_match.group(1).replace('_', ' ')
            player_name = player_match.group(2).strip()

            # Real name from the "Name" column (2nd <td>)
            tds = re.findall(r'<td[^>]*>(.*?)</td>', row_html, re.DOTALL)
            real_name = ''
            if len(tds) >= 2:
                real_name = re.sub(r'<[^>]+>', '', tds[1]).strip()

            # Role from markup-object-name span
            role = None
            role_match = re.search(
                r'<span class="markup-object-name">([^<]+)</span>', row_html,
            )
            if role_match:
                raw = role_match.group(1).strip().lower()
                role = ROSTER_ROLE_MAP.get(raw, raw)

            # Joined date (last ofl-toggle-1-1 span in the row)
            date_spans = re.findall(
                r'<span class="ofl-toggle-1-1[^"]*"[^>]*>([^<]*)</span>', row_html,
            )
            joined_str = date_spans[-1].strip() if date_spans else ''

            joined_year = None
            joined_month = None
            if joined_str:
                dm = re.match(r'(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\.?\s+(\d{4})', joined_str)
                if dm:
                    joined_month = ROSTER_MONTH_MAP.get(dm.group(1))
                    joined_year = int(dm.group(2))

            # Upsert player — resolve fandom_id with caching
            fandom_id = await _resolve_fandom_id(session, player_page_title)
            if not fandom_id:
                async with driver.session(database=DB_CONFIG['database']) as neo_session:
                    existing = await neo_session.run(
                        "MATCH (p:Player {name: $name}) RETURN p.fandom_id AS fid LIMIT 1",
                        name=player_name,
                    )
                    row = await existing.single()
                    if row:
                        fandom_id = row['fid']
                    else:
                        # skip silently — no fandom page and no existing player
                        continue

            async with driver.session(database=DB_CONFIG['database']) as neo_session:
                await neo_session.run("""
                    MERGE (p:Player {fandom_id: $fid})
                    SET p.name = $name,
                        p.real_name = COALESCE(p.real_name, $real_name),
                        p.status = 'Active'
                """, fid=fandom_id, name=player_name, real_name=real_name or None)
                created_players += 1

                # Create current-year node and PLAYED_FOR edge
                await neo_session.run("""
                    MATCH (p:Player {fandom_id: $fid})
                    MERGE (t:Team {name: $team, year: $cur})
                    SET t.region = $region
                    MERGE (p)-[r:PLAYED_FOR]->(t)
                    SET r.start_month = $sm,
                        r.role = $role
                """, fid=fandom_id, team=team_name, cur=CURRENT_YEAR,
                     region=detect_region(team_name),
                     sm=joined_month, role=role)
                roster_count += 1

        if roster_count > 0:
            created_rosters += 1
            print(f'{roster_count} players')
        else:
            print('empty')

    print(f'  Rosters for {created_rosters}/{len(team_names)} teams, {created_players} players (no roster: {no_roster}, failed: {failed}).')


async def _resolve_fandom_id(session: aiohttp.ClientSession, title: str) -> Optional[str]:
    """Resolve a fandom page title to its numeric page ID."""
    try:
        data = await fandom_get(session, {'titles': title, 'prop': 'pageprops'})
        pages = data.get('query', {}).get('pages', {})
        for k, v in pages.items():
            if 'missing' not in v:
                return str(v['pageid'])
    except Exception:
        pass
    return None


async def scrape_and_save(driver, limit: int = None):
    """Main scraping entry point."""
    async with aiohttp.ClientSession() as session:
        # Gather all candidate player pages
        all_candidates = await gather_all_candidates(session)

        if limit:
            all_candidates = all_candidates[:limit]

        print("\nResolving page IDs...")
        page_ids = await get_page_ids(session, all_candidates)
        print("Resolved %d/%d pages" % (len(page_ids), len(all_candidates)))

        # Build player list
        players = []
        for title, page_id in page_ids.items():
            players.append({"fandom_id": page_id, "name": title, "role": None})

        if players:
            await process_players_careers(driver, session, players)

        # Step 5: Scrape current rosters for all active teams
        # (catches players missed by category/search approach)
        print('\n[4/6] Scraping current rosters for active teams...')
        await scrape_current_rosters(driver, session)

        # Step 5: Create REBRANDED_TO relationships for team renames
        print("\n[5/6] Creating team rename relations...")
        await create_rename_relations(driver, session)

        # Step 6: Create BELONGS_TO relationships linking teams to regions
        print("\n[6/6] Creating BELONGS_TO relations...")
        await create_belongs_to_relations(driver)


async def main():
    """Entry point."""
    print("=" * 60)
    print("  Pro Player Career Data Extractor (All Regions)")
    print("=" * 60)

    print("\nConnecting to Neo4j...")
    driver = AsyncGraphDatabase.driver(
        DB_CONFIG["uri"],
        auth=(DB_CONFIG["user"], DB_CONFIG["password"]),
    )
    try:
        await driver.verify_connectivity()
        print("Connected to Neo4j.\n")

        await create_indexes(driver)
        print("Indexes/constraints ready.\n")

        await scrape_and_save(driver, limit=None)

        print("\nDone! Data saved to Neo4j.")
    finally:
        await driver.close()


if __name__ == "__main__":
    asyncio.run(main())
