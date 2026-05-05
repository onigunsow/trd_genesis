"""Markdown chunker — split .md files into semantic chunks.

REQ-PGVEC-02-6:
- Split on section headers (## , ### ) as primary boundaries
- Within sections, split on double newlines or at 400 tokens (hard limit: 500)
- 50-token overlap between adjacent chunks
- Table rows never split mid-row
- Each chunk has metadata: {section_header, tickers_mentioned[], date_range}
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from trading.embeddings.config import (
    CHARS_PER_TOKEN,
    CHUNK_MAX_TOKENS,
    CHUNK_OVERLAP_TOKENS,
    CHUNK_TARGET_TOKENS,
    estimate_tokens,
)

# Regex patterns
SECTION_HEADER_RE = re.compile(r"^(#{2,3})\s+(.+)$", re.MULTILINE)
TABLE_ROW_RE = re.compile(r"^\|.*\|$", re.MULTILINE)
TICKER_RE = re.compile(r"\b\d{6}\b")  # KRX 6-digit stock codes
DATE_RANGE_RE = re.compile(r"\d{4}-\d{2}-\d{2}")


@dataclass
class Chunk:
    """A semantic chunk of markdown content."""

    chunk_index: int
    text: str
    tokens: int
    metadata: dict[str, Any] = field(default_factory=dict)


def _extract_tickers(text: str) -> list[str]:
    """Extract KRX 6-digit stock codes from text."""
    return sorted(set(TICKER_RE.findall(text)))


def _extract_date_range(text: str) -> str | None:
    """Extract date range from text (first and last dates found)."""
    dates = DATE_RANGE_RE.findall(text)
    if not dates:
        return None
    if len(dates) == 1:
        return dates[0]
    return f"{dates[0]}~{dates[-1]}"


def _is_table_block(text: str) -> bool:
    """Check if a text block is a markdown table (all lines start with |)."""
    lines = text.strip().split("\n")
    return len(lines) >= 2 and all(line.strip().startswith("|") for line in lines if line.strip())


def _split_preserving_tables(text: str) -> list[str]:
    """Split text on double newlines but keep table blocks intact.

    Also splits very long single-line/paragraph blocks at CHUNK_TARGET_TOKENS.
    """
    blocks: list[str] = []
    current_block: list[str] = []
    in_table = False

    for line in text.split("\n"):
        stripped = line.strip()

        # Detect table start/end
        if stripped.startswith("|") and "|" in stripped[1:]:
            in_table = True
            current_block.append(line)
        elif in_table and not stripped.startswith("|"):
            # End of table
            in_table = False
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
            if stripped:
                current_block.append(line)
            else:
                # Double newline equivalent
                pass
        elif not stripped and not in_table:
            # Empty line (paragraph boundary)
            if current_block:
                blocks.append("\n".join(current_block))
                current_block = []
        else:
            current_block.append(line)

    if current_block:
        blocks.append("\n".join(current_block))

    # Post-process: split any blocks that exceed CHUNK_MAX_TOKENS
    final_blocks: list[str] = []
    for block in blocks:
        if block.strip() and estimate_tokens(block) > CHUNK_MAX_TOKENS:
            final_blocks.extend(_split_large_block(block))
        elif block.strip():
            final_blocks.append(block)

    return final_blocks


def _merge_small_blocks(blocks: list[str], min_tokens: int = 50) -> list[str]:
    """Merge very small blocks with their neighbors."""
    if not blocks:
        return blocks

    merged: list[str] = []
    buffer = ""

    for block in blocks:
        if buffer:
            combined = buffer + "\n\n" + block
            if estimate_tokens(combined) <= CHUNK_TARGET_TOKENS:
                buffer = combined
            else:
                merged.append(buffer)
                buffer = block
        else:
            buffer = block

    if buffer:
        merged.append(buffer)

    return merged


def _split_large_block(text: str, max_tokens: int = CHUNK_MAX_TOKENS) -> list[str]:
    """Split a block that exceeds max_tokens into smaller pieces.

    Preserves table rows as atomic units.
    """
    if estimate_tokens(text) <= max_tokens:
        return [text]

    # If it's a table, split by rows but keep header
    if _is_table_block(text):
        lines = text.strip().split("\n")
        if len(lines) <= 3:
            return [text]

        # Keep header (first 2 lines: header + separator)
        header = "\n".join(lines[:2])
        chunks: list[str] = []
        current_rows = [header]
        for row in lines[2:]:
            current_rows.append(row)
            if estimate_tokens("\n".join(current_rows)) >= max_tokens:
                # Save current and start new chunk with header
                chunks.append("\n".join(current_rows[:-1]))
                current_rows = [header, row]
        if current_rows:
            chunks.append("\n".join(current_rows))
        return chunks

    # Split by sentences/lines
    lines = text.split("\n")

    # If it's a single long line (no newlines), split by character boundary
    if len(lines) <= 1:
        chunks = []
        char_limit = max_tokens * CHARS_PER_TOKEN
        for i in range(0, len(text), char_limit):
            chunks.append(text[i : i + char_limit])
        return chunks

    chunks = []
    current: list[str] = []

    for line in lines:
        current.append(line)
        if estimate_tokens("\n".join(current)) >= max_tokens:
            # Pop last line if it pushed over limit
            if len(current) > 1:
                chunks.append("\n".join(current[:-1]))
                current = [line]
            else:
                # Single line exceeds limit — force include
                chunks.append("\n".join(current))
                current = []

    if current:
        chunks.append("\n".join(current))

    return chunks


def chunk_markdown(text: str, source_file: str = "") -> list[Chunk]:
    """Split markdown text into semantic chunks.

    Args:
        text: Full markdown content.
        source_file: Source file identifier for metadata.

    Returns:
        List of Chunk objects with index, text, tokens, and metadata.
    """
    if not text.strip():
        return []

    # Split into sections by headers
    sections: list[tuple[str, str]] = []  # (header, content)
    header_matches = list(SECTION_HEADER_RE.finditer(text))

    if not header_matches:
        # No headers — treat entire text as one section
        sections.append(("", text))
    else:
        # Content before first header
        pre_content = text[: header_matches[0].start()].strip()
        if pre_content:
            sections.append(("", pre_content))

        # Each section
        for i, match in enumerate(header_matches):
            header = match.group(2).strip()
            start = match.end()
            end = header_matches[i + 1].start() if i + 1 < len(header_matches) else len(text)
            content = text[start:end].strip()
            if content:
                sections.append((header, content))

    # Process each section into chunks
    all_chunks: list[Chunk] = []
    chunk_index = 0

    for section_header, section_content in sections:
        # Split section into paragraph-level blocks
        blocks = _split_preserving_tables(section_content)
        blocks = _merge_small_blocks(blocks)

        # Split any oversized blocks
        final_blocks: list[str] = []
        for block in blocks:
            if estimate_tokens(block) > CHUNK_MAX_TOKENS:
                final_blocks.extend(_split_large_block(block))
            else:
                final_blocks.append(block)

        # Create chunks with overlap
        for i, block_text in enumerate(final_blocks):
            # Add overlap from previous block
            if i > 0 and CHUNK_OVERLAP_TOKENS > 0:
                prev_text = final_blocks[i - 1]
                overlap_chars = CHUNK_OVERLAP_TOKENS * CHARS_PER_TOKEN
                overlap = prev_text[-overlap_chars:] if len(prev_text) > overlap_chars else ""
                if overlap:
                    block_text = overlap.strip() + "\n" + block_text

            tokens = estimate_tokens(block_text)
            metadata: dict[str, Any] = {"section_header": section_header}

            tickers = _extract_tickers(block_text)
            if tickers:
                metadata["tickers_mentioned"] = tickers

            date_range = _extract_date_range(block_text)
            if date_range:
                metadata["date_range"] = date_range

            all_chunks.append(Chunk(
                chunk_index=chunk_index,
                text=block_text,
                tokens=tokens,
                metadata=metadata,
            ))
            chunk_index += 1

    return all_chunks
