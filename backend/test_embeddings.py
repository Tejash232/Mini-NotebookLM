
from app.vector_store import embedding_model

sentences = [
    "How can I apply for an income certificate?",
    "What is the process to obtain an income certificate?",
    "How do I cook fried rice?",
]

vectors = embedding_model.encode(
    sentences,
    normalize_embeddings=True,
)

print("Number of sentences:", len(sentences))
print("Embedding dimensions:", vectors.shape[1])

print(
    "Similarity between the first two sentences:",
    float(vectors[0] @ vectors[1]),
)

print(
    "Similarity between the first and third sentences:",
    float(vectors[0] @ vectors[2]),
)
