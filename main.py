#!/usr/bin/env python3

import subprocess
import json
import os
import math
import re
from datetime import datetime
import requests

# Cloudflare D1 Database Functions
def setup_wrangler():
    """Setup and authenticate wrangler"""
    try:
        # Logout first to ensure clean state
        subprocess.run(["npx", "wrangler", "auth", "logout"], capture_output=True, text=True)

        # Login to wrangler
        print("Please authenticate with Cloudflare Wrangler...")
        result = subprocess.run(["npx", "wrangler", "auth", "login"], capture_output=False, text=True)

        if result.returncode != 0:
            print("Wrangler authentication failed. Please try again.")
            return False

        return True
    except Exception as e:
        print(f"Error setting up wrangler: {e}")
        return False

def create_database_schema():
    """Create the patient test results and API keys table schema"""
    schema_sql = """
    CREATE TABLE IF NOT EXISTS patient_test_results (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        patient_id TEXT NOT NULL,
        test_name TEXT NOT NULL,
        test_value TEXT NOT NULL,
        test_date TEXT NOT NULL,
        status TEXT,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(patient_id, test_name, test_date, created_at)
    );

    CREATE TABLE IF NOT EXISTS api_keys (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_name TEXT NOT NULL UNIQUE,
        key_value TEXT NOT NULL,
        created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """

    try:
        # Write schema to temporary file
        with open('schema.sql', 'w') as f:
            f.write(schema_sql)

        # Execute the schema creation
        result = subprocess.run([
            "npx", "wrangler", "d1", "execute", "patient-db",
            "--file=schema.sql"
        ], capture_output=True, text=True)

        # Clean up
        if os.path.exists('schema.sql'):
            os.remove('schema.sql')

        if result.returncode != 0:
            print(f"Error creating database schema: {result.stderr}")
            return False

        print("Database schema created successfully")
        return True

    except Exception as e:
        print(f"Error creating database schema: {e}")
        return False

def insert_test_results(patient_id, test_results, test_date):
    """Insert test results for a patient"""
    try:
        # Patient IDs are now stored in lowercase, so no need to convert
        # patient_id = patient_id.lower()
        # Create a batch insert SQL
        values = []
        for row in test_results:
            if len(row) >= 4:
                test_name, test_value, unit, _status = row[:4]
            else:
                test_name, test_value, unit = row[:3]
            # Combine value and unit
            full_value = f"{test_value} {unit}"
            # Escape single quotes for SQL
            safe_patient_id = patient_id.replace("'", "''")
            safe_test_name = test_name.replace("'", "''")
            safe_full_value = full_value.replace("'", "''")
            safe_test_date = test_date.replace("'", "''")
            values.append(f"('{safe_patient_id}', '{safe_test_name}', '{safe_full_value}', '{safe_test_date}')")

        if not values:
            return False

        batch_sql = f"""
        INSERT OR REPLACE INTO patient_test_results
        (patient_id, test_name, test_value, test_date)
        VALUES {','.join(values)};
        """

        # Write to temporary file
        with open('insert.sql', 'w') as f:
            f.write(batch_sql)

        # Execute the insert
        result = subprocess.run([
            "npx", "wrangler", "d1", "execute", "patient-db",
            "--file=insert.sql"
        ], capture_output=True, text=True)

        # Clean up
        if os.path.exists('insert.sql'):
            os.remove('insert.sql')

        if result.returncode != 0:
            print(f"Error inserting test results: {result.stderr}")
            return False

        print(f"Successfully inserted {len(test_results)} test results for patient {patient_id}")
        return True

    except Exception as e:
        print(f"Error inserting test results: {e}")
        return False

def get_patient_test_history(patient_id):
    """Get all test results for a patient, grouped by test date"""
    try:
        # Patient IDs are now stored in lowercase, so no need to convert
        # patient_id = patient_id.lower()
        # Query to get all test results for the patient
        query = f"""
        SELECT test_name, test_value, test_date, created_at
        FROM patient_test_results
        WHERE patient_id = '{patient_id}'
        ORDER BY test_date DESC, created_at DESC;
        """

        # Execute the query
        result = subprocess.run([
            "npx", "wrangler", "d1", "execute", "patient-db",
            "--command", query
        ], capture_output=True, text=True)

        if result.returncode != 0:
            print(f"Error querying patient history: {result.stderr}")
            return {}

        # Parse the JSON result
        try:
            output_lines = result.stdout.strip().split('\n')
            # Find the JSON part (usually after some wrangler output)
            json_start = -1
            for i, line in enumerate(output_lines):
                if line.strip().startswith('[') or line.strip().startswith('{'):
                    json_start = i
                    break

            if json_start == -1:
                return {}

            json_data = '\n'.join(output_lines[json_start:])
            data = json.loads(json_data)

            # Group by test date
            history_by_date = {}
            for row in data:
                test_date = row['test_date']
                if test_date not in history_by_date:
                    history_by_date[test_date] = []
                history_by_date[test_date].append({
                    'test_name': row['test_name'],
                    'test_value': row['test_value'],
                    'created_at': row['created_at']
                })

            return history_by_date

        except json.JSONDecodeError:
            print("Error parsing database response")
            return {}

    except Exception as e:
        print(f"Error retrieving patient history: {e}")
        return {}

def store_api_key_http(api_base, key_name, key_value):
    """Store an API key in the database using HTTP API"""
    try:
        response = requests.post(f"{api_base}/api/api-keys", json={
            "key_name": key_name,
            "key_value": key_value
        }, timeout=30)

        response.raise_for_status()
        data = response.json()

        if data.get('success'):
            print(f"Successfully stored API key: {key_name}")
            return True
        else:
            print(f"Error storing API key: {data}")
            return False

    except requests.exceptions.RequestException as e:
        print(f"Error storing API key via HTTP: {e}")
        return False
    except Exception as e:
        print(f"Error storing API key: {e}")
        return False

def get_api_key_http(api_base, key_name):
    """Retrieve an API key from the database using HTTP API"""
    try:
        response = requests.get(f"{api_base}/api/api-keys?key_name={key_name}", timeout=30)
        response.raise_for_status()
        data = response.json()

        if data and 'key_value' in data:
            return data['key_value']
        else:
            return None

    except requests.exceptions.RequestException as e:
        print(f"Error retrieving API key via HTTP: {e}")
        return None
    except Exception as e:
        print(f"Error retrieving API key: {e}")
        return None

def format_comparative_results(old_results, new_results, old_date, new_date):
    """Format results showing old vs new values"""
    all_test_names = set()

    # Collect all test names
    for row in old_results:
        if not row:
            continue
        all_test_names.add(row[0])
    for row in new_results:
        if not row:
            continue
        all_test_names.add(row[0])

    # Create comparative data
    comparative_data = []
    for test_name in sorted(all_test_names):
        # Find old value
        old_value = "-"
        for row in old_results:
            if not row:
                continue
            name, value, unit = row[:3]
            if name == test_name:
                old_value = f"{value} {unit}"
                break

        # Find new value
        new_value = "-"
        for row in new_results:
            if not row:
                continue
            name, value, unit = row[:3]
            if name == test_name:
                new_value = f"{value} {unit}"
                break

        comparative_data.append((test_name, old_value, new_value))

    return comparative_data

# HTTP API Functions for Database Operations
def insert_test_results_http(api_base, patient_id, test_results, test_date, status_map=None):
    """Insert test results using HTTP API, storing status tags when available."""
    # Patient IDs are now stored in lowercase, so no need to convert
    # patient_id = patient_id.lower()
    # Convert test results to API format
    api_data = []
    for row in test_results:
        if len(row) >= 4:
            test_name, test_value, unit, _status = row[:4]
        else:
            test_name, test_value, unit = row[:3]
        status_tag = None
        if status_map:
            status_tag = status_map.get(normalize_test_name(test_name)) or status_map.get(test_name.lower())
        api_data.append({
            "test_name": test_name,
            "test_value": f"{test_value} {unit}",
            "test_date": test_date,
            "status": status_tag
        })

    try:
        response = requests.post(f"{api_base}/api/test-results?patientId={patient_id}", json=api_data, timeout=30)
        response.raise_for_status()
        return response.json()
    except Exception as e:
        print(f"Error storing test results: {e}")
        return None

def get_patient_history_http(api_base, patient_id):
    """Get patient history using HTTP API"""
    try:
        # Patient IDs are now stored in lowercase, so no need to convert
        # patient_id = patient_id.lower()
        response = requests.get(f"{api_base}/api/patient-history?patientId={patient_id}", timeout=30)
        response.raise_for_status()
        data = response.json()
        return data.get('history', {})
    except Exception as e:
        print(f"Error retrieving patient history: {e}")
        return {}

def check_existing_patient(api_base, patient_id):
    """Check if patient exists and display their details"""
    try:
        # Patient IDs are now stored in lowercase, so no need to convert
        # patient_id = patient_id.lower()
        # First, try to get comparative results (multiple sessions)
        response = requests.get(f"{api_base}/api/comparative-results?patientId={patient_id}", timeout=30)
        response.raise_for_status()
        data = response.json()

        comparative_data = data.get('comparativeData', [])
        sessions = data.get('sessions', [])
        session_count = data.get('sessionCount', 0)

        if session_count > 0 and comparative_data:
            print(f"\n{'='*60}")
            print(f"EXISTING PATIENT FOUND: {patient_id}")
            print(f"{'='*60}")
            print(f"Total test sessions: {session_count}")
            print(f"Session dates: {', '.join(sessions)}")
            print("\nRecent test results:")

            # Show up to 5 recent metrics
            for i, metric in enumerate(comparative_data[:5]):
                print(f"  {i+1}. {metric['test_name']}: {metric.get('value_1', 'N/A')}")

            if len(comparative_data) > 5:
                print(f"  ... and {len(comparative_data) - 5} more metrics")

            print(f"{'='*60}")
            return True

        # If no comparative data, check for patient history (single session)
        patient_history = get_patient_history_http(api_base, patient_id)
        if patient_history and len(patient_history) > 0:
            print(f"\n{'='*60}")
            print(f"EXISTING PATIENT FOUND: {patient_id}")
            print(f"{'='*60}")
            print(f"Patient has {len(patient_history)} test result(s) from single session")

            # Show some recent results
            history_items = list(patient_history.items())[:5]
            for i, (test_name, test_data) in enumerate(history_items):
                if isinstance(test_data, dict):
                    value = test_data.get('value', 'N/A')
                    unit = test_data.get('unit', '')
                    print(f"  {i+1}. {test_name}: {value} {unit}")
                else:
                    print(f"  {i+1}. {test_name}: {test_data}")

            if len(patient_history) > 5:
                print(f"  ... and {len(patient_history) - 5} more metrics")

            print(f"{'='*60}")
            return True

        print(f"\nPatient ID '{patient_id}' not found in database.")
        return False

    except requests.exceptions.RequestException as e:
        if "404" in str(e) or "not found" in str(e).lower():
            print(f"\nPatient ID '{patient_id}' not found in database.")
            return False
        else:
            print(f"Error checking patient data: {e}")
            return False

def get_comparative_results_http(api_base, patient_id):
    """Get comparative results using HTTP API"""
    try:
        # Normalize patient ID to lowercase
        patient_id = patient_id.lower()
        response = requests.get(f"{api_base}/api/comparative-results?patientId={patient_id}", timeout=30)
        response.raise_for_status()
        data = response.json()
        # Return full response data including sessions, sessionCount, and comparativeData
        return data
    except Exception as e:
        print(f"Error retrieving comparative results: {e}")
        return {}
"""
PDF Header and Footer Adder

This script adds custom headers and footers to every page of a PDF file.
"""

import sys
from pathlib import Path
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter, A4
from reportlab.lib.units import inch
from reportlab.lib.colors import black, gray, Color
from reportlab.pdfbase import pdfdoc
from reportlab.pdfbase.pdfdoc import PDFDictionary, PDFName
import requests
import json
import time
from io import BytesIO


def add_header_footer_to_pdf(input_path, output_path, header_left_text="", header_right_text="", footer_gradient=None, header_gradient=None, font_size=10):
    """
    Add header and footer to every page of a PDF.

    Args:
        input_path: Path to input PDF file
        output_path: Path to output PDF file
        header_text: Text for header
        footer_text: Text for footer
        font_size: Font size for header/footer
    """
    print(f"Processing PDF: {input_path}")

    # Read the input PDF
    reader = PdfReader(input_path)
    writer = PdfWriter()

    # Process each page
    for page_num, page in enumerate(reader.pages):
        print(f"Processing page {page_num + 1}/{len(reader.pages)}")

        # Leave first page untouched (no overlay) so input page 1 == output page 1
        if page_num == 0:
            writer.add_page(page)
            continue

        # Get page dimensions
        page_width = float(page.mediabox.width)
        page_height = float(page.mediabox.height)

        # Create header/footer overlay for subsequent pages only
        overlay_buffer = create_header_footer_canvas(
            page_width, page_height, header_left_text, header_right_text, footer_gradient, header_gradient, font_size, False
        )

        # Read the overlay PDF
        overlay_reader = PdfReader(overlay_buffer)
        overlay_page = overlay_reader.pages[0]

        # Merge the overlay with the original page
        page.merge_page(overlay_page)

        # Add the modified page to the writer
        writer.add_page(page)

    # Save the output PDF
    with open(output_path, "wb") as output_file:
        writer.write(output_file)

    print(f"PDF saved to: {output_path}")
    print(f"Processed {len(reader.pages)} pages")


def extract_text_from_pdf(pdf_path):
    """Extract all text from PDF file."""
    reader = PdfReader(pdf_path)
    text = ""
    for page in reader.pages:
        text += page.extract_text() + "\n"
    return text.strip()

