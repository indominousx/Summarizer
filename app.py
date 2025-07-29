from flask import Flask, request, jsonify, render_template
from flask_cors import CORS
import logging
import os
import tempfile
import uuid
from werkzeug.utils import secure_filename
import PyPDF2
import docx
import cohere
from datetime import datetime
import threading
import time
import glob
import json

app = Flask(__name__)
CORS(app, origins="*") 

# ========== PDF Processing Config ==========
COHERE_API_KEY = "uKsA32m34CIcuikNVKceLmWpVFYU7hXMncnEgKmH"  # Replace with your Cohere key
CHUNK_SIZE = 3000                # Characters per chunk (Cohere limit ~3000–4000)
# ==========================================

# Initialize Cohere client
cohere_client = cohere.Client(COHERE_API_KEY)


logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger('syllabus-processor')

# File monitoring variables
monitoring_active = False
processed_files = set()
monitor_thread = None
# Hashmap to store file processing status: filename -> 0 (not summarized) or 1 (summarized)
file_status_map = {}
status_file_path = None

def initialize_status_tracking():
    """Initialize the file status tracking system"""
    global status_file_path
    status_file_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "file_status.json")
    load_file_status()

def load_file_status():
    """Load the file status hashmap from disk"""
    global file_status_map
    try:
        if os.path.exists(status_file_path):
            with open(status_file_path, 'r', encoding='utf-8') as f:
                file_status_map = json.load(f)
                logger.info(f"Loaded file status for {len(file_status_map)} files")
        else:
            file_status_map = {}
            logger.info("No existing file status found, starting fresh")
    except Exception as e:
        logger.error(f"Error loading file status: {e}")
        file_status_map = {}

def save_file_status():
    """Save the file status hashmap to disk"""
    try:
        with open(status_file_path, 'w', encoding='utf-8') as f:
            json.dump(file_status_map, f, indent=2)
        logger.info(f"Saved file status for {len(file_status_map)} files")
    except Exception as e:
        logger.error(f"Error saving file status: {e}")

def update_file_status(filename, status):
    """Update the status of a file (0 = not summarized, 1 = summarized)"""
    global file_status_map
    file_status_map[filename] = status
    save_file_status()
    logger.info(f"Updated status for {filename}: {status}")

def is_file_summarized(filename):
    """Check if a file has already been summarized"""
    return file_status_map.get(filename, 0) == 1

def monitor_uploads_folder():
    """Monitor the uploadsfiles folder for new files using polling"""
    global monitoring_active, processed_files
    
    uploads_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploadsfiles")
    
    # Create uploadsfiles folder if it doesn't exist
    if not os.path.exists(uploads_folder):
        os.makedirs(uploads_folder)
        logger.info(f"Created uploadsfiles folder: {uploads_folder}")
    
    logger.info(f"Started monitoring folder: {uploads_folder}")
    
    while monitoring_active:
        try:
            # Check for PDF and DOCX files
            pdf_files = glob.glob(os.path.join(uploads_folder, "*.pdf"))
            docx_files = glob.glob(os.path.join(uploads_folder, "*.docx"))
            all_files = pdf_files + docx_files
            
            for file_path in all_files:
                filename = os.path.basename(file_path)
                
                # Check if file has already been summarized using hashmap
                if not is_file_summarized(filename):
                    # Mark as being processed to avoid duplicate processing
                    if file_path not in processed_files:
                        update_file_status(filename, 0)  # Mark as being processed
                        process_new_file(file_path)
                        processed_files.add(file_path)
                else:
                    logger.debug(f"File {filename} already summarized, skipping")
            
            # Check every 2 seconds
            time.sleep(2)
            
        except Exception as e:
            logger.error(f"Error in file monitoring: {e}")
            time.sleep(5)  # Wait longer on error
    
    logger.info("File monitoring stopped")

def process_new_file(file_path):
    """Process a newly detected file"""
    try:
        filename = os.path.basename(file_path)
        file_ext = os.path.splitext(filename)[1].lower()
        
        logger.info(f"Auto-processing new file: {filename}")
        
        # Wait a moment for file to be fully written
        time.sleep(1)
        
        # Extract text based on file type
        if file_ext == '.pdf':
            text = extract_text_from_pdf(file_path)
        elif file_ext == '.docx':
            text = extract_text_from_docx(file_path)
        else:
            logger.info(f"Skipping unsupported file type: {filename}")
            return
        
        if not text.strip():
            logger.warning(f"No text could be extracted from {filename}")
            return
        
        # Generate summary
        if len(text) > CHUNK_SIZE:
            # Process large files in chunks
            chunks = chunk_text(text, CHUNK_SIZE)
            summaries = []
            for i, chunk in enumerate(chunks):
                logger.info(f"Summarizing chunk {i+1}/{len(chunks)} for {filename}")
                summary = summarize_text(chunk)
                if summary:
                    summaries.append(summary)
            final_summary = "\n\n".join(summaries)
        else:
            # Process smaller files directly
            final_summary = summarize_text(text)
        
        if final_summary:
            # Save summary to New folder
            save_summary_to_file(final_summary, filename)
            # Mark file as successfully summarized
            update_file_status(filename, 1)
            logger.info(f"Successfully auto-processed and saved summary for: {filename}")
        else:
            logger.error(f"Failed to generate summary for: {filename}")
            # Keep status as 0 (not summarized) so it can be retried later
            
    except Exception as e:
        logger.error(f"Error auto-processing file {file_path}: {str(e)}")
        # Keep status as 0 (not summarized) so it can be retried later

