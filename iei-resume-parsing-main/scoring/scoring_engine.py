import json
from config import load_scoring_config

def calculate_candidate_score(profile, config=None):
    """
    Computes scores for a candidate profile based on configuration rules.
    Generates a breakdown and a written evaluation report explaining the scoring.
    """
    if config is None:
        config = load_scoring_config()
        
    confidence_scores = profile.get("confidence_scores", {})
        
    # --- 1. Qualification Score ---
    qual_config = config.get("qualification", {})
    highest_qual = profile.get("highest_qualification", "None")
    qual_score = 0.0
    qual_explanation = ""
    
    if highest_qual == "PhD":
        qual_score = float(qual_config.get("phd", 10))
        qual_explanation = f"Highest degree is PhD -> {qual_score} points"
    elif highest_qual == "Post Graduate":
        qual_score = float(qual_config.get("post_graduate", 10))
        qual_explanation = f"Highest degree is Post Graduate -> {qual_score} points"
    elif highest_qual == "Graduate":
        qual_score = float(qual_config.get("graduate", 5))
        qual_explanation = f"Highest degree is Graduate -> {qual_score} points"
    else:
        qual_score = 0.0
        qual_explanation = "No standard academic degree detected -> 0.0 points"
        
    # Apply confidence override
    qual_conf = confidence_scores.get("qualification", {})
    qual_conf_score = qual_conf.get("score", 100)
    if qual_conf_score < 60:
        qual_score = 0.0
        qual_explanation = f"⚠️ WARNING: Low extraction confidence ({qual_conf_score}%). Score overridden to 0.0. Reason: {qual_conf.get('reason', 'Uncertain extraction.')}"
        
    # --- 2. Publications Score ---
    pub_config = config.get("publications", {})
    pubs_data = profile.get("publications", {})
    total_pubs = pubs_data.get("total_publications", 0)
    pub_score = 0.0
    pub_explanation = f"Total publications detected: {total_pubs}\n"
    pub_explanation += f"   (IEEE: {pubs_data.get('ieee', 0)}, Springer: {pubs_data.get('springer', 0)}, Elsevier: {pubs_data.get('elsevier', 0)}, Scopus: {pubs_data.get('scopus', 0)}, SCI: {pubs_data.get('sci', 0)}, UGC: {pubs_data.get('ugc', 0)}, Journals: {pubs_data.get('journal_papers', 0)}, Conferences: {pubs_data.get('conference_papers', 0)}, Books: {pubs_data.get('book_chapters', 0)})\n"
    
    # Match band
    matched_band = False
    for band in pub_config.get("bands", []):
        min_p = band.get("min", 0)
        max_p = band.get("max", 9999)
        if min_p <= total_pubs <= max_p:
            pub_score = float(band.get("score", 0))
            pub_explanation += f"   Scoring Band Applied: {min_p}-{max_p} publications -> {pub_score} points"
            matched_band = True
            break
    if not matched_band:
        pub_explanation += "   Scoring Band Applied: No matching band found -> 0.0 points"
        
    # Apply confidence override
    pub_conf = confidence_scores.get("publications", {})
    pub_conf_score = pub_conf.get("score", 100)
    if pub_conf_score < 60:
        pub_score = 0.0
        pub_explanation = f"⚠️ WARNING: Low extraction confidence ({pub_conf_score}%). Score overridden to 0.0. Reason: {pub_conf.get('reason', 'Uncertain extraction.')}"
        
    # --- 3. Teaching Experience Score ---
    teach_config = config.get("teaching_experience", {})
    exp_years = profile.get("academic_experience_years", 0.0)
    total_exp = profile.get("total_experience_years", 0.0)
    res_exp = profile.get("research_experience_years", 0.0)
    admin_exp = profile.get("administrative_experience_years", 0.0)
    
    teach_score = 0.0
    teach_explanation = f"Academic Teaching Experience: {exp_years} years\n"
    teach_explanation += f"   Research Experience: {res_exp} years\n"
    teach_explanation += f"   Administrative Experience: {admin_exp} years\n"
    teach_explanation += f"   Total Professional Experience: {total_exp} years\n"
    
    matched_band = False
    for band in teach_config.get("bands", []):
        min_y = float(band.get("min", 0))
        max_y = float(band.get("max", 99))
        if min_y <= exp_years < max_y:
            teach_score = float(band.get("score", 0))
            teach_explanation += f"   Scoring Band Applied: {min_y}-{max_y} Academic years -> {teach_score} points"
            matched_band = True
            break
    if not matched_band and exp_years >= 0.0:
        bands = teach_config.get("bands", [])
        if bands:
            last_band = bands[-1]
            teach_score = float(last_band.get("score", 0))
            teach_explanation += f"   Scoring Band Applied: Over {last_band.get('min')} Academic years -> {teach_score} points"
            
    # Check experience warning
    exp_warn = profile.get("experience_warning")
    if exp_warn:
        teach_explanation += f"\n   ⚠️ WARNING: {exp_warn}"
            
    # Apply confidence override
    teach_conf = confidence_scores.get("teaching_experience", {})
    teach_conf_score = teach_conf.get("score", 100)
    if teach_conf_score < 60:
        teach_score = 0.0
        teach_explanation = f"⚠️ WARNING: Low extraction confidence ({teach_conf_score}%). Score overridden to 0.0. Reason: {teach_conf.get('reason', 'Uncertain extraction.')}"
        if exp_warn:
            teach_explanation += f"\n   ⚠️ WARNING: {exp_warn}"
            
    # --- 4. Research Guidance Score ---
    rg_config = config.get("research_guidance", {})
    rg_data = profile.get("research_guidance", {})
    phd_guided = rg_data.get("phd_scholars_guided", 0)
    pg_guided = rg_data.get("pg_students_guided", 0)
    proj_supervised = rg_data.get("research_projects_supervised", 0)
    
    phd_val = float(rg_config.get("phd_score_per_student", 2.0))
    pg_val = float(rg_config.get("pg_score_per_student", 1.0))
    proj_val = float(rg_config.get("project_score_per_project", 1.0))
    rg_max = float(rg_config.get("max_score", 10.0))
    
    subtotal_rg = (phd_guided * phd_val) + (pg_guided * pg_val) + (proj_supervised * proj_val)
    rg_score = min(subtotal_rg, rg_max)
    
    rg_explanation = f"PhD scholars guided: {phd_guided} ({phd_val} pts/student) -> {phd_guided * phd_val} pts\n"
    rg_explanation += f"PG students guided: {pg_guided} ({pg_val} pts/student) -> {pg_guided * pg_val} pts\n"
    rg_explanation += f"Research projects: {proj_supervised} ({proj_val} pts/project) -> {proj_supervised * proj_val} pts\n"
    rg_explanation += f"Subtotal: {subtotal_rg} points (Capped at Max: {rg_max} points)"

    # Apply confidence override
    rg_conf = confidence_scores.get("research_guidance", {})
    rg_conf_score = rg_conf.get("score", 100)
    if rg_conf_score < 60:
        rg_score = 0.0
        rg_explanation = f"⚠️ WARNING: Low extraction confidence ({rg_conf_score}%). Score overridden to 0.0. Reason: {rg_conf.get('reason', 'Uncertain extraction.')}"

    # --- 5. Patents Score ---
    pat_config = config.get("patents", {})
    pat_data = profile.get("patents", {})
    granted = pat_data.get("granted_patents", 0)
    filed = pat_data.get("filed_patents", 0)
    
    granted_val = float(pat_config.get("granted_score", 5.0))
    filed_val = float(pat_config.get("filed_score", 2.0))
    pat_max = float(pat_config.get("max_score", 10.0))
    
    subtotal_pat = (granted * granted_val) + (filed * filed_val)
    pat_score = min(subtotal_pat, pat_max)
    
    pat_explanation = f"Granted patents: {granted} ({granted_val} pts/patent) -> {granted * granted_val} pts\n"
    pat_explanation += f"Filed patents: {filed} ({filed_val} pts/patent) -> {filed * filed_val} pts\n"
    pat_explanation += f"Subtotal: {subtotal_pat} points (Capped at Max: {pat_max} points)"

    # Apply confidence override
    pat_conf = confidence_scores.get("patents", {})
    pat_conf_score = pat_conf.get("score", 100)
    if pat_conf_score < 60:
        pat_score = 0.0
        pat_explanation = f"⚠️ WARNING: Low extraction confidence ({pat_conf_score}%). Score overridden to 0.0. Reason: {pat_conf.get('reason', 'Uncertain extraction.')}"

    # --- 6. Extra-Curricular Score ---
    ec_config = config.get("extra_curricular", {})
    activities = profile.get("extra_curricular_activities", [])
    ec_val = float(ec_config.get("score_per_activity", 1.0))
    ec_max = float(ec_config.get("max_score", 5.0))
    
    subtotal_ec = len(activities) * ec_val
    ec_score = min(subtotal_ec, ec_max)
    
    ec_explanation = f"Activities detected: {', '.join(activities) if activities else 'None'}\n"
    ec_explanation += f"Count: {len(activities)} ({ec_val} pts/activity) -> {subtotal_ec} pts (Capped at Max: {ec_max} points)"
    
    # --- Total Score ---
    total_score = qual_score + pub_score + teach_score + rg_score + pat_score + ec_score
    
    # --- Write Explanation Report ---
    name = profile.get("name", "Candidate")
    report = f"============================================================\n"
    report += f"FACULTY RECRUITMENT SCORECARD: {name.upper()}\n"
    report += f"============================================================\n\n"
    
    report += f"1. ACADEMIC QUALIFICATION SCORE: {qual_score} pts\n"
    report += f"   - {qual_explanation}\n\n"
    
    report += f"2. RESEARCH PUBLICATIONS SCORE: {pub_score} pts\n"
    report += f"   - {pub_explanation}\n\n"
    
    report += f"3. TEACHING EXPERIENCE SCORE: {teach_score} pts\n"
    for line in teach_explanation.split("\n"):
        report += f"   - {line}\n"
    report += "\n"
    
    report += f"4. RESEARCH GUIDANCE SCORE: {rg_score} pts\n"
    for line in rg_explanation.split("\n"):
        report += f"   - {line}\n"
    report += "\n"
    
    report += f"5. PATENTS SCORE: {pat_score} pts\n"
    for line in pat_explanation.split("\n"):
        report += f"   - {line}\n"
    report += "\n"
    
    report += f"6. EXTRA-CURRICULAR SCORE: {ec_score} pts\n"
    for line in ec_explanation.split("\n"):
        report += f"   - {line}\n"
    report += "\n"
    
    report += f"------------------------------------------------------------\n"
    report += f"FINAL CALCULATED SCORE: {total_score} points\n"
    report += f"------------------------------------------------------------\n\n"
    
    # --- Append Extraction Confidence Scorecard block ---
    report += f"============================================================\n"
    report += f"EXTRACTION CONFIDENCE SCORECARD\n"
    report += f"============================================================\n"
    
    fields_to_print = [
        ("Candidate Name", "name"),
        ("Qualification", "qualification"),
        ("Teaching Experience", "teaching_experience"),
        ("Publications List", "publications"),
        ("Research Guidance", "research_guidance"),
        ("Patent Count", "patents")
    ]
    
    for label, key in fields_to_print:
        f_conf = confidence_scores.get(key, {})
        f_score = f_conf.get("score", 100)
        f_val = f_conf.get("extracted_value", "N/A")
        f_reason = f_conf.get("reason", "N/A")
        
        report += f"{label} Extraction:\n"
        report += f"  - Extracted Value: {f_val}\n"
        report += f"  - Confidence Score: {f_score}%\n"
        report += f"  - Reason: {f_reason}\n\n"
    
    return {
        "qualification_score": qual_score,
        "publication_score": pub_score,
        "teaching_score": teach_score,
        "research_guidance_score": rg_score,
        "patent_score": pat_score,
        "extracurricular_score": ec_score,
        "total_score": total_score,
        "explanation_report": report
    }
