
from app.chunking import split_pages_into_chunks

pages = [
    {
        "page_number": 1,
        "text": (
            "An AI agent can perform tasks autonomously. " * 100
        ),
    }
]

chunks = split_pages_into_chunks(
    pages,
    filename="sample.pdf",
)

print("Total chunks:", len(chunks))

for chunk in chunks[:2]:
    print("\nMetadata:", chunk["metadata"])
    print("Text length:", len(chunk["text"]))
    print("Text preview:", chunk["text"][:100])
