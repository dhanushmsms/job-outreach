"""
Scraper — Apify-powered, targets contract + sponsorship roles.
Sources: Apify (LinkedIn Jobs, LinkedIn Posts, Indeed) + GitHub API + HN Hiring
Country split across 3 free Apify accounts (500 contacts/month each).
"""

import logging
import re
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass
class Contact:
    name: str
    email: Optional[str]
    company: str
    title: str
    country: str
    linkedin_url: Optional[str] = None
    source: str = "unknown"
    role_type: str = ""  # "contract", "sponsorship", "permanent", ""
    job_title: str = ""  # the role being advertised / searched for


# ── Country config ─────────────────────────────────────────────────────────────

COUNTRY_INDEED_CODE = {
    "United Kingdom": "GB",
    "Ireland":        "IE",
    "Dubai":          "AE",
    "New Zealand":    "NZ",
    "India":          "IN",
    "Europe":         "DE",
    "Germany":        "DE",
}

COUNTRY_LINKEDIN_SEARCH = {
    "United Kingdom": "United Kingdom",
    "Ireland":        "Ireland",
    "Dubai":          "Dubai, United Arab Emirates",
    "New Zealand":    "New Zealand",
    "India":          "India",
    "Europe":         "Europe",
}

COUNTRY_LINKEDIN_GEO = {
    "United Kingdom": "101165590",
    "Ireland":        "104738515",
    "Dubai":          "106204383",
    "New Zealand":    "105490917",
    "India":          "102713980",
    "Europe":         "100506914",
}

# Preferred sub-locations searched first; remainder of country fills up to max_results
# Format: {country: [(location_label, geo_id_or_None), ...]}
# India → Bangalore ONLY (no country-wide fallback — too noisy)
# UK → Belfast first, then whole UK
# Other countries → use single location (city or country)
COUNTRY_PREFERRED_LOCATIONS = {
    "India": [
        ("Bangalore, Karnataka, India", "105556813"),
        # NO India-wide fallback — Bangalore only
    ],
    "United Kingdom": [
        ("Belfast, Northern Ireland, United Kingdom", "104869965"),
        ("United Kingdom", "101165590"),   # UK-wide fallback
    ],
    "Europe": [
        ("Ireland", "104738515"),
        ("Switzerland", "106693272"),
        ("Netherlands", "102890719"),
        ("Germany", "101282230"),
        ("Europe", "100506914"),
    ],
}

# Job titles that are clearly irrelevant — contacts with these roles get filtered out
IRRELEVANT_JOB_KEYWORDS = [
    "store assistant", "shop assistant", "retail assistant", "sales assistant",
    "warehouse", "delivery driver", "driver", "cleaner", "cleaning",
    "chef", "cook", "kitchen", "barista", "bartender", "waiter", "waitress",
    "cashier", "checkout", "security guard", "security officer",
    "care assistant", "carer", "care worker", "support worker",
    "teaching assistant", "nursery", "childcare",
    "plumber", "electrician", "carpenter", "builder", "labourer",
    "housekeeper", "hotel", "hospitality staff",
    "packer", "picker", "forklift",
]

CONTRACT_KEYWORDS = ["contract", "contractor", "freelance", "interim", "fixed-term", "outside ir35"]
SPONSORSHIP_KEYWORDS = [
    "visa sponsorship", "sponsor", "skilled worker", "work permit",
    "relocation", "tier 2", "global talent", "sponsorship available",
]

# Apify actor IDs (verified from Apify store)
ACTOR_LINKEDIN_JOBS  = "hKByXkMQaC5Qt9UMN"   # curious_coder/linkedin-jobs-scraper
ACTOR_LINKEDIN_POSTS = "buIWk2uOUzTmcLsuB"   # harvestapi/linkedin-post-search
ACTOR_INDEED         = "hMvNSpz3JnHgl5jkh"   # misceres/indeed-scraper

APIFY_BASE = "https://api.apify.com/v2"


# ── Apify helpers ──────────────────────────────────────────────────────────────

