import streamlit as st
import os
import tempfile
import json
import pandas as pd
import matplotlib.pyplot as plt

# Import project modules
from database.db_manager import (
    init_db, save_candidate_pipeline_results, get_all_candidates_ranked,
    get_candidate_details, clear_all_candidates, delete_candidate,
    update_candidate_scores
)
from parsers.file_extractor import parse_file
from parsers.info_extractor import parse_extracted_entities
from scoring.scoring_engine import calculate_candidate_score
from config import load_scoring_config, save_scoring_config
from exports.excel_generator import build_evaluation_excel, build_marking_scheme_excel, build_publications_workbook
from utils.logger import get_logger, get_buffered_logs, clear_buffered_logs

# Setup Logger
logger = get_logger()

# Set Streamlit Page Configuration
st.set_page_config(
    page_title="Faculty Resume Parser & Evaluation System",
    page_icon="🎓",
    layout="wide",
    initial_sidebar_state="expanded"
)

# Custom CSS for Premium Design Aesthetics
st.markdown("""
<style>
    /* Main Layout Styling */
    .main {
        background-color: #F8F9FA;
    }
    
    /* Title Styling */
    .title-text {
        font-family: 'Outfit', 'Inter', sans-serif;
        color: #1F497D;
        font-weight: 800;
        margin-bottom: 5px;
    }
    
    .subtitle-text {
        color: #5A6B82;
        font-size: 1.1rem;
        margin-bottom: 25px;
    }
    
    /* Premium Card Design */
    .metric-card {
        background: white;
        border-radius: 12px;
        padding: 20px;
        box-shadow: 0 4px 6px rgba(0, 0, 0, 0.05);
        border: 1px solid #E9ECEF;
        transition: transform 0.2s ease, box-shadow 0.2s ease;
    }
    
    .metric-card:hover {
        transform: translateY(-2px);
        box-shadow: 0 8px 12px rgba(0, 0, 0, 0.08);
        border-color: #D1D8E0;
    }
    
    .metric-card-header {
        font-size: 0.9rem;
        text-transform: uppercase;
        color: #8898AA;
        font-weight: bold;
        letter-spacing: 1px;
    }
    
    .metric-card-value {
        font-size: 2rem;
        font-weight: 700;
        color: #1F497D;
        margin-top: 5px;
    }
    
    /* Report Text Box */
    .report-box {
        background-color: #1E293B;
        color: #F8FAFC;
        font-family: 'Courier New', Courier, monospace;
        padding: 20px;
        border-radius: 8px;
        border-left: 5px solid #3B82F6;
        line-height: 1.5;
        overflow-x: auto;
    }
    
    /* Custom Sidebar Header */
    .sidebar-title {
        color: #1F497D;
        font-weight: 700;
        font-size: 1.2rem;
        margin-bottom: 15px;
        border-bottom: 2px solid #E9ECEF;
        padding-bottom: 8px;
    }
</style>
""", unsafe_allow_html=True)

# Initialize Database on Startup
if "db_initialized" not in st.session_state:
    try:
        init_db()
        st.session_state["db_initialized"] = True
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error(f"Failed to initialize SQLite Database: {e}")

# Load Configuration
scoring_config = load_scoring_config()