# Route for serving the frontend
@app.route('/')
def index():
    return render_template('index.html')

# Route for handling file uploads
@app.route('/upload', methods=['POST'])
def upload_file():
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'error': 'No file uploaded'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'error': 'No file selected'}), 400
        
        # Check file extension
        allowed_extensions = {'.pdf', '.docx'}
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in allowed_extensions:
            return jsonify({'success': False, 'error': 'Invalid file type. Please upload PDF or DOCX files only.'}), 400
        
        # Save uploaded file temporarily
        filename = secure_filename(file.filename)
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, f"{uuid.uuid4()}_{filename}")
        file.save(temp_path)
        
        try:
            # Extract text based on file type
            if file_ext == '.pdf':
                text = extract_text_from_pdf(temp_path)
            elif file_ext == '.docx':
                text = extract_text_from_docx(temp_path)
            
            if not text.strip():
                return jsonify({'success': False, 'error': 'No text could be extracted from the file'}), 400
            
            # Generate summary
            summary = summarize_text(text)
            
            # Save summary to New folder
            save_summary_to_file(summary, filename)
            
            # Calculate word count
            word_count = len(text.split())
            return jsonify({
                'success': True,
                'summary': summary,
                'filename': filename,
                'word_count': word_count
            })
            
        finally:
            # Clean up temporary file
            if os.path.exists(temp_path):
                os.remove(temp_path)
                
    except Exception as e:
        logger.error(f"Error processing file: {str(e)}")
        return jsonify({'success': False, 'error': 'An error occurred while processing the file'}), 500

# Extract text from PDF
def extract_text_from_pdf(file_path):
    text = ""
    with open(file_path, 'rb') as f:
        reader = PyPDF2.PdfReader(f)
        for page in reader.pages:
            text += page.extract_text()
    return text

# Extract text from DOCX
def extract_text_from_docx(file_path):
    doc = docx.Document(file_path)
    return "\n".join([para.text for para in doc.paragraphs])

# Split large text into manageable chunks
def chunk_text(text, max_length):
    return [text[i:i+max_length] for i in range(0, len(text), max_length)]

# Summarize using Cohere
def summarize_text(text):
    try:
        response = cohere_client.summarize(
            text=text,
            length='long',
            format='paragraph',
            model='command',
            temperature=0.3
        )
        return response.summary
    except Exception as e:
        logger.error(f"Error during summarization: {e}")
        return ""