def extract_text_from_first_page(pdf_path):
    """Extract text from only the first page of PDF file."""
    reader = PdfReader(pdf_path)
    if len(reader.pages) > 0:
        return reader.pages[0].extract_text().strip()
    return ""

def extract_health_score(summary_text):
    """Extract health score from Gemini summary or calculate based on content."""
    # Try to find a score in the text (if Gemini provides one)
    import re
    score_match = re.search(r'health score[:\s]*(\d+)', summary_text, re.IGNORECASE)
    if score_match:
        return int(score_match.group(1))

    # If no explicit score, calculate based on content
    score = 100  # Start with perfect score

    # Deduct points for various issues found in the text
    if "elevated" in summary_text.lower() or "high" in summary_text.lower():
        score -= 15
    if "low" in summary_text.lower() or "deficiency" in summary_text.lower():
        score -= 10
    if "risk" in summary_text.lower():
        score -= 10
    if "abnormal" in summary_text.lower():
        score -= 5
    if "concern" in summary_text.lower():
        score -= 5

    # Ensure score is between 0 and 100
    return max(0, min(100, score))

def wrap_text_with_markdown(text, canvas, max_width):
    """Wrap text while preserving markdown formatting (*text* patterns), handling bold across lines."""
    import re

    # Parse text into parts: list of (part_text, is_bold)
    parts = []
    bold_pattern = r'\*+(.+?)\*+'
    matches = list(re.finditer(bold_pattern, text))
    last_end = 0
    for match in matches:
        # Normal text before
        if match.start() > last_end:
            parts.append((text[last_end:match.start()], False))
        # Bold text
        parts.append((match.group(1), True))
        last_end = match.end()
    # Remaining normal text
    if last_end < len(text):
        parts.append((text[last_end:], False))

    # Now wrap the parts
    lines = []
    current_line_parts = []
    current_width = 0

    for part_text, is_bold in parts:
        words = part_text.split()
        for word in words:
            font = "Helvetica-Bold" if is_bold else "Helvetica"
            word_width = canvas.stringWidth(word, font, 14.5 if not is_bold else 15.5)
            space_width = canvas.stringWidth(" ", font, 14.5 if not is_bold else 15.5) if current_line_parts else 0

            if current_width + space_width + word_width <= max_width:
                if current_line_parts:
                    current_line_parts.append((" ", is_bold))  # Space with same bold
                current_line_parts.append((word, is_bold))
                current_width += space_width + word_width
            else:
                # Start new line
                if current_line_parts:
                    lines.append(current_line_parts)
                    current_line_parts = []
                    current_width = 0
                # Add word to new line
                current_line_parts.append((word, is_bold))
                current_width = word_width

    if current_line_parts:
        lines.append(current_line_parts)

    # Convert lines back to strings with markdown for compatibility
    wrapped_lines = []
    for line_parts in lines:
        line_text = ""
        for part, is_bold in line_parts:
            if is_bold:
                line_text += f"*{part}*"
            else:
                line_text += part
        wrapped_lines.append(line_text)

    return wrapped_lines

def render_text_with_bold(canvas, text, x, y):
    """Render text with support for *bold* or **bold** markdown formatting."""
    import re

    # Set default font
    canvas.setFont("Times-Roman", 14.5)
    canvas.setFillColor(Color(0.4, 0.0, 0.0))  # Darker, readable red

    # Find all bold patterns - handle both *text* and **text** formats
    bold_pattern = r'\*+(.+?)\*+'
    parts = re.split(bold_pattern, text)

    current_x = x

    for i, part in enumerate(parts):
        if i % 2 == 1:  # Odd indices are the captured bold text
            # Render bold text
            canvas.setFont("Helvetica-Bold", 15.5)
            canvas.drawString(current_x, y, part)
            current_x += canvas.stringWidth(part, "Helvetica-Bold", 15.5)
        else:
            # Render normal text
            canvas.setFont("Helvetica", 14.5)
            canvas.drawString(current_x, y, part)
            current_x += canvas.stringWidth(part, "Helvetica", 14.5)

def extract_alarming_summary_from_gemini(summary_text):
    """Extract the alarming summary from Gemini's response using strict format markers."""
    if not summary_text:
        return "Your test results reveal multiple concerning health indicators that demand immediate attention. Early intervention is essential to prevent serious complications. Please consult your healthcare provider urgently to address these critical findings."

    # Look for the strict format markers
    start_marker = "**ALARMING_PATIENT_SUMMARY_START**"
    end_marker = "**ALARMING_PATIENT_SUMMARY_END**"

    start_idx = summary_text.find(start_marker)
    end_idx = summary_text.find(end_marker)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        # Extract the content between the markers
        summary_start = start_idx + len(start_marker)
        alarming_text = summary_text[summary_start:end_idx].strip()
        print(f"DEBUG: Extracted alarming summary from markers: {alarming_text[:100]}...")
        return alarming_text

    # Fallback: Look for the old format if markers aren't found
    print("DEBUG: Strict markers not found, trying fallback extraction")
    lines = summary_text.split('\n')
    alarming_section = []
    in_alarming_section = False

    for line in lines:
        line = line.strip()
        if '**Alarming Patient Summary:**' in line or 'Alarming Patient Summary:' in line or '**Alarming Summary:**' in line:
            in_alarming_section = True
            continue
        elif in_alarming_section and (line.startswith('**') or line.startswith('#') or (not line and alarming_section)):
            # Stop if we hit another section header or empty line after starting
            break
        elif in_alarming_section and line:
            alarming_section.append(line)

    if alarming_section:
        print(f"DEBUG: Extracted alarming summary from fallback: {' '.join(alarming_section)[:100]}...")
        return '\n'.join(alarming_section)
    else:
        # Fallback: try to find any section that mentions "alarming" or similar
        for line in lines:
            if 'alarming' in line.lower() and ('summary' in line.lower() or 'patient' in line.lower()):
                # Return the next few lines
                idx = lines.index(line)
                return '\n'.join(lines[idx+1:idx+4])  # Skip the header line

    print("DEBUG: No alarming summary found, using default")
    # Final fallback
    return "Your test results reveal multiple concerning health indicators that demand immediate attention. Early intervention is essential to prevent serious complications. Please consult your healthcare provider urgently to address these critical findings."


def extract_all_test_results_from_gemini(gemini_response):
    """Extract all test results from Gemini's structured response (supports optional status and range columns)."""
    import re
    all_tests = []

    # Find the test results section (after the alarming summary)
    start_marker = "**ALL_TEST_RESULTS_START**"
    end_marker = "**ALL_TEST_RESULTS_END**"

    start_idx = gemini_response.find(start_marker)
    end_idx = gemini_response.find(end_marker)

    if start_idx != -1 and end_idx != -1 and end_idx > start_idx:
        # Extract the content between markers
        results_text = gemini_response[start_idx + len(start_marker):end_idx].strip()

        # Parse each line
        lines = results_text.split('\n')
        for line in lines:
            line = line.strip()
            if line and '|' in line:
                parts = [part.strip() for part in line.split('|')]
                if len(parts) >= 5:
                    test_name, value, unit, status, normal_range = parts[:5]
                    if test_name and value and unit and status:
                        all_tests.append((test_name, value, unit, status, normal_range))
                elif len(parts) == 4:
                    test_name, value, unit, status = parts
                    if test_name and value and unit and status:
                        all_tests.append((test_name, value, unit, status))
                elif len(parts) == 3:
                    test_name, value, unit = parts
                    if test_name and value and unit:
                        all_tests.append((test_name, value, unit))

    return all_tests

def extract_all_test_results_from_text(text):
    """Extract all test results from PDF text for console output."""
    import re
    all_tests = []

    # Extract test results using the original working pattern
    lines = text.split('\n')

    for line in lines:
        line = line.strip()
        if not line:
            continue

        # Skip header and non-data lines
        skip_keywords = ['name', 'date', 'test asked', 'report status', 'processed at', 'plot', 'aarogyam',
                        'tests done', 'home collection', 'referred by', 'patient name', 'report availability',
                        'note', 'test details', 'report status', 'ready', 'processing', 'cancelled']
        if any(keyword in line.lower() for keyword in skip_keywords):
            continue

        # Match the format: VALUE TEST_NAME RANGE UNIT
        # Pattern: number, then text, then range (numbers-numbers or number-number), then unit
        match = re.match(r'^([\d.]+)\s+([A-Z\s\(\)-]+?)\s+([\d.-]+(?:\s*-\s*[\d.]+)?)\s*([A-Za-z%/µ]+(?:\s*10[³^6]\s*/\s*µL|\s*10³\s*/\s*µL|\s*10\^\d/\w+|\s*/\s*\w+)*)', line)
        if match:
            value, test_name, range_val, unit = match.groups()
            test_name = test_name.strip()
            unit = unit.strip()

            # Clean up the test name (remove extra spaces, title case)
            test_name = re.sub(r'\s+', ' ', test_name).title()

            # Skip if test name is too short or contains non-medical terms
            if (len(test_name) < 3 or
                any(term in test_name.lower() for term in ['page', 'date', 'lab', 'code', 'sex', 'age']) or
                not any(keyword in test_name.lower() for keyword in ['cholesterol', 'triglycerides', 'iron', 'tsh', 'alkaline', 'phosphatase', 'ggt', 'platelet', 'count', 'rbc', 'wbc', 'hemoglobin', 'glucose', 'creatinine', 'urea', 'uric', 'acid', 'bun', 'crp', 'vitamin', 'calcium', 'potassium', 'sodium', 'magnesium', 'phosphorus', 'protein', 'albumin', 'globulin', 'bilirubin', 'enzyme', 'hormone', 'thyroid', 'liver', 'kidney', 'blood', 'serum', 'plasma', 'urine', 'cell', 'count', 'level', 'ratio', 'index', 'profile', 'test', 'concentration', 'activity', 'volume', 'mass', 'transferrin', 'saturation', 'mch', 'mchc', 'rdw', 'eos', 'neutrophils', 'lymphocytes', 'monocytes', 'basophils', 'eosinophils', 'hemogram', 'lipid', 'liver', 'kidney', 'diabetes', 'thyroid', 'vitamin', 'hormone', 'electrolyte', 'electrolytes'])):
                continue

            all_tests.append((test_name, value, unit))

    # Also try to find specific known tests
    specific_patterns = [
        (r'PLATELET COUNT\s*([\d.]+)\s*X\s*10³\s*/\s*µL', 'Platelet Count', 'X 10³ / µL'),
        (r'IRON\s*([\d.]+)\s*µg/dL', 'Iron', 'µg/dL'),
        (r'TSH\s*([\d.]+)\s*µIU/mL', 'TSH', 'µIU/mL'),
        (r'HDL CHOLESTEROL\s*([\d.]+)\s*mg/dL', 'HDL Cholesterol', 'mg/dL'),
        (r'LDL CHOLESTEROL\s*([\d.]+)\s*mg/dL', 'LDL Cholesterol', 'mg/dL'),
        (r'TRIGLYCERIDES\s*([\d.]+)\s*mg/dL', 'Triglycerides', 'mg/dL'),
        (r'TOTAL CHOLESTEROL\s*([\d.]+)\s*mg/dL', 'Total Cholesterol', 'mg/dL'),
        (r'ALKALINE PHOSPHATASE\s*([\d.]+)\s*U/L', 'Alkaline Phosphatase', 'U/L'),
        (r'GAMMA GLUTAMYL TRANSFERASE\s*\(GGT\)\s*([\d.]+)\s*U/L', 'Gamma Glutamyl Transferase (GGT)', 'U/L'),
        (r'HIGH SENSITIVITY C-REACTIVE PROTEIN\s*\(HS-CRP\)\s*([\d.]+)\s*mg/L', 'High Sensitivity C-Reactive Protein (HS-CRP)', 'mg/L'),
    ]

    for pattern, test_name, unit in specific_patterns:
        matches = re.findall(pattern, text, re.IGNORECASE)
        for match in matches:
            value = match
            # Check if we already have this test
            if not any(t[0].lower() == test_name.lower() for t in all_tests):
                all_tests.append((test_name, value, unit))

    # Remove duplicates based on test name
    seen = set()
    unique_tests = []
    for test in all_tests:
        test_name_lower = test[0].lower()
        if test_name_lower not in seen:
            seen.add(test_name_lower)
            unique_tests.append(test)

    return unique_tests


def extract_normal_ranges_from_text(text):
    """Parse normal/reference ranges from the raw PDF text for each test."""
    import re

    ranges = {}
    lines = text.split('\n')

    # Pattern matching: VALUE  TEST_NAME  RANGE  UNIT
    line_pattern = re.compile(
        r'^([\d.]+)\s+([A-Z][A-Z\s\-\(/\)]+?)\s+([\d.,]+\s*-\s*[\d.,]+)\s*([A-Za-z%/µ]+.*)$',
        re.IGNORECASE
    )

    # Secondary pattern: TEST_NAME ... normal/reference range: RANGE UNIT
    ref_pattern = re.compile(
        r'([A-Za-z][A-Za-z0-9\s\-\(/\)]+?)\s*(?:normal range|reference range|ref range)\s*[:\-]?\s*([\d.,]+\s*-\s*[\d.,]+)\s*([A-Za-z%/µ]+.*)?',
        re.IGNORECASE
    )

    for raw_line in lines:
        line = raw_line.strip()
        if not line:
            continue

        match = line_pattern.match(line)
        if match:
            _, test_name, range_val, unit = match.groups()
            cleaned_name = re.sub(r'\s+', ' ', test_name).strip()
            ranges[cleaned_name.lower()] = f"{range_val.strip()} {unit.strip()}".strip()
            continue

        ref_match = ref_pattern.search(line)
        if ref_match:
            test_name, range_val, unit = ref_match.groups()
            cleaned_name = re.sub(r'\s+', ' ', test_name).strip()
            unit_text = unit.strip() if unit else ''
            ranges[cleaned_name.lower()] = f"{range_val.strip()} {unit_text}".strip()

    return ranges


