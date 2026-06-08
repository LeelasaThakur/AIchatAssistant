import os
from pypdf import PdfReader
import docx


def allowed_file(filename: str, allowed_extensions: set) -> bool:
    """Return True if the filename has an extension in the whitelist."""
    return "." in filename and filename.rsplit(".", 1)[1].lower() in allowed_extensions


def extract_text_from_file(file_path: str) -> str | None:
    """
    Extract raw text from TXT, PDF, and DOCX files.
    Returns None for images or unsupported formats.
    """
    ext = os.path.splitext(file_path)[1].lower()

    try:
        if ext == ".txt":
            try:
                with open(file_path, "r", encoding="utf-8") as fh:
                    return fh.read()
            except UnicodeDecodeError:
                with open(file_path, "r", encoding="latin-1") as fh:
                    return fh.read()

        elif ext == ".pdf":
            reader = PdfReader(file_path)
            pages = [page.extract_text() for page in reader.pages if page.extract_text()]
            return "\n".join(pages)

        elif ext in (".doc", ".docx"):
            document = docx.Document(file_path)
            parts = [p.text for p in document.paragraphs]
            for table in document.tables:
                for row in table.rows:
                    parts.append(" | ".join(cell.text for cell in row.cells))
            return "\n".join(parts)

    except Exception as exc:
        print(f"Error parsing file {file_path}: {exc}")
        return f"[Error extracting text: {exc}]"

    return None  # image or unsupported type