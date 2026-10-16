
from langchain_text_splitters import RecursiveCharacterTextSplitter


def split_pages_into_chunks(
    pages: list[dict],
    filename: str,
    chunk_size: int = 1000,
    chunk_overlap: int = 150,
) -> list[dict]:
    """Split extracted page text into chunks with source metadata."""

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        length_function=len,
        separators=["\n\n", "\n", " ", ""],
    )

    chunks = []

    for page in pages:
        page_chunks = splitter.split_text(page["text"])

        for chunk_index, chunk_text in enumerate(page_chunks):
            chunks.append({
                "text": chunk_text,
                "metadata": {
                    "source": filename,
                    "page_number": page["page_number"],
                    "chunk_index": chunk_index,
                },
            })

    return chunks
