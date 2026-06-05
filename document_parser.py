import os
from pypdf import PdfReader
import docx

def allowed_file(filename, allowed_extensions):
    """Checks if a file extension is in the whitelist"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in allowed_extensions

def extract_text_from_file(file_path):
    """
    Extracts raw text content from TXT, PDF, and DOCX files.
    Returns None if the file is an image or unsupported format.
    """
    ext = os.path.splitext(file_path)[1].lower()
    
    try:
        if ext == '.txt':
            # Try reading as UTF-8 first, fallback to Latin-1
            try:
                with open(file_path, 'r', encoding='utf-8') as f:
                    return f.read()
            except UnicodeDecodeError:
                with open(file_path, 'r', encoding='latin-1') as f:
                    return f.read()
                    
        elif ext == '.pdf':
            reader = PdfReader(file_path)
            text = []
            for page in reader.pages:
                page_text = page.extract_text()
                if page_text:
                    text.append(page_text)
            return "\n".join(text)
            
        elif ext in ('.doc', '.docx'):
            doc = docx.Document(file_path)
            text = [p.text for p in doc.paragraphs]
            # Also extract text from tables if any
            for table in doc.tables:
                for row in table.rows:
                    row_text = [cell.text for cell in row.cells]
                    text.append(" | ".join(row_text))
            return "\n".join(text)
            
    except Exception as e:
        print(f"Error parsing file {file_path}: {e}")
        return f"[Error extracting text from file: {str(e)}]"
        
    return None