# --- SIDEBAR NAVIGATION ---
with st.sidebar:
    st.markdown('<div class="sidebar-title">🎓 Faculty Evaluation</div>', unsafe_allow_html=True)
    
    # Navigation Radio
    page = st.radio(
        "Navigate Menu",
        [
            "📤 Resume Upload",
            "🔍 Extracted Data",
            "📊 Candidate Scorecard",
            "📥 Excel Download",
            "🏆 Candidates Ranking",
            "⚙️ Processing Logs"
        ]
    )
    
    st.markdown("<br><hr>", unsafe_allow_html=True)
    
    # --- Live Scoring Configuration ---
    st.markdown('<div class="sidebar-title">⚙️ Configure Scoring</div>', unsafe_allow_html=True)
    with st.expander("Qualification Criteria"):
        grad_score = st.number_input("Graduate Degree Score", value=scoring_config["qualification"]["graduate"], min_value=0, max_value=20)
        pg_score = st.number_input("PG Degree Score", value=scoring_config["qualification"]["post_graduate"], min_value=0, max_value=20)
        phd_score = st.number_input("PhD Degree Score", value=scoring_config["qualification"]["phd"], min_value=0, max_value=20)
        
    with st.expander("Research Guidance Cap"):
        phd_per = st.number_input("PhD Scholar Guided (pts/each)", value=scoring_config["research_guidance"]["phd_score_per_student"], min_value=0.0, step=0.5)
        pg_per = st.number_input("PG Student Guided (pts/each)", value=scoring_config["research_guidance"]["pg_score_per_student"], min_value=0.0, step=0.5)
        proj_per = st.number_input("Research Project (pts/each)", value=scoring_config["research_guidance"]["project_score_per_project"], min_value=0.0, step=0.5)
        rg_max = st.number_input("Guidance Max Points", value=scoring_config["research_guidance"]["max_score"], min_value=0.0, step=1.0)
        
    with st.expander("Patents & Extra-Curricular"):
        pat_granted = st.number_input("Granted Patent (pts/each)", value=scoring_config["patents"]["granted_score"], min_value=0.0, step=0.5)
        pat_filed = st.number_input("Filed Patent (pts/each)", value=scoring_config["patents"]["filed_score"], min_value=0.0, step=0.5)
        pat_max = st.number_input("Patents Max Points", value=scoring_config["patents"]["max_score"], min_value=0.0, step=1.0)
        
        ec_per = st.number_input("Extra-Curricular (pts/each)", value=scoring_config["extra_curricular"]["score_per_activity"], min_value=0.0, step=0.5)
        ec_max = st.number_input("Extra-Curricular Max Points", value=scoring_config["extra_curricular"]["max_score"], min_value=0.0, step=1.0)
        
    if st.button("💾 Apply & Recalculate Scores"):
        # Compile new config
        new_config = {
            "qualification": {
                "graduate": grad_score,
                "post_graduate": pg_score,
                "phd": phd_score
            },
            "publications": scoring_config["publications"], # Preserve publication bands for now
            "teaching_experience": scoring_config["teaching_experience"], # Preserve teaching bands
            "research_guidance": {
                "phd_score_per_student": phd_per,
                "pg_score_per_student": pg_per,
                "project_score_per_project": proj_per,
                "max_score": rg_max
            },
            "patents": {
                "granted_score": pat_granted,
                "filed_score": pat_filed,
                "max_score": pat_max
            },
            "extra_curricular": {
                "score_per_activity": ec_per,
                "max_score": ec_max
            }
        }
        
        # Save to file
        save_scoring_config(new_config)
        logger.info("Scoring rules updated by user.")
        
        # Recalculate scores for all candidates in the database
        candidates = get_all_candidates_ranked()
        if candidates:
            with st.spinner("Recalculating scores for all candidates..."):
                recalculated_count = 0
                for cand in candidates:
                    cand_details = get_candidate_details(cand["id"])
                    if cand_details:
                        profile = cand_details["extracted_info"]
                        # Re-calculate
                        scores_info = calculate_candidate_score(profile, new_config)
                        # Update DB
                        update_candidate_scores(cand["id"], scores_info)
                        recalculated_count += 1
                logger.info(f"Recalculation complete. Updated scores for {recalculated_count} candidates.")
                st.sidebar.success(f"Config saved! Recalculated scores for {recalculated_count} candidates.")
        else:
            st.sidebar.success("Configuration saved! (No candidates in database to recalculate)")
            
# --- PAGE COMPONENT ROUTER ---

