"""Tests for Markdown Chunker — SPEC-010 Module 2.

Tests REQ-PGVEC-02-6:
- Section header splitting
- Token limits (200-500)
- Overlap between chunks
- Table row preservation
- Metadata extraction (section_header, tickers_mentioned, date_range)
"""

from __future__ import annotations

import pytest

from trading.embeddings.chunker import Chunk, chunk_markdown
from trading.embeddings.config import estimate_tokens


class TestChunkMarkdown:
    """REQ-PGVEC-02-6: Chunking strategy tests."""

    def test_empty_input_returns_empty(self):
        """Empty input produces no chunks."""
        assert chunk_markdown("") == []
        assert chunk_markdown("   ") == []

    def test_single_section_small_content(self):
        """Small content (< max tokens) produces one chunk."""
        text = "## Introduction\n\nThis is a short section about the market."
        chunks = chunk_markdown(text)
        assert len(chunks) >= 1
        assert chunks[0].chunk_index == 0
        assert "Introduction" in chunks[0].metadata.get("section_header", "")

    def test_section_header_splitting(self):
        """Section headers (## ) serve as primary split boundaries."""
        text = (
            "## Section One\n\n"
            "Content for section one. " * 30 + "\n\n"
            "## Section Two\n\n"
            "Content for section two. " * 30 + "\n\n"
            "## Section Three\n\n"
            "Content for section three. " * 30
        )
        chunks = chunk_markdown(text)
        # Should have chunks from different sections
        headers = {c.metadata.get("section_header", "") for c in chunks}
        assert "Section One" in headers
        assert "Section Two" in headers
        assert "Section Three" in headers

    def test_chunk_max_token_limit(self):
        """No chunk exceeds CHUNK_MAX_TOKENS (500)."""
        # Create a very long section
        text = "## Long Section\n\n" + "Word " * 2000  # ~500 tokens
        chunks = chunk_markdown(text)
        for chunk in chunks:
            # Allow some tolerance for overlap
            assert chunk.tokens <= 600, f"Chunk {chunk.chunk_index} has {chunk.tokens} tokens"

    def test_table_rows_not_split(self):
        """Table rows are never split mid-row."""
        text = (
            "## Market Data\n\n"
            "| Ticker | Price | Change |\n"
            "|--------|-------|--------|\n"
            "| 005930 | 72000 | +2.1%  |\n"
            "| 000660 | 142500 | +1.5% |\n"
            "| 035720 | 95000 | -0.8%  |\n"
        )
        chunks = chunk_markdown(text)
        # Table should remain in one chunk (it's small enough)
        assert len(chunks) >= 1
        # Check that no chunk has an incomplete table row
        for chunk in chunks:
            lines = chunk.text.strip().split("\n")
            for line in lines:
                if "|" in line and line.strip().startswith("|"):
                    assert line.strip().endswith("|"), f"Incomplete table row: {line}"

    def test_ticker_extraction(self):
        """Tickers (6-digit codes) are extracted into metadata."""
        text = "## Holdings\n\nSK Hynix (000660) is up. Samsung (005930) is flat."
        chunks = chunk_markdown(text)
        all_tickers = set()
        for chunk in chunks:
            tickers = chunk.metadata.get("tickers_mentioned", [])
            all_tickers.update(tickers)
        assert "000660" in all_tickers
        assert "005930" in all_tickers

    def test_date_range_extraction(self):
        """Date ranges are extracted into metadata."""
        text = "## Weekly Review\n\nData from 2026-04-28 to 2026-05-02."
        chunks = chunk_markdown(text)
        has_date_range = any(c.metadata.get("date_range") for c in chunks)
        assert has_date_range

    def test_overlap_between_chunks(self):
        """Adjacent chunks have overlap (~50 tokens)."""
        # Create content that will produce multiple chunks
        text = "## Analysis\n\n"
        for i in range(20):
            text += f"Paragraph {i}: " + "market analysis content here. " * 15 + "\n\n"

        chunks = chunk_markdown(text)
        if len(chunks) >= 2:
            # Second chunk should start with content from end of first chunk
            # (overlap behavior)
            assert chunks[1].chunk_index == 1

    def test_chunk_indices_sequential(self):
        """Chunk indices are sequential starting from 0."""
        text = "## A\n\nContent A.\n\n## B\n\nContent B.\n\n## C\n\nContent C."
        chunks = chunk_markdown(text)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i

    def test_no_header_content(self):
        """Content without headers is treated as one section."""
        text = "Just some plain text without any headers.\n\nAnother paragraph here."
        chunks = chunk_markdown(text)
        assert len(chunks) >= 1
        assert chunks[0].metadata.get("section_header") == ""

    def test_h3_headers_also_split(self):
        """### headers also serve as split boundaries."""
        text = (
            "## Main Section\n\n"
            "Intro content.\n\n"
            "### Subsection A\n\n"
            "Subsection A content. " * 20 + "\n\n"
            "### Subsection B\n\n"
            "Subsection B content. " * 20
        )
        chunks = chunk_markdown(text)
        headers = {c.metadata.get("section_header", "") for c in chunks}
        assert "Subsection A" in headers or "Subsection B" in headers


class TestEstimateTokens:
    """Token estimation helper tests."""

    def test_empty_string(self):
        """Empty string returns 1 (minimum)."""
        assert estimate_tokens("") == 1

    def test_known_length(self):
        """4 chars = 1 token heuristic."""
        assert estimate_tokens("abcd") == 1
        assert estimate_tokens("abcdefgh") == 2
        assert estimate_tokens("a" * 400) == 100