# Minimal fallback reference ranges (generic adult values; may differ by lab/reference)
REFERENCE_RANGE_OVERRIDES = {
    "hemoglobin": "12 - 15 g/dL",
    "ldl cholesterol - direct": "0 - 100 mg/dL",
    "ldl chol": "0 - 100 mg/dL",
    "hdl cholesterol - direct": "40 - 90 mg/dL",
    "total cholesterol": "0 - 200 mg/dL",
    "triglycerides": "0 - 150 mg/dL",
    "vitamin d total": "20 - 50 ng/mL",
    "vit d": "20 - 50 ng/mL",
    "vitamin b-12": "200 - 900 pg/mL",
    "magnesium": "1.7 - 2.4 mg/dL",
    "calcium": "8.5 - 10.5 mg/dL",
    "creatinine - serum": "0.5 - 1.1 mg/dL",
    "bun / sr.creatinine ratio": "10 - 20 Ratio",
    "glucose": "70 - 100 mg/dL",
    "hba1c": "0 - 5.6 %",
    "hs-crp": "0 - 3 mg/L",
    "tsh - ultrasensitive": "0.4 - 4.5 µIU/mL",
    "vitamin d3": "20 - 50 ng/mL",
    "vitamin d2": "0 - 10 ng/mL",
    "ferritin": "20 - 200 ng/mL",
    "iron": "60 - 170 µg/dL",
}


def parse_numeric(value_text):
    import re
    m = re.search(r"[-+]?[0-9]*\.?[0-9]+", value_text)
    if not m:
        return None
    try:
        return float(m.group(0))
    except:
        return None


def compute_status_from_range(value_text, range_text):
    import re
    if not range_text:
        return None

    # Upper/lower only
    upper_match = re.search(r"<\s*([0-9]*\.?[0-9]+)", range_text)
    lower_match = re.search(r">\s*([0-9]*\.?[0-9]+)", range_text)
    span_match = re.search(r"([0-9]*\.?[0-9]+)\s*-\s*([0-9]*\.?[0-9]+)", range_text)

    value_num = parse_numeric(value_text)
    if value_num is None:
        return None

    if span_match:
        low = float(span_match.group(1))
        high = float(span_match.group(2))
        if low <= value_num <= high:
            return "Good"
        else:
            return "Bad"
    if upper_match:
        upper = float(upper_match.group(1))
        if value_num <= upper:
            return "Good"
        else:
            return "Bad"
    if lower_match:
        lower = float(lower_match.group(1))
        if value_num >= lower:
            return "Good"
        else:
            return "Bad"
    return None


def normalize_test_name(name: str) -> str:
    """Normalize test names for matching across sessions (lower, alnum only)."""
    if not name:
        return ""
    return re.sub(r"[^a-z0-9]", "", str(name).lower())


def merge_comparative_rows(rows, session_count):
    """Merge duplicate comparative rows whose names normalize to the same token."""
    merged = {}
    for row in rows:
        if not row:
            continue
        name = row[0]
        norm = normalize_test_name(name)

        # Ensure row has expected length: 1 + session_count + session_count (sessions)
        expected_len = 1 + session_count + session_count
        if len(row) < expected_len:
            padded = list(row) + ["-"] * (expected_len - len(row))
            row = tuple(padded)

        if norm not in merged:
            merged[norm] = list(row)
        else:
            existing = merged[norm]
            # Merge values for each session column (1..session_count)
            for i in range(1, session_count + 1):
                if i < len(row) and i < len(existing):
                    new_val = row[i]
                    if (existing[i] in ["-", "", None]) and new_val not in ["-", "", None]:
                        existing[i] = new_val
            # Prefer longer/more descriptive name if existing name is shorter
            if len(str(existing[0])) < len(str(name)):
                existing[0] = name
    return [tuple(v) for v in merged.values()]

def print_test_results_to_console(test_results):
    """Print all test results in structured format to console."""
    if not test_results:
        print("No test results found to display.")
        return

    print("\n" + "="*80)
    print("ALL TEST RESULTS - STRUCTURED OUTPUT")
    print("="*80)
    print(f"{'Test Name':<35} {'Value':<12} {'Unit':<12} {'Status':<10} {'Range':<20}")
    print("-"*100)

    for row in test_results:
        if len(row) >= 5:
            test_name, value, unit, status, normal_range = row[:5]
        elif len(row) >= 4:
            test_name, value, unit, status = row[:4]
            normal_range = ""
        else:
            test_name, value, unit = row
            status = ""
            normal_range = ""
        print(f"{test_name:<35} {value:<12} {unit:<12} {status:<10} {normal_range:<20}")

    print("-"*80)
    print(f"Total test results found: {len(test_results)}")
    print("="*80 + "\n")

def extract_metrics_from_gemini_table(summary_text):
    """Extract metrics directly from Gemini's table or numbered list format."""
    import re
    concerning_metrics = []

    # First try the numbered list format (current Gemini format)
    # Pattern to match numbered list like: 1. **HS-CRP:** 30.15 mg/L � **Very High**
    numbered_pattern = r'(\d+)\.\s*\*\*([^*:]+?):\*\*\s*([^\u00ad\n]+?)\s*\u00ad\s*\*\*([^*\n]+?)\*\*'

    matches = re.findall(numbered_pattern, summary_text)
    print(f"DEBUG: Numbered pattern found {len(matches)} matches")

    if matches:
        for number, metric_name, value_unit, status in matches:
            metric_name = metric_name.strip()
            value_unit = value_unit.strip()
            status = status.strip()
            print(f"DEBUG: Numbered - {number}: {metric_name} = {value_unit} ({status})")

            if metric_name and value_unit and status:
                concerning_metrics.append((metric_name, value_unit, status))

    # If no numbered list found, try the table format
    if not concerning_metrics:
        # Pattern to match table rows like: | Metric Name | Value | Status |
        table_pattern = r'\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|\s*([^|]+?)\s*\|'

        matches = re.findall(table_pattern, summary_text)
        print(f"DEBUG: Table pattern found {len(matches)} matches")

        for match in matches:
            metric_name, value_unit, status = match
            metric_name = metric_name.strip()
            value_unit = value_unit.strip()
            status = status.strip()

            # Skip header rows
            if metric_name.lower() in ['metric name', 'metric', 'name', '---', '']:
                continue

            # Skip if this looks like a unit row or separator
            if any(skip in value_unit.lower() for skip in ['units', 'current value', '---']):
                continue

            # Clean up the metric name (remove extra formatting)
            metric_name = re.sub(r'\*\*', '', metric_name).strip()

            # Clean up status
            status = re.sub(r'\*\*', '', status).strip()

            if metric_name and value_unit and status:
                concerning_metrics.append((metric_name, value_unit, status))
                print(f"DEBUG: Table - {metric_name}: {value_unit} ({status})")

    return concerning_metrics

def extract_concerning_metrics(summary_text):
    """Extract concerning metrics from Gemini summary with status messages."""
    import re
    concerning_metrics = []

    # Pattern to match metrics like "HS-CRP: 30.15 mg/L" or "Total Cholesterol: 227 mg/dL"
    metric_patterns = [
        r'HS-CRP:\s*([\d.]+)\s*mg/L',
        r'Total Cholesterol:\s*([\d.]+)\s*mg/dL',
        r'LDL Cholesterol:\s*([\d.]+)\s*mg/dL',
        r'Triglycerides:\s*([\d.]+)\s*mg/dL',
        r'Iron:\s*([\d.]+)\s*µg/dL',
        r'TSH:\s*([\d.]+)\s*µIU/mL',
        r'Alkaline Phosphatase:\s*([\d.]+)\s*U/L',
        r'GGT:\s*([\d.]+)\s*U/L',
        r'Platelet count:\s*([\d.]+)\s*X\s*10³\s*/\s*µL',
        r'RDW-CV:\s*([\d.]+)%',
        r'RBC:\s*([\d.]+)\s*X\s*10^6/µL',
        r'MCH:\s*([\d.]+)\s*pq',
        r'MCHC:\s*([\d.]+)\s*g/dL',
        r'Monocytes - Absolute Count:\s*([\d.]+)\s*X\s*10³\s*/\s*µL',
        r'BUN\s*/\s*SR\.CREATININE\s*RATIO:\s*([\d.]+)\s*Ratio',
        r'Uric Acid:\s*([\d.]+)\s*mg/dL'
    ]

    metric_names = [
        "HS-CRP", "Total Cholesterol", "LDL Cholesterol", "Triglycerides",
        "Iron", "TSH", "ALP", "GGT", "Platelet Count", "RDW-CV",
        "RBC", "MCH", "MCHC", "Monocytes", "BUN/Creatinine Ratio", "Uric Acid"
    ]

    for pattern, name in zip(metric_patterns, metric_names):
        match = re.search(pattern, summary_text, re.IGNORECASE)
        if match:
            value = match.group(1)
            # Determine if this metric is concerning based on context
            if is_metric_concerning(name, value, summary_text):
                status = get_metric_status(name, summary_text)
                concerning_metrics.append((name, f"{value} {get_unit(name)}", status))

    # If no specific metrics found, try to extract from general text
    if not concerning_metrics:
        # Look for any metric mentions in the concerning areas
        concerning_section = ""
        if "**3. Areas that Need Improvement:**" in summary_text:
            start = summary_text.find("**3. Areas that Need Improvement:**")
            end = summary_text.find("**4.", start)
            if end == -1:
                end = len(summary_text)
            concerning_section = summary_text[start:end]

        # Extract any numeric values with units from concerning section
        number_pattern = r'(\d+(?:\.\d+)?)\s*(mg/dL|µg/dL|µIU/mL|U/L|%|X\s*10³\s*/\s*µL|X\s*10\^6/µL|pq|g/dL|Ratio)'
        for match in re.finditer(number_pattern, concerning_section):
            value, unit = match.groups()
            # Look for the metric name before this value
            start_pos = max(0, match.start() - 50)
            before_text = concerning_section[start_pos:match.start()]
            # Extract potential metric name
            words = before_text.split()
            if words:
                metric_name = words[-1].strip(':-,')
                if metric_name and len(metric_name) > 2:
                    status = get_metric_status(metric_name, concerning_section)
                    concerning_metrics.append((metric_name, f"{value} {unit}", status))

    # Ensure all metrics have a status
    for i, (name, value, status) in enumerate(concerning_metrics):
        if not status or status == "Everything looks good":
            status = get_metric_status(name, summary_text)
            concerning_metrics[i] = (name, value, status)

    return concerning_metrics[:6]  # Limit to 6 metrics

def is_metric_concerning(metric_name, value, full_text):
    """Determine if a metric is concerning based on context."""
    try:
        val = float(value)
        metric_lower = metric_name.lower()

        # Check if this metric is mentioned as concerning in the text
        concerning_keywords = ["high", "elevated", "low", "deficiency", "abnormal", "concern", "risk"]
        metric_context_start = full_text.find(metric_name) - 100
        metric_context_end = full_text.find(metric_name) + len(metric_name) + 100
        if metric_context_start < 0:
            metric_context_start = 0
        if metric_context_end > len(full_text):
            metric_context_end = len(full_text)

        context = full_text[metric_context_start:metric_context_end].lower()

        for keyword in concerning_keywords:
            if keyword in context:
                return True

        return False
    except:
        return False

def get_metric_status(metric_name, full_text):
    """Get a specific 1-3 word status message for a metric from Gemini analysis."""
    # Find the metric in the text and analyze surrounding context
    metric_pos = full_text.lower().find(metric_name.lower())
    if metric_pos == -1:
        return "Concerning"

    # Get context around the metric (100 characters before and after)
    start_pos = max(0, metric_pos - 100)
    end_pos = min(len(full_text), metric_pos + len(metric_name) + 100)
    context = full_text[start_pos:end_pos].lower()

    # Analyze context for specific status indicators
    if "cardiac risk" in context or "high cardiac risk" in context:
        return "Cardiac Risk"
    elif "significantly deranged" in context or "severely high" in context:
        return "Severely High"
    elif "elevated" in context:
        return "Elevated"
    elif "high" in context:
        return "High"
    elif "low" in context or "deficiency" in context or "deficient" in context:
        return "Deficient"
    elif "slightly high" in context:
        return "Slightly High"
    elif "slightly low" in context:
        return "Slightly Low"
    elif "abnormal" in context:
        return "Abnormal"
    elif "outside recommended" in context or "not optimal" in context:
        return "Not Optimal"
    elif "need" in context and ("management" in context or "attention" in context):
        return "Needs Attention"
    elif "thyroid" in context and ("elevated" in context or "high" in context):
        return "Thyroid Issue"
    elif "liver" in context and ("elevated" in context or "high" in context):
        return "Liver Concern"
    elif "risk" in context:
        return "At Risk"
    else:
        # Fallback based on metric type
        metric_lower = metric_name.lower()
        if "cholesterol" in metric_lower:
            return "High Cholesterol"
        elif "iron" in metric_lower:
            return "Iron Deficiency"
        elif "tsh" in metric_lower:
            return "Thyroid Imbalance"
        elif "liver" in metric_lower or "alp" in metric_lower or "ggt" in metric_lower:
            return "Liver Function"
        elif "platelet" in metric_lower:
            return "Low Platelets"
        else:
            return "Concerning"

