import requests
import json
import re
from utils.logger import get_logger

logger = get_logger()

OLLAMA_CHAT_URL = "http://localhost:11434/api/chat"


def _query_ollama_json(prompt, model="qwen2.5:3b", timeout=45):
    """
    Sends a prompt to local Ollama and returns the parsed JSON response body,
    or None if Ollama is unreachable, times out, or returns non-JSON content.
    Callers are expected to fall back to the regex/heuristic pipeline on None.
    """
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False,
        "format": "json"
    }
    headers = {"Content-Type": "application/json"}

    response = requests.post(OLLAMA_CHAT_URL, json=payload, headers=headers, timeout=timeout)
    response.raise_for_status()

    result = response.json()
    content = result.get("message", {}).get("content", "").strip()
    if not content:
        return None

    cleaned_content = re.sub(r"^```(?:json)?\s*", "", content)
    cleaned_content = re.sub(r"\s*```$", "", cleaned_content).strip()
    return json.loads(cleaned_content)


def extract_candidate_profile_via_llm(text, model="qwen2.5:3b"):
    """
    Single holistic pass over the resume text. Instead of stitching together
    separate, context-blind regex guesses for name/email/phone/organization,
    this asks the local model to read the WHOLE document once and report all
    of those fields together - so it can use context (e.g. "this email sits
    in a publication citation, not the header" or "this institution is where
    they studied, not where they worked") the way a regex one-liner can't.

    Returns a dict:
        {
            "name": str | None,
            "email": str | None,
            "phone": str | None,
            "employment_records": [ {designation, organization, start_date, end_date, employment_type}, ... ]
        }
    or None if Ollama was unreachable / returned something unusable. Callers
    must NOT trust these fields blindly - ground them against raw_text first
    (see info_extractor._email_is_grounded / _phone_is_grounded / _name_is_grounded
    / _organization_is_grounded), the same way you'd never trust unvalidated
    input from any other untrusted source.
    """
    prompt = f"""Read the following resume text carefully and extract the candidate's own profile information.

Return a single JSON object with this exact schema:
{{
  "name": "the candidate's full personal name, exactly as written near the top of their OWN resume - NOT a reviewer, reference, co-author, or institution name",
  "email": "the candidate's own personal contact email, exactly as written - NOT a journal/editor/submission/admin email mentioned inside a publication citation",
  "phone": "the candidate's own personal contact phone number, exactly as written - NOT a patent number, ISBN, ID number, or any other unrelated digit sequence",
  "employment_records": [
    {{
      "designation": "designation/role",
      "organization": "organization/institution/company name, copied exactly as written in the text - never guessed or normalized",
      "start_date": "starting date",
      "end_date": "ending date or 'Present'",
      "employment_type": "Academic, Research, Industry, or Administrative"
    }}
  ],
  "education_records": [
    {{
      "level": "UG, PG, or PhD",
      "degree": "degree name, e.g. B.Tech, M.Tech, PhD, exactly as written",
      "institution": "the specific college/university name where THIS degree was completed, exactly as written - never guessed",
      "year": "year of completion if stated, else null"
    }}
  ],
  "publication_records": [
    {{
      "title": "title of the paper, exactly as written (without surrounding quote marks)",
      "journal_name": "journal or conference name, exactly as written",
      "published_under": "publisher/proceedings detail (e.g. IEEE, Springer, Elsevier) if stated, else null",
      "year": "year of publication if stated, else null",
      "impact_factor": "impact factor if explicitly stated in the text, else null - never estimate or guess one",
      "scopus_indexed": "Yes if the text explicitly says Scopus-indexed, No if it explicitly says it is not, else null"
    }}
  ]
}}

Rules:
- Only extract organization/institution/journal names that literally appear in the text. Never invent, infer, or normalize one.
- Only treat Experience/Employment/Teaching Experience section entries as employment records. A degree earned at an institution is NOT employment there unless the text explicitly says so.
- One education_records entry per degree (UG, PG, PhD) actually mentioned - do not invent degrees that aren't there.
- One publication_records entry per distinct paper/citation actually listed in a Publications-type section. Do not merge multiple papers into one entry or split one paper into multiple entries.
- If you cannot confidently find a field's value in the text, set it to null rather than guessing.
- Output raw JSON only. No explanation, no markdown formatting, no extra text.

Resume Text:
{text}"""

    try:
        logger.info(f"Querying local Ollama model '{model}' for full candidate profile...")
        parsed = _query_ollama_json(prompt, model=model)

        if parsed is None:
            logger.warning("Ollama returned empty response content.")
            return None

        if not isinstance(parsed, dict):
            logger.warning(f"Expected a JSON object from LLM, got: {type(parsed)}")
            return None

        employment_records = parsed.get("employment_records", [])
        if not isinstance(employment_records, list):
            employment_records = []

        education_records = parsed.get("education_records", [])
        if not isinstance(education_records, list):
            education_records = []

        publication_records = parsed.get("publication_records", [])
        if not isinstance(publication_records, list):
            publication_records = []

        logger.info(f"LLM profile extraction returned name={bool(parsed.get('name'))}, "
                     f"email={bool(parsed.get('email'))}, phone={bool(parsed.get('phone'))}, "
                     f"{len(employment_records)} employment record(s), {len(education_records)} education record(s), "
                     f"{len(publication_records)} publication record(s).")

        return {
            "name": parsed.get("name") or None,
            "email": parsed.get("email") or None,
            "phone": parsed.get("phone") or None,
            "employment_records": employment_records,
            "education_records": education_records,
            "publication_records": publication_records
        }

    except json.JSONDecodeError as jde:
        logger.error(f"Failed to parse LLM content as JSON: {jde}")
        return None
    except requests.exceptions.RequestException as ree:
        logger.warning(f"Ollama request failed (is Ollama running?): {ree}")
        return None
    except Exception as e:
        logger.error(f"Unexpected error in LLM extraction: {e}")
        return None


def extract_employment_records_via_llm(text, model="qwen2.5:3b"):
    """
    Backward-compatible wrapper kept for any existing callers that only want
    the employment list. Prefer extract_candidate_profile_via_llm() in new
    code - it makes the same single call and also grounds name/email/phone.
    """
    profile = extract_candidate_profile_via_llm(text, model=model)
    if not profile:
        return []
    return profile.get("employment_records", [])