def _apify_run(actor_id: str, token: str, input_data: dict, timeout_secs: int = 120) -> list[dict]:
    """Run an Apify actor synchronously and return the dataset items."""
    try:
        # Start the run
        resp = requests.post(
            f"{APIFY_BASE}/acts/{actor_id}/run-sync-get-dataset-items",
            params={"token": token, "timeout": timeout_secs, "memory": 256},
            json=input_data,
            timeout=timeout_secs + 30,
        )
        if resp.status_code == 402:
            logger.warning(f"Apify credit limit reached for actor {actor_id}")
            return []
        if resp.status_code not in (200, 201):
            logger.warning(f"Apify {actor_id} returned {resp.status_code}: {resp.text[:200]}")
            return []
        data = resp.json()
        return data if isinstance(data, list) else data.get("items", [])
    except Exception as e:
        logger.error(f"Apify run failed [{actor_id}]: {e}")
        return []


def _get_apify_token(apify_keys: list[dict], country: str) -> Optional[str]:
    """Return the Apify token assigned to this country."""
    for key in apify_keys:
        if country in key.get("countries", []):
            return key["token"]
    # Fallback — use first key
    return apify_keys[0]["token"] if apify_keys else None


def _detect_role_type(text: str) -> str:
    text = text.lower()
    if any(k in text for k in CONTRACT_KEYWORDS):
        return "contract"
    if any(k in text for k in SPONSORSHIP_KEYWORDS):
        return "sponsorship"
    return ""


def _extract_all_emails(text: str) -> list[str]:
    """Extract all valid emails from text, de-duped and cleaned."""
    if not text:
        return []
    blocked = ["noreply", "no-reply", "example", ".png", ".jpg", "linkedin", "apify",
               "indeed", "sentry", "wix", "cloudflare", "mailchimp", "bounce"]
    found = []
    seen = set()
    for m in re.finditer(r'[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}', text):
        email = m.group(0).strip(".,;:\"'")
        el = email.lower()
        if el in seen:
            continue
        if any(x in el for x in blocked):
            continue
        if "@" not in email or "." not in email.split("@")[-1]:
            continue
        seen.add(el)
        found.append(email)
    return found


def _best_email(emails: list[str]) -> Optional[str]:
    """Pick the most useful email: personal/specific beats generic inboxes."""
    if not emails:
        return None
    generic_prefixes = ["info", "hello", "contact", "enquiries", "enquiry",
                        "admin", "support", "hr", "recruitment", "careers",
                        "jobs", "hiring", "talent", "apply", "team", "office"]
    personal, generic = [], []
    for e in emails:
        local = e.split("@")[0].lower()
        if any(local == p or local.startswith(p + ".") for p in generic_prefixes):
            generic.append(e)
        else:
            personal.append(e)
    return (personal + generic)[0]


def _extract_email(text: str) -> Optional[str]:
    """Pull the best email from any text blob (personal > generic, no blocked)."""
    return _best_email(_extract_all_emails(text))


def _is_relevant_role(title: str) -> bool:
    """Return False if the job title is clearly unrelated to data/ops/analytics."""
    if not title:
        return True  # no title → don't filter
    tl = title.lower()
    return not any(kw in tl for kw in IRRELEVANT_JOB_KEYWORDS)


# ── LinkedIn Jobs (Apify) ──────────────────────────────────────────────────────

def scrape_linkedin_jobs(
    country: str,
    roles: list[str],
    token: str,
    needs_sponsorship: bool = False,
    max_results: int = 50,
) -> list[Contact]:
    """Scrape LinkedIn job postings — returns hiring manager name + company.
    Searches preferred sub-locations first (e.g. Bangalore for India,
    Belfast for UK, Ireland/Switzerland/Netherlands for Europe).
    """
    contacts = []
    seen_companies: set[str] = set()

    # Build ordered list of (location_label, geo_id) to search
    preferred = COUNTRY_PREFERRED_LOCATIONS.get(country)
    if preferred:
        location_passes = preferred
    else:
        location_passes = [(COUNTRY_LINKEDIN_SEARCH.get(country, country),
                            COUNTRY_LINKEDIN_GEO.get(country))]

    for role in roles[:3]:
        if len(contacts) >= max_results:
            break
        query = role + (" visa sponsorship" if needs_sponsorship else " hiring manager")

        for loc_label, _geo_id in location_passes:
            if len(contacts) >= max_results:
                break
            search_url = (
                f"https://www.linkedin.com/jobs/search/"
                f"?keywords={requests.utils.quote(query)}"
                f"&location={requests.utils.quote(loc_label)}"
                f"&f_JT=C&f_TPR=r604800"
            )
            items = _apify_run(ACTOR_LINKEDIN_JOBS, token, {
                "urls":  [search_url],
                "count": 25,
            })
            for item in items:
                if len(contacts) >= max_results:
                    break
                desc = str(item.get("descriptionText") or item.get("descriptionHtml") or "")
                job_position = str(item.get("title") or role)
                poster_title = str(item.get("jobPosterTitle") or "Hiring Manager")
                # Filter out irrelevant job postings
                if not _is_relevant_role(job_position):
                    continue
                # Extract best email from all text sources
                all_text = " ".join([poster_title, desc,
                                     str(item.get("companyWebsite") or ""),
                                     str(item.get("externalApplyLink") or "")])
                email = _extract_email(all_text)
                company = (item.get("companyName") or "").strip()
                poster = item.get("jobPosterName") or ""
                linkedin_url = item.get("jobPosterProfileUrl") or item.get("link") or ""
                if not company:
                    continue
                key = f"{poster.lower()}|{company.lower()}"
                if key in seen_companies:
                    continue
                seen_companies.add(key)
                contacts.append(Contact(
                    name=poster,
                    email=email,
                    company=company,
                    title=poster_title,
                    country=country,
                    linkedin_url=linkedin_url,
                    source="linkedin_jobs",
                    role_type=_detect_role_type(desc),
                    job_title=job_position,
                ))
            logger.info(f"[{country}] LinkedIn Jobs '{role}' @ {loc_label}: {len(items)} results")
            time.sleep(1)

    return contacts[:max_results]