# Save summary to file in New folder
def save_summary_to_file(summary, filename):
    try:
        # Create the summarizedfiles folder path
        new_folder_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "summarizedfiles")

        # Ensure the summarizedfiles folder exists
        if not os.path.exists(new_folder_path):
            os.makedirs(new_folder_path)
        
        # Create a unique filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        base_name = os.path.splitext(filename)[0]
        summary_filename = f"summary_{base_name}_{timestamp}.txt"
        summary_path = os.path.join(new_folder_path, summary_filename)
        
        # Write the summary to file
        with open(summary_path, 'w', encoding='utf-8') as f:
            f.write(f"Summary of: {filename}\n")
            f.write(f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n")
            f.write("=" * 50 + "\n\n")
            f.write(summary)
        
        logger.info(f"Summary saved to: {summary_path}")
        return summary_path
        
    except Exception as e:
        logger.error(f"Error saving summary to file: {e}")
        return None

def start_file_monitoring():
    """Start monitoring the uploadsfiles folder for new files"""
    global monitoring_active, monitor_thread
    
    if monitoring_active:
        logger.info("File monitoring is already active")
        return True
    
    try:
        # Initialize status tracking system
        initialize_status_tracking()
        
        monitoring_active = True
        monitor_thread = threading.Thread(target=monitor_uploads_folder, daemon=True)
        monitor_thread.start()
        logger.info("File monitoring started successfully")
        return True
        
    except Exception as e:
        logger.error(f"Error starting file monitoring: {e}")
        monitoring_active = False
        return False

def stop_file_monitoring():
    """Stop the file monitoring"""
    global monitoring_active, monitor_thread
    
    if not monitoring_active:
        logger.info("File monitoring is not active")
        return
    
    monitoring_active = False
    if monitor_thread:
        monitor_thread.join(timeout=5)  # Wait up to 5 seconds for thread to stop
        monitor_thread = None
    logger.info("File monitoring stopped")

@app.route('/process-syllabus', methods=['POST'])
def process_syllabus():
    try:
        logger.info("Processing syllabus file")
        
        if 'file' not in request.files:
            logger.error("No file provided")
            return jsonify({"error": "No file provided"}), 400
            
        file = request.files['file']
        
        if file.filename == '':
            logger.error("No file selected")
            return jsonify({"error": "No file selected"}), 400
            
        # Check file type
        file_ext = os.path.splitext(file.filename)[1].lower()
        if file_ext not in ['.pdf', '.docx']:
            logger.error(f"Unsupported file type: {file_ext}")
            return jsonify({"error": "Only PDF and DOCX files are supported"}), 400
        
        # Save the file temporarily
        temp_dir = tempfile.gettempdir()
        filename = secure_filename(f"{uuid.uuid4()}{file_ext}")
        file_path = os.path.join(temp_dir, filename)
        file.save(file_path)
        
        logger.info(f"File saved at {file_path}")
        
        # Extract text based on file type
        if file_ext == '.pdf':
            raw_text = extract_text_from_pdf(file_path)
        elif file_ext == '.docx':
            raw_text = extract_text_from_docx(file_path)
        
        logger.info(f"Extracted {len(raw_text)} characters of text")
        
        # Process the text in chunks
        chunks = chunk_text(raw_text, CHUNK_SIZE)
        logger.info(f"Split into {len(chunks)} chunks for summarization")
        
        # Summarize each chunk
        summaries = []
        for i, chunk in enumerate(chunks):
            logger.info(f"Summarizing chunk {i+1}/{len(chunks)}")
            summary = summarize_text(chunk)
            if summary:
                summaries.append(summary)
        
        # Combine summaries
        final_summary = "\n\n".join(summaries)
        
        # Save summary to New folder
        original_filename = file.filename
        save_summary_to_file(final_summary, original_filename)
        
        # Clean up
        try:
            os.remove(file_path)
            logger.info("Temporary file removed")
        except Exception as e:
            logger.warning(f"Failed to remove temporary file: {e}")
        
        if not final_summary:
            logger.error("Failed to generate summary")
            return jsonify({"error": "Failed to generate summary"}), 500
            
        logger.info(f"Successfully generated summary ({len(final_summary)} chars)")
        return jsonify({"success": True, "summary": final_summary})
        
    except Exception as e:
        logger.error(f"Error in process_syllabus: {str(e)}")
        return jsonify({"error": str(e)}), 500

@app.route('/health', methods=['GET'])
def health_check():
    return jsonify({"status": "healthy", "message": "Flask server is running"}), 200

@app.route('/monitor/start', methods=['POST'])
def start_monitoring():
    """Endpoint to start file monitoring"""
    try:
        if not monitoring_active:
            success = start_file_monitoring()
            if success:
                return jsonify({"success": True, "message": "File monitoring started"}), 200
            else:
                return jsonify({"success": False, "message": "Failed to start file monitoring"}), 500
        else:
            return jsonify({"success": False, "message": "File monitoring is already running"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/monitor/stop', methods=['POST'])
def stop_monitoring():
    """Endpoint to stop file monitoring"""
    try:
        if monitoring_active:
            stop_file_monitoring()
            return jsonify({"success": True, "message": "File monitoring stopped"}), 200
        else:
            return jsonify({"success": False, "message": "File monitoring is not running"}), 400
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/monitor/status', methods=['GET'])
def monitoring_status():
    """Check if file monitoring is active"""
    return jsonify({
        "monitoring_active": monitoring_active,
        "uploads_folder": os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploadsfiles"),
        "processed_files_count": len(processed_files),
        "total_files_tracked": len(file_status_map),
        "summarized_files": sum(1 for status in file_status_map.values() if status == 1)
    }), 200

@app.route('/files/status', methods=['GET'])
def get_files_status():
    """Get the status of all tracked files"""
    return jsonify({
        "files": file_status_map,
        "summary": {
            "total_files": len(file_status_map),
            "summarized": sum(1 for status in file_status_map.values() if status == 1),
            "not_summarized": sum(1 for status in file_status_map.values() if status == 0)
        }
    }), 200

@app.route('/files/reset-status', methods=['POST'])
def reset_file_status():
    """Reset the status of a specific file or all files"""
    try:
        data = request.get_json()
        filename = data.get('filename') if data else None
        
        if filename:
            # Reset specific file
            if filename in file_status_map:
                update_file_status(filename, 0)
                return jsonify({"success": True, "message": f"Reset status for {filename}"}), 200
            else:
                return jsonify({"success": False, "message": f"File {filename} not found"}), 404
        else:
            # Reset all files
            for filename in file_status_map:
                file_status_map[filename] = 0
            save_file_status()
            return jsonify({"success": True, "message": "Reset status for all files"}), 200
            
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

if __name__ == '__main__':
    logger.info("Starting Flask server on http://localhost:5000")
    
    # Start file monitoring
    start_file_monitoring()
    
    try:
        app.run(host='0.0.0.0', port=5000)
    except KeyboardInterrupt:
        logger.info("Shutting down...")
        stop_file_monitoring()
    finally:
        stop_file_monitoring()