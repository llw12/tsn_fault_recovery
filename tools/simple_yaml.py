"""Small deterministic YAML subset parser used to avoid runtime pip installs.

Supports the mappings, sequences, inline JSON arrays, strings, booleans and
numbers used by scenario schema v1. Unsupported YAML constructs fail fast.
"""

from __future__ import annotations

import json
from pathlib import Path


class YamlSubsetError(ValueError):
    pass


def _inline_parts(text: str) -> list[str]:
    parts, start, depth, quote = [], 0, 0, None
    for index, ch in enumerate(text):
        if quote:
            if ch == quote and (index == 0 or text[index - 1] != "\\"):
                quote = None
        elif ch in "'\"":
            quote = ch
        elif ch in "[{":
            depth += 1
        elif ch in "]}":
            depth -= 1
        elif ch == "," and depth == 0:
            parts.append(text[start:index].strip()); start = index + 1
    parts.append(text[start:].strip())
    return [part for part in parts if part]


def _scalar(text: str):
    text = text.strip()
    if not text:
        raise YamlSubsetError("missing scalar")
    if text[0] in "[{":
        try:
            return json.loads(text.replace("'", '"'))
        except json.JSONDecodeError:
            if text.startswith("[") and text.endswith("]"):
                return [_scalar(item) for item in _inline_parts(text[1:-1])]
            if text.startswith("{") and text.endswith("}"):
                result = {}
                for item in _inline_parts(text[1:-1]):
                    if ":" not in item:
                        raise YamlSubsetError(f"invalid inline mapping entry: {item}")
                    key, value = item.split(":", 1)
                    key = key.strip().strip("'\"")
                    if not key or key in result:
                        raise YamlSubsetError(f"invalid or duplicate inline mapping key: {key!r}")
                    result[key] = _scalar(value.strip())
                return result
            raise YamlSubsetError(f"invalid inline collection: {text}")
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "'\"":
        return text[1:-1]
    lowered = text.lower()
    if lowered in {"true", "false"}:
        return lowered == "true"
    if lowered in {"null", "~"}:
        return None
    try:
        return float(text) if any(ch in text for ch in ".eE") else int(text)
    except ValueError:
        return text


def loads(text: str):
    tokens: list[tuple[int, str, int]] = []
    for line_number, raw in enumerate(text.splitlines(), 1):
        if "\t" in raw[: len(raw) - len(raw.lstrip())]:
            raise YamlSubsetError(f"line {line_number}: tabs are not allowed for indentation")
        content = raw.strip()
        if not content or content.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        if indent % 2:
            raise YamlSubsetError(f"line {line_number}: indentation must use multiples of two spaces")
        tokens.append((indent, content, line_number))
    if not tokens:
        raise YamlSubsetError("empty YAML document")

    def mapping_entry(content: str, line_number: int) -> tuple[str, str]:
        if ":" not in content:
            raise YamlSubsetError(f"line {line_number}: expected key: value")
        key, value = content.split(":", 1)
        key = key.strip()
        if not key:
            raise YamlSubsetError(f"line {line_number}: empty mapping key")
        return key, value.strip()

    def parse_block(index: int, indent: int):
        if index >= len(tokens) or tokens[index][0] != indent:
            line = tokens[index][2] if index < len(tokens) else "EOF"
            raise YamlSubsetError(f"line {line}: unexpected indentation")
        is_list = tokens[index][1].startswith("-")
        result = [] if is_list else {}
        while index < len(tokens) and tokens[index][0] == indent:
            _, content, line_number = tokens[index]
            if is_list:
                if not content.startswith("-"):
                    break
                rest = content[1:].strip()
                index += 1
                if not rest:
                    if index >= len(tokens) or tokens[index][0] <= indent:
                        raise YamlSubsetError(f"line {line_number}: empty sequence item")
                    value, index = parse_block(index, tokens[index][0])
                    result.append(value)
                elif rest.startswith(("[", "{")):
                    result.append(_scalar(rest))
                elif ":" in rest:
                    key, value_text = mapping_entry(rest, line_number)
                    item = {}
                    if value_text:
                        item[key] = _scalar(value_text)
                    else:
                        if index >= len(tokens) or tokens[index][0] <= indent:
                            raise YamlSubsetError(f"line {line_number}: missing nested value")
                        item[key], index = parse_block(index, tokens[index][0])
                    if index < len(tokens) and tokens[index][0] > indent:
                        continuation_indent = tokens[index][0]
                        continuation, index = parse_block(index, continuation_indent)
                        if not isinstance(continuation, dict):
                            raise YamlSubsetError(f"line {tokens[index - 1][2]}: list mapping continuation must be a mapping")
                        item.update(continuation)
                    result.append(item)
                else:
                    result.append(_scalar(rest))
            else:
                if content.startswith("-"):
                    break
                key, value_text = mapping_entry(content, line_number)
                if key in result:
                    raise YamlSubsetError(f"line {line_number}: duplicate mapping key {key!r}")
                index += 1
                if value_text:
                    result[key] = _scalar(value_text)
                else:
                    if index >= len(tokens) or tokens[index][0] <= indent:
                        raise YamlSubsetError(f"line {line_number}: missing nested value for {key!r}")
                    result[key], index = parse_block(index, tokens[index][0])
        return result, index

    document, final_index = parse_block(0, tokens[0][0])
    if final_index != len(tokens):
        raise YamlSubsetError(f"line {tokens[final_index][2]}: trailing unparsed content")
    return document


def load(path: Path):
    return loads(path.read_text(encoding="utf-8"))
