document.addEventListener('DOMContentLoaded', function() {
    const uploadForm = document.getElementById('uploadForm');
    const fileInput = document.getElementById('fileInput');
    const fileLabel = document.querySelector('.file-label');
    const fileText = document.querySelector('.file-text');
    const submitBtn = document.getElementById('submitBtn');
    const loadingSection = document.getElementById('loadingSection');
    const summarySection = document.getElementById('summarySection');
    const errorSection = document.getElementById('errorSection');
    const summaryContent = document.getElementById('summaryContent');
    const errorContent = document.getElementById('errorContent');

    // Handle file input change
    fileInput.addEventListener('change', function() {
        const file = this.files[0];
        if (file) {
            fileLabel.classList.add('file-selected');
            fileText.textContent = `Selected: ${file.name}`;
        } else {
            fileLabel.classList.remove('file-selected');
            fileText.textContent = 'Choose PDF or DOCX file';
        }
    });

    // Handle form submission
    uploadForm.addEventListener('submit', function(e) {
        e.preventDefault();
        
        const file = fileInput.files[0];
        if (!file) {
            showError('Please select a file to upload.');
            return;
        }

        // Validate file type
        const allowedTypes = ['application/pdf', 'application/vnd.openxmlformats-officedocument.wordprocessingml.document'];
        if (!allowedTypes.includes(file.type)) {
            showError('Please select a valid PDF or DOCX file.');
            return;
        }

        // Show loading state
        showLoading();
        
        // Create FormData object
        const formData = new FormData();
        formData.append('file', file);

        // Send file to server
        fetch('/upload', {
            method: 'POST',
            body: formData
        })
        .then(response => {
            if (!response.ok) {
                throw new Error(`HTTP error! status: ${response.status}`);
            }
            return response.json();
        })
        .then(data => {
            hideLoading();
            if (data.success) {
                showSummary(data.summary, data.word_count);
            } else {
                showError(data.error || 'An error occurred while processing the file.');
            }
        })
        .catch(error => {
            hideLoading();
            console.error('Error:', error);
            showError('Failed to upload file. Please try again.');
        });
    });

    function showLoading() {
        hideAllSections();
        loadingSection.style.display = 'block';
        submitBtn.disabled = true;
        submitBtn.textContent = 'Processing...';
    }

    function hideLoading() {
        loadingSection.style.display = 'none';
        submitBtn.disabled = false;
        submitBtn.textContent = 'Summarize Document';
    }

    function showSummary(summary, wordCount) {
        hideAllSections();
        summaryContent.innerHTML = `<div><strong>Word Count:</strong> ${wordCount}</div><hr>${summary}`;
        summarySection.style.display = 'block';
    }

    function showError(message) {
        hideAllSections();
        errorContent.textContent = message;
        errorSection.style.display = 'block';
    }

    function hideAllSections() {
        loadingSection.style.display = 'none';
        summarySection.style.display = 'none';
        errorSection.style.display = 'none';
    }

    // Drag and drop functionality
    const container = document.querySelector('.container');
    
    ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
        container.addEventListener(eventName, preventDefaults, false);
    });

    function preventDefaults(e) {
        e.preventDefault();
        e.stopPropagation();
    }

    ['dragenter', 'dragover'].forEach(eventName => {
        container.addEventListener(eventName, highlight, false);
    });

    ['dragleave', 'drop'].forEach(eventName => {
        container.addEventListener(eventName, unhighlight, false);
    });

    function highlight(e) {
        fileLabel.style.borderColor = '#667eea';
        fileLabel.style.background = '#f0f2ff';
    }

    function unhighlight(e) {
        fileLabel.style.borderColor = '#ddd';
        fileLabel.style.background = '#f8f9fa';
    }

    container.addEventListener('drop', handleDrop, false);

    function handleDrop(e) {
        const dt = e.dataTransfer;
        const files = dt.files;
        
        if (files.length > 0) {
            fileInput.files = files;
            const event = new Event('change', { bubbles: true });
            fileInput.dispatchEvent(event);
        }
    }
});