def get_unit(metric_name):
    """Get the appropriate unit for a metric."""
    unit_map = {
        "HS-CRP": "mg/L",
        "Total Cholesterol": "mg/dL",
        "LDL Cholesterol": "mg/dL",
        "Triglycerides": "mg/dL",
        "Iron": "µg/dL",
        "TSH": "µIU/mL",
        "ALP": "U/L",
        "GGT": "U/L",
        "Platelet Count": "X 10³ / µL",
        "RDW-CV": "%",
        "RBC": "X 10^6/µL",
        "MCH": "pq",
        "MCHC": "g/dL",
        "Monocytes": "X 10³ / µL",
        "BUN/Creatinine Ratio": "Ratio",
        "Uric Acid": "mg/dL"
    }
    return unit_map.get(metric_name, "")

def categorize_metric_status(status):
    """Categorize metric status into good, moderate, or bad (default moderate)."""
    if not status:
        return "good"
    
    status_lower = status.lower().strip()
    
    # Direct classification from Gemini
    if status_lower == "good":
        return "good"
    elif status_lower == "moderate":
        return "moderate"
    elif status_lower == "bad":
        return "bad"
    
    # Fallback to keyword matching
    # Good indicators
    if any(word in status_lower for word in ["normal", "optimal", "excellent", "acceptable", "fine", "within range"]):
        return "good"
    
    # Moderate indicators
    if any(word in status_lower for word in ["slightly", "mild", "needs attention", "borderline", "monitor", "insufficient", "low normal", "high normal"]):
        return "moderate"
    
    # Bad indicators
    if any(word in status_lower for word in ["high", "elevated", "very high", "low", "deficient", "deficiency", "critical", "severely"]):
        return "bad"
    
    return "moderate"

def get_status_color(category):
    """Get color for status category."""
    if category == "good":
        return Color(0.0, 0.28, 0.0)  # Deep green for high contrast
    elif category == "moderate":
        return Color(0.55, 0.33, 0.0)  # Deep amber for high contrast
    elif category == "bad":
        return Color(0.65, 0.0, 0.0)  # Deep red for high contrast
    else:
        return Color(0.15, 0.15, 0.15)  # Dark neutral for unknown

