import re
import os
from datetime import datetime, date, timedelta
from utils.logger import get_logger
from parsers.llm_extractor import extract_employment_records_via_llm, extract_candidate_profile_via_llm

logger = get_logger()

# Regex Constants
EMAIL_REGEX = re.compile(r"[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+")
PHONE_REGEX = re.compile(r"(\+?\d{1,3}[-.\s]?)?\(?\d{3}\)?[-.\s]?\d{3}[-.\s]?\d{4}|\b\d{10,12}\b")

# Local-parts that signal an email belongs to a journal/editor/helpdesk rather than the candidate
EMAIL_NOISE_LOCAL_PARTS = (
    "noreply", "no-reply", "admin", "info", "support", "editor", "editorial",
    "webmaster", "postmaster", "helpdesk", "submissions", "contact", "office"
)

# Context that signals a "10-12 digit number" is actually an ID/patent/ISBN, not a phone number
PHONE_NOISE_CONTEXT_REGEX = re.compile(
    r"\b(patent|application\s*no|isbn|issn|doi|orcid|roll\s*no|id\s*no|reg(?:istration)?\s*no|pin\s*code|zip\s*code)\b",
    re.IGNORECASE
)

# Word-bounded Degree Patterns (expanded for parenthesis and tables)
PHD_PATTERNS = [
    r"\bph\.?d\.?\b",
    r"\bdoctor\s+of\s+philosophy\b",
    r"\bd\.phil\b",
    r"\bphd\b"
]
PG_PATTERNS = [
    r"\bm\.?tech\b",
    r"\bm\.?e\.?\b",
    r"\bm\.?sc\b",
    r"\bm\.?c\.?a\b",
    r"\bm\.?b\.?a\b",
    r"\bm\.?s\.?\b",
    r"\bpost\s*graduate\b",
    r"\bmaster\s+of\s+[a-zA-Z ]{3,20}\b",
    r"\bm\.?phil\b"
]
UG_PATTERNS = [
    r"\bb\.?tech\b",
    r"\bb\.?e\.?\b",
    r"\bb\.?sc\b",
    r"\bb\.?c\.?a\b",
    r"\bb\.?com\b",
    r"\bb\.?a\b",
    r"\bgraduate\b",
    r"\bbachelor\s+of\s+[a-zA-Z ]{3,20}\b"
]

# Academic designations for teaching & leadership experience (legacy fallback fallback)
TEACHING_TITLES = [
    r"\bsr\.?\s*assistant\s+professor\b",
    r"\bassistant\s+professor\b",
    r"\bassociate\s+professor\b",
    r"\bsenior\s+lecturer\b",
    r"\blecturer\b",
    r"\bprofessor\b",
    r"\bhead\s+of\s+(?:the\s+)?department\b",
    r"\bhod\b",
    r"\bdean\b",
    r"\bprincipal\b",
    r"\bresearch\s+scientist\b",
    r"\bacademics?\s+coordinator\b"
]

def clean_and_validate_candidate_name(name_str):
    """
    Sanitizes, formats and validates name candidates.
    Returns cleaned string if valid, else None.
    """
    if not name_str:
        return None
        
    cleaned = re.sub(r"^(?:dr\.?|prof\.?|mr\.?|mrs\.?|ms\.?)\s+", "", name_str, flags=re.IGNORECASE)
    cleaned = re.sub(r"^[\s\-\*•\d\.\,\(\)]+", "", cleaned)
    cleaned = re.sub(r"[\s\-\*•\d\.\,\(\)]+$", "", cleaned)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    
    cleaned = re.sub(r"\b([A-Za-z])\.(?=[A-Za-z])", r"\1. ", cleaned)
    
    # NOTE: This list intentionally contains only GENERIC words (institution types,
    # resume-section words, contact labels). It must never contain specific institution
    # names (e.g. a particular university/college) - hardcoding those only makes the
    # filter pass for whichever sample resumes you tested against, and it silently lets
    # every OTHER institution's name through untouched. If a real institution name keeps
    # slipping through, add a generic pattern (e.g. "always ends in University/Institute"),
    # not the literal name.
    exclude_keywords = [
        "department", "dept", "university", "college", "institute", "institution", "school", "academy",
        "engineering", "technology", "science", "sciences", "humanities", "mathematics",
        "professor", "lecturer", "dean", "principal", "director", "coordinator", "curriculum",
        "vitae", "resume", "biodata", "objective", "experience", "education", "publications",
        "projects", "patents", "guidance", "ratified", "ratification", "syllabus",
        "subject", "taught", "designation", "email", "phone", "mobile", "address", "contact",
        "h-index", "citation", "orcid", "researcher", "member", "organizer", "convenor",
        "work", "history", "academics", "foundation", "trust", "board", "council", "society",
        "limited", "private", "ltd", "pvt", "inc", "corp", "campus"
    ]
    cleaned_lower = cleaned.lower()
    for kw in exclude_keywords:
        if re.search(r"\b" + re.escape(kw) + r"\b", cleaned_lower):
            return None
            
    words = cleaned.split()
    if not (1 <= len(words) <= 4):
        return None
        
    for w in words:
        w_clean = w.replace(".", "").replace("-", "")
        if not w_clean.isalpha():
            return None
            
    return cleaned

def extract_name_from_filename(filename):
    """
    Extracts a fallback name from a resume filename.
    """
    if not filename:
        return "Unknown"
    base, _ = os.path.splitext(filename)
    base = re.sub(r"[\-_\+]+", " ", base)
    base = re.sub(r"\b(cv|resume|biodata|academic|profile|faculty|evaluation|evaluator|updated|latest|draft)\b", "", base, flags=re.IGNORECASE)
    base = re.sub(r"\s+", " ", base).strip()
    
    cleaned = clean_and_validate_candidate_name(base)
    if cleaned:
        return cleaned
    return "Unknown"

