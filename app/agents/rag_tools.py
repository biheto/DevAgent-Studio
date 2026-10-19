from __future__ import annotations

import ast
import hashlib
import math
import re
from collections import Counter
from pathlib import Path
from typing import Any

import tiktoken

from app.agents.project_tools import scan_project

DOC_SUFFIXES = {".md", ".txt", ".rst", ".adoc", ".java", ".py", ".ts", ".tsx", ".sql", ".yml", ".yaml"}
STOPWORDS = {"the", "and", "for", "with", "this", "that", "from", "class", "public", "private", "return"}
DEFAULT_CHUNK_TOKENS = 350
DEFAULT_OVERLAP_TOKENS = 50
_ENCODING = tiktoken.get_encoding("cl100k_base")


def process_knowledge(project_path: str, max_files: int) -> dict[str, Any]:
    scan = scan_project(project_path, max_files)
    root = Path(scan["root"])
    documents = []
    chunks = []

    for file in scan["files"]:
        if file["suffix"] not in DOC_SUFFIXES:
            continue
        path = root / file["path"]
        text = _safe_read(path)
        if not text:
            continue
        documents.append({"path": file["path"], "size": len(text)})
        chunks.extend(_chunk_text(file["path"], text))

    keywords = _keywords(chunks)
    faq = _faq(chunks, keywords)
    report = _report(scan, documents, chunks, keywords, faq)
    return {
        "scan": scan,
        "documents": documents,
        "chunks": chunks,
        "keywords": keywords,
        "faq": faq,
        "report_markdown": report,
    }


def _safe_read(path: Path) -> str:
    try:
        if path.stat().st_size > 500_000:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _token_count(text: str) -> int:
    return len(_ENCODING.encode(text))


def _chunk_text(
    path: str,
    text: str,
    chunk_size: int = DEFAULT_CHUNK_TOKENS,
    overlap: int = DEFAULT_OVERLAP_TOKENS,
) -> list[dict[str, Any]]:
    normalized = re.sub(r"\n{3,}", "\n\n", text.strip())
    suffix = Path(path).suffix.lower()
    if suffix == ".md":
        units = _markdown_units(normalized)
        strategy = "markdown_heading_paragraph"
    elif suffix == ".py":
        units = _python_units(normalized)
        strategy = "python_ast_symbol"
    elif suffix in {".java", ".ts", ".tsx"}:
        units = _source_units(normalized, suffix)
        strategy = "source_declaration"
    else:
        units = [(normalized, {})]
        strategy = "recursive_text"

    if suffix not in {".java", ".py", ".ts", ".tsx"}:
        units = _semantic_units(units)
        strategy = f"{strategy}_semantic"

    parent_size = chunk_size * 2
    parents = _pack_units(units, parent_size, min(overlap, parent_size // 4))
    chunks = []
    child_overlap = min(overlap, max(0, chunk_size // 4))
    for parent_index, (parent_content, metadata) in enumerate(parents, 1):
        parent_id = f"{path}#p{parent_index}"
        child_texts = _split_with_overlap(parent_content, chunk_size, child_overlap)
        for child_index, content in enumerate(child_texts, 1):
            if not content.strip():
                continue
            chunks.append(
                {
                    "path": path,
                    "chunk_id": f"{parent_id}-c{child_index}",
                    "content": content.strip(),
                    "metadata": {
                        "chunking_strategy": strategy,
                        "chunk_level": "child",
                        "parent_chunk_id": parent_id,
                        "parent_content": parent_content.strip(),
                        "semantic_embedding": "local_token_hash",
                        **metadata,
                    },
                    "token_count": _token_count(content),
                }
            )
    return chunks


def _semantic_units(units: list[tuple[str, dict[str, Any]]]) -> list[tuple[str, dict[str, Any]]]:
    segmented: list[tuple[str, dict[str, Any]]] = []
    for text, metadata in units:
        sentences = [part.strip() for part in re.split(r"(?<=[.!?。！？])\s+|\n+", text) if part.strip()]
        if len(sentences) < 2:
            segmented.append((text, metadata))
            continue
        current = [sentences[0]]
        previous = _sentence_embedding(sentences[0])
        for sentence in sentences[1:]:
            vector = _sentence_embedding(sentence)
            similarity = _cosine(previous, vector)
            if similarity < 0.08:
                segmented.append((" ".join(current), {**metadata, "semantic_boundary": True}))
                current = [sentence]
            else:
                current.append(sentence)
            previous = vector
        segmented.append((" ".join(current), metadata))
    return segmented


def _sentence_embedding(text: str, dimension: int = 128) -> list[float]:
    vector = [0.0] * dimension
    terms = re.findall(r"[A-Za-z][A-Za-z0-9_-]{1,}|[\u4e00-\u9fff]+", text.lower())
    for term in terms:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        vector[int.from_bytes(digest[:4], "big") % dimension] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _cosine(left: list[float], right: list[float]) -> float:
    return sum(a * b for a, b in zip(left, right, strict=True))


def _split_with_overlap(text: str, max_tokens: int, overlap: int) -> list[str]:
    token_ids = _ENCODING.encode(text)
    if len(token_ids) <= max_tokens:
        return [text]
    stride = max(1, max_tokens - overlap)
    chunks = []
    for start in range(0, len(token_ids), stride):
        end = min(start + max_tokens, len(token_ids))
        content = _ENCODING.decode(token_ids[start:end])
        while content and _token_count(content.strip()) > max_tokens:
            end -= 1
            content = _ENCODING.decode(token_ids[start:end])
        if content.strip():
            chunks.append(content.strip())
        if end >= len(token_ids):
            break
    return chunks


def _markdown_units(text: str) -> list[tuple[str, dict[str, Any]]]:
    heading_path: list[str] = []
    units: list[tuple[str, dict[str, Any]]] = []
    current: list[str] = []

    def flush() -> None:
        content = "\n".join(current).strip()
        if content:
            units.append((content, {"heading_path": heading_path.copy()}))
        current.clear()

    for line in text.splitlines():
        match = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", line)
        if match:
            flush()
            level = len(match.group(1))
            heading = match.group(2).strip()
            heading_path[:] = heading_path[: level - 1]
            heading_path.append(heading)
            current.append(line)
        elif not line.strip():
            flush()
        else:
            current.append(line)
    flush()
    return units


def _python_units(text: str) -> list[tuple[str, dict[str, Any]]]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return [(text, {})]
    lines = text.splitlines()
    declarations = [node for node in tree.body if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef))]
    if not declarations:
        return [(text, {})]
    units: list[tuple[str, dict[str, Any]]] = []
    cursor = 1
    for node in declarations:
        start = node.lineno
        end = getattr(node, "end_lineno", node.lineno)
        if cursor < start:
            prefix = "\n".join(lines[cursor - 1 : start - 1]).strip()
            if prefix:
                units.append((prefix, {}))
        symbol = node.name
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            parents = [parent.name for parent in declarations if isinstance(parent, ast.ClassDef) and parent.lineno < start <= getattr(parent, "end_lineno", 0)]
            if parents:
                symbol = f"{parents[-1]}.{symbol}"
        if isinstance(node, ast.ClassDef):
            methods = [child for child in node.body if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef))]
            if methods:
                first_method = min(method.lineno for method in methods)
                class_header = "\n".join(lines[start - 1 : first_method - 1]).strip()
                if class_header:
                    units.append((class_header, {"symbol": node.name, "start_line": start, "end_line": first_method - 1}))
                for method in methods:
                    method_start = min([method.lineno, *(decorator.lineno for decorator in method.decorator_list)])
                    method_end = getattr(method, "end_lineno", method.lineno)
                    units.append(("\n".join(lines[method_start - 1 : method_end]), {"symbol": f"{node.name}.{method.name}", "start_line": method_start, "end_line": method_end}))
            else:
                units.append(("\n".join(lines[start - 1 : end]), {"symbol": symbol, "start_line": start, "end_line": end}))
        else:
            units.append(("\n".join(lines[start - 1 : end]), {"symbol": symbol, "start_line": start, "end_line": end}))
        cursor = end + 1
    tail = "\n".join(lines[cursor - 1 :]).strip()
    if tail:
        units.append((tail, {}))
    return units


_DECLARATION = re.compile(
    r"(?m)^\s*(?:(?:export|default|abstract|final|public|private|protected|static|async)\s+)*(?:class|interface|enum|function|const|let|var)\s+([A-Za-z_$][\w$]*)"
)


def _source_units(text: str, suffix: str) -> list[tuple[str, dict[str, Any]]]:
    declaration = _DECLARATION
    if suffix == ".java":
        java_method = re.compile(
            r"(?m)^\s*(?:(?:public|private|protected|static|final|synchronized|abstract)\s+)+[\w<>?,.\[\] ]+\s+([A-Za-z_$][\w$]*)\s*\([^;{}]*\)\s*(?:throws\s+[\w., ]+)?\s*\{"
        )
        matches = sorted([*declaration.finditer(text), *java_method.finditer(text)], key=lambda item: item.start())
    else:
        matches = list(declaration.finditer(text))
    if not matches:
        return [(text, {})]
    units: list[tuple[str, dict[str, Any]]] = []
    if matches[0].start() > 0 and text[: matches[0].start()].strip():
        units.append((text[: matches[0].start()].strip(), {}))
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        source = text[match.start() : end].strip()
        units.append((source, {"symbol": match.group(1), "start_line": text.count("\n", 0, match.start()) + 1, "end_line": text.count("\n", 0, end) + 1}))
    return units