if page == "📤 Resume Upload":
    st.markdown('<h1 class="title-text">📤 Faculty Resume Upload</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle-text">Upload single or multiple academic resumes (PDF, DOCX, PNG, JPG, JPEG) to extract, score, and evaluate candidates.</p>', unsafe_allow_html=True)
    
    uploaded_files = st.file_uploader(
        "Choose CVs or Resumes",
        type=["pdf", "docx", "png", "jpg", "jpeg"],
        accept_multiple_files=True
    )
    
    if uploaded_files:
        st.info(f"{len(uploaded_files)} files selected for parsing.")
        
        if st.button("🚀 Process Candidates"):
            progress_bar = st.progress(0.0)
            status_text = st.empty()
            
            success_count = 0
            fail_count = 0
            
            for idx, uploaded_file in enumerate(uploaded_files):
                filename = uploaded_file.name
                status_text.text(f"Processing ({idx+1}/{len(uploaded_files)}): {filename}...")
                logger.info(f"--- Pipeline start for uploaded file: {filename} ---")
                
                try:
                    # Write to a temporary file
                    with tempfile.NamedTemporaryFile(delete=False, suffix=os.path.splitext(filename)[1]) as temp_file:
                        temp_file.write(uploaded_file.getbuffer())
                        temp_path = temp_file.name
                    
                    # 1. Text Extraction
                    parsed_payload = parse_file(temp_path, logger)
                    
                    # 2. Heuristic Entity Extraction
                    candidate_profile, sections = parse_extracted_entities(parsed_payload, logger)
                    
                    # 3. Calculate Scores & Explanations
                    scores_info = calculate_candidate_score(candidate_profile, scoring_config)
                    
                    # Assemble resume metadata
                    resume_meta = {
                        "filename": filename,
                        "file_type": parsed_payload["file_type"],
                        "raw_text": parsed_payload["raw_text"],
                        "sections": sections
                    }
                    
                    # 4. Save into DB
                    candidate_id = save_candidate_pipeline_results(candidate_profile, resume_meta, scores_info)
                    
                    # Clean up temp file
                    os.unlink(temp_path)
                    
                    success_count += 1
                    logger.info(f"Successfully processed and saved {filename} with Candidate ID: {candidate_id}")
                    
                except Exception as e:
                    fail_count += 1
                    logger.error(f"Error processing file {filename}: {e}")
                    import traceback
                    logger.error(traceback.format_exc())
                    st.error(f"Error processing {filename}: {e}")
                    
                # Update progress
                progress_bar.progress((idx + 1) / len(uploaded_files))
                
            progress_bar.empty()
            status_text.empty()
            
            if success_count > 0:
                st.success(f"Pipeline completed successfully. Processed: {success_count} files. Failed: {fail_count} files.")
            else:
                st.error("Failed to process uploaded resumes. Check 'Processing Logs' for details.")


elif page == "🔍 Extracted Data":
    st.markdown('<h1 class="title-text">🔍 Extracted Resume Data</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle-text">View the structured extraction schema and the parsed segment sections side-by-side.</p>', unsafe_allow_html=True)
    
    candidates = get_all_candidates_ranked()
    
    if not candidates:
        st.warning("No candidates found in database. Please upload resumes first.")
    else:
        # Candidate selection dropdown
        options = {c["id"]: f"{c['name']} ({c['filename']})" for c in candidates}
        selected_id = st.selectbox("Select Candidate to view details", options.keys(), format_func=lambda x: options[x])
        
        cand_detail = get_candidate_details(selected_id)
        
        if cand_detail:
            # Metadata Columns
            col1, col2, col3 = st.columns(3)
            with col1:
                st.text_input("Name", cand_detail["name"], disabled=True)
                st.text_input("Highest Qualification", cand_detail["highest_qualification"], disabled=True)
            with col2:
                st.text_input("Email", cand_detail.get("email", "Not Found"), disabled=True)
                st.text_input("Teaching Experience (Years)", f"{cand_detail['teaching_experience_years']} years", disabled=True)
            with col3:
                st.text_input("Phone Number", cand_detail.get("phone", "Not Found"), disabled=True)
                st.text_input("Parsed Resume Filename", cand_detail["filename"], disabled=True)
                
            st.write("---")
            
            # Sub-sections tabs
            tab_struct, tab_secs, tab_timeline, tab_raw = st.tabs(["Structured JSON Metadata", "Segmented Sections", "💼 Experience Timeline Debug Mode", "Raw Document Text"])
            
            with tab_struct:
                st.subheader("Extracted Features Payload")
                st.json(cand_detail["extracted_info"])
                
            with tab_secs:
                st.subheader("Segmented Document Sections")
                sections_dict = cand_detail["sections"]
                for sec_name, sec_text in sections_dict.items():
                    if sec_text.strip():
                        with st.expander(sec_name.replace("_", " ").title(), expanded=False):
                            st.write(sec_text)
                            
            with tab_timeline:
                st.subheader("💼 Experience Timeline Debug Mode")
                extracted_info = cand_detail.get("extracted_info", {})
                positions = extracted_info.get("positions", [])
                if not positions:
                    st.info("No timeline positions detected for this candidate.")
                else:
                    # Build a Pandas DataFrame to display beautifully
                    df_positions = pd.DataFrame(positions)
                    # Select and order columns
                    cols_show = ["designation", "organization", "start_date", "end_date", "duration_years", "classification"]
                    # Ensure all columns exist
                    for col in cols_show:
                        if col not in df_positions.columns:
                            df_positions[col] = "N/A"
                    
                    df_positions = df_positions[cols_show]
                    df_positions.columns = ["Designation", "Organization", "Start Date (YYYY-MM-DD)", "End Date (YYYY-MM-DD)", "Duration (Years)", "Classification"]
                    st.dataframe(df_positions, use_container_width=True, hide_index=True)
                            
            with tab_raw:
                st.subheader("Complete Extracted Text")
                st.text_area("Full text", cand_detail["raw_text"], height=400, disabled=True)