# ── LinkedIn Posts — "#hiring" posts (Apify) ──────────────────────────────────

def scrape_linkedin_posts(
    country: str,
    roles: list[str],
    token: str,
    needs_sponsorship: bool = False,
    max_results: int = 30,
    linkedin_cookie: str = None,
) -> list[Contact]:
    """
    Scrape LinkedIn posts where hiring managers announce openings.
    These people ARE the decision makers — highest reply rate.
    li_at cookie significantly increases results — add to settings.yaml.
    """
    contacts = []
    seen_keys: set[str] = set()
    roles_str = " OR ".join(roles[:3])
    sponsorship_tag = "sponsorship" if needs_sponsorship else ""

    # Build recruiter-focused queries targeting people who are hiring
    preferred = COUNTRY_PREFERRED_LOCATIONS.get(country)
    city = preferred[0][0].split(",")[0] if preferred else country

    # Use city for India (Bangalore only), country for others
    location_tag = city if country == "India" else country

    queries = [
        f"#hiring {roles_str} {location_tag}",
        f"we are hiring {roles_str} {location_tag}",
        f"looking to hire {roles_str} {location_tag}",
        f"recruiter {roles_str} {location_tag} {sponsorship_tag}".strip(),
        f"agency {roles_str} {location_tag} {sponsorship_tag}".strip(),
    ]
    # Remove duplicate/empty queries
    queries = list(dict.fromkeys(q.strip() for q in queries if q.strip()))[:4]

    actor_input = {
        "searchQueries": queries,
        "maxResults":    max_results,
    }
    if linkedin_cookie:
        actor_input["cookie"] = linkedin_cookie

    items = _apify_run(ACTOR_LINKEDIN_POSTS, token, actor_input)

    for item in items:
        text = str(item.get("text") or item.get("content") or "")
        author_raw = item.get("author") or {}
        if isinstance(author_raw, dict):
            author = author_raw.get("name") or ""
        else:
            author = str(author_raw or "")
        author = author or item.get("authorName") or ""
        company = item.get("authorCompany") or item.get("company") or ""
        title = item.get("authorTitle") or item.get("title") or "Hiring Manager"

        # Filter out posts from clearly irrelevant people
        if not _is_relevant_role(str(title)) and not _is_relevant_role(text[:200]):
            continue

        # Extract best email from the post text
        email = _extract_email(text)

        linkedin_url = item.get("url") or item.get("postUrl") or ""

        if not author and not company:
            continue

        key = f"{str(author).lower()}|{str(company).lower()}"
        if key in seen_keys:
            continue
        seen_keys.add(key)

        contacts.append(Contact(
            name=str(author),
            email=email,
            company=str(company),
            title=str(title),
            country=country,
            linkedin_url=linkedin_url,
            source="linkedin_posts",
            role_type=_detect_role_type(text),
            job_title=roles_str,
        ))

    logger.info(f"[{country}] LinkedIn Posts: {len(contacts)} results")
    return contacts[:max_results]


