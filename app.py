import streamlit as st
import os
from io import BytesIO
from datetime import datetime
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from reportlab.pdfgen import canvas
from reportlab.lib.utils import ImageReader

# Reuse the full processing pipeline from main.py
import main as core

# Password protection
PASSWORD = "LiveHealthy@12"  # Change this to your desired password

# API base used in main.py for key fetch and DB ops
API_BASE = "https://admin.fitcarvaan.com"

def run_pipeline(input_path: str, patient_id: str) -> str:
    """Run the same processing pipeline as main.py and return output PDF path."""

    # Fetch API key via HTTP (same as main)
    gemini_api_key = core.get_api_key_http(API_BASE, "gemini_api_key")
    if not gemini_api_key:
        raise RuntimeError("Gemini API key not found in Cloudflare (via admin.fitcarvaan.com)")

    # Extract patient info from first page
    first_page_text = core.extract_text_from_first_page(input_path)
    if first_page_text:
        patient_info = core.extract_patient_info_from_first_page(first_page_text, gemini_api_key)
    else:
        patient_info = {
            "patient_name": "Not Found",
            "patient_age": "Not Found",
            "patient_gender": "Not Found",
            "report_date": "Not Found",
            "tests_asked": [],
            "report_status": "Not Found",
        }

    # Full text
    extracted_text = core.extract_text_from_pdf(input_path)

    # Build normal ranges dict (mirrors main.py logic)
    normal_ranges = core.extract_normal_ranges_from_text(extracted_text)
    for k, v in core.REFERENCE_RANGE_OVERRIDES.items():
        if k not in normal_ranges:
            normal_ranges[k] = v
    normalized_ranges = {core.normalize_test_name(k): v for k, v in normal_ranges.items()}
    normal_ranges.update(normalized_ranges)

    # Gemini summary with retry (same as main but shorter retries)
    max_retries = 3
    for attempt in range(max_retries):
        ai_summary, gemini_usage = core.get_gemini_patient_summary(extracted_text, gemini_api_key)
        all_test_results = core.extract_all_test_results_from_gemini(ai_summary)
        if all_test_results:
            break
        if attempt == max_retries - 1:
            raise RuntimeError("Gemini response did not contain test results after retries")

    # Supplement normal_ranges from Gemini 5-tuples (test_name, value, unit, status, normal_range)
    for row in all_test_results:
        if len(row) >= 5:
            rng = row[4]
            if rng and rng != "-":
                key = core.normalize_test_name(row[0])
                if key not in normal_ranges:
                    normal_ranges[key] = rng

    # Extract exam date from text
    exam_date = "Date Not Found"
    import re
    date_match = re.search(r'(\d{1,2}\s+[A-Za-z]+,?\s+\d{4})', extracted_text)
    if date_match:
        exam_date = date_match.group(1).strip()

    # Store results and fetch comparative
    comparative_api_response = None
    comparative_data = None
    try:
        # Build status map so tags persist in storage
        status_map = {}
        for row in all_test_results:
            name = row[0]
            status = core.get_metric_status(name, ai_summary)
            status_map[name.lower()] = core.categorize_metric_status(status).title()

        core.insert_test_results_http(API_BASE, patient_id, all_test_results, exam_date, status_map=status_map)
        comparative_api_response = core.get_comparative_results_http(API_BASE, patient_id)
        comparative_api_data = comparative_api_response.get("comparativeData", []) if comparative_api_response else []
        sessions = comparative_api_response.get("sessions", []) if comparative_api_response else []
        session_count = comparative_api_response.get("sessionCount", len(sessions)) if comparative_api_response else 0
        if comparative_api_data:
            comparative_data = []
            for item in comparative_api_data:
                row = [item.get("test_name", "")]
                for i in range(session_count):
                    value_key = f"value_{i + 1}"
                    row.append(item.get(value_key, "-"))
                row.extend(sessions)
                comparative_data.append(tuple(row))
    except Exception as db_err:
        # Non-fatal: continue without comparative data
        import traceback
        print(f"[app.py] DB/comparative error: {db_err}\n{traceback.format_exc()}")
        comparative_data = None
        session_count = 2

    # Concerning metrics
    concerning_metrics = core.extract_concerning_metrics(ai_summary)

    # Build PDF (same ordering as main)
    reader = PdfReader(input_path)
    writer = PdfWriter()

    first_page = reader.pages[0]
    page_width = float(first_page.mediabox.width)
    page_height = float(first_page.mediabox.height)

    # Cover
    cover_buffer = core.create_cover_page(page_width, page_height, patient_info)
    writer.add_page(PdfReader(cover_buffer).pages[0])

    # Summary page
    summary_buffer = core.create_summary_page(page_width, page_height, ai_summary, 2, concerning_metrics)
    writer.add_page(PdfReader(summary_buffer).pages[0])

    # Remaining pages with header/footer images
    header_image_path = "header.png"
    footer_image_path = "footer.png"
    remaining_pages = reader.pages[1:]
    for i, page in enumerate(remaining_pages):
        if i < len(remaining_pages) - 1:
            # Compute header/footer heights
            header_height = 0
            footer_height = 0

            if os.path.exists(header_image_path):
                try:
                    img = ImageReader(header_image_path)
                    img_w, img_h = img.getSize()
                    header_height = (img_h * page_width) / img_w
                except Exception:
                    header_height = 0

            if os.path.exists(footer_image_path):
                try:
                    img = ImageReader(footer_image_path)
                    img_w, img_h = img.getSize()
                    footer_height = (img_h * page_width) / img_w
                except Exception:
                    footer_height = 0

            extended_height = header_height + float(page.mediabox.height) + footer_height
            extended_buffer = BytesIO()
            c = canvas.Canvas(extended_buffer, pagesize=(page_width, extended_height))
            c.setPageCompression(1)

            if os.path.exists(header_image_path) and header_height > 0:
                try:
                    compressed_img = core.compress_image_to_jpeg(header_image_path, quality=60)
                    if compressed_img:
                        c.drawImage(ImageReader(compressed_img), 0, extended_height - header_height, width=page_width, height=header_height)
                    else:
                        c.drawImage(header_image_path, 0, extended_height - header_height, width=page_width, height=header_height)
                except Exception:
                    pass

            if os.path.exists(footer_image_path) and footer_height > 0:
                try:
                    c.drawImage(footer_image_path, 0, 0, width=page_width, height=footer_height)
                except Exception:
                    pass

            c.save()
            extended_buffer.seek(0)
            extended_page = PdfReader(extended_buffer).pages[0]

            transform = Transformation().translate(0, footer_height)
            page.add_transformation(transform)
            extended_page.merge_page(page)
            writer.add_page(extended_page)
        else:
            writer.add_page(page)

    # Detailed results pages
    display_data = comparative_data if comparative_data else all_test_results
    session_count = comparative_api_response.get("sessionCount", 2) if comparative_api_response else 2
    results_buffers = core.create_detailed_results_page(page_width, page_height, display_data, exam_date, comparative=bool(comparative_data), session_count=session_count, concerning_metrics=concerning_metrics, ai_summary=ai_summary, normal_ranges=normal_ranges)
    for buffer in results_buffers:
        buffer.seek(0)
        rb = PdfReader(buffer)
        if rb.pages:
            writer.add_page(rb.pages[0])

    output_path = "temp_output.pdf"
    with open(output_path, "wb") as f:
        writer.write(f)

    return output_path

