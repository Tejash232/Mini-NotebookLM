
import pymupdf


def extract_pdf_pages(file_path: str) -> list[dict]:
    """Extract text from a PDF while preserving page numbers."""

    pages = []

    with pymupdf.open(file_path) as pdf:
        for page_number, page in enumerate(pdf, start=1):
            text = page.get_text("text").strip()

            if text:
                pages.append({
                    "page_number": page_number,
                    "text": text,
                })

    return pages