# ── Indeed (Apify) ─────────────────────────────────────────────────────────────

def scrape_indeed(
    country: str,
    roles: list[str],
    token: str,
    needs_sponsorship: bool = False,
    max_results: int = 50,
) -> list[Contact]:
    """Scrape Indeed job postings — sometimes includes recruiter email."""
    country_code = COUNTRY_INDEED_CODE.get(country, "com")
    contacts = []

    # Preferred location for Indeed searches
    preferred = COUNTRY_PREFERRED_LOCATIONS.get(country)
    preferred_location = preferred[0][0] if preferred else None
    default_location = country if country != "Dubai" else "Dubai, UAE"

    for role in roles[:3]:
        if len(contacts) >= max_results:
            break
        query = role + (" visa sponsorship" if needs_sponsorship else "")

        # India: Bangalore only, no country fallback
        if country == "India":
            locations_to_try = [preferred_location] if preferred_location else [default_location]
        else:
            locations_to_try = ([preferred_location, default_location]
                                if preferred_location and preferred_location != default_location
                                else [default_location])

        for location in locations_to_try:
            if len(contacts) >= max_results:
                break
            items = _apify_run(ACTOR_INDEED, token, {
                "keyword":    query,
                "location":   location,
                "country":    country_code,
                "maxItems":   25,
                "startUrls":  [],
                "maxAge":     7,
            })
            for item in items:
                if len(contacts) >= max_results:
                    break
                job_position = str(item.get("positionName") or role)
                # Filter irrelevant job types
                if not _is_relevant_role(job_position):
                    continue
                desc = str(item.get("description") or item.get("descriptionHTML") or "")
                # Extract best email from all available fields
                all_text = " ".join([desc,
                                     str(item.get("externalApplyLink") or ""),
                                     str(item.get("applyEmail") or "")])
                email = _extract_email(all_text)
                company = (item.get("company") or "").strip()
                if not company:
                    continue
                contacts.append(Contact(
                    name=item.get("companyInfo", {}).get("name", "") if isinstance(item.get("companyInfo"), dict) else "",
                    email=email,
                    company=company,
                    title=job_position,
                    country=country,
                    linkedin_url=item.get("url") or item.get("companyIndeedUrl"),
                    source="indeed",
                    role_type=_detect_role_type(desc),
                    job_title=job_position,
                ))
            logger.info(f"[{country}] Indeed '{role}' @ {location}: {len(items)} results")
            time.sleep(0.5)

    return contacts[:max_results]


# ── GitHub API — free, no key ──────────────────────────────────────────────────

def scrape_github_eng_managers(
    roles: list[str],
    country: str,
    max_results: int = 15,
) -> list[Contact]:
    """GitHub user search — tech hiring managers with public emails."""
    search_terms = ["engineering manager hiring", "data team hiring", "analytics hiring"]
    contacts = []

    for term in search_terms:
        if len(contacts) >= max_results:
            break
        try:
            resp = requests.get(
                "https://api.github.com/search/users",
                params={"q": f"{term} location:{country}", "per_page": 8},
                headers={"Accept": "application/vnd.github.v3+json"},
                timeout=10,
            )
            if resp.status_code == 403:
                break
            for user in resp.json().get("items", []):
                try:
                    d = requests.get(
                        user["url"], timeout=5,
                        headers={"Accept": "application/vnd.github.v3+json"}
                    ).json()
                    company = (d.get("company") or "").strip().lstrip("@")
                    if company and d.get("email"):
                        contacts.append(Contact(
                            name=d.get("name") or d.get("login", ""),
                            email=d.get("email"),
                            company=company,
                            title=(d.get("bio") or "Engineering Manager")[:60],
                            country=country,
                            source="github",
                        ))
                    time.sleep(0.3)
                except Exception:
                    continue
            time.sleep(1)
        except Exception as e:
            logger.warning(f"GitHub search failed: {e}")

    return contacts[:max_results]


# ── Hacker News Hiring thread — free ──────────────────────────────────────────

