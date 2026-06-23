import os
import json

CONFIG_FILE_PATH = os.path.join(os.path.dirname(__file__), "scoring_config.json")

DEFAULT_CONFIG = {
    "qualification": {
        "graduate": 5,
        "post_graduate": 10,
        "phd": 10
    },
    "publications": {
        "bands": [
            {"min": 0, "max": 2, "score": 2},
            {"min": 3, "max": 5, "score": 5},
            {"min": 6, "max": 10, "score": 8},
            {"min": 11, "max": 9999, "score": 10}
        ]
    },
    "teaching_experience": {
        "bands": [
            {"min": 0, "max": 2.0, "score": 1},
            {"min": 2.0, "max": 5.0, "score": 3},
            {"min": 5.0, "max": 99.0, "score": 5}
        ]
    },
    "research_guidance": {
        "phd_score_per_student": 2.0,
        "pg_score_per_student": 1.0,
        "project_score_per_project": 1.0,
        "max_score": 10.0
    },
    "patents": {
        "granted_score": 5.0,
        "filed_score": 2.0,
        "max_score": 10.0
    },
    "extra_curricular": {
        "score_per_activity": 1.0,
        "max_score": 5.0
    }
}

def load_scoring_config():
    """Loads the scoring rules from scoring_config.json, or creates it if not found."""
    if not os.path.exists(CONFIG_FILE_PATH):
        save_scoring_config(DEFAULT_CONFIG)
        return DEFAULT_CONFIG
    try:
        with open(CONFIG_FILE_PATH, "r") as f:
            return json.load(f)
    except Exception as e:
        print(f"Error loading scoring configuration: {e}. Using defaults.")
        return DEFAULT_CONFIG

def save_scoring_config(config):
    """Saves the scoring rules to scoring_config.json."""
    try:
        with open(CONFIG_FILE_PATH, "w") as f:
            json.dump(config, f, indent=2)
        return True
    except Exception as e:
        print(f"Error saving scoring configuration: {e}")
        return False