def select_best_email(raw_text, personal_info_text=""):
    """
    Scans ALL email matches in the document and scores them, instead of
    blindly trusting whichever one appears first. Prefers matches inside
    the personal-info header block and penalizes editor/admin-style
    addresses that commonly show up in publication citations.
    """
    matches = list(EMAIL_REGEX.finditer(raw_text))
    if not matches:
        return None

    personal_info_len = len(personal_info_text)

    def score(m):
        text = m.group(0).strip()
        local_part = text.split("@")[0].lower()
        s = 0
        if any(local_part == noise or local_part.startswith(noise) for noise in EMAIL_NOISE_LOCAL_PARTS):
            s -= 100
        if m.start() < personal_info_len:
            s += 50
        s += max(0, 20 - (m.start() // 200))  # mild preference for earlier-in-document
        return s

    best = max(matches, key=score)
    return best.group(0).strip()


def select_best_phone(raw_text, personal_info_text=""):
    """
    Scans ALL phone-shaped matches and scores them, instead of trusting
    the first 10-12 digit run found anywhere in the document (which is
    just as likely to be a patent application number, ISBN, or roll number).
    """
    matches = list(PHONE_REGEX.finditer(raw_text))
    if not matches:
        return None

    personal_info_len = len(personal_info_text)

    def score(m):
        text = m.group(0)
        start = m.start()
        context_window = raw_text[max(0, start - 30):start + len(text) + 10]
        s = 0
        if PHONE_NOISE_CONTEXT_REGEX.search(context_window):
            s -= 100
        if start < personal_info_len:
            s += 50
        digits_only = re.sub(r"\D", "", text)
        if len(digits_only) == 10 and digits_only[0] in "6789":
            s += 20  # shape of a valid Indian mobile number
        s += max(0, 20 - (start // 200))
        return s

    best = max(matches, key=score)
    return best.group(0).strip()


def _looks_like_place_or_heading(line_clean):
    """
    Extra guard against picking a city/place/banner line as a name.
    Generic org-suffix words are already filtered in clean_and_validate_candidate_name;
    this catches short single-word lines that are plausible-looking but not personal names
    (e.g. a city name sitting above the actual name line on the resume header).
    """
    words = line_clean.split()
    if len(words) == 1 and len(words[0]) > 2:
        # A single bare word with no title-case signal of a multi-token personal name
        # is far more likely to be a place/city/banner than "Firstname Lastname".
        return True
    return False


def _score_name_candidate(cleaned, line_idx):
    """
    Scores a name candidate instead of accepting the first line that merely
    survives the exclude-keyword filter. Prefers 2-3 word, Title-Case lines
    near the top of the document over single bare words.
    """
    words = cleaned.split()
    score = 0
    if 2 <= len(words) <= 3:
        score += 15
    elif len(words) == 1:
        score -= 10  # single-word lines are too often a place, not a full name
    if all(w[0].isupper() for w in words if w):
        score += 10
    score += max(0, 10 - line_idx)  # earlier lines still weighted, but no longer a hard "first wins"
    return score


def extract_personal_info(sections, potential_name=None, raw_text="", filename="", llm_profile=None):
    """
    Extracts name, email, and phone number from the resume.

    Priority order - each tier is only used if the one above it is missing
    or fails to ground against the actual document text:
      1. LLM holistic read (sees the whole doc, can use context regex can't)
      2. Layout analysis (PDF largest-font-on-page-1, born-digital PDFs only)
      3. Regex/heuristic scoring over candidate lines and matches
      4. Filename fallback (name only)
    """
    name_source = "None"
    llm_profile = llm_profile or {}

    personal_info_text = sections.get("personal_info", "")

    # --- Email ---
    email = None
    llm_email = llm_profile.get("email")
    if llm_email and _email_is_grounded(llm_email, raw_text):
        email = llm_email.strip()
    if not email:
        email = select_best_email(raw_text, personal_info_text)

    # --- Phone ---
    phone = None
    llm_phone = llm_profile.get("phone")
    if llm_phone and _phone_is_grounded(llm_phone, raw_text):
        phone = str(llm_phone).strip()
    if not phone:
        phone = select_best_phone(raw_text, personal_info_text)

    # --- Name ---
    name = "Unknown"
    llm_name = llm_profile.get("name")
    cleaned_llm_name = clean_and_validate_candidate_name(llm_name) if llm_name else None
    if cleaned_llm_name and _name_is_grounded(cleaned_llm_name, raw_text):
        name = cleaned_llm_name
        name_source = "llm_holistic"

    if name == "Unknown" and potential_name:
        cleaned_potential = clean_and_validate_candidate_name(potential_name)
        if cleaned_potential:
            name = cleaned_potential
            name_source = "layout_analysis"
            
    if name == "Unknown":
        intro_text = sections.get("personal_info", "")
        lines = [l.strip() for l in intro_text.split("\n") if l.strip()]
        filter_pattern = re.compile(r"@|\+?\d{8,}|\b(email|phone|mobile|tel|address|curriculum|vitae|resume|page|contact|http|www|profile|nationality|gender|dob|summary)\b", re.IGNORECASE)
        
        name_candidates = []
        for idx, line in enumerate(lines[:15]):
            if filter_pattern.search(line):
                continue
            cleaned = clean_and_validate_candidate_name(line)
            if cleaned and not _looks_like_place_or_heading(cleaned):
                name_candidates.append((cleaned, idx))

        if name_candidates:
            best_cleaned, _ = max(name_candidates, key=lambda c: _score_name_candidate(c[0], c[1]))
            name = best_cleaned
            name_source = "text_heuristics"
                
    if name == "Unknown" and filename:
        name_from_file = extract_name_from_filename(filename)
        if name_from_file and name_from_file != "Unknown":
            name = name_from_file
            name_source = "filename_fallback"
            
    return {
        "name": name,
        "email": email,
        "phone": phone,
        "name_source": name_source
    }

def extract_highest_qualification(sections, raw_text=""):
    """
    Checks the education section and overall text for degrees.
    Returns the highest qualification.
    """
    edu_text = (sections.get("education", "") + "\n" + sections.get("personal_info", "")).lower()
    
    def matches_any(patterns, text):
        return any(re.search(p, text) for p in patterns)
        
    if matches_any(PHD_PATTERNS, edu_text):
        return "PhD"
    elif matches_any(PG_PATTERNS, edu_text):
        return "Post Graduate"
    elif matches_any(UG_PATTERNS, edu_text):
        return "Graduate"
    
    raw_text_lower = raw_text.lower()
    if matches_any(PHD_PATTERNS, raw_text_lower):
        return "PhD"
    elif matches_any(PG_PATTERNS, raw_text_lower):
        return "Post Graduate"
    elif matches_any(UG_PATTERNS, raw_text_lower):
        return "Graduate"
        
    return "None"

# ====================================================
# EXPERIENCE EXTRACTION TIMELINE ENGINE
# ====================================================

def get_month_num(m_name):
    months = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
    for idx, name in enumerate(months):
        if name in m_name:
            return idx + 1
    return 1

def normalize_date_to_ymd(date_str):
    """
    Step 2 - Validate Dates. Convert date strings to YYYY-MM-DD format.
    """
    if not date_str:
        return None
    date_clean = date_str.strip().lower()
    
    # Check ongoing keywords
    if any(w in date_clean for w in ["present", "current", "till date", "onwards", "now", "tilldate"]):
        return date.today().strftime("%Y-%m-%d")
        
    # Remove leading/trailing symbols
    date_clean = re.sub(r"^[\(\[\s\-\,]+|[\)\]\s\-\,]+$", "", date_clean)
    
    months_pattern = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    
    # 1. Month Day, Year (e.g. "February 01, 2018")
    match_long = re.search(rf"\b{months_pattern}\s+(\d{{1,2}})(?:st|nd|rd|th)?[,\s]+(\d{{4}})\b", date_clean)
    if match_long:
        m_name, d_str, y_str = match_long.group(1), match_long.group(2), match_long.group(3)
        month = get_month_num(m_name)
        return f"{int(y_str):04d}-{month:02d}-{int(d_str):02d}"
        
    # 2. Day Month Year (e.g. "01 July 2014")
    match_long_reverse = re.search(rf"\b(\d{{1,2}})(?:st|nd|rd|th)?\s+{months_pattern}\s+(\d{{4}})\b", date_clean)
    if match_long_reverse:
        d_str, m_name, y_str = match_long_reverse.group(1), match_long_reverse.group(2), match_long_reverse.group(3)
        month = get_month_num(m_name)
        return f"{int(y_str):04d}-{month:02d}-{int(d_str):02d}"
        
    # 3. Month Year (e.g. "July 2014")
    match_month_year = re.search(rf"\b{months_pattern}\s+(\d{{4}})\b", date_clean)
    if match_month_year:
        m_name, y_str = match_month_year.group(1), match_month_year.group(2)
        month = get_month_num(m_name)
        return f"{int(y_str):04d}-{month:02d}-01"
        
    # 4. MM/YYYY or MM-YYYY (e.g. "03/2010")
    match_mm_yyyy = re.search(r"\b(\d{1,2})[/\-](\d{4})\b", date_clean)
    if match_mm_yyyy:
        m_str, y_str = match_mm_yyyy.group(1), match_mm_yyyy.group(2)
        return f"{int(y_str):04d}-{int(m_str):02d}-01"
        
    # 5. YYYY-MM-DD or YYYY/MM/DD
    match_iso = re.search(r"\b(\d{4})[/\-](\d{1,2})[/\-](\d{1,2})\b", date_clean)
    if match_iso:
        y_str, m_str, d_str = match_iso.group(1), match_iso.group(2), match_iso.group(3)
        return f"{int(y_str):04d}-{int(m_str):02d}-{int(d_str):02d}"
        
    # 6. YYYY-MM
    match_iso_my = re.search(r"\b(\d{4})[/\-](\d{1,2})\b", date_clean)
    if match_iso_my:
        y_str, m_str = match_iso_my.group(1), match_iso_my.group(2)
        return f"{int(y_str):04d}-{int(m_str):02d}-01"
        
    # 7. Year-only
    match_year = re.search(r"\b((?:19|20)\d{2})\b", date_clean)
    if match_year:
        y_str = match_year.group(1)
        return f"{int(y_str):04d}-01-01"
        
    return None

def classify_designation(designation):
    """
    Step 5 - Experience Classification.
    """
    des_lower = designation.lower()
    
    # Administrative checks
    if any(w in des_lower for w in ["dean", "principal", "hod", "head of", "director", "chairperson"]):
        return "Administrative"
        
    # Academic checks
    if any(w in des_lower for w in ["teaching assistant", "lecturer", "professor", "academic coordinator", "academics coordinator", "instructor"]):
        return "Academic Teaching"
        
    # Research checks
    if any(w in des_lower for w in ["scientist", "postdoctoral", "fellow", "researcher", "research associate", "research assistant"]):
        return "Research"
        
    # Industry checks
    if any(w in des_lower for w in ["engineer", "consultant", "analyst", "developer", "programmer", "manager", "lead", "specialist"]):
        return "Industry"
        
    return "Industry"

# Words that show up on essentially every academic CV/publication list regardless
# of whether a SPECIFIC claimed value is real - matching on these alone is not
# evidence of anything. Without filtering these out, a fabricated paper titled
# "Totally Fabricated Study" in a "Nonexistent Journal" would get marked grounded
# just because the word "Journal" appears somewhere else in a real citation.
_GENERIC_GROUNDING_STOPWORDS = {
    "university", "universities", "college", "colleges", "institute", "institutes",
    "institution", "institutions", "school", "schools", "academy", "academies",
    "journal", "journals", "conference", "conferences", "proceedings", "symposium",
    "international", "national", "technology", "technologies", "engineering",
    "science", "sciences", "research", "education", "studies", "department",
    "transactions", "letters", "review", "press", "publishing", "publication",
    "publications", "applied", "advanced", "society", "association", "academic"
}


def _value_grounded_in_text(value, raw_text, min_word_len=2, min_word_ratio=0.6):
    """
    Generic grounding check shared by the name/email/phone/organization/education/
    publication validators: confirms a value the LLM claims to have read off the
    page actually has support in the source document, rather than trusting it
    just because it's well-formatted JSON. Uses word-boundary matching (not raw
    substring) and ignores generic academic filler words that would trivially
    "match" on any CV regardless of whether the specific claim is real.
    """
    if not value:
        return False
    value_clean = str(value).strip()
    if not value_clean:
        return False
    if value_clean.lower() in raw_text.lower():
        return True
    raw_lower = raw_text.lower()
    all_words = [w for w in re.findall(r"[a-zA-Z]+", value_clean) if len(w) >= min_word_len]
    significant_words = [w for w in all_words if w.lower() not in _GENERIC_GROUNDING_STOPWORDS]
    words_to_check = significant_words if significant_words else all_words
    if not words_to_check:
        return False
    hits = sum(1 for w in words_to_check if re.search(r"\b" + re.escape(w.lower()) + r"\b", raw_lower))
    return hits >= max(1, int(round(len(words_to_check) * min_word_ratio)))


def _organization_is_grounded(org, raw_text):
    """
    Sanity-checks an LLM-returned organization name against the actual resume text.
    The employment LLM call has no built-in verification - it can hallucinate an
    organization, or grab one from the Education section instead of the real employer.
    This doesn't try to be clever; it just confirms the org name (or most of its
    significant words) actually appears somewhere in the document before we trust it.
    """
    if not org:
        return False
    org_clean = org.strip().lower()
    if org_clean in ("", "unknown", "n/a", "none", "not specified"):
        return False
    return _value_grounded_in_text(org, raw_text, min_word_len=4, min_word_ratio=0.5)


def _name_is_grounded(name, raw_text):
    """Confirms an LLM-claimed candidate name actually has support in the document."""
    if not name:
        return False
    return _value_grounded_in_text(name, raw_text, min_word_len=2, min_word_ratio=0.6)


def _email_is_grounded(email, raw_text):
    """Confirms an LLM-claimed email is the literal email present in the document."""
    if not email or "@" not in email:
        return False
    return email.strip().lower() in raw_text.lower()


def _phone_is_grounded(phone, raw_text):
    """
    Confirms an LLM-claimed phone number's digits actually appear (contiguously,
    formatting aside) in the document - compares digit-streams so "+91 98765 43210"
    and "919876543210" still match.
    """
    if not phone:
        return False
    digits = re.sub(r"\D", "", str(phone))
    if len(digits) < 7:
        return False
    raw_digits = re.sub(r"\D", "", raw_text)
    return digits in raw_digits


# ====================================================
# EDUCATION INSTITUTION RESOLUTION (LLM + grounding)
# ====================================================

EDUCATION_LEVEL_ALIASES = {
    "UG": ("ug", "undergraduate", "graduate", "bachelor", "bachelors"),
    "PG": ("pg", "postgraduate", "post graduate", "post-graduate", "master", "masters"),
    "PHD": ("phd", "ph.d", "doctoral", "doctorate")
}


def _normalize_education_level(level_str):
    if not level_str:
        return None
    level_clean = level_str.strip().lower()
    for canonical, aliases in EDUCATION_LEVEL_ALIASES.items():
        if level_clean in aliases:
            return canonical
    return None


def resolve_education_institutions(education_records, raw_text):
    """
    Turns the LLM's raw education_records list into a clean {UG, PG, PhD} -> institution
    mapping, dropping any record whose institution name isn't actually grounded in the
    resume text (i.e. don't trust a hallucinated college name just because it's well-formed JSON).
    If the same level appears more than once, keeps the first grounded one.
    """
    resolved = {"UG": None, "PG": None, "PHD": None}
    for rec in education_records or []:
        if not isinstance(rec, dict):
            continue
        level = _normalize_education_level(rec.get("level"))
        institution = (rec.get("institution") or "").strip()
        if not level or not institution:
            continue
        if resolved[level]:
            continue  # already have a grounded one for this level
        if _value_grounded_in_text(institution, raw_text, min_word_len=4, min_word_ratio=0.5):
            resolved[level] = institution
        else:
            logger.warning(f"Education institution '{institution}' ({level}) not grounded in resume text - dropping.")
    return {
        "ug_institution": resolved["UG"] or "Not specified",
        "pg_institution": resolved["PG"] or "Not specified",
        "phd_institution": resolved["PHD"] or "Not specified"
    }


# ====================================================
# PUBLICATION DETAIL RESOLUTION (LLM + grounding)
# ====================================================

def resolve_publication_records(publication_records, raw_text):
    """
    Validates each LLM-extracted publication record against the resume text.
    A record is only kept as-is if its title or journal name has real support
    in the document; otherwise it's flagged rather than silently trusted, since
    a publication's title/journal is exactly the kind of detail an LLM can
    smooth over or invent when a citation is messy.
    """
    resolved = []
    for rec in publication_records or []:
        if not isinstance(rec, dict):
            continue
        title = (rec.get("title") or "").strip()
        journal_name = (rec.get("journal_name") or "").strip()
        if not title and not journal_name:
            continue

        title_grounded = _value_grounded_in_text(title, raw_text, min_word_len=4, min_word_ratio=0.6) if title else False
        journal_grounded = _value_grounded_in_text(journal_name, raw_text, min_word_len=4, min_word_ratio=0.7) if journal_name else False
        verified = title_grounded or journal_grounded

        if not verified:
            logger.warning(f"Publication record '{title or journal_name}' not grounded in resume text - flagging as unverified.")

        resolved.append({
            "title": title if title_grounded else (f"Unverified: {title}" if title else "Not specified"),
            "journal_name": journal_name if journal_grounded else (f"Unverified: {journal_name}" if journal_name else ""),
            "published_under": (rec.get("published_under") or "").strip(),
            "year": str(rec.get("year")).strip() if rec.get("year") else "",
            "impact_factor": str(rec.get("impact_factor")).strip() if rec.get("impact_factor") else "",
            "scopus_indexed": rec.get("scopus_indexed") if rec.get("scopus_indexed") in ("Yes", "No") else "",
            "verified": verified
        })
    return resolved


# ====================================================
# FDP / STTP (Faculty Development & Short-Term Training Programmes)
# ====================================================
# NOTE: "FDP/STTP" replaces what used to be a generic "Extra-Curricular Activities"
# column on the interview marking sheet. For a FACULTY (not student) evaluation,
# what matters is training/development programmes attended, not clubs/sports.
# Best-guess interpretation of a garbled client request - confirm the exact label
# with the client; this is a one-line rename if they meant something else.

FDP_STTP_PATTERNS = [
    re.compile(r"\bfdp\b", re.IGNORECASE),
    re.compile(r"\bfaculty\s+development\s+(?:program|programme)s?\b", re.IGNORECASE),
    re.compile(r"\bsttp\b", re.IGNORECASE),
    re.compile(r"\bshort\s*term\s+training\s+(?:program|programme)s?\b", re.IGNORECASE),
    re.compile(r"\brefresher\s+course\b", re.IGNORECASE),
    re.compile(r"\b(?:workshops?|seminars?)\s+attended\b", re.IGNORECASE),
    re.compile(r"\battended\s+(?:\d+\s+)?(?:workshops?|seminars?)\b", re.IGNORECASE),
]


def extract_fdp_sttp_count(sections, raw_text=""):
    """
    Counts FDP/STTP-style entries (faculty development programmes, short-term
    training programmes, refresher courses, workshops/seminars attended).
    Regex-based, matching the style of extract_extra_curriculars - this is a
    simple keyword/line count, not a structured list, since these are usually
    listed as one-line bullet items with wildly inconsistent formatting.
    """
    target_text = sections.get("certifications", "") + "\n" + sections.get("extra_curricular", "")
    if not target_text.strip():
        target_text = raw_text

    count = 0
    matched_lines = []
    for line in target_text.split("\n"):
        line_clean = line.strip()
        if not line_clean:
            continue
        if any(p.search(line_clean) for p in FDP_STTP_PATTERNS):
            count += 1
            matched_lines.append(line_clean)

    return {"fdp_sttp_count": count, "fdp_sttp_items": matched_lines}


def calculate_experience_metrics(positions_raw, raw_text=""):
    """
    Timeline engine: parses date ranges, merges overlaps, groups by categories
    and returns experience year metrics.
    """
    positions = []
    
    total_intervals = []
    academic_intervals = []
    research_intervals = []
    admin_intervals = []
    industry_intervals = []
    
    for pos in positions_raw:
        designation = pos.get("designation", "").strip()
        org = pos.get("organization", "").strip()
        if org and org.lower() not in ("unknown", "n/a", "none", "not specified") and raw_text and not _organization_is_grounded(org, raw_text):
            logger.warning(f"Organization '{org}' for '{designation}' not found in resume text - marking unverified instead of trusting it blindly.")
            org = f"Unverified: {org}"
        start_date_str = pos.get("start_date", "").strip()
        end_date_str = pos.get("end_date", "").strip()
        
        # Step 2: Validate dates
        start_ymd = normalize_date_to_ymd(start_date_str)
        end_ymd = normalize_date_to_ymd(end_date_str)
        
        if not start_ymd or not end_ymd:
            logger.warning(f"Unparseable dates for: {designation} ({start_date_str} to {end_date_str})")
            continue
            
        try:
            start_dt = datetime.strptime(start_ymd, "%Y-%m-%d").date()
            end_dt = datetime.strptime(end_ymd, "%Y-%m-%d").date()
        except Exception as e:
            logger.error(f"Error converting to dates: {e}")
            continue
            
        if end_dt < start_dt:
            logger.warning(f"End date before start date: {designation} ({start_ymd} to {end_ymd})")
            continue
            
        # Step 5: Classify
        classification = classify_designation(designation)
        duration_years = round((end_dt - start_dt).days / 365.25, 1)
        
        positions.append({
            "designation": designation,
            "organization": org,
            "start_date": start_ymd,
            "end_date": end_ymd,
            "timeline_start": start_ymd[:7],  # Step 3: YYYY-MM
            "timeline_end": end_ymd[:7],
            "duration_years": duration_years,
            "classification": classification
        })
        
        interval = {"start": start_dt, "end": end_dt}
        total_intervals.append(interval)
        
        if classification == "Academic Teaching":
            academic_intervals.append(interval)
        elif classification == "Research":
            research_intervals.append(interval)
        elif classification == "Administrative":
            admin_intervals.append(interval)
        elif classification == "Industry":
            industry_intervals.append(interval)
            
    # Step 4: Interval merging logic
    def merge_intervals(intervals_list):
        if not intervals_list:
            return []
        sorted_list = sorted(intervals_list, key=lambda x: x["start"])
        merged = []
        for current in sorted_list:
            if not merged:
                merged.append(dict(current))
            else:
                prev = merged[-1]
                # Merge contiguous (within 31 days) or overlapping
                if current["start"] <= prev["end"] + timedelta(days=31):
                    prev["end"] = max(prev["end"], current["end"])
                else:
                    merged.append(dict(current))
        return merged
        
    merged_total = merge_intervals(total_intervals)
    merged_academic = merge_intervals(academic_intervals)
    merged_research = merge_intervals(research_intervals)
    merged_admin = merge_intervals(admin_intervals)
    merged_industry = merge_intervals(industry_intervals)
    
    def sum_durations_years(merged_list):
        total_days = sum((item["end"] - item["start"]).days for item in merged_list)
        return total_days / 365.25
        
    total_val = sum_durations_years(merged_total)
    academic_val = sum_durations_years(merged_academic)
    research_val = sum_durations_years(merged_research)
    admin_val = sum_durations_years(merged_admin)
    industry_val = sum_durations_years(merged_industry)
    
    # Sort positions chronologically
    positions.sort(key=lambda x: x["start_date"])
    
    return {
        "positions_detected": len(positions),
        "total_professional_experience_years": int(total_val),
        "academic_experience_years": int(academic_val),
        "research_experience_years": int(research_val),
        "administrative_experience_years": int(admin_val),
        "industry_experience_years": int(industry_val),
        "positions": positions
    }

# ====================================================
# LEGACY FALLBACK PARSERS (For offline/non-LLM use)
# ====================================================

def parse_single_date_legacy(date_str):
    date_str = date_str.strip().lower()
    if not date_str:
        return None
    
    current_year = datetime.now().year
    current_month = datetime.now().month
    
    if date_str in ["present", "current", "till date", "onwards", "now"]:
        return current_year + ((current_month - 1) / 12.0)
        
    dd_mm_yyyy = re.match(r"(\d{1,2})[/\-](\d{1,2})[/\-](\d{4})", date_str)
    if dd_mm_yyyy:
        m, y = int(dd_mm_yyyy.group(2)), int(dd_mm_yyyy.group(3))
        return y + ((m - 1) / 12.0)
        
    mm_yyyy = re.match(r"(\d{1,2})[/\-](\d{4})", date_str)
    if mm_yyyy:
        m, y = int(mm_yyyy.group(1)), int(mm_yyyy.group(2))
        return y + ((m - 1) / 12.0)
        
    year_match = re.search(r"\b(19|20)\d{2}\b", date_str)
    if not year_match:
        return None
    year = int(year_match.group(0))
    
    month_val = 5.5
    for idx, m_name in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]):
        if m_name in date_str:
            month_val = float(idx)
            break
    return year + (month_val / 12.0)

def format_float_date(val, default_present=False):
    current_year = datetime.now().year
    current_month = datetime.now().month
    present_val = current_year + ((current_month - 1) / 12.0)
    
    if default_present and abs(val - present_val) < 0.05:
        return "Present"
        
    year = int(val)
    month = int(round((val - year) * 12)) + 1
    if month < 1:
        month = 1
    if month > 12:
        month = 12
    return f"{year:04d}-{month:02d}-01"

def expand_year_ranges(line):
    pattern = re.compile(r"\b((?:19|20)\d{2})[-–—\s/]+(\d{2})\b")
    def replace_match(match):
        year1_str = match.group(1)
        year2_str = match.group(2)
        year1 = int(year1_str)
        century = year1 // 100
        year2 = century * 100 + int(year2_str)
        if year2 < year1:
            year2 = (century + 1) * 100 + int(year2_str)
        return f"{year1_str} to {year2}"
    return pattern.sub(replace_match, line)

def find_dates_on_line(line):
    months_pat = r"(?:jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|jun(?:e)?|jul(?:y)?|aug(?:ust)?|sep(?:tember)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"
    day_pat = r"\b\d{1,2}(?:st|nd|rd|th)?\b"
    year_pat = r"\b(?:19|20)\d{2}\b"
    
    p1 = rf"\b{months_pat}\s+{day_pat}\s*,?\s*{year_pat}"
    p2 = rf"\b{day_pat}\s+{months_pat}\s*,?\s*{year_pat}"
    p3 = rf"\b{months_pat}\s+{year_pat}"
    p4 = rf"\b\d{{1,2}}[/\-]\d{{4}}\b"
    p5 = rf"\b\d{{1,2}}[/\-]\d{{1,2}}[/\-]\d{{4}}\b"
    p6 = year_pat
    
    DATE_REGEX = re.compile(f"({p5}|{p4}|{p1}|{p2}|{p3}|{p6})", re.IGNORECASE)
    PRESENT_REGEX = re.compile(r"\b(present|current|till\s+date|onwards|now)\b", re.IGNORECASE)
    
    line = re.sub(r"\b((?:19|20)\d{2})to\b", r"\1 to", line, flags=re.IGNORECASE)
    line = re.sub(r"\b((?:19|20)\d{2})to([a-zA-Z])", r"\1 to \2", line, flags=re.IGNORECASE)
    line = re.sub(r"\b((?:19|20)\d{2})([-–—])([a-zA-Z])", r"\1 \2 \3", line, flags=re.IGNORECASE)
    line = expand_year_ranges(line)
    
    matches = []
    for m in DATE_REGEX.finditer(line):
        matches.append({
            "type": "date",
            "text": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "val": parse_single_date_legacy(m.group(0))
        })
    for m in PRESENT_REGEX.finditer(line):
        matches.append({
            "type": "present",
            "text": m.group(0),
            "start": m.start(),
            "end": m.end(),
            "val": datetime.now().year + ((datetime.now().month - 1) / 12.0)
        })
        
    matches.sort(key=lambda x: x["start"])
    
    filtered = []
    for m in matches:
        overlap = False
        for sel in filtered:
            if not (m["end"] <= sel["start"] or m["start"] >= sel["end"]):
                overlap = True
                break
        if not overlap:
            filtered.append(m)
            
    filtered.sort(key=lambda x: x["start"])
    return filtered

def extract_teaching_experience(sections, raw_text=""):
    """
    Identifies teaching designations and calculates experience timelines (fallback style).
    """
    teach_text = sections.get("teaching_experience", "")
    exp_text = sections.get("experience", "")
    
    target_text = teach_text if teach_text.strip() else exp_text
    if not target_text.strip():
        target_text = raw_text
        
    lines = [l.strip() for l in target_text.split("\n") if l.strip()]
    
    current_year = datetime.now().year
    current_month = datetime.now().month
    present_val = current_year + ((current_month - 1) / 12.0)
    
    positions_raw = []
    
    for i, line in enumerate(lines):
        if any(w in line.lower() for w in ["ratified", "ratification", "approval"]):
            continue
            
        matched_designation = None
        for pattern in TEACHING_TITLES:
            m_title = re.search(pattern, line, re.IGNORECASE)
            if m_title:
                matched_designation = m_title.group(0).strip()
                matched_designation = re.sub(r"\s+", " ", matched_designation).title()
                break
                
        if matched_designation:
            matched_designation = re.sub(r"^Sr\.?\s*", "Sr ", matched_designation, flags=re.IGNORECASE)
            dates = find_dates_on_line(line)
            
            if not dates and i + 1 < len(lines):
                next_line = lines[i+1].strip()
                next_has_designation = any(re.search(pat, next_line, re.IGNORECASE) for pat in TEACHING_TITLES)
                if not next_has_designation:
                    dates = find_dates_on_line(next_line)
                    
            start_val, end_val = None, None
            is_assumed_end = False
            if len(dates) >= 2:
                start_val = dates[0]["val"]
                end_val = dates[1]["val"]
                is_assumed_end = False
            elif len(dates) == 1:
                start_val = dates[0]["val"]
                is_assumed_end = True
                
                line_lower = line.lower()
                preceding = line_lower[max(0, dates[0]["start"] - 8):dates[0]["start"]]
                is_open = ("from" in preceding or 
                           "since" in preceding or 
                           "since" in line_lower or
                           "onwards" in line_lower or 
                           "onward" in line_lower or
                           any(w in line_lower for w in ["working", "serving", "ongoing", "current", "present", "active", "joined"]))
                
                if is_open:
                    end_val = present_val
                else:
                    end_val = present_val
                    
            if start_val and end_val and end_val >= start_val:
                positions_raw.append({
                    "designation": matched_designation,
                    "start": start_val,
                    "end": end_val,
                    "is_assumed_end": is_assumed_end
                })
                
    if not positions_raw:
        return {"positions": []}
        
    positions_raw.sort(key=lambda x: x["start"])
    
    for idx in range(len(positions_raw) - 1):
        curr_pos = positions_raw[idx]
        next_pos = positions_raw[idx+1]
        
        if curr_pos.get("is_assumed_end", False) and abs(curr_pos["end"] - present_val) < 0.05:
            if next_pos["start"] > curr_pos["start"]:
                curr_pos["end"] = next_pos["start"]
                
    formatted_positions = []
    for pos in positions_raw:
        start_str = format_float_date(pos["start"])
        end_str = format_float_date(pos["end"], default_present=True)
        formatted_positions.append({
            "designation": pos["designation"],
            "start_date": start_str,
            "end_date": end_str
        })
        
    return {"positions": formatted_positions}

# ====================================================
# OTHER ENTITY HEURISTIC EXTRACTORS
# ====================================================

def extract_research_guidance(sections, raw_text=""):
    """
    Extracts number of PhD scholars guided, PG students guided, and projects supervised.
    """
    guidance_text = sections.get("research_guidance", "")
    is_fallback = False
    if not guidance_text.strip():
        guidance_text = sections.get("projects", "") + "\n" + raw_text
        is_fallback = True
        
    phd_count = 0
    pg_count = 0
    projects_count = 0
    
    phd_strict_patterns = [
        re.compile(r"\b(?:guided|supervised|advised|guidance|supervision|advisor|mentor|mentored)\b.*?\b(\d{1,2})\b.*?\b(?:phd|ph\.?d|doctoral)\b", re.IGNORECASE),
        re.compile(r"\b(\d{1,2})\b.*?\b(?:phd|ph\.?d|doctoral)\b.*?\b(?:scholars|students|candidates|guided|supervised|guidance)\b", re.IGNORECASE),
        re.compile(r"\b(?:phd|ph\.?d|doctoral)\b.*?\b(?:scholars|students|candidates|guided|supervised|guidance|advisor|supervised)\b.*?\b(\d{1,2})\b", re.IGNORECASE)
    ]
    phd_loose_patterns = [
        re.compile(r"\b(?:phd|ph\.?d|doctoral)\b.*?\b(\d{1,2})\b", re.IGNORECASE)
    ]
    
    pg_strict_patterns = [
        re.compile(r"\b(?:guided|supervised|advised|guidance|supervision|advisor|mentor|mentored)\b.*?\b(\d{1,2})\b.*?\b(?:pg|post\s*graduate|m\.?tech|m\.?e\.?|m\.?sc|m\.?c\.?a|mba)\b", re.IGNORECASE),
        re.compile(r"\b(\d{1,2})\b.*?\b(?:pg|post\s*graduate|m\.?tech|m\.?e\.?|m\.?sc|m\.?c\.?a|mba)\b.*?\b(?:scholars|students|candidates|guided|supervised|guidance)\b", re.IGNORECASE),
        re.compile(r"\b(?:pg|post\s*graduate|m\.?tech|m\.?e\.?|m\.?sc|m\.?c\.?a|mba)\b.*?\b(?:scholars|students|candidates|guided|supervised|guidance|advisor|supervised)\b.*?\b(\d{1,2})\b", re.IGNORECASE)
    ]
    pg_loose_patterns = [
        re.compile(r"\b(?:pg|post\s*graduate|m\.?tech|m\.?e\.?|m\.?sc|m\.?c\.?a|mba)\b.*?\b(\d{1,2})\b", re.IGNORECASE)
    ]
    
    proj_patterns = [
        re.compile(r"\b(?:supervised|completed|ongoing|sponsored|funded|research|handled|guided)\b.*?\bprojects?\b.*?\b(\d{1,2})\b", re.IGNORECASE),
        re.compile(r"\b(\d{1,2})\b.*?\b(?:sponsored|funded|research|academic|consultancy)?\s*projects?\b", re.IGNORECASE),
        re.compile(r"\bprojects?\b.*?\b(\d{1,2})\b", re.IGNORECASE)
    ]
    
    for line in guidance_text.split("\n"):
        line_clean = line.strip()
        if not line_clean:
            continue
            
        for pat in phd_strict_patterns:
            m = pat.search(line_clean)
            if m:
                phd_count = max(phd_count, int(m.group(1)))
                
        if not is_fallback:
            for pat in phd_loose_patterns:
                m = pat.search(line_clean)
                if m:
                    phd_count = max(phd_count, int(m.group(1)))
                    
        for pat in pg_strict_patterns:
            m = pat.search(line_clean)
            if m:
                pg_count = max(pg_count, int(m.group(1)))
                
        if not is_fallback:
            for pat in pg_loose_patterns:
                m = pat.search(line_clean)
                if m:
                    pg_count = max(pg_count, int(m.group(1)))
                    
        for pat in proj_patterns:
            m = pat.search(line_clean)
            if m:
                projects_count = max(projects_count, int(m.group(1)))
                
    return {
        "phd_scholars_guided": phd_count,
        "pg_students_guided": pg_count,
        "research_projects_supervised": projects_count
    }

def extract_publications(sections):
    """
    Counts references in the publications section and breaks them down by categories.
    """
    pub_text = sections.get("publications", "")
    if not pub_text.strip():
        return {
            "total_publications": 0,
            "ieee": 0,
            "springer": 0,
            "elsevier": 0,
            "scopus": 0,
            "sci": 0,
            "ugc": 0,
            "journal_papers": 0,
            "conference_papers": 0,
            "book_chapters": 0
        }
        
    lines = pub_text.split("\n")
    citation_lines = []
    
    for line in lines:
        cleaned = line.strip()
        if not cleaned:
            continue
        if len(cleaned) < 30:
            continue
            
        is_header = any(w in cleaned.lower() for w in ["s.no", "sl.no", "paper title", "journal name", "publication title", "year of publication", "author(s)"])
        if is_header:
            continue
            
        is_list_item = False
        if re.match(r"^(?:\d+[\.\)\]]|\-\s*|\[\d+\]|•|\*)\s*", cleaned):
            is_list_item = True
            
        has_year = bool(re.search(r"\b(19|20)\d{2}\b", cleaned))
        is_ref_contact = any(w in cleaned.lower() for w in ["email:", "phone:", "mobile:", "reference contact", "referee", "supervisor name"])
        
        if (is_list_item or has_year) and not is_ref_contact:
            citation_lines.append(cleaned)
            
    if not citation_lines:
        citation_lines = [l.strip() for l in lines if len(l.strip()) > 50]
        
    total_pubs = len(citation_lines)
    
    ieee_cnt = 0
    springer_cnt = 0
    elsevier_cnt = 0
    scopus_cnt = 0
    sci_cnt = 0
    ugc_cnt = 0
    journal_cnt = 0
    conf_cnt = 0
    book_cnt = 0
    
    for citation in citation_lines:
        cit_lower = citation.lower()
        
        if "ieee" in cit_lower:
            ieee_cnt += 1
        if "springer" in cit_lower:
            springer_cnt += 1
        if "elsevier" in cit_lower:
            elsevier_cnt += 1
        if "scopus" in cit_lower:
            scopus_cnt += 1
        if "sci" in cit_lower or "sci-indexed" in cit_lower:
            if re.search(r"\bsci\b", cit_lower):
                sci_cnt += 1
        if re.search(r"\bugc\b", cit_lower):
            ugc_cnt += 1
        if "journal" in cit_lower:
            journal_cnt += 1
        if "conference" in cit_lower or "proceedings" in cit_lower or "symposium" in cit_lower:
            conf_cnt += 1
        if "book chapter" in cit_lower or "book" in cit_lower:
            book_cnt += 1
            
    if journal_cnt == 0 and conf_cnt == 0 and total_pubs > 0:
        for citation in citation_lines:
            cit_lower = citation.lower()
            if "international journal" in cit_lower or "transactions" in cit_lower or "letters" in cit_lower:
                journal_cnt += 1
            elif "international conference" in cit_lower or "congress" in cit_lower:
                conf_cnt += 1
                
        if journal_cnt == 0 and conf_cnt == 0:
            journal_cnt = total_pubs
            
    return {
        "total_publications": total_pubs,
        "ieee": ieee_cnt,
        "springer": springer_cnt,
        "elsevier": elsevier_cnt,
        "scopus": scopus_cnt,
        "sci": sci_cnt,
        "ugc": ugc_cnt,
        "journal_papers": journal_cnt,
        "conference_papers": conf_cnt,
        "book_chapters": book_cnt
    }

def extract_patents(sections):
    """
    Extracts patent counts (Granted vs Filed) from the patents section.
    """
    patent_text = sections.get("patents", "")
    if not patent_text.strip():
        return {
            "granted_patents": 0,
            "filed_patents": 0
        }
        
    granted_cnt = 0
    filed_cnt = 0
    
    lines = [l.strip() for l in patent_text.split("\n") if l.strip()]
    
    for line in lines:
        line_lower = line.lower()
        is_granted = any(w in line_lower for w in ["granted", "awarded", "patent no", "patent number"])
        is_filed = any(w in line_lower for w in ["filed", "application", "pending", "published"])
        
        if is_granted:
            granted_cnt += 1
        elif is_filed:
            filed_cnt += 1
        else:
            if "patent" in line_lower:
                filed_cnt += 1
                
    if granted_cnt == 0 and filed_cnt == 0:
        granted_patterns = [
            re.compile(r"(\d+)\s*patents?\s*(?:granted|awarded)", re.IGNORECASE),
            re.compile(r"(?:granted|awarded)\s*patents?\s*[:\-]?\s*(\d+)", re.IGNORECASE),
            re.compile(r"(\d+)\s*(?:granted|awarded)", re.IGNORECASE)
        ]
        filed_patterns = [
            re.compile(r"(\d+)\s*patents?\s*(?:filed|pending|application|published)", re.IGNORECASE),
            re.compile(r"(?:filed|pending|application|published)\s*patents?\s*[:\-]?\s*(\d+)", re.IGNORECASE),
            re.compile(r"(\d+)\s*(?:filed|pending|application|published)", re.IGNORECASE)
        ]
        
        for p in granted_patterns:
            m = p.search(patent_text)
            if m:
                granted_cnt = max(granted_cnt, int(m.group(1)))
                break
        for p in filed_patterns:
            m = p.search(patent_text)
            if m:
                filed_cnt = max(filed_cnt, int(m.group(1)))
                break
                
    return {
        "granted_patents": granted_cnt,
        "filed_patents": filed_cnt
    }

def extract_extra_curriculars(sections, raw_text=""):
    """
    Scans the extracurricular section and overall text for volunteering, activities, etc.
    """
    ext_text = (sections.get("extra_curricular", "") + "\n" + sections.get("awards", "")).lower()
    if not ext_text.strip():
        ext_text = raw_text.lower()
        
    activities = []
    
    keywords_mapping = {
        "nss": ["nss", "national service scheme"],
        "ncc": ["ncc", "national cadet corps"],
        "sports": ["sports", "athletics", "cricket", "football", "basketball", "badminton"],
        "volunteer": ["volunteer", "social work", "ngo", "community service"],
        "coordinator": ["coordinator", "event organizer", "convenor", "organizing committee"],
        "clubs": ["clubs", "cultural club", "rotary club", "toastmasters", "literary society"],
        "hackathons": ["hackathon", "coding competition", "ideathon"],
        "professional_bodies": ["ieee member", "acm member", "iste", "csi member", "ieee branch"]
    }
    
    for activity_name, keywords in keywords_mapping.items():
        for kw in keywords:
            if re.search(r"\b" + re.escape(kw) + r"\b", ext_text):
                activities.append(activity_name.replace("_", " ").title())
                break
                
    return activities

def extract_skills_normalized(raw_text):
    """
    Identifies academic and tech skills in CV text, normalizing synonyms.
    """
    text_lower = raw_text.lower()
    skills = []
    
    skill_mappings = {
        "Machine Learning": [r"\bml\b", r"\bmachine\s+learning\b", r"\bstatistical\s+learning\b"],
        "Artificial Intelligence": [r"\bai\b", r"\bartificial\s+intelligence\b"],
        "Deep Learning": [r"\bdl\b", r"\bdeep\s+learning\b", r"\bneural\s+networks?\b"],
        "Natural Language Processing": [r"\bnlp\b", r"\bnatural\s+language\s+processing\b", r"\btext\s+mining\b"],
        "Computer Vision": [r"\bcv\b", r"\bcomputer\s+vision\b", r"\bimage\s+processing\b"],
        "Data Science": [r"\bdata\s+science\b", r"\bdata\s+analytics\b", r"\bbig\s+data\b"],
        "Data Structures & Algorithms": [r"\bdsa\b", r"\bdata\s+structures\b", r"\balgorithms\b"],
        "Internet of Things": [r"\biot\b", r"\binternet\s+of\s+things\b", r"\bembedded\s+systems?\b"],
        "Blockchain": [r"\bblockchain\b", r"\bsmart\s+contracts?\b", r"\bcryptography\b"],
        "Cloud Computing": [r"\bcloud\s+computing\b", r"\baws\b", r"\bazure\b", r"\bgcp\b"],
        "Database Management Systems": [r"\bdbms\b", r"\bdatabase\b", r"\bsql\b", r"\bnosql\b", r"\bmysql\b", r"\boracle\b"]
    }
    
    for skill_name, patterns in skill_mappings.items():
        for pat in patterns:
            if re.search(pat, text_lower):
                skills.append(skill_name)
                break
    return skills

def parse_extracted_entities(extracted_payload, logger=None):
    """
    Integrates all heuristic extractors to output structured candidate data.
    """
    if logger:
        logger.info("Extracting details from segmented sections...")
        
    raw_text = extracted_payload["raw_text"]
    potential_name = extracted_payload["potential_name"]
    filename = extracted_payload.get("filename", "")
    
    # Detect sections
    from parsers.section_detector import detect_sections
    sections = detect_sections(raw_text, logger)
    sections["filename"] = filename
    
    # 0. Single holistic LLM pass - one Ollama call reused for personal info,
    #    employment records, education institutions, AND publication detail,
    #    instead of guessing each field separately and blindly. Returns None if
    #    Ollama isn't reachable; every downstream use of llm_profile is grounded
    #    against raw_text before being trusted.
    llm_profile = extract_candidate_profile_via_llm(raw_text)
    llm_positions = (llm_profile or {}).get("employment_records", [])
    education_info = resolve_education_institutions((llm_profile or {}).get("education_records", []), raw_text)
    publications_detail = resolve_publication_records((llm_profile or {}).get("publication_records", []), raw_text)

    # 1. Personal info (LLM-aware, with regex/heuristic fallback)
    personal_info = extract_personal_info(sections, potential_name, raw_text, filename, llm_profile)
    
    # 2. Qualifications
    highest_qual = extract_highest_qualification(sections, raw_text)
    
    # 3. Experience & Timeline Redesign (Hybrid Architecture)
    warning_msg = None
    if not llm_positions:
        logger.warning("LLM returned no positions. Falling back to rule-based experience parser...")
        fallback_data = extract_teaching_experience(sections, raw_text)
        
        raw_fallback_positions = []
        for pos in fallback_data.get("positions", []):
            raw_fallback_positions.append({
                "designation": pos.get("designation", ""),
                "organization": "Unknown",
                "start_date": pos.get("start_date", ""),
                "end_date": pos.get("end_date", "")
            })
            
        if raw_fallback_positions:
            metrics = calculate_experience_metrics(raw_fallback_positions, raw_text)
            warning_msg = "Experience extraction fallback applied (Ollama local LLM was not reachable)."
        else:
            metrics = {
                "positions_detected": 0,
                "total_professional_experience_years": 0,
                "academic_experience_years": 0,
                "research_experience_years": 0,
                "administrative_experience_years": 0,
                "industry_experience_years": 0,
                "positions": []
            }
            warning_msg = "Experience section detected but timeline construction failed."
    else:
        metrics = calculate_experience_metrics(llm_positions, raw_text)
        if metrics["positions_detected"] == 0:
            warning_msg = "Experience section detected but timeline construction failed."
            
    # 4. Research Guidance
    guidance = extract_research_guidance(sections, raw_text)
    
    # 5. Publications
    pubs = extract_publications(sections)
    
    # 6. Patents
    patents = extract_patents(sections)
    
    # 7. Extra-curriculars
    activities = extract_extra_curriculars(sections, raw_text)
    
    # 7b. FDP / STTP attended (faculty development & short-term training programmes)
    fdp_sttp = extract_fdp_sttp_count(sections, raw_text)
    
    # 8. Normalized Skills
    skills = extract_skills_normalized(raw_text)
    
    # Calculate Confidence Scores
    confidence_scores = {}
    
    # Name Confidence
    name = personal_info["name"]
    name_source = personal_info.get("name_source", "None")
    if name == "Unknown":
        confidence_scores["name"] = {
            "score": 0,
            "extracted_value": "Unknown",
            "reason": "No valid candidate name block found at top of resume."
        }
    elif name_source == "llm_holistic":
        confidence_scores["name"] = {
            "score": 97,
            "extracted_value": name,
            "reason": "Candidate name identified by the LLM's holistic read of the full document, verified against the raw text."
        }
    elif name_source == "layout_analysis":
        confidence_scores["name"] = {
            "score": 95,
            "extracted_value": name,
            "reason": "Candidate name identified from largest font size block on first page."
        }
    elif name_source == "filename_fallback":
        confidence_scores["name"] = {
            "score": 70,
            "extracted_value": name,
            "reason": "Candidate name extracted from file name fallback."
        }
    else:
        confidence_scores["name"] = {
            "score": 85,
            "extracted_value": name,
            "reason": "Candidate name extracted from top header lines."
        }
        
    # Qualification Confidence
    if highest_qual == "PhD":
        confidence_scores["qualification"] = {
            "score": 95,
            "extracted_value": "PhD",
            "reason": "Explicit PhD degree detected in education section."
        }
    elif highest_qual == "Post Graduate":
        confidence_scores["qualification"] = {
            "score": 90,
            "extracted_value": "Post Graduate",
            "reason": "Post Graduate degree keywords matched (M.Tech, MCA, etc.) in education section."
        }
    elif highest_qual == "Graduate":
        confidence_scores["qualification"] = {
            "score": 85,
            "extracted_value": "Graduate",
            "reason": "Graduate degree keywords (B.Tech, BCA, BE, etc.) matched in education section."
        }
    else:
        confidence_scores["qualification"] = {
            "score": 30,
            "extracted_value": "None",
            "reason": "No standard university degrees detected in education sections."
        }
        
    # Teaching Experience Confidence
    if warning_msg == "Experience section detected but timeline construction failed.":
        confidence_scores["teaching_experience"] = {
            "score": 30,
            "extracted_value": "0 Years",
            "reason": "Timeline construction failed: unparseable dates or roles."
        }
    elif warning_msg == "Experience extraction fallback applied (Ollama local LLM was not reachable).":
        confidence_scores["teaching_experience"] = {
            "score": 65,
            "extracted_value": f"{metrics['academic_experience_years']} Years",
            "reason": "Ollama local LLM was not reachable. Fallback rule-based parsing applied."
        }
    elif metrics["positions_detected"] > 0:
        confidence_scores["teaching_experience"] = {
            "score": 95,
            "extracted_value": f"{metrics['academic_experience_years']} Years (Academic)",
            "reason": f"Ollama local LLM successfully parsed {metrics['positions_detected']} structured timeline records."
        }
    else:
        confidence_scores["teaching_experience"] = {
            "score": 50,
            "extracted_value": "0 Years",
            "reason": "No employment history or academic designations found."
        }
        
    # Publications Confidence
    total_pubs = pubs["total_publications"]
    if total_pubs > 0:
        if sections.get("publications", "").strip():
            confidence_scores["publications"] = {
                "score": 95,
                "extracted_value": f"{total_pubs} Publications",
                "reason": "Extracted publications list with year/citation patterns from the publications section."
            }
        else:
            confidence_scores["publications"] = {
                "score": 75,
                "extracted_value": f"{total_pubs} Publications",
                "reason": "Extracted citation-like lines from raw text fallback search."
            }
    else:
        confidence_scores["publications"] = {
            "score": 50,
            "extracted_value": "0 Publications",
            "reason": "No publications section or citation-like lines detected."
        }
        
    # Research Guidance Confidence
    phd_g = guidance["phd_scholars_guided"]
    pg_g = guidance["pg_students_guided"]
    proj_s = guidance["research_projects_supervised"]
    if phd_g > 0 or pg_g > 0 or proj_s > 0:
        confidence_scores["research_guidance"] = {
            "score": 90,
            "extracted_value": f"Guided: {phd_g} PhD, {pg_g} PG, {proj_s} Projects",
            "reason": "Mentorship/project counts extracted via pattern matching."
        }
    else:
        confidence_scores["research_guidance"] = {
            "score": 50,
            "extracted_value": "0 Students/Projects Guided",
            "reason": "No mentorship or guidance keywords matched."
        }
        
    # Patents Confidence
    granted_p = patents["granted_patents"]
    filed_p = patents["filed_patents"]
    if granted_p > 0 or filed_p > 0:
        confidence_scores["patents"] = {
            "score": 90,
            "extracted_value": f"Patents: {granted_p} Granted, {filed_p} Filed",
            "reason": "Patents count extracted via status analysis."
        }
    else:
        confidence_scores["patents"] = {
            "score": 50,
            "extracted_value": "0 Patents",
            "reason": "No patent records matched."
        }
        
    # Assemble full structure
    candidate_profile = {
        "name": name,
        "email": personal_info["email"],
        "phone": personal_info["phone"],
        "highest_qualification": highest_qual,
        "education": education_info,  # {ug_institution, pg_institution, phd_institution}
        "teaching_experience_years": float(metrics["academic_experience_years"]), # Legacy mapping for UI compatibility
        "academic_experience_years": float(metrics["academic_experience_years"]),
        "total_experience_years": float(metrics["total_professional_experience_years"]),
        "research_experience_years": float(metrics["research_experience_years"]),
        "administrative_experience_years": float(metrics["administrative_experience_years"]),
        "industry_experience_years": float(metrics.get("industry_experience_years", 0)),
        "positions": metrics["positions"],
        "positions_detected": metrics["positions_detected"],
        "experience_warning": warning_msg,
        "research_guidance": guidance,
        "publications": pubs,
        "publications_detail": publications_detail,  # structured per-paper records, for the publications workbook
        "patents": patents,
        "extra_curricular_activities": activities,
        "fdp_sttp": fdp_sttp,  # {fdp_sttp_count, fdp_sttp_items} - replaces "Extra-Curricular" on the marking sheet
        "skills": skills,
        "confidence_scores": confidence_scores
    }
    
    if logger:
        logger.info(f"Successfully compiled profile for candidate: {candidate_profile['name']}")
        logger.info(f"Education: {highest_qual}, Exp (Academic): {metrics['academic_experience_years']} yrs, Total Exp: {metrics['total_professional_experience_years']} yrs")
        
    return candidate_profile, sections
