"""Embedding pipeline — SPEC-TRADING-010 pgvector semantic context retrieval.

Modules:
    config   — Embedding model configuration (Voyage AI / OpenAI)
    chunker  — Split .md files into 200-500 token chunks with overlap
    embedder — Generate embeddings via configured model API
    indexer  — Upsert chunks + embeddings into context_embeddings table
    searcher — Cosine similarity search against pgvector
"""