def scrape_hacker_news_hiring(
    roles: list[str],
    country: str,
    needs_sponsorship: bool = False,
    max_results: int = 20,
) -> list[Contact]:
    """HN 'Who is Hiring?' thread — tech companies with real emails in posts."""
    keywords = [r.lower() for r in roles]
    sponsorship_kw = ["visa", "sponsor", "relocation", "remote"] if needs_sponsorship else ["contract", "remote"]

    try:
        resp = requests.get(
            "https://hn.algolia.com/api/v1/search?query=who+is+hiring&tags=story&hitsPerPage=1",
            timeout=10,
        )
        hits = resp.json().get("hits", [])
        if not hits:
            return []

        story = requests.get(
            f"https://hacker-news.firebaseio.com/v0/item/{hits[0]['objectID']}.json",
            timeout=10,
        ).json()
        contacts = []

        for cid in (story.get("kids") or [])[:80]:
            if len(contacts) >= max_results:
                break
            try:
                c = requests.get(
                    f"https://hacker-news.firebaseio.com/v0/item/{cid}.json",
                    timeout=5,
                ).json()
                text = (c.get("text") or "").lower()
                if not any(k in text for k in keywords):
                    continue
                if not any(k in text for k in sponsorship_kw):
                    continue
                email = _extract_email(c.get("text") or "")
                company_match = re.search(r'\|([^|]+)\|', c.get("text") or "")
                company = company_match.group(1).strip() if company_match else "Unknown"
                contacts.append(Contact(
                    name=c.get("by", ""),
                    email=email,
                    company=company,
                    title="Hiring Manager",
                    country=country,
                    source="hacker_news",
                ))
            except Exception:
                continue

    except Exception as e:
        logger.warning(f"HN scrape failed: {e}")
        return []

    return contacts


# ── Dedup ──────────────────────────────────────────────────────────────────────

def deduplicate(contacts: list[Contact]) -> list[Contact]:
    seen = set()
    result = []
    for c in contacts:
        key = c.email.lower() if c.email else f"{c.name.lower()}|{c.company.lower()}"
        if key and key not in seen:
            seen.add(key)
            result.append(c)
    return result


# ── Main entry point ───────────────────────────────────────────────────────────

def scrape_all(
    country: str,
    target_roles: list[str],
    anthropic_api_keys=None,       # kept for API compatibility, not used
    adzuna_keys: list[dict] = None, # kept for API compatibility, not used
    apify_keys: list[dict] = None,
    career_page_urls: list[str] = None,
    needs_sponsorship: bool = False,
    max_results: int = 150,
    scrape_model: str = None,       # kept for API compatibility, not used
    linkedin_cookie: str = None,
) -> list[Contact]:
    """
    Main scraper — Apify (LinkedIn Jobs + Posts + Indeed) + GitHub + HN.
    Apify token auto-selected by country assignment in settings.yaml.
    """
    all_contacts: list[Contact] = []
    token = _get_apify_token(apify_keys or [], country)

    if not token:
        logger.error("No Apify token found — add apify_keys to settings.yaml")
        return []

    # 1. LinkedIn Jobs — hiring manager names + companies
    if len(all_contacts) < max_results:
        li_jobs = scrape_linkedin_jobs(country, target_roles, token, needs_sponsorship,
                                       max_results=50)
        all_contacts.extend(li_jobs)
        logger.info(f"[{country}] LinkedIn Jobs: {len(li_jobs)}")

    # 2. LinkedIn Posts — "#hiring" posts, highest reply rate
    if len(all_contacts) < max_results:
        li_posts = scrape_linkedin_posts(country, target_roles, token, needs_sponsorship,
                                         max_results=30, linkedin_cookie=linkedin_cookie)
        all_contacts.extend(li_posts)
        logger.info(f"[{country}] LinkedIn Posts: {len(li_posts)}")

    # 3. Indeed — job postings, sometimes includes recruiter email
    if len(all_contacts) < max_results:
        indeed = scrape_indeed(country, target_roles, token, needs_sponsorship,
                               max_results=50)
        all_contacts.extend(indeed)
        logger.info(f"[{country}] Indeed: {len(indeed)}")

    # 4. GitHub — free, real emails for tech roles
    if len(all_contacts) < max_results:
        gh = scrape_github_eng_managers(target_roles, country, max_results=15)
        all_contacts.extend(gh)
        logger.info(f"[{country}] GitHub: {len(gh)}")

    # 5. HN Hiring thread — free, real emails
    if len(all_contacts) < max_results:
        hn = scrape_hacker_news_hiring(target_roles, country, needs_sponsorship, max_results=20)
        all_contacts.extend(hn)
        logger.info(f"[{country}] HN: {len(hn)}")

    result = deduplicate(all_contacts)
    logger.info(f"[{country}] Final deduped: {len(result)}")
    return result[:max_results]