elif page == "📊 Candidate Scorecard":
    st.markdown('<h1 class="title-text">📊 Candidate Scorecard & Report</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle-text">View numerical scorecard breakdowns and explainable evaluation transcripts.</p>', unsafe_allow_html=True)
    
    candidates = get_all_candidates_ranked()
    
    if not candidates:
        st.warning("No candidates found in database. Please upload resumes first.")
    else:
        options = {c["id"]: f"{c['name']} (Score: {c['total_score']} | Rank: {c['rank']})" for c in candidates}
        selected_id = st.selectbox("Select Candidate Scorecard", options.keys(), format_func=lambda x: options[x])
        
        cand_detail = get_candidate_details(selected_id)
        
        if cand_detail:
            # Score Cards Row
            m_col1, m_col2, m_col3, m_col4 = st.columns(4)
            with m_col1:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-card-header">Total Score</div>
                    <div class="metric-card-value">{cand_detail['total_score']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m_col2:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-card-header">Academic Score</div>
                    <div class="metric-card-value">{cand_detail['qualification_score']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m_col3:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-card-header">Publications Score</div>
                    <div class="metric-card-value">{cand_detail['publication_score']}</div>
                </div>
                """, unsafe_allow_html=True)
            with m_col4:
                st.markdown(f"""
                <div class="metric-card">
                    <div class="metric-card-header">Teaching Score</div>
                    <div class="metric-card-value">{cand_detail['teaching_score']}</div>
                </div>
                """, unsafe_allow_html=True)
                
            st.markdown("<br>", unsafe_allow_html=True)
            
            # Guidance, Patents, Extra-curriculars
            s_col1, s_col2, s_col3 = st.columns(3)
            with s_col1:
                st.metric("Research Guidance Score", f"{cand_detail['research_guidance_score']} / {scoring_config['research_guidance']['max_score']}")
            with s_col2:
                st.metric("Patents Score", f"{cand_detail['patent_score']} / {scoring_config['patents']['max_score']}")
            with s_col3:
                st.metric("Extra-Curricular Score", f"{cand_detail['extracurricular_score']} / {scoring_config['extra_curricular']['max_score']}")
                
            st.markdown("<br><hr>", unsafe_allow_html=True)
            
            # Explainable AI report
            st.subheader("Explainable AI Evaluation Report")
            st.markdown(f'<div class="report-box"><pre>{cand_detail["explanation_report"]}</pre></div>', unsafe_allow_html=True)


elif page == "📥 Excel Download":
    st.markdown('<h1 class="title-text">📥 Excel Generation & Export</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle-text">Generate the master ranking sheet and individual scorecards inside a single, styled Excel workbook.</p>', unsafe_allow_html=True)
    
    candidates = get_all_candidates_ranked()
    
    if not candidates:
        st.warning("No candidates found in database. Please upload resumes first.")
    else:
        # Generate Excel Action
        excel_filename = "ranked_candidates.xlsx"
        
        with tempfile.TemporaryDirectory() as temp_dir:
            excel_path = os.path.join(temp_dir, excel_filename)
            
            if st.button("📊 Compile Excel Workbook"):
                try:
                    with st.spinner("Generating styled excel worksheet..."):
                        build_evaluation_excel(candidates, excel_path)
                        
                        # Read data to pass to downloader
                        with open(excel_path, "rb") as f:
                            bytes_data = f.read()
                            
                        st.success("Excel sheet generated successfully!")
                        st.download_button(
                            label="📥 Download ranked_candidates.xlsx",
                            data=bytes_data,
                            file_name=excel_filename,
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                except Exception as e:
                    logger.error(f"Excel generation failed: {e}")
                    st.error(f"Excel compile error: {e}")
                    
        st.write("---")

        # --- NEW: USSC Interview Marking Scheme (extracted data, not marks) ---
        st.subheader("🎓 USSC Interview Marking Scheme")
        st.caption(
            "Replicates the client's interview marking sheet, but fills the Qualification/Experience/Publication "
            "columns with data actually extracted from each resume, instead of pre-filling every candidate with the "
            "maximum possible marks. \"FDP / STTP Attended\" replaces the original \"Extra-Curricular Activities\" "
            "column - confirm this label with the client, it's a one-cell rename if wrong. Personal Interview and "
            "Total columns are left blank for the panel to complete live."
        )
        ms_col1, ms_col2, ms_col3 = st.columns(3)
        with ms_col1:
            ms_subject = st.text_input("Subject", value="Computer Engg.", key="ms_subject")
        with ms_col2:
            ms_post = st.text_input("Post", value="Professor", key="ms_post")
        with ms_col3:
            ms_date = st.text_input("Interview Date", value="", key="ms_date")

        with tempfile.TemporaryDirectory() as ms_temp_dir:
            ms_path = os.path.join(ms_temp_dir, "ussc_marking_scheme.xlsx")
            if st.button("📋 Compile Marking Scheme"):
                try:
                    with st.spinner("Building marking scheme with extracted candidate data..."):
                        build_marking_scheme_excel(candidates, ms_path, subject=ms_subject, post=ms_post, interview_date=ms_date)
                        with open(ms_path, "rb") as f:
                            ms_bytes = f.read()
                        st.success("Marking scheme generated!")
                        st.download_button(
                            label="📥 Download ussc_marking_scheme.xlsx",
                            data=ms_bytes,
                            file_name="ussc_marking_scheme.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                except Exception as e:
                    logger.error(f"Marking scheme generation failed: {e}")
                    st.error(f"Marking scheme compile error: {e}")

        st.write("---")

        # --- NEW: Publications Detail Workbook (one sheet per candidate) ---
        st.subheader("📚 Publications Detail Workbook")
        st.caption(
            "One workbook, one tab per candidate, matching the client's per-faculty publications template "
            "(Title / Journal Name / Published Under / Year / Impact Factor / SCOPUS INDEX). Rows the system "
            "couldn't verify against the resume text are highlighted, not hidden - double check those before relying on them."
        )
        with tempfile.TemporaryDirectory() as pub_temp_dir:
            pub_path = os.path.join(pub_temp_dir, "publications_detail.xlsx")
            if st.button("📚 Compile Publications Workbook"):
                try:
                    with st.spinner("Building per-candidate publications workbook..."):
                        build_publications_workbook(candidates, pub_path)
                        with open(pub_path, "rb") as f:
                            pub_bytes = f.read()
                        st.success("Publications workbook generated!")
                        st.download_button(
                            label="📥 Download publications_detail.xlsx",
                            data=pub_bytes,
                            file_name="publications_detail.xlsx",
                            mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                        )
                except Exception as e:
                    logger.error(f"Publications workbook generation failed: {e}")
                    st.error(f"Publications workbook compile error: {e}")

        st.write("---")
        
        # Danger zone: Delete candidates or reset database
        st.subheader("⚠️ Database Control Actions")
        
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("##### Remove Candidate")
            cand_to_delete = st.selectbox(
                "Select Candidate to Delete",
                options=[(c["id"], f"{c['name']} (ID: {c['id']})") for c in candidates],
                format_func=lambda x: x[1]
            )
            if st.button("🗑️ Delete Selected Candidate"):
                try:
                    delete_candidate(cand_to_delete[0])
                    logger.info(f"Deleted candidate with ID: {cand_to_delete[0]}")
                    st.success("Candidate deleted successfully!")
                    st.rerun()
                except Exception as e:
                    st.error(f"Delete failed: {e}")
        with c2:
            st.markdown("##### Full Database Reset")
            st.write("Deleting all candidates will clear all uploaded data, parsed resumes, evaluation records, and history from SQLite.")
            if st.button("🚨 Reset Database"):
                try:
                    clear_all_candidates()
                    logger.info("Database reset: Deleted all candidate profiles.")
                    st.success("Database reset successfully! All records cleared.")
                    st.rerun()
                except Exception as e:
                    st.error(f"Reset failed: {e}")


elif page == "🏆 Candidates Ranking":
    st.markdown('<h1 class="title-text">🏆 Candidate Ranking Dashboard</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle-text">Visualize recruitment scores, compare publications, and rank applicant CVs.</p>', unsafe_allow_html=True)
    
    candidates = get_all_candidates_ranked()
    
    if not candidates:
        st.warning("No candidates found in database. Please upload resumes first.")
    else:
        # Convert to Pandas DataFrame for analysis
        df = pd.DataFrame(candidates)
        
        # Selectable table display columns
        table_cols = [
            "rank", "name", "highest_qualification", "teaching_experience_years", 
            "total_experience_years", "research_experience_years", "administrative_experience_years",
            "qualification_score", "publication_score", "teaching_score", 
            "research_guidance_score", "patent_score", "extracurricular_score", "total_score"
        ]
        
        df_table = df[table_cols].copy()
        df_table.columns = [
            "Rank", "Name", "Highest Degree", "Teaching Exp (Yrs)", 
            "Total Exp (Yrs)", "Research Exp (Yrs)", "Admin Exp (Yrs)",
            "Qualification Pts", "Publications Pts", "Teaching Pts", 
            "Guidance Pts", "Patents Pts", "Extra-Curricular Pts", "Total Score"
        ]
        
        st.markdown("### 📋 Master Candidate Rankings")
        st.dataframe(df_table, hide_index=True, use_container_width=True)
        
        st.write("---")
        
        # Charts Row
        st.markdown("### 📊 Analytics & Visual Comparisons")
        c1, c2 = st.columns(2)
        
        with c1:
            st.write("**Total Evaluation Scores**")
            fig, ax = plt.subplots(figsize=(6, 4))
            df_sorted = df.sort_values("total_score", ascending=True)
            ax.barh(df_sorted["name"], df_sorted["total_score"], color="#1F497D")
            ax.set_xlabel("Total Score")
            ax.set_title("Candidate Scores comparison")
            plt.tight_layout()
            st.pyplot(fig)
            
        with c2:
            st.write("**Publications vs. Experience**")
            # Build scatter plot
            # Extract total publications count
            from database.db_manager import get_candidate_details
            pub_counts = []
            exp_years = []
            names = []
            for cand in candidates:
                details = get_candidate_details(cand["id"])
                if details:
                    pub_counts.append(details["extracted_info"]["publications"]["total_publications"])
                    exp_years.append(details["teaching_experience_years"])
                    names.append(details["name"])
                    
            fig2, ax2 = plt.subplots(figsize=(6, 4))
            ax2.scatter(exp_years, pub_counts, s=150, color="#E06666", edgecolors="#990000", alpha=0.8)
            
            # Annotate points
            for idx, txt in enumerate(names):
                ax2.annotate(txt, (exp_years[idx], pub_counts[idx]), xytext=(5, 5), textcoords="offset points")
                
            ax2.set_xlabel("Teaching Experience (Years)")
            ax2.set_ylabel("Total Publications Count")
            ax2.set_title("Experience vs. Publication Productivity")
            plt.tight_layout()
            st.pyplot(fig2)


elif page == "⚙️ Processing Logs":
    st.markdown('<h1 class="title-text">⚙️ System Processing Logs</h1>', unsafe_allow_html=True)
    st.markdown('<p class="subtitle-text">Real-time compilation logs for extraction, section parsing, scoring computations, and errors.</p>', unsafe_allow_html=True)
    
    logs = get_buffered_logs()
    
    # Text container for logs
    log_text = "\n".join(logs) if logs else "No system logs generated yet."
    
    st.text_area("Log Console Output", log_text, height=450, disabled=True)
    
    c1, c2 = st.columns([1, 6])
    with c1:
        if st.button("🗑️ Clear Log Buffer"):
            clear_buffered_logs()
            st.success("Logs cleared!")
            st.rerun()