def main():
    st.set_page_config(page_title="FitCarvaan Report Portal", layout="wide")

    # App styling
    st.markdown(
        """
        <style>
            body {background: radial-gradient(circle at 20% 20%, #f7fafc, #edf2f7);} 
            .fc-card {padding: 1.25rem 1.5rem; border-radius: 16px; background: white; box-shadow: 0 12px 30px rgba(0,0,0,0.08);} 
            .fc-tag {display:inline-block; padding:4px 10px; border-radius:999px; background:#e6fffa; color:#065f46; font-size:12px; font-weight:600; margin-right:8px;}
            .fc-accent {color:#0f766e; font-weight:700;}
        </style>
        """,
        unsafe_allow_html=True,
    )

    st.markdown("<div class='fc-tag'>FitCarvaan</div> <span class='fc-accent'>Secure Blood Report Processor</span>", unsafe_allow_html=True)
    st.markdown("## Upload, analyze, and brand reports with your headers/footers")

    password = st.text_input("Enter Password", type="password")
    if password != PASSWORD:
        st.stop()

    with st.container():
        col1, col2 = st.columns([1.1, 1])
        with col1:
            st.markdown("### Patient PDF & ID")
            uploaded_file = st.file_uploader("Upload patient PDF", type="pdf")
            patient_id = st.text_input("Patient ID", placeholder="e.g., shweta001")
        with col2:
            st.markdown("### Notes")
            st.write("- Enter patient id")
            st.write("- Confirm if patient exists or not")
            st.write("- Processing takes 30-45 seconds")

    # Check if patient exists (matches CLI flow) and ask for confirmation
    existing_patient = None
    proceed_choice = None
    if patient_id:
        try:
            existing_patient = core.check_existing_patient(API_BASE, patient_id)
        except Exception as e:
            st.error(f"Error checking patient: {e}")

    if existing_patient:
        proceed_choice = st.radio(
            "Patient already exists. Continue with this patient?",
            ["No", "Yes"],
            index=0,
            key="proceed_existing_choice",
        )

    st.markdown("---")
    action_col, info_col = st.columns([1, 1])
    with action_col:
        trigger = st.button("Process PDF", type="primary", use_container_width=True)
    with info_col:
        st.caption("Processing may take a few seconds while Gemini analyzes the report.")

    if uploaded_file and patient_id and trigger:
        if existing_patient and proceed_choice != "Yes":
            st.warning("Choose 'Yes' to continue with this existing patient or pick another ID.")
            st.stop()
        with st.spinner("Processing PDF..."):
            # Save input
            input_path = "temp_input.pdf"
            with open(input_path, "wb") as f:
                f.write(uploaded_file.getvalue())

            try:
                output_path = run_pipeline(input_path, patient_id)
                with open(output_path, "rb") as f:
                    st.download_button(
                        label="Download Processed PDF",
                        data=f,
                        file_name=f"Fit Carvaan_{patient_id}.pdf",
                        mime="application/pdf",
                    )
            except Exception as e:
                st.error(f"Error processing PDF: {e}")
            finally:
                for p in ["temp_input.pdf", "temp_output.pdf"]:
                    if os.path.exists(p):
                        try:
                            os.remove(p)
                        except Exception:
                            pass
if __name__ == "__main__":
    main()