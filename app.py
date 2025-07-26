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

if __name__ == '__main__':
    logger.info("Starting Flask server on http://localhost:5000")
    app.run(host='0.0.0.0', port=5000)