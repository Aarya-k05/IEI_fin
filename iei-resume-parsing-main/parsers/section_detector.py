import re

# Section Heading Patterns (Case Insensitive)
SECTION_PATTERNS = {
    "education": re.compile(r"\b(education|academics|academic qualification|academic profile|educational background|qualifications|academic credentials|scholastic details|academic details)\b", re.IGNORECASE),
    "teaching_experience": re.compile(r"\b(teaching experience|academic experience|teaching background|teaching activities|instructional experience)\b", re.IGNORECASE),
    "experience": re.compile(r"\b(experience|professional experience|employment history|work experience|industry experience|employment|career history|occupational details)\b", re.IGNORECASE),
    "publications": re.compile(r"\b(publications|research papers|journal papers|academic contributions|list of publications|scholarly articles|published works|selected publications|conference papers|book chapters|publications list)\b", re.IGNORECASE),
    "research_guidance": re.compile(r"\b(research guidance|thesis supervision|phd guidance|guidance|supervision|research supervisor|doctoral advisor|student mentorship|project guidance)\b", re.IGNORECASE),
    "patents": re.compile(r"\b(patents|patent filed|patent granted|inventions|intellectual property|list of patents)\b", re.IGNORECASE),
    "projects": re.compile(r"\b(projects|research projects|funded projects|consultancy projects|sponsored projects|completed projects)\b", re.IGNORECASE),
    "certifications": re.compile(r"\b(certifications|courses|training|workshops|professional development|seminars|certifications & courses)\b", re.IGNORECASE),
    "extra_curricular": re.compile(r"\b(extra curricular|volunteer|nss|sports|student activities|hackathons|professional bodies|co-curricular|outreach|social work|memberships|hobbies)\b", re.IGNORECASE),
    "awards": re.compile(r"\b(awards|achievements|honors|recognition|fellowships|scholarships|academic awards|achievements & awards)\b", re.IGNORECASE)
}

def detect_sections(raw_text, logger=None):
    """
    Splits the raw resume text into dictionary sections based on heading patterns.
    Text before the first detected heading is assigned to 'personal_info'.
    """
    if logger:
        logger.info("Detecting resume sections...")
        
    lines = raw_text.split('\n')
    sections_indices = []
    
    # Analyze lines to locate heading candidates
    for i, line in enumerate(lines):
        cleaned_line = line.strip()
        if not cleaned_line:
            continue
            
        # Ignore extremely long lines as headers (must be < 60 chars)
        if len(cleaned_line) > 60:
            continue
            
        # Strip list numbering or bullet points at start of line
        # e.g., "1. Education" -> "Education", "A) Experience" -> "Experience"
        header_text = re.sub(r"^[\d\.\-\*•\[\]a-zA-Z\)]+\s+", "", cleaned_line).strip()
        
        # Check against section patterns
        matched_section = None
        for sec_name, pattern in SECTION_PATTERNS.items():
            # Match strictly to avoid mid-sentence matches
            # The header text must match the section keyword pattern
            if pattern.search(header_text) and len(header_text) < 35:
                matched_section = sec_name
                break
                
        if matched_section:
            # If the last detected section was at the same line or adjacent, skip
            if sections_indices and sections_indices[-1]["line_idx"] == i:
                continue
            sections_indices.append({
                "section": matched_section,
                "line_idx": i,
                "header": cleaned_line
            })
            
    # Segment text by indices
    sections = {
        "personal_info": "",
        "education": "",
        "teaching_experience": "",
        "experience": "",
        "publications": "",
        "research_guidance": "",
        "patents": "",
        "projects": "",
        "certifications": "",
        "extra_curricular": "",
        "awards": ""
    }
    
    if not sections_indices:
        # Fallback: Put entire text into personal_info
        sections["personal_info"] = raw_text
        return sections
        
    # Sort indices by line index
    sections_indices.sort(key=lambda x: x["line_idx"])
    
    # Text before first header goes to personal_info
    first_idx = sections_indices[0]["line_idx"]
    sections["personal_info"] = "\n".join(lines[:first_idx])
    
    for i in range(len(sections_indices)):
        current_sec = sections_indices[i]["section"]
        start_line = sections_indices[i]["line_idx"]
        
        if i < len(sections_indices) - 1:
            end_line = sections_indices[i+1]["line_idx"]
        else:
            end_line = len(lines)
            
        # Retrieve section text (excluding the header line itself)
        sec_text = "\n".join(lines[start_line+1:end_line])
        
        # If section already exists (multiple headers of same type), append
        if sections[current_sec]:
            sections[current_sec] += "\n" + sec_text
        else:
            sections[current_sec] = sec_text
            
    # If teaching_experience is empty but experience is not, we'll keep both.
    # Experience parsing will analyze experience if teaching_experience is missing.
    
    if logger:
        detected_secs = [k for k, v in sections.items() if len(v.strip()) > 0]
        logger.info(f"Sections detected: {', '.join(detected_secs)}")
        
    return sections