def get_gemini_patient_summary(text, api_key):
    """Send text to Gemini API and get patient summary with token usage."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"

    prompt = f"""
    Please analyze the following medical/health document and provide a concise patient summary. Focus on:

    1. Patient's current health status
    2. Key medical findings or test results
    3. Areas that need improvement (things that were bad)
    4. Specific parameters or metrics that require attention
    5. Recommendations for improvement
    6. Important observations or notices

    CRITICAL REQUIREMENT: Please identify and list UP TO 6 health metrics from the document that are concerning (both critical and medium risk). For each metric, provide:
    - Metric name (MANDATORY: Keep ALL names to MAXIMUM 2 WORDS and UNDER 15 characters total. Use these exact abbreviations: "Hemoglobin"→"HEMOGLOBIN", "MCHC"→"MCHC", "RDW-CV"→"RDW-CV", "RDW-SD"→"RDW-SD", "LDL Chol"→"LDL CHOL", "Vit D"→"VIT D", "Magnesium"→"MAGNESIUM", "CRP"→"CRP", "TSH"→"TSH", "Iron"→"IRON", "Calcium"→"CALCIUM", "Sodium"→"SODIUM", "Troponin I"→"TROPONIN I", "Glucose"→"GLUCOSE". Output ALL metric names in UPPERCASE. NEVER use more than 2 words or exceed 15 characters.)
    - Current value with units
    - Brief status: MUST be exactly one of these three words: "Good", "Moderate", or "Bad" (Good = normal/optimal range, Moderate = borderline or slightly abnormal, Bad = significantly abnormal or high risk)

    IMPORTANT: ONLY use metrics that are ACTUALLY PRESENT in the document. Do NOT invent, hallucinate, or make up metrics that are not in the PDF. Only include metrics that exist in the document text you were given.

    IMPORTANT FORMATS (MANDATORY):

    1) Metrics Table (top findings):
    | Metric Name | Current Value with Units | Status |
    |-------------|--------------------------|--------|
    | [Metric 1 Name] | [value] [units] | [status] |
    | [Metric 2 Name] | [value] [units] | [status] |
    ...continue for as many metrics as you find in the document...

    2) ALL_TEST_RESULTS (EVERY test in the PDF):
    MUST be pipe-delimited with FIVE fields per line:
    Test Name | Value | Unit | Status | Normal Range
    Where Status is EXACTLY one of: Good, Moderate, Bad
    Example: HEMOGLOBIN|13.2|g/dL|Good|12 - 15 g/dL (make sure normal range is concise and not long, eg 10-20 value)
    Ensure EVERY test line includes both status AND normal range (use "-" if truly not available).

    Do NOT use numbered lists or bullet points for tables. Use the exact formats above.

    Only include metrics that are actually present and concerning in the document. Do not add fictional or made-up metrics.

    Additionally, please provide an overall health score from 0-100 based on the severity and number of health issues found.

    CRITICAL FORMATTING REQUIREMENT: After the metrics table, you MUST provide the alarming patient summary in EXACTLY this format:

    **ALARMING_PATIENT_SUMMARY_START**
    [One-liner summary (15-20 words) explaining key health implications, e.g., "Low hemoglobin and MCHC suggest anemia; high RDW-CV/SD indicate red blood cell variation; elevated LDL increases heart disease risk."]
    **ALARMING_PATIENT_SUMMARY_END**

    SUMMARY CONTENT: Provide ONE concise sentence (15-20 words) summarizing the main health concerns from the critical metrics in simple terms.

    MOST IMPORTANT: After the alarming patient summary, you MUST extract ALL test results from the document in this EXACT format:

    **ALL_TEST_RESULTS_START**
    Test Name|Value|Unit|Status|Normal Range
    Test Name|Value|Unit|Status|Normal Range
    Test Name|Value|Unit|Status|Normal Range
    ...continue for ALL test results found in the document...
    **ALL_TEST_RESULTS_END**

    CRITICAL RULES FOR TEST RESULTS EXTRACTION:
    - Extract EVERY test result mentioned in the document
    - Do NOT omit any test results, even if they are normal
    - Do NOT add test results that are not in the document
    - Do NOT include any headers, column names, or section titles as test results
    - Specifically EXCLUDE: "TEST NAME", "Test Name", "Parameter", "Value", "Unit", "Units", "Reference Range", "Method", "Status", or any similar header text
    - Use the exact test names as they appear in the document
    - Include all values and units exactly as shown
    - Each line must be: Test Name|Value|Unit (pipe-separated)
    - Include results from all sections (CARDIAC RISK MARKERS, COMPLETE HEMOGRAM, IRON DEFICIENCY, LIPID, LIVER, RENAL, THYROID, etc.)
    - Do not include headers, footers, patient info, dates, or any non-test data
    - Only include actual test measurements with their values and units

    Document text:
    {text}

    Please provide a clear, concise summary that a healthcare professional would find useful, including the health score, exactly 6 metrics, the concise 1-2 sentence alarming patient summary, and ALL test results.
    """

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }]
    }

    headers = {
        "Content-Type": "application/json"
    }

    try:
        response = requests.post(url, json=payload, headers=headers)
        response.raise_for_status()

        result = response.json()
        if "candidates" in result and len(result["candidates"]) > 0:
            gemini_response = result["candidates"][0]["content"]["parts"][0]["text"]

            # Extract token usage information
            usage_info = {}
            if "usageMetadata" in result:
                usage_metadata = result["usageMetadata"]
                usage_info = {
                    "input_tokens": usage_metadata.get("promptTokenCount", 0),
                    "output_tokens": usage_metadata.get("candidatesTokenCount", 0),
                    "total_tokens": usage_metadata.get("totalTokenCount", 0)
                }

            print("=== FULL GEMINI RESPONSE ===")
            print(gemini_response)
            print("=" * 50)
            print(f"Gemini Token Usage: {usage_info}")
            return gemini_response, usage_info
        else:
            return "Error: No response from Gemini API", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

    except requests.exceptions.RequestException as e:
        return f"Error calling Gemini API: {str(e)}", {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

def extract_patient_info_from_first_page(text, api_key):
    """Send first page text to Gemini API and extract patient name, date, test asked, and report status."""
    url = f"https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash-lite:generateContent?key={api_key}"

    prompt = f"""
    Please analyze the following medical report first page and extract the following information in JSON format:

    1. Patient Name
    2. Patient Age (just the number, e.g., "43")
    3. Patient Gender (single letter: "M" for Male, "F" for Female)
    4. Report Date
    5. Tests Asked/Requested (list all tests mentioned)
    6. Report Status

    Look for information like:
    - Patient name (usually at the top)
    - Patient age (usually with name, like "43 years" or "43Y")
    - Patient gender (usually "M", "F", "Male", "Female")
    - Date of report/collection
    - List of tests ordered or performed
    - Status of the report (e.g., "Final", "Preliminary", "Complete", etc.)

    Return ONLY a valid JSON object with these exact keys:
    {{
        "patient_name": "extracted name",
        "patient_age": "extracted age number only",
        "patient_gender": "M or F",
        "report_date": "extracted date",
        "tests_asked": ["test1", "test2", "test3"],
        "report_status": "extracted status"
    }}

    If any information is not found, use "Not Found" for strings or empty array for tests.

    Document text:
    {text}
    """

    payload = {
        "contents": [{
            "parts": [{
                "text": prompt
            }]
        }],
        "generationConfig": {
            "temperature": 0.1,
            "topK": 1,
            "topP": 1,
            "maxOutputTokens": 1024,
        }
    }

    try:
        response = requests.post(url, json=payload, timeout=60)
        response.raise_for_status()

        gemini_response = response.json()
        if "candidates" in gemini_response and len(gemini_response["candidates"]) > 0:
            raw_text = gemini_response["candidates"][0]["content"]["parts"][0]["text"]

            # Extract JSON from the response
            import json
            import re

            # Try to find JSON in the response
            json_match = re.search(r'\{.*\}', raw_text, re.DOTALL)
            if json_match:
                try:
                    patient_info = json.loads(json_match.group(0))
                    return patient_info
                except json.JSONDecodeError:
                    print(f"Failed to parse JSON from Gemini response: {raw_text}")
                    return {
                        "patient_name": "Not Found",
                        "patient_age": "Not Found",
                        "patient_gender": "Not Found",
                        "report_date": "Not Found",
                        "tests_asked": [],
                        "report_status": "Not Found"
                    }
            else:
                print(f"No JSON found in Gemini response: {raw_text}")
                return {
                    "patient_name": "Not Found",
                    "patient_age": "Not Found",
                    "patient_gender": "Not Found",
                    "report_date": "Not Found",
                    "tests_asked": [],
                    "report_status": "Not Found"
                }
        else:
            return {
                "patient_name": "Not Found",
                "patient_age": "Not Found",
                "patient_gender": "Not Found",
                "report_date": "Not Found",
                "tests_asked": [],
                "report_status": "Not Found"
            }

    except requests.exceptions.RequestException as e:
        print(f"Error calling Gemini API for patient info: {str(e)}")
        return {
            "patient_name": "Not Found",
            "patient_age": "Not Found",
            "patient_gender": "Not Found",
            "report_date": "Not Found",
            "tests_asked": [],
            "report_status": "Not Found"
        }

def compress_image_to_jpeg(image_path, quality=60, max_size=2000):
    """Compress an image to JPEG format with given quality and max size."""
    from PIL import Image
    from io import BytesIO
    try:
        img = Image.open(image_path)
        if img.mode != 'RGB':
            img = img.convert('RGB')
        
        # Resize if too large
        if max(img.size) > max_size:
            img.thumbnail((max_size, max_size), Image.Resampling.LANCZOS)
        
        buffer = BytesIO()
        img.save(buffer, format='JPEG', quality=quality, optimize=True)
        buffer.seek(0)
        return buffer
    except Exception as e:
        print(f"Warning: Could not compress image {image_path}: {e}")
        return None

def create_cover_page(width, height, patient_info=None):
    """Create a cover page with the cover image and patient information."""
    from reportlab.lib.utils import ImageReader
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setPageCompression(1)

    # Draw the cover image to fill the entire page
    cover_image_path = "coverpage.png"
    try:
        compressed_img = compress_image_to_jpeg(cover_image_path, quality=60)
        if compressed_img:
            c.drawImage(ImageReader(compressed_img), 0, 0, width=width, height=height)
        else:
            c.drawImage(cover_image_path, 0, 0, width=width, height=height)
    except Exception as e:
        print(f"Warning: Could not load cover image {cover_image_path}: {e}")
        # If image fails to load, just use white background
        c.setFillColor(Color(1.0, 1.0, 1.0))
        c.rect(0, 0, width, height, fill=True, stroke=False)

    # Clean cover page - no gridlines

    # Add extracted patient information starting at row 5, column 0
    if patient_info:
        # Starting position: row 5.3 (5.3 inches from bottom), column 0 (left edge)
        start_x = 0.2 * inch  # Small margin from left edge
        start_y = 5.3 * inch  # 5.3 inches from bottom

        # Set text color to greyish black for better readability
        c.setFillColor(Color(0.2, 0.2, 0.2))  # Dark grey, not pure black

        # Patient name (big text) with age and gender
        name = patient_info.get('patient_name', 'Patient Name')
        age = patient_info.get('patient_age', '')
        gender = patient_info.get('patient_gender', '')
        
        if name and name != 'Not Found':
            # Format: "Shweta Arora, 43Y | F"
            display_name = name
            age_gender_text = ""
            if age and age != 'Not Found' and gender and gender != 'Not Found':
                age_gender_text = f", {age}Y | {gender}"
            elif age and age != 'Not Found':
                age_gender_text = f", {age}Y"
            elif gender and gender != 'Not Found':
                age_gender_text = f" | {gender}"
            
            # Draw name in large font
            c.setFont("Helvetica-Bold", 19)
            c.drawString(start_x, start_y, display_name)
            
            # Draw age/gender in smaller font (30% smaller = 19 * 0.7 = 13.3, rounded to 13)
            if age_gender_text:
                c.setFont("Helvetica-Bold", 13)
                name_width = c.stringWidth(display_name, "Helvetica-Bold", 19)
                c.drawString(start_x + name_width, start_y, age_gender_text)

        # Date (smaller text, single spacing)
        c.setFont("Helvetica", 12.6)
        date = patient_info.get('report_date', 'Date')
        if date and date != 'Not Found':
            c.drawString(start_x, start_y - 0.25 * inch, date)  # Single spacing

        # Report status
        status = patient_info.get('report_status', 'Status')
        if status and status != 'Not Found':
            c.drawString(start_x, start_y - 0.5 * inch, status)  # Single spacing

        # Tests asked (may be multiple lines)
        tests = patient_info.get('tests_asked', [])
        if tests:
            test_text = tests[0] if tests else 'Tests'
            c.drawString(start_x, start_y - 0.75 * inch, test_text)  # Single spacing

    c.save()
    buffer.seek(0)
    return buffer

def create_summary_page(width, height, summary_text, page_num, concerning_metrics=None):
    """Create a medical diagnostic lab dashboard page identical to HTML mockup."""
    from reportlab.lib.utils import ImageReader
    buffer = BytesIO()
    c = canvas.Canvas(buffer, pagesize=(width, height))
    c.setPageCompression(1)

    # White background
    c.setFillColor(Color(1.0, 1.0, 1.0))
    c.rect(0, 0, width, height, fill=True, stroke=False)

    # Draw the full-page image with skeleton and boxes
    full_page_image_path = "full_page_with_boxes.png"
    try:
        # Draw the image covering the entire page
        compressed_img = compress_image_to_jpeg(full_page_image_path, quality=60)
        if compressed_img:
            c.drawImage(ImageReader(compressed_img), 0, 0, width=width, height=height, preserveAspectRatio=False)
        else:
            c.drawImage(full_page_image_path, 0, 0, width=width, height=height, preserveAspectRatio=False)
    except Exception as e:
        print(f"Warning: Could not load full-page image: {e}. Using fallback.")

    # Overlay alarming summary in the urgent health box
    alarming_summary = extract_alarming_summary_from_gemini(summary_text)
    # Clean the text to remove unmatched *
    import re
    alarming_summary = re.sub(r'(?<!\*)\*(?!\*)', '', alarming_summary)  # Remove single * not part of **

    urgent_box_x = 0.3 * inch  # Start at 0.3 inch
    urgent_box_y = 7.8 * inch  # Position at 7.8 inch
    urgent_box_width = 7.38 * inch  # End at 7.68 inch (10% earlier)
    urgent_box_height = 1.3 * inch
    text_start_x = urgent_box_x + 0.4 * inch
    text_start_y = urgent_box_y + urgent_box_height - 0.55 * inch
    text_line_height = 0.18 * inch

    c.setFont("Times-Roman", 10.44)
    c.setFillColor(Color(0.4, 0.0, 0.0))  # Darker, readable red

    all_rendered_lines = []
    summary_lines = alarming_summary.split('\n')[:6]
    for line in summary_lines:
        if line.strip():
            max_text_width = urgent_box_width - 0.8 * inch
            wrapped_lines = wrap_text_with_markdown(line, c, max_text_width)
            all_rendered_lines.extend(wrapped_lines)

    for i, rendered_line in enumerate(all_rendered_lines[:6]):
        safe_width = urgent_box_width - 0.8 * inch
        # Since wrap_text_with_markdown already handles wrapping, just render the line
        render_text_with_bold(c, rendered_line, text_start_x, text_start_y - i * text_line_height)

    # Overlay metrics in the 6 boxes (3 left, 3 right)
    concerning_metrics = extract_metrics_from_gemini_table(summary_text)
    if not concerning_metrics:
        concerning_metrics = extract_concerning_metrics(summary_text)
    if not concerning_metrics:
        concerning_metrics = [
            ("HS-CRP", "30.15 mg/L", "High Risk"),
            ("Total Cholesterol", "227 mg/dL", "High"),
            ("Iron", "42.2 µg/dL", "Low"),
            ("TSH", "6.61 µIU/mL", "Elevated"),
            ("ALP", "147.88 U/L", "Elevated"),
            ("Vitamin D", "25 nmol/L", "Low")
        ]
    concerning_metrics = concerning_metrics[:6]

    # Define box positions (based on your input: column 0 for left, row 6 for top, spaced vertically)
    box_positions = [
        # Left boxes
        (0 * inch, 6 * inch, 1.5 * inch, 0.8 * inch),  # Top-left
        (0 * inch, 5 * inch, 1.5 * inch, 0.8 * inch),  # Middle-left
        (0 * inch, 4 * inch, 1.5 * inch, 0.8 * inch),  # Bottom-left
        # Right boxes
        (6 * inch, 6 * inch, 1.5 * inch, 0.8 * inch),  # Top-right
        (6 * inch, 5 * inch, 1.5 * inch, 0.8 * inch),  # Middle-right
        (6 * inch, 4 * inch, 1.5 * inch, 0.8 * inch),  # Bottom-right
    ]

    for i, (x, y, box_width, box_height) in enumerate(box_positions):
        if i < len(concerning_metrics):
            test_name, value, status = concerning_metrics[i]
            test_name = test_name.upper()
            if i == 0:  # Top-left
                name_offset = 0.5 * inch
                value_offset = 0.8 * inch
                name_y = 7.54 * inch  # Moved down by 5% (0.04 inch)
                name_font = "Times-Bold"
                name_color = Color(0.91, 0.88, 0.82)  # Light pale cream
                value_y = y + box_height + 0.18 * inch  # Moved up by additional 20% (offset -0.18)
                status_y = value_y - 0.25 * inch
            elif i == 1:  # Middle-left
                name_offset = 0.45 * inch
                value_offset = 0.8 * inch
                name_y = y + 0.744 * inch  # Moved down by 15% (0.12 inch)
                name_font = "Times-Bold"
                name_color = Color(0.91, 0.88, 0.82)  # Light pale cream
                value_y = y + 0.304 * inch  # Moved down by 20% (0.16 inch)
                status_y = value_y - 0.25 * inch
            elif i == 2:  # Bottom-left
                name_offset = 0.45 * inch
                value_offset = 0.8 * inch
                name_y = y + 0.048 * inch  # Moved down by additional 10% (0.08 inch)
                name_font = "Times-Bold"
                name_color = Color(0.91, 0.88, 0.82)  # Light pale cream
                value_y = y - 0.248 * inch  # Moved down by 20% (0.16 inch)
                status_y = value_y - 0.25 * inch
            elif i == 3:  # Top-right
                name_offset = 0.1375 * inch  # Moved right by 10% (0.0125 inch)
                value_offset = 0.65 * inch
                name_y = 7.3 * inch  # Moved down by 5% (0.04 inch)
                name_font = "Times-Bold"
                name_color = Color(0.91, 0.88, 0.82)  # Light pale cream
                value_y = 6.76 * inch  # Moved up by 15% (0.12 inch)
                status_y = value_y - 0.25 * inch
            elif i == 4:  # Middle-right
                name_offset = 0.0825 * inch  # Moved right by 10% (0.0075 inch)
                value_offset = 0.585 * inch  # Moved left by 10% (0.065 inch)
                name_y = 5.464 * inch  # Moved down by 20% (0.16 inch)
                name_font = "Times-Bold"
                name_color = Color(0.91, 0.88, 0.82)  # Light pale cream
                value_y = 5.104 * inch  # Moved up by 5% (0.04 inch)
                status_y = value_y - 0.25 * inch
            elif i == 5:  # Bottom-right
                name_offset = 0.0825 * inch  # Moved right by 10% (0.0075 inch)
                value_offset = 0.585 * inch  # Moved left by 10% (0.065 inch)
                name_y = y + 0.048 * inch  # Moved down by additional 10% (0.08 inch)
                name_font = "Times-Bold"
                name_color = Color(0.91, 0.88, 0.82)  # Light pale cream
                value_y = y - 0.328 * inch  # Moved down by additional 10% (0.08 inch)
                status_y = value_y - 0.25 * inch
            c.setFont(name_font, 10)
            c.setFillColor(name_color)
            c.drawString(x + name_offset, name_y, test_name)
            c.setFont("Times-Roman", 15.5)
            c.setFillColor(Color(0.4, 0.0, 0.0))
            c.drawString(x + value_offset, value_y, value)
            c.setFont("Times-Bold", 15.5)
            c.setFillColor(Color(0.4, 0.0, 0.0))
            c.drawString(x + value_offset, status_y, status)

    c.save()
    buffer.seek(0)
    return buffer


def create_detailed_results_page(width, height, test_results, exam_date, comparative=False, session_count=2, concerning_metrics=None, ai_summary="", normal_ranges=None):
    """Create detailed results pages showing test results in a table format across multiple pages if needed."""
    from reportlab.pdfgen import canvas
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.lib.colors import Color
    from io import BytesIO

    normal_ranges = normal_ranges or {}

    # Calculate header and footer heights
    header_height = 0
    footer_height = 0

    # Get header image dimensions
    header_image_path = "header.png"
    if os.path.exists(header_image_path):
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(header_image_path)
            img_width, img_height = img.getSize()
            # Scale to fit page width
            header_height = (img_height * width) / img_width
        except Exception as e:
            print(f"Warning: Could not get header image dimensions: {e}")

    # Get footer image dimensions
    footer_image_path = "footer.png"
    if os.path.exists(footer_image_path):
        try:
            from reportlab.lib.utils import ImageReader
            img = ImageReader(footer_image_path)
            img_width, img_height = img.getSize()
            # Scale to fit page width
            footer_height = (img_height * width) / img_width
        except Exception as e:
            print(f"Warning: Could not get footer image dimensions: {e}")

    # Reserve vertical space for snapshot + key metrics + titles
    reserved_height = 2.8 * inch

    # Dynamic rows-per-page: consistent sizing across pages
    row_height_first = 0.5 * inch
    row_height_follow = 0.5 * inch

    # First page capacity (accounting for snapshot area)
    first_page_reserved = 2.8 * inch
    available_first = height - header_height - footer_height - first_page_reserved
    first_page_rows = min(18, max(1, int(available_first / row_height_first)))

    # Subsequent pages target ~18 rows for balanced readability
    follow_rows_target = 18
    follow_page_rows = follow_rows_target

    if len(test_results) <= first_page_rows:
        total_pages_needed = 1
    else:
        remaining = len(test_results) - first_page_rows
        total_pages_needed = 1 + math.ceil(remaining / follow_page_rows)

    start_idx_global = 0

    buffers = []

    # Build status map preferring explicit statuses from ALL_TEST_RESULTS, then Gemini table/concerning metrics, then heuristic
    metric_statuses = {}

    # 1) Use statuses that Gemini already provided in ALL_TEST_RESULTS
    for result in test_results:
        if len(result) >= 4:
            metric_statuses[normalize_test_name(result[0])] = result[3]

    # 2) Table-derived statuses (only fill missing)
    if ai_summary:
        try:
            table_metrics = extract_metrics_from_gemini_table(ai_summary)
            for m_name, _val, m_status in table_metrics:
                norm_name = normalize_test_name(m_name)
                if m_status and m_name and norm_name not in metric_statuses:
                    metric_statuses[norm_name] = m_status
        except Exception as e:
            print(f"Warning: failed to parse table statuses: {e}")

    # 3) Concerning metrics overrides (fill missing)
    if concerning_metrics:
        for name, value, status in concerning_metrics:
            key = normalize_test_name(name)
            if key not in metric_statuses:
                metric_statuses[key] = status

    # 4) Heuristic from full summary (fill remaining)
    if ai_summary:
        for result in test_results:
            test_name = result[0]
            key = normalize_test_name(test_name)
            if key not in metric_statuses:
                metric_statuses[key] = get_metric_status(test_name, ai_summary)

    # Fill remaining statuses using normal ranges when available
    for result in test_results:
        if comparative:
            test_name = result[0]
            val_text = str(result[1]) if len(result) > 1 else ""
        else:
            test_name, val_text, unit = result[:3]
        key = normalize_test_name(test_name)
        if not metric_statuses.get(key):
            range_text = normal_ranges.get(key, "") or normal_ranges.get(test_name.lower(), "")
            status_from_range = compute_status_from_range(val_text, range_text)
            if status_from_range:
                metric_statuses[key] = status_from_range

    # Last fallback: default good
    for result in test_results:
        test_name = result[0]
        key = normalize_test_name(test_name)
        if not metric_statuses.get(key):
            metric_statuses[key] = "Good"

    # Pre-compute categories for every metric and counts (only three buckets)
    metric_categories = {}
    metric_display_names = {}
    good_count = moderate_count = bad_count = 0

    if comparative:
        # For comparative data, derive status per value using normal ranges and take the worst per test
        for result in test_results:
            if not result:
                continue
            test_name = result[0]
            values = result[1:session_count + 1]
            normal_range_text = normal_ranges.get(test_name.lower(), "")

            value_categories = []
            for val in values:
                status_val = compute_status_from_range(str(val), normal_range_text)
                if not status_val:
                    status_val = metric_statuses.get(test_name.lower(), get_metric_status(test_name, ai_summary))
                value_categories.append(categorize_metric_status(status_val))

            if "bad" in value_categories:
                category = "bad"
            elif "moderate" in value_categories:
                category = "moderate"
            else:
                category = "good"

            norm_key = normalize_test_name(test_name)
            metric_categories[norm_key] = category
            if norm_key not in metric_display_names:
                metric_display_names[norm_key] = test_name
            if category == "good":
                good_count += 1
            elif category == "moderate":
                moderate_count += 1
            elif category == "bad":
                bad_count += 1
    else:
        for result in test_results:
            test_name = result[0]
            norm_key = normalize_test_name(test_name)
            status = metric_statuses.get(norm_key, "")
            category = categorize_metric_status(status)
            metric_categories[norm_key] = category
            if norm_key not in metric_display_names:
                metric_display_names[norm_key] = test_name
            if category == "good":
                good_count += 1
            elif category == "moderate":
                moderate_count += 1
            elif category == "bad":
                bad_count += 1

    for page_num in range(total_pages_needed):
        # Select per-page row height and capacity
        if page_num == 0:
            row_height = row_height_first
            rows_this_page = first_page_rows
        else:
            row_height = row_height_follow
            rows_this_page = follow_page_rows
        # Create extended page height
        extended_height = header_height + height + footer_height

        buffer = BytesIO()
        c = canvas.Canvas(buffer, pagesize=(width, extended_height))
        c.setPageCompression(1)

        # Draw header at the top
        if os.path.exists(header_image_path) and header_height > 0:
            try:
                compressed_img = compress_image_to_jpeg(header_image_path, quality=60)
                if compressed_img:
                    c.drawImage(ImageReader(compressed_img), 0, extended_height - header_height, width=width, height=header_height)
                else:
                    c.drawImage(header_image_path, 0, extended_height - header_height, width=width, height=header_height)
            except Exception as e:
                print(f"Warning: Could not draw header image: {e}")

        # Draw footer at the bottom
        if os.path.exists(footer_image_path) and footer_height > 0:
            try:
                compressed_img = compress_image_to_jpeg(footer_image_path, quality=60)
                if compressed_img:
                    c.drawImage(ImageReader(compressed_img), 0, 0, width=width, height=footer_height)
                else:
                    c.drawImage(footer_image_path, 0, 0, width=width, height=footer_height)
            except Exception as e:
                print(f"Warning: Could not draw footer image: {e}")

        # Content area starts after header
        content_start_y = footer_height
        content_height = height

        # Soft paper-like background for content area
        c.setFillColor(Color(0.976, 0.957, 0.961))  # #F9F4F5
        c.rect(0, content_start_y, width, content_height, fill=True, stroke=False)

        # HEALTH SNAPSHOT + KEY METRICS only on first page
        if page_num == 0:
            top_y = content_start_y + content_height

            # Title
            c.setFont("Helvetica-Bold", 18)
            c.setFillColor(Color(0.08, 0.18, 0.38))
            c.drawString(0.3 * inch, top_y - 0.4 * inch, "Health Snapshot")
            c.setFont("Helvetica", 10)
            c.setFillColor(Color(0.35, 0.35, 0.42))
            c.drawString(0.3 * inch, top_y - 0.6 * inch, "Instant view of your current status")

            # Snapshot card
            snapshot_height = 1.05 * inch
            snapshot_y = top_y - 0.75 * inch - snapshot_height
            snapshot_width = width - 1.0 * inch
            snapshot_x = (width - snapshot_width) / 2
            c.setFillColor(Color(0.985, 0.98, 0.975))
            c.roundRect(snapshot_x, snapshot_y, snapshot_width, snapshot_height, 8, fill=True, stroke=False)
            c.setStrokeColor(Color(0.78, 0.75, 0.72))
            c.setLineWidth(1)
            c.roundRect(snapshot_x, snapshot_y, snapshot_width, snapshot_height, 8, fill=False, stroke=True)

            # Snapshot numbers with clean badges (number inside, label below)
            col_w = snapshot_width / 3
            badge_radius = 0.3 * inch
            metrics_info = [
                ("GOOD", good_count, Color(0.12, 0.45, 0.30)),
                ("MODERATE", moderate_count, Color(0.70, 0.50, 0.22)),
                ("ATTENTION", bad_count, Color(0.68, 0.18, 0.18)),
            ]

            for idx, (label, count, col) in enumerate(metrics_info):
                x = snapshot_x + col_w * idx + col_w / 2
                badge_y = snapshot_y + snapshot_height - 0.32 * inch

                # Soft ring
                c.setFillColor(Color(0.985, 0.98, 0.975))
                c.setStrokeColor(Color(0.86, 0.82, 0.80))
                c.setLineWidth(1)
                c.circle(x, badge_y, badge_radius + 3, fill=True, stroke=True)

                # Solid badge with number
                c.setFillColor(col)
                c.setStrokeColor(col)
                c.circle(x, badge_y, badge_radius, fill=True, stroke=False)
                c.setFillColor(Color(1, 1, 1))
                c.setFont("Helvetica-Bold", 18)
                c.drawCentredString(x, badge_y - 0.04 * inch, str(count))

                # Label below
                c.setFont("Helvetica", 11)
                c.setFillColor(Color(0.25, 0.28, 0.32))
                c.drawCentredString(x, badge_y - 0.48 * inch, label)

            # KEY METRICS section (top 5 to improve)
            key_title_y = snapshot_y - 0.28 * inch
            c.setFont("Helvetica-Bold", 14)
            c.setFillColor(Color(0.08, 0.18, 0.38))
            c.drawString(snapshot_x, key_title_y, "Key Metrics")
            c.setFont("Helvetica", 9.5)
            c.setFillColor(Color(0.12, 0.12, 0.16))
            c.drawString(snapshot_x, key_title_y - 0.18 * inch, "Top 5 metrics that need your focus")

            key_block_h = 1.3 * inch
            key_y = key_title_y - 0.3 * inch - key_block_h
            c.setFillColor(Color(0.985, 0.98, 0.975))
            c.roundRect(snapshot_x, key_y, snapshot_width, key_block_h, 6, fill=True, stroke=False)
            c.setStrokeColor(Color(0.78, 0.75, 0.72))
            c.setLineWidth(0.8)
            c.roundRect(snapshot_x, key_y, snapshot_width, key_block_h, 6, fill=False, stroke=True)

            bad_list = [name for name, cat in metric_categories.items() if cat == "bad"]
            moderate_list = [name for name, cat in metric_categories.items() if cat == "moderate"]

            priority_names = []
            for name in bad_list + moderate_list:
                if name not in priority_names:
                    priority_names.append(name)
                if len(priority_names) >= 5:
                    break

            c.setFont("Helvetica-Bold", 10.5)
            c.setFillColor(Color(0.1, 0.1, 0.12))
            c.drawString(snapshot_x + 0.14 * inch, key_y + key_block_h - 0.2 * inch, "Metrics to improve")

            chip_padding_x = 0.14 * inch
            chip_padding_y = 0.22 * inch
            chip_gap = 0.1 * inch
            max_chip_width = snapshot_width - 2 * chip_padding_x
            cursor_x = snapshot_x + chip_padding_x
            cursor_y = key_y + key_block_h - 0.55 * inch
            c.setFont("Helvetica", 9.5)

            if priority_names:
                for name in priority_names:
                    label = metric_display_names.get(name, name).upper()
                    chip_width = min(max_chip_width, c.stringWidth(label, "Helvetica", 9.5) + 0.32 * inch)
                    if cursor_x + chip_width > snapshot_x + snapshot_width - chip_padding_x:
                        cursor_x = snapshot_x + chip_padding_x
                        cursor_y -= 0.32 * inch

                    color = get_status_color(metric_categories.get(name, "moderate"))
                    # Soften chip color to harmonize with paper background
                    color = Color(min(color.red + 0.15, 1.0), min(color.green + 0.12, 1.0), min(color.blue + 0.10, 1.0))
                    c.setFillColor(color)
                    c.setStrokeColor(color)
                    c.roundRect(cursor_x, cursor_y, chip_width, 0.26 * inch, 3, fill=True, stroke=False)
                    c.setFillColor(Color(1, 1, 1))
                    c.drawString(cursor_x + 0.12 * inch, cursor_y + 0.08 * inch, label)
                    cursor_x += chip_width + chip_gap
            else:
                c.setFillColor(Color(0.12, 0.12, 0.16))
                c.drawString(snapshot_x + 0.14 * inch, cursor_y, "No concerning metrics detected")

            # LAB RESULTS title (only first page)
            matrix_title_y = key_y - 0.35 * inch
            c.setFont("Helvetica-Bold", 14)
            c.setFillColor(Color(0.08, 0.18, 0.38))
            c.drawString(0.3 * inch, matrix_title_y, "Lab Results")
            c.setFont("Helvetica", 9.5)
            c.setFillColor(Color(0.12, 0.12, 0.16))
            c.drawString(0.3 * inch, matrix_title_y - 0.18 * inch, "Clinical layout with per-value signals")

            table_start_y = matrix_title_y - 0.35 * inch
        else:
            # No snapshot/key metrics on later pages
            table_start_y = content_start_y + content_height - 0.5 * inch
        header_table_height = 0.35 * inch  # Even taller header for two rows

        if comparative:
            # Dynamic columns: Test Name | Value 1 | Value 2 | ... | Value N
            col1_width = width * 0.35  # Test name column (fixed)
            value_columns_width = width - col1_width - 0.6 * inch  # Remaining width for value columns
            value_col_width = value_columns_width / session_count  # Equal width for each value column
        else:
            # 2 columns: Test Name | Values (wider name column to avoid overlap)
            col1_width = width * 0.42  # Test name column
            col2_width = width * 0.48  # Values column

        # Table header with modern styling (card-like)
        c.setFillColor(Color(0.985, 0.98, 0.975))
        c.roundRect(0.3 * inch, table_start_y - header_table_height, width - 0.6 * inch, header_table_height, 5, fill=True, stroke=False)

        # Add modern border for header
        c.setStrokeColor(Color(0.72, 0.68, 0.66))
        c.setLineWidth(1.4)
        c.roundRect(0.3 * inch, table_start_y - header_table_height, width - 0.6 * inch, header_table_height, 5, fill=False, stroke=True)

        # First header row - vertically centered
        c.setFont("Helvetica-Bold", 11)
        c.setFillColor(Color(0.07, 0.16, 0.34))
        # Center "Test Name" vertically in the top half of header
        test_name_y = table_start_y - (header_table_height / 4) - 0.05 * inch  # Center in top half
        c.drawString(0.4 * inch, test_name_y, "Test Name")

        if comparative:
            # Show session dates as column headers (first line)
            header_y = table_start_y - (header_table_height / 4) - 0.05 * inch
            # Extract session timestamps from the first result (they're at the end)
            if test_results and len(test_results) > 0:
                sessions = test_results[0][-session_count:]  # Last N elements are session timestamps
                for i in range(session_count):
                    col_x = 0.4 * inch + col1_width + (i * value_col_width)
                    session_timestamp = sessions[i] if i < len(sessions) else f"Session {i + 1}"

                    # Split timestamp into date and time, enable wrapping
                    if ', ' in session_timestamp:
                        parts = session_timestamp.split(', ', 1)
                        date_part = parts[0]  # e.g., "19 Dec"
                        time_part = parts[1] if len(parts) > 1 else ""  # e.g., "2025 17:09"
                    else:
                        # Fallback if no comma+space found
                        parts = session_timestamp.split(' ', 1)
                        date_part = parts[0]
                        time_part = parts[1] if len(parts) > 1 else ""

                    # Draw date on first line with text wrapping
                    available_width = value_col_width - 0.1 * inch
                    if c.stringWidth(date_part, "Helvetica-Bold", 10) > available_width:
                        # Wrap date if too long
                        words = date_part.split()
                        lines = []
                        current_line = ""
                        for word in words:
                            test_line = current_line + (" " if current_line else "") + word
                            if c.stringWidth(test_line, "Helvetica-Bold", 10) <= available_width:
                                current_line = test_line
                            else:
                                if current_line:
                                    lines.append(current_line)
                                current_line = word
                        if current_line:
                            lines.append(current_line)

                        # Draw wrapped date
                        for j, line in enumerate(lines[:2]):  # Max 2 lines
                            y_pos = header_y - (j * 0.12 * inch)
                            c.drawString(col_x, y_pos, line.strip())
                    else:
                        c.drawString(col_x, header_y, date_part)
        else:
            # Use exam date as the column title, vertically centered in header
            values_y = table_start_y - (header_table_height / 4) - 0.05 * inch
            c.drawString(0.4 * inch + col1_width, values_y, exam_date)

        # Second header row - exam date(s), vertically centered
        c.setFont("Helvetica-Bold", 8)
        # Center exam date(s) vertically in the bottom half of header
        exam_date_y = table_start_y - header_table_height + (header_table_height / 4) - 0.03 * inch  # Center in bottom half

        if comparative:
            # Extract session timestamps and show times (second line)
            if test_results and len(test_results) > 0:
                # Session timestamps are at the end of the tuple
                sessions = test_results[0][-session_count:]  # Last N elements are session timestamps
                for i in range(session_count):
                    col_x = 0.4 * inch + col1_width + (i * value_col_width)
                    session_timestamp = sessions[i] if i < len(sessions) else f"Session {i + 1}"

                    # Extract time part from timestamp
                    if ', ' in session_timestamp:
                        parts = session_timestamp.split(', ', 1)
                        time_part = parts[1] if len(parts) > 1 else session_timestamp  # e.g., "2025 17:09"
                    else:
                        # Fallback if no comma+space found
                        parts = session_timestamp.split(' ', 1)
                        time_part = parts[1] if len(parts) > 1 else session_timestamp

                    # Draw time on second line with text wrapping
                    available_width = value_col_width - 0.1 * inch
                    if c.stringWidth(time_part, "Helvetica-Bold", 8) > available_width:
                        # Wrap time if too long
                        words = time_part.split()
                        lines = []
                        current_line = ""
                        for word in words:
                            test_line = current_line + (" " if current_line else "") + word
                            if c.stringWidth(test_line, "Helvetica-Bold", 8) <= available_width:
                                current_line = test_line
                            else:
                                if current_line:
                                    lines.append(current_line)
                                current_line = word
                        if current_line:
                            lines.append(current_line)

                        # Draw wrapped time
                        for j, line in enumerate(lines[:2]):  # Max 2 lines
                            y_pos = exam_date_y - (j * 0.10 * inch)
                            c.drawString(col_x, y_pos, line.strip())
                    else:
                        c.drawString(col_x, exam_date_y, time_part)
            else:
                for i in range(session_count):
                    col_x = 0.4 * inch + col1_width + (i * value_col_width)
                    c.drawString(col_x, exam_date_y, f"Session {i + 1}")
        else:
            # Lower line left blank for cleaner look
            pass

        # Adjust table data start position to account for taller header
        table_start_y = table_start_y - header_table_height

        # Table data
        current_y = table_start_y - row_height
        c.setFont("Helvetica", 10.2)  # Slightly smaller to avoid overlap
        c.setLineWidth(0.4)

        start_idx = start_idx_global
        end_idx = min(start_idx + rows_this_page, len(test_results))

        for i, result in enumerate(test_results[start_idx:end_idx]):
            if comparative:
                # Extract test name and all values
                test_name = result[0]
                values = result[1:session_count + 1]  # Next N elements are values
                normal_range_text = normal_ranges.get(normalize_test_name(test_name), "") or normal_ranges.get(test_name.lower(), "")

                # Derive per-value statuses using normal ranges; fallback to Gemini summary mapping
                value_statuses = []
                value_categories = []
                for val in values:
                    status_val = compute_status_from_range(str(val), normal_range_text)
                    if not status_val:
                        status_val = metric_statuses.get(test_name.lower(), get_metric_status(test_name, ai_summary))
                    value_statuses.append(status_val)
                    value_categories.append(categorize_metric_status(status_val))

                # Row-level category: take the worst among values
                if "bad" in value_categories:
                    category = "bad"
                elif "moderate" in value_categories:
                    category = "moderate"
                else:
                    category = "good"
                status_color = get_status_color(category)
            else:
                test_name, value, unit = result[:3]
                normal_range_text = normal_ranges.get(normalize_test_name(test_name), "") or normal_ranges.get(test_name.lower(), "-")
                # Color row based on metric status
                status = metric_statuses.get(normalize_test_name(test_name), "")
                category = metric_categories.get(normalize_test_name(test_name), categorize_metric_status(status))
                status_color = get_status_color(category)

            # Neutral row background (striped)
            if i % 2 == 0:
                c.setFillColor(Color(0.992, 0.988, 0.986))
            else:
                c.setFillColor(Color(1.0, 0.998, 0.996))
            c.rect(0.3 * inch, current_y, width - 0.6 * inch, row_height, fill=True, stroke=False)

            # Subtle border
            c.setStrokeColor(Color(0.85, 0.85, 0.88))
            c.setLineWidth(0.25)
            c.rect(0.3 * inch, current_y, width - 0.6 * inch, row_height, fill=False, stroke=True)

            # Test name with controlled two-line clamp
            c.setFillColor(Color(0.08, 0.08, 0.15))

            available_width = col1_width - 0.1 * inch
            name_lines = []
            words = test_name.split()
            current_line = ""
            for word in words:
                test_line = current_line + (" " if current_line else "") + word
                if c.stringWidth(test_line, "Helvetica", 10) <= available_width:
                    current_line = test_line
                else:
                    if current_line:
                        name_lines.append(current_line)
                    if c.stringWidth(word, "Helvetica", 10) > available_width:
                        # Split long word across lines instead of truncating
                        remaining = word
                        while remaining:
                            chunk = remaining
                            while c.stringWidth(chunk, "Helvetica", 10) > available_width and len(chunk) > 1:
                                chunk = chunk[:-1]
                            name_lines.append(chunk)
                            remaining = remaining[len(chunk):]
                        current_line = ""
                    else:
                        current_line = word
            if current_line:
                name_lines.append(current_line)
            name_lines = name_lines[:2]

            name_base_y = current_y + row_height - 0.14 * inch
            line_height = 0.15 * inch
            for idx, line in enumerate(name_lines):
                y_pos = name_base_y - idx * line_height
                c.drawString(0.4 * inch, y_pos, line.strip())

            # Normal range from extracted text when available
            c.setFont("Helvetica-Bold", 9)
            c.setFillColor(Color(0.05, 0.05, 0.05))
            c.drawString(0.4 * inch, current_y + 0.08 * inch, f"Normal range: {normal_range_text or '-'}")
            c.setFont("Courier-Bold", 11.5)

            # Tags removed; only color coding remains

                # Values - simplified (no bars)

            if comparative:
                for j, val in enumerate(values):
                    col_x = 0.4 * inch + col1_width + (j * value_col_width)
                    val_str = str(val)

                    # Wrap value text to up to 2 lines
                    available_width = value_col_width - 0.14 * inch
                    val_words = val_str.split()
                    val_lines = []
                    current_val = ""
                    for vw in val_words:
                        test_val = current_val + (" " if current_val else "") + vw
                        if c.stringWidth(test_val, "Courier-Bold", 10) <= available_width:
                            current_val = test_val
                        else:
                            if current_val:
                                val_lines.append(current_val)
                            # Split long token if needed
                            if c.stringWidth(vw, "Courier-Bold", 10) > available_width:
                                remaining = vw
                                while remaining:
                                    chunk = remaining
                                    while c.stringWidth(chunk, "Courier-Bold", 10) > available_width and len(chunk) > 1:
                                        chunk = chunk[:-1]
                                    val_lines.append(chunk)
                                    remaining = remaining[len(chunk):]
                                current_val = ""
                            else:
                                current_val = vw
                    if current_val:
                        val_lines.append(current_val)
                    val_lines = val_lines[:2]

                    # Value text with per-value status color
                    val_cat = value_categories[j] if j < len(value_categories) else category
                    c.setFillColor(get_status_color(val_cat))
                    c.setFont("Courier-Bold", 11.5)
                    line_height_val = 0.15 * inch
                    base_y_val = current_y + 0.18 * inch
                    for idx, vline in enumerate(val_lines):
                        c.drawString(col_x, base_y_val - idx * line_height_val, vline.strip())

                        # No bars; text color only
            else:
                values_text = f"{value} {unit}"
                col_x = 0.4 * inch + col1_width
                available_width = col2_width - 0.14 * inch
                val_words = values_text.split()
                val_lines = []
                current_val = ""
                for vw in val_words:
                    test_val = current_val + (" " if current_val else "") + vw
                    if c.stringWidth(test_val, "Courier-Bold", 10) <= available_width:
                        current_val = test_val
                    else:
                        if current_val:
                            val_lines.append(current_val)
                        if c.stringWidth(vw, "Courier-Bold", 10) > available_width:
                            remaining = vw
                            while remaining:
                                chunk = remaining
                                while c.stringWidth(chunk, "Courier-Bold", 10) > available_width and len(chunk) > 1:
                                    chunk = chunk[:-1]
                                val_lines.append(chunk)
                                remaining = remaining[len(chunk):]
                            current_val = ""
                        else:
                            current_val = vw
                if current_val:
                    val_lines.append(current_val)
                val_lines = val_lines[:2]

                c.setFillColor(status_color)
                c.setFont("Courier-Bold", 11.5)
                line_height_val = 0.15 * inch
                base_y_val = current_y + 0.18 * inch
                for idx, vline in enumerate(val_lines):
                    c.drawString(col_x, base_y_val - idx * line_height_val, vline.strip())

                # No bars; text color only
            c.setFont("Helvetica", 10.2)

            current_y -= row_height

        c.save()
        buffer.seek(0)
        buffers.append(buffer)

        # Advance global start index for next page
        start_idx_global = end_idx

    # Return all page buffers
    return buffers


def insert_blank_page_after_first(writer, width, height):
    """Insert a blank page after the first page."""
    # Get all current pages
    pages = list(writer.pages)

    # Clear writer and add pages with blank page inserted
    writer = PdfWriter()

    # Add first page
    if len(pages) > 0:
        writer.add_page(pages[0])

    # Add blank page
    blank_buffer = BytesIO()
    c = canvas.Canvas(blank_buffer, pagesize=(width, height))
    c.setPageCompression(1)
    c.save()
    blank_buffer.seek(0)
    blank_reader = PdfReader(blank_buffer)
    writer.add_page(blank_reader.pages[0])

    # Add remaining pages
    for page in pages[1:]:
        writer.add_page(page)

    return writer

def main():
    """Main function to run the PDF header/footer addition."""

    # Step 1: Ask user to choose a file
    print("Available PDF files in current directory:")
    pdf_files = []
    
    # Check current directory first
    for file in Path(".").glob("*.pdf"):
        # Skip files that already have headers/footers added
        if "_with_header_footer" not in file.name:
            pdf_files.append(file.name)
    
    # If no PDFs found in current directory, check parent directory
    if not pdf_files:
        print("No PDF files found in current directory, checking parent directory...")
        for file in Path("..").glob("*.pdf"):
            # Skip files that already have headers/footers added
            if "_with_header_footer" not in file.name:
                pdf_files.append(file.name)
    
    if not pdf_files:
        print("No PDF files found in current or parent directory.")
        sys.exit(1)

    for i, pdf_file in enumerate(pdf_files, 1):
        print(f"{i}. {pdf_file}")

    while True:
        try:
            choice = input("\nChoose a PDF file (enter number): ").strip()
            choice_idx = int(choice) - 1
            if 0 <= choice_idx < len(pdf_files):
                input_pdf = pdf_files[choice_idx]
                break
            else:
                print(f"Please enter a number between 1 and {len(pdf_files)}")
        except ValueError:
            print("Please enter a valid number")

    # Set output filename dynamically
    output_pdf = f"Fit Carvaan_{input_pdf}"

    # Setup HTTP API for database operations
    print("Setting up Cloudflare Database API...")
    API_BASE = "https://admin.fitcarvaan.com"
    use_database = True

    # Ask for patient ID and check if exists
    while True:
        try:
            patient_id = input("\nEnter patient ID: ").strip()
            if not patient_id:
                print("Patient ID cannot be empty. Please enter a valid patient ID.")
                continue

            # Check if patient exists and show details
            if check_existing_patient(API_BASE, patient_id):
                continue_choice = input(f"\nPatient '{patient_id}' already exists. Continue with this patient? (y/n): ").strip().lower()
                if continue_choice not in ['y', 'yes']:
                    print("Please enter a different patient ID.")
                    continue

            break

        except EOFError:
            # Running in non-interactive mode
            print("Running in non-interactive mode. Using default patient ID: shweta001")
            patient_id = "shweta001"
            break
        except KeyboardInterrupt:
            print("\nOperation cancelled by user.")
            sys.exit(0)

    # Custom header text - left and right aligned
    header_left_text = "FIT CARVAAN"
    header_right_text = "96% Reports released within 06 Hours of sample reaching the lab, 9 out of 10 Doctors Trust that ThyroCare Reports are Accurate and Reliable and 1200 + Tests and Profiles"

    # Dark green gradients using #4c5e59
    # Footer gradient: from #4c5e59 to darker
    footer_gradient = (
        Color(0.298, 0.369, 0.349),  # #4c5e59
        Color(0.259, 0.329, 0.310)   # darker #42504f
    )

    # Header gradient: from darker to #4c5e59
    header_gradient = (
        Color(0.259, 0.329, 0.310),  # darker #42504f
        Color(0.298, 0.369, 0.349)   # #4c5e59
    )

    # Font size
    font_size = 8

    # Get Gemini API key from database
    print("Fetching Gemini API key from database...")
    gemini_api_key = get_api_key_http(API_BASE, "gemini_api_key")

    if not gemini_api_key:
        print("❌ ERROR: Gemini API key not found in database!")
        print("Please store the API key in Cloudflare first using the API endpoints.")
        print("Contact your administrator or use the API key management endpoints.")
        sys.exit(1)


    # Check if input file exists
    if not Path(input_pdf).exists():
        print(f"Error: Input file '{input_pdf}' not found.")
        sys.exit(1)

    try:
        # Step 0: Extract patient info from first page
        print("Step 0: Extracting patient information from first page...")
        first_page_text = extract_text_from_first_page(input_pdf)
        if first_page_text:
            patient_info = extract_patient_info_from_first_page(first_page_text, gemini_api_key)
            print("Patient information extracted:")
            print(f"  Name: {patient_info.get('patient_name', 'Not Found')}")
            print(f"  Age: {patient_info.get('patient_age', 'Not Found')}")
            print(f"  Gender: {patient_info.get('patient_gender', 'Not Found')}")
            print(f"  Date: {patient_info.get('report_date', 'Not Found')}")
            print(f"  Status: {patient_info.get('report_status', 'Not Found')}")
            print(f"  Tests: {', '.join(patient_info.get('tests_asked', []))}")
        else:
            print("No text found on first page")
            patient_info = {
                "patient_name": "Not Found",
                "patient_age": "Not Found",
                "patient_gender": "Not Found",
                "report_date": "Not Found",
                "tests_asked": [],
                "report_status": "Not Found"
            }

        # Step 1: Process PDF and extract text
        print("Step 1: Processing PDF and extracting text...")
        extracted_text = extract_text_from_pdf(input_pdf)
        print(f"Extracted {len(extracted_text)} characters of text")
        normal_ranges = extract_normal_ranges_from_text(extracted_text)
        # Apply overrides for key labs if missing
        for k, v in REFERENCE_RANGE_OVERRIDES.items():
            if k not in normal_ranges:
                normal_ranges[k] = v
        capture_count = len(normal_ranges)
        # Add normalized keys for consistent lookup
        normalized_ranges = {normalize_test_name(k): v for k, v in normal_ranges.items()}
        normal_ranges.update(normalized_ranges)
        print(f"Captured {capture_count} normal/reference ranges (including overrides)")

        # Extract exam date from the PDF text
        exam_date = "Date Not Found"
        import re
        # Look for date patterns like "19 Dec, 2025" or similar
        date_match = re.search(r'(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})', extracted_text)
        if date_match:
            exam_date = date_match.group(1).strip()

        # Step 2: Get AI summary from Gemini with retry logic
        print("Step 2: Generating AI patient summary...")
        max_retries = 5
        retry_count = 0
        ai_summary = ""
        gemini_usage = {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0}

        while retry_count < max_retries:
            try:
                ai_summary, gemini_usage = get_gemini_patient_summary(extracted_text, gemini_api_key)

                # Step 3: Extract all test results from Gemini's response
                print(f"Step 3: Extracting all test results from Gemini response (attempt {retry_count + 1})...")
                all_test_results = extract_all_test_results_from_gemini(ai_summary)
                print(f"Extracted {len(all_test_results)} test results from Gemini")

                # Merge normal ranges supplied by Gemini (fifth column) into our range map
                for row in all_test_results:
                    if len(row) >= 5:
                        name, _v, _u, _s, normal_range = row[:5]
                        nr_clean = str(normal_range).strip()
                        if nr_clean and nr_clean != "-":
                            normal_ranges[name.lower()] = nr_clean
                            normal_ranges[normalize_test_name(name)] = nr_clean

                # Debug: Check if markers are present
                if "**ALL_TEST_RESULTS_START**" not in ai_summary or "**ALL_TEST_RESULTS_END**" not in ai_summary:
                    print("DEBUG: Test result markers not found in Gemini response")
                    # Show a snippet of the response for debugging
                    response_preview = ai_summary[:500] + "..." if len(ai_summary) > 500 else ai_summary
                    print(f"DEBUG: Response preview: {response_preview}")
                else:
                    print("DEBUG: Test result markers found in Gemini response")

                # Check if we got valid results (more than 0 test results and non-zero tokens)
                if len(all_test_results) > 0 and gemini_usage.get('total_tokens', 0) > 0:
                    print("AI summary generated successfully")
                    break
                else:
                    print(f"Warning: Invalid response - {len(all_test_results)} test results and {gemini_usage.get('total_tokens', 0)} tokens.")
                    print("This may indicate Gemini API returned incomplete or malformed response.")
                    if retry_count < max_retries - 1:
                        print(f"Automatically retrying Gemini API call... ({retry_count + 1}/{max_retries})")
                        retry_count += 1
                        import time
                        time.sleep(3)  # Wait 3 seconds before retry
                        continue
                    else:
                        print(f"CRITICAL ERROR: Failed to get valid AI response after {max_retries} attempts.")
                        print("Cannot proceed with PDF processing due to AI extraction failure.")
                        print("Please check your internet connection and Gemini API key, then try again.")
                        return  # Exit the function completely

            except Exception as e:
                print(f"Error in Gemini API call (attempt {retry_count + 1}): {e}")
                if retry_count < max_retries - 1:
                    print(f"Retrying... ({retry_count + 1}/{max_retries})")
                    retry_count += 1
                    import time
                    time.sleep(3)
                    continue
                else:
                    print(f"CRITICAL ERROR: Gemini API failed after {max_retries} attempts: {e}")
                    print("Cannot proceed with PDF processing. Please try again later.")
                    return  # Exit the function completely

        # Step 3: Handle database operations if enabled
        comparative_data = None
        if use_database and patient_id:
            # Store new test results FIRST
            # Build status map for persistence (use Gemini status if present, else range/heuristic)
            status_map = {}
            metric_statuses = {}
            for row in all_test_results:
                if len(row) >= 4:
                    name, value, unit, status = row[:4]
                else:
                    name, value, unit = row[:3]
                    # Derive status from range first, then Gemini summary
                    range_text = normal_ranges.get(normalize_test_name(name), "") or normal_ranges.get(name.lower(), "")
                    status = compute_status_from_range(str(value), range_text) or get_metric_status(name, ai_summary)
                norm_name = normalize_test_name(name)
                metric_statuses[norm_name] = status
                status_map[norm_name] = categorize_metric_status(status).title()

            if insert_test_results_http(API_BASE, patient_id, all_test_results, exam_date, status_map=status_map):
                print(f"Successfully stored test results for patient {patient_id}")
            else:
                print(f"Failed to store test results for patient {patient_id}")

            # THEN get comparative results from API (including the newly stored data)
            comparative_api_response = get_comparative_results_http(API_BASE, patient_id)
            comparative_api_data = comparative_api_response.get('comparativeData', [])
            if comparative_api_data:
                print(f"Found existing test history for patient {patient_id}")
                # Transform API response to expected tuple format with dynamic session info
                comparative_data = []
                sessions = comparative_api_response.get('sessions', [])
                session_count = comparative_api_response.get('sessionCount', len(sessions))

                for item in comparative_api_data:
                    # Create tuple with test_name followed by all values, then all session timestamps
                    row_data = [item['test_name']]
                    for i in range(session_count):
                        value_key = f'value_{i + 1}'
                        row_data.append(item.get(value_key, '-'))
                    # Add session timestamps at the end
                    row_data.extend(sessions)
                    comparative_data.append(tuple(row_data))

                # Deduplicate rows where the same metric has slightly different names across sessions
                comparative_data = merge_comparative_rows(comparative_data, session_count)

                print(f"Showing comparative results across {session_count} test sessions")

        # Step 4: Display test results in console
        print_test_results_to_console(all_test_results)

        # Step 5: Extract concerning metrics for personalized alarming summary
        concerning_metrics = extract_concerning_metrics(ai_summary)

        # Step 6: Create final PDF with AI summary
        print("Step 6: Creating final PDF with AI summary...")
        reader = PdfReader(input_pdf)
        writer = PdfWriter()

        # Get page dimensions from first page
        first_page = reader.pages[0]
        page_width = float(first_page.mediabox.width)
        page_height = float(first_page.mediabox.height)

        # Add cover page with image and patient info instead of original first page
        cover_buffer = create_cover_page(page_width, page_height, patient_info)
        cover_reader = PdfReader(cover_buffer)
        writer.add_page(cover_reader.pages[0])

        # Add summary page with concerning metrics (no white strip)
        summary_buffer = create_summary_page(page_width, page_height, ai_summary, 2, concerning_metrics)
        summary_reader = PdfReader(summary_buffer)
        writer.add_page(summary_reader.pages[0])

        # Add remaining pages (with headers and footers attached before and after content, except the last page)
        remaining_pages = reader.pages[1:]
        for i, page in enumerate(remaining_pages):
            if i < len(remaining_pages) - 1:  # Not the last page
                # Get original page dimensions
                page_width = float(page.mediabox.width)
                page_height = float(page.mediabox.height)

                # Calculate header and footer heights
                header_height = 0
                footer_height = 0

                # Get header image dimensions
                header_image_path = "header.png"
                if os.path.exists(header_image_path):
                    try:
                        from reportlab.lib.utils import ImageReader
                        img = ImageReader(header_image_path)
                        img_width, img_height = img.getSize()
                        # Scale to fit page width
                        header_height = (img_height * page_width) / img_width
                    except Exception as e:
                        print(f"Warning: Could not get header image dimensions: {e}")

                # Get footer image dimensions
                footer_image_path = "footer.png"
                if os.path.exists(footer_image_path):
                    try:
                        from reportlab.lib.utils import ImageReader
                        img = ImageReader(footer_image_path)
                        img_width, img_height = img.getSize()
                        # Scale to fit page width
                        footer_height = (img_height * page_width) / img_width
                    except Exception as e:
                        print(f"Warning: Could not get footer image dimensions: {e}")

                # Create extended page height
                extended_height = header_height + page_height + footer_height

                # Create new extended page
                from reportlab.pdfgen import canvas
                from io import BytesIO

                extended_buffer = BytesIO()
                c = canvas.Canvas(extended_buffer, pagesize=(page_width, extended_height))
                c.setPageCompression(1)

                # Draw header at the top
                if os.path.exists(header_image_path) and header_height > 0:
                    try:
                        compressed_img = compress_image_to_jpeg(header_image_path, quality=60)
                        if compressed_img:
                            c.drawImage(ImageReader(compressed_img), 0, extended_height - header_height, width=page_width, height=header_height)
                        else:
                            c.drawImage(header_image_path, 0, extended_height - header_height, width=page_width, height=header_height)
                    except Exception as e:
                        print(f"Warning: Could not draw header image: {e}")

                # Draw footer at the bottom
                if os.path.exists(footer_image_path) and footer_height > 0:
                    try:
                        c.drawImage(footer_image_path, 0, 0, width=page_width, height=footer_height)
                    except Exception as e:
                        print(f"Warning: Could not draw footer image: {e}")

                # Draw the original page content in the middle (scaled to fit)
                content_y = footer_height
                content_height = page_height

                # Save the extended template
                c.save()
                extended_buffer.seek(0)

                # Create the final page by merging the original page onto the extended template
                extended_reader = PdfReader(extended_buffer)
                extended_page = extended_reader.pages[0]

                # Position the original page content in the middle of the extended page
                # The original content should be placed at y = footer_height
                from pypdf import Transformation
                transformation = Transformation().translate(0, footer_height)
                page.add_transformation(transformation)

                # Merge the original page onto the extended page
                extended_page.merge_page(page)

                # Add the merged page to the writer
                writer.add_page(extended_page)
            else:
                # Last page - add without header/footer
                writer.add_page(page)

        # Extract all test results from Gemini's response for the detailed results page
        all_test_results = extract_all_test_results_from_gemini(ai_summary)
        if all_test_results:
            # Use comparative data if available, otherwise use regular test results
            display_data = comparative_data if comparative_data else all_test_results

            # Get session count for dynamic column layout
            session_count = comparative_api_response.get('sessionCount', 2) if comparative_api_response else 2

            # Add detailed results pages with test results at the very end (may span multiple pages, no white strip)
            results_buffers = create_detailed_results_page(page_width, page_height, display_data, exam_date, comparative=bool(comparative_data), session_count=session_count, concerning_metrics=concerning_metrics, ai_summary=ai_summary, normal_ranges=normal_ranges)
            for i, buffer in enumerate(results_buffers):
                buffer.seek(0)  # Ensure buffer is at the beginning
                results_reader = PdfReader(buffer)
                if len(results_reader.pages) > 0:
                    writer.add_page(results_reader.pages[0])

        # Save final PDF
        with open(output_pdf, "wb") as output_file:
            writer.write(output_file)

        # Compress the PDF using pikepdf to reduce file size
        print("Compressing PDF to reduce file size...")
        try:
            import pikepdf
            from pikepdf import Pdf
            import time
            
            # Small delay to ensure file handles are released
            time.sleep(1.0)
            
            # Compress directly to a temporary file, then replace
            temp_compressed = output_pdf.replace('.pdf', '_temp_compressed.pdf')
            
            # Open original PDF and compress to temp file
            with Pdf.open(output_pdf) as pdf:
                pdf.save(temp_compressed, 
                        compress_streams=True,
                        stream_decode_level=pikepdf.StreamDecodeLevel.generalized,
                        recompress_flate=True)
            
            # Now replace the original with the compressed version
            # Use multiple attempts with delays in case of temporary locking
            max_attempts = 3
            for attempt in range(max_attempts):
                try:
                    os.replace(temp_compressed, output_pdf)
                    print("PDF compression completed successfully")
                    break
                except OSError as e:
                    if attempt < max_attempts - 1:
                        print(f"File replacement attempt {attempt + 1} failed, retrying...")
                        time.sleep(0.5)
                    else:
                        raise e
            
        except ImportError:
            print("pikepdf not available. PDF saved without compression")
        except Exception as e:
            print(f"PDF compression failed: {e}")
            # Clean up the temporary compressed file if it exists
            try:
                if 'temp_compressed' in locals() and os.path.exists(temp_compressed):
                    os.remove(temp_compressed)
            except:
                pass

        print(f"Final PDF saved to: {output_pdf}")
        print("AI patient summary added to page 2")
        print("Success! PDF processing completed.")

        # Display Gemini token usage and cost
        if gemini_usage:
            input_tokens = gemini_usage.get('input_tokens', 0)
            output_tokens = gemini_usage.get('output_tokens', 0)
            total_tokens = gemini_usage.get('total_tokens', 0)

            # Calculate costs (pricing: $0.1 per 1M input tokens, $0.3 per 1M output tokens)
            input_cost = (input_tokens / 1_000_000) * 0.1
            output_cost = (output_tokens / 1_000_000) * 0.3
            total_cost_usd = input_cost + output_cost
            total_cost_inr = total_cost_usd * 90

            print(f"\nGemini API Token Usage:")
            print(f"  Input tokens: {input_tokens}")
            print(f"  Output tokens: {output_tokens}")
            print(f"  Total tokens: {total_tokens}")
            print(f"  Cost: ${total_cost_usd:.4f} USD (${input_cost:.4f} input + ${output_cost:.4f} output)")
            print(f"  Cost in INR: ₹{total_cost_inr:.2f}")
    except Exception as e:
        print(f"Error processing PDF: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
