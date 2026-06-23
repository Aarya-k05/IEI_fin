import sqlite3
import os
import json

DB_FILE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), "faculty_evaluator.db")

def get_connection():
    """Returns a SQLite connection with foreign keys enabled."""
    conn = sqlite3.connect(DB_FILE_PATH)
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    """Initializes the SQLite database with correct schemas."""
    conn = get_connection()
    cursor = conn.cursor()
    
    # Table 1: candidates
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        email TEXT,
        phone TEXT,
        highest_qualification TEXT,
        teaching_experience_years REAL DEFAULT 0,
        total_experience_years REAL DEFAULT 0,
        research_experience_years REAL DEFAULT 0,
        administrative_experience_years REAL DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    );
    """)
    
    # Ensure new experience columns exist if database was already initialized
    for col in ["total_experience_years", "research_experience_years", "administrative_experience_years"]:
        try:
            cursor.execute(f"ALTER TABLE candidates ADD COLUMN {col} REAL DEFAULT 0;")
        except sqlite3.OperationalError:
            pass
            
    # Table 2: resume_data
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS resume_data (
        candidate_id INTEGER PRIMARY KEY,
        filename TEXT NOT NULL,
        file_type TEXT NOT NULL,
        raw_text TEXT,
        sections_json TEXT,       -- Stores segmented document sections (JSON)
        extracted_info_json TEXT, -- Stores parsed resume details (JSON)
        FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
    );
    """)
    
    # Table 3: scores
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS scores (
        candidate_id INTEGER PRIMARY KEY,
        qualification_score REAL DEFAULT 0,
        publication_score REAL DEFAULT 0,
        research_guidance_score REAL DEFAULT 0,
        teaching_score REAL DEFAULT 0,
        patent_score REAL DEFAULT 0,
        extracurricular_score REAL DEFAULT 0,
        total_score REAL DEFAULT 0,
        explanation_report TEXT,
        FOREIGN KEY(candidate_id) REFERENCES candidates(id) ON DELETE CASCADE
    );
    """)
    
    conn.commit()
    conn.close()

def save_candidate_pipeline_results(candidate_info, resume_meta, scores_info):
    """
    Saves candidate info, resume details, and scores into the database in a single transaction.
    Returns the created candidate_id.
    """
    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Insert into candidates
        cursor.execute("""
        INSERT INTO candidates (
            name, email, phone, highest_qualification, teaching_experience_years,
            total_experience_years, research_experience_years, administrative_experience_years
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            candidate_info.get("name", "Unknown"),
            candidate_info.get("email"),
            candidate_info.get("phone"),
            candidate_info.get("highest_qualification"),
            candidate_info.get("teaching_experience_years", 0.0), # Academic Teaching
            candidate_info.get("total_experience_years", 0.0),
            candidate_info.get("research_experience_years", 0.0),
            candidate_info.get("administrative_experience_years", 0.0)
        ))
        candidate_id = cursor.lastrowid
        
        # 2. Insert into resume_data
        sections_str = json.dumps(resume_meta.get("sections", {}))
        extracted_info_str = json.dumps(candidate_info)
        cursor.execute("""
        INSERT INTO resume_data (candidate_id, filename, file_type, raw_text, sections_json, extracted_info_json)
        VALUES (?, ?, ?, ?, ?, ?)
        """, (
            candidate_id,
            resume_meta.get("filename", "unknown_file"),
            resume_meta.get("file_type", "unknown_type"),
            resume_meta.get("raw_text", ""),
            sections_str,
            extracted_info_str
        ))
        
        # 3. Insert into scores
        cursor.execute("""
        INSERT INTO scores (
            candidate_id, qualification_score, publication_score, 
            research_guidance_score, teaching_score, patent_score, 
            extracurricular_score, total_score, explanation_report
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            candidate_id,
            scores_info.get("qualification_score", 0.0),
            scores_info.get("publication_score", 0.0),
            scores_info.get("research_guidance_score", 0.0),
            scores_info.get("teaching_score", 0.0),
            scores_info.get("patent_score", 0.0),
            scores_info.get("extracurricular_score", 0.0),
            scores_info.get("total_score", 0.0),
            scores_info.get("explanation_report", "")
        ))
        
        conn.commit()
        return candidate_id
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def get_candidate_details(candidate_id):
    """Retrieves full details of a single candidate by ID, joining all three tables."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT c.*, r.filename, r.file_type, r.raw_text, r.sections_json, r.extracted_info_json,
           s.qualification_score, s.publication_score, s.research_guidance_score,
           s.teaching_score, s.patent_score, s.extracurricular_score, s.total_score, s.explanation_report
    FROM candidates c
    JOIN resume_data r ON c.id = r.candidate_id
    JOIN scores s ON c.id = s.candidate_id
    WHERE c.id = ?
    """, (candidate_id,))
    row = cursor.fetchone()
    conn.close()
    
    if row:
        data = dict(row)
        data["sections"] = json.loads(data["sections_json"]) if data["sections_json"] else {}
        data["extracted_info"] = json.loads(data["extracted_info_json"]) if data["extracted_info_json"] else {}
        return data
    return None

def get_all_candidates_ranked():
    """Retrieves all candidates, sorted by total_score descending, and adds ranks."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("""
    SELECT c.id, c.name, c.email, c.phone, c.highest_qualification, 
           c.teaching_experience_years, c.total_experience_years, 
           c.research_experience_years, c.administrative_experience_years,
           r.filename,
           s.qualification_score, s.publication_score, s.research_guidance_score,
           s.teaching_score, s.patent_score, s.extracurricular_score, s.total_score
    FROM candidates c
    JOIN resume_data r ON c.id = r.candidate_id
    JOIN scores s ON c.id = s.candidate_id
    ORDER BY s.total_score DESC, c.name ASC
    """)
    rows = cursor.fetchall()
    conn.close()
    
    candidates = []
    for idx, row in enumerate(rows):
        d = dict(row)
        d["rank"] = idx + 1
        candidates.append(d)
    return candidates

def delete_candidate(candidate_id):
    """Deletes a candidate by ID. Cascades automatically delete scores and resume_data."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM candidates WHERE id = ?", (candidate_id,))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def clear_all_candidates():
    """Clears all tables in the database."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("DELETE FROM candidates;")
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()

def update_candidate_scores(candidate_id, scores_info):
    """Updates the score vectors and explanation report for a candidate."""
    conn = get_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
        UPDATE scores SET
            qualification_score = ?,
            publication_score = ?,
            research_guidance_score = ?,
            teaching_score = ?,
            patent_score = ?,
            extracurricular_score = ?,
            total_score = ?,
            explanation_report = ?
        WHERE candidate_id = ?
        """, (
            scores_info.get("qualification_score", 0.0),
            scores_info.get("publication_score", 0.0),
            scores_info.get("research_guidance_score", 0.0),
            scores_info.get("teaching_score", 0.0),
            scores_info.get("patent_score", 0.0),
            scores_info.get("extracurricular_score", 0.0),
            scores_info.get("total_score", 0.0),
            scores_info.get("explanation_report", ""),
            candidate_id
        ))
        conn.commit()
        return True
    except Exception as e:
        conn.rollback()
        raise e
    finally:
        conn.close()