def _recursive_parts(text: str, max_tokens: int) -> list[str]:
    if _token_count(text) <= max_tokens:
        return [text.strip()] if text.strip() else []
    for separator in ("\n\n", "\n", ". ", "。", " "):
        parts = text.split(separator)
        if len(parts) > 1:
            rebuilt: list[str] = []
            carry = ""
            for part in parts:
                candidate = f"{carry}{separator if carry else ''}{part}"
                if carry and _token_count(candidate) > max_tokens:
                    rebuilt.append(carry)
                    carry = part
                else:
                    carry = candidate
            if carry:
                rebuilt.append(carry)
            if any(_token_count(part) < _token_count(text) for part in rebuilt):
                return [small for part in rebuilt for small in _recursive_parts(part, max_tokens)]
    token_ids = _ENCODING.encode(text)
    return [_ENCODING.decode(token_ids[index : index + max_tokens]) for index in range(0, len(token_ids), max_tokens)]


def _pack_units(units: list[tuple[str, dict[str, Any]]], max_tokens: int, overlap: int) -> list[tuple[str, dict[str, Any]]]:
    packed: list[tuple[str, dict[str, Any]]] = []
    current = ""
    current_meta: dict[str, Any] = {}
    for unit, metadata in units:
        for part in _recursive_parts(unit, max_tokens):
            candidate = f"{current}\n\n{part}".strip() if current else part
            if current and _token_count(candidate) > max_tokens:
                packed.append((current, current_meta))
                carry = _ENCODING.decode(_ENCODING.encode(current)[-overlap:]) if overlap else ""
                current = f"{carry}\n{part}".strip() if carry else part
                current_meta = dict(metadata or current_meta)
                if _token_count(current) > max_tokens:
                    parts = _recursive_parts(current, max_tokens)
                    packed.extend((item, current_meta) for item in parts[:-1])
                    current = parts[-1] if parts else ""
            else:
                current = candidate
                if metadata:
                    current_meta = dict(metadata)
    if current.strip():
        packed.append((current, current_meta))
    return packed


def _keywords(chunks: list[dict[str, Any]]) -> list[str]:
    counter: Counter[str] = Counter()
    for chunk in chunks:
        words = re.findall(r"[A-Za-z][A-Za-z0-9_-]{2,}|[\u4e00-\u9fa5]{2,}", chunk["content"])
        for word in words:
            lower = word.lower()
            if lower not in STOPWORDS and len(lower) <= 32:
                counter[lower] += 1
    return [word for word, _ in counter.most_common(20)]


def _faq(chunks: list[dict[str, Any]], keywords: list[str]) -> list[dict[str, str]]:
    faq = []
    for keyword in keywords[:8]:
        source = next((chunk for chunk in chunks if keyword.lower() in chunk["content"].lower()), None)
        if not source:
            continue
        faq.append(
            {
                "question": f"{keyword} 在项目中主要指什么？",
                "answer": f"可优先查看 `{source['path']}` 中的相关片段，并结合上下文补充业务解释。",
            }
        )
    return faq


def _report(scan: dict[str, Any], documents: list[dict[str, Any]], chunks: list[dict[str, Any]], keywords: list[str], faq: list[dict[str, str]]) -> str:
    keyword_text = ", ".join(keywords) if keywords else "暂未提取"
    doc_text = "\n".join(f"- `{doc['path']}` ({doc['size']} chars)" for doc in documents[:30]) or "- 暂未发现可处理文档"
    faq_text = "\n".join(f"- Q: {item['question']}\n  A: {item['answer']}" for item in faq) or "- 暂未生成 FAQ"
    return f"""# {scan['project_name']} RAG 知识加工报告

## 文档概览

- 文档数量：{len(documents)}
- 切片数量：{len(chunks)}
- 高频关键词：{keyword_text}

## 文档列表

{doc_text}

## 自动 FAQ

{faq_text}

## 后续建议

- 将 chunks 写入 pgvector / Milvus 等向量库。
- 为每个回答展示来源路径和 chunk_id。
- 增加文档过期检测和知识冲突检测。
"""
