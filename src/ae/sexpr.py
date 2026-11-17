"""Minimal KiCad S-expression reader/writer.

KiCad files are S-expressions: atoms are bare tokens or double-quoted strings.
We keep quoting information (``Q``) so round-trips preserve strings vs symbols.
Used to read symbol/footprint libraries (pin audit) and to emit schematics.
"""
from __future__ import annotations

import re
from typing import Union


class Q(str):
    """A quoted string atom."""


Node = Union[str, Q, list]
_TOKEN = re.compile(r'\s*(?:(\()|(\))|"((?:[^"\\]|\\.)*)"|([^\s()"]+))')


def parse(text: str) -> Node:
    """Parse one S-expression from ``text`` (first top-level form)."""
    stack: list[list] = []
    current: list | None = None
    pos = 0
    while pos < len(text):
        m = _TOKEN.match(text, pos)
        if not m:
            if text[pos:].strip() == "":
                break
            raise ValueError(f"bad s-expression near offset {pos}: {text[pos:pos+30]!r}")
        pos = m.end()
        open_, close, quoted, bare = m.groups()
        if open_:
            new: list = []
            if current is not None:
                stack.append(current)
                current.append(new)
            current = new
        elif close:
            if current is None:
                raise ValueError("unbalanced ')'")
            if not stack:
                return current
            current = stack.pop()
        elif quoted is not None:
            val = Q(quoted.replace('\\"', '"').replace("\\\\", "\\"))
            (current if current is not None else _fail()).append(val)
        else:
            (current if current is not None else _fail()).append(bare)
    raise ValueError("unterminated s-expression")


def _fail():
    raise ValueError("atom outside list")


def dumps(node: Node, indent: int = 0) -> str:
    """Serialise with KiCad-like tab indentation (one child list per line)."""
    pad = "\t" * indent
    if isinstance(node, Q):
        return '"' + node.replace("\\", "\\\\").replace('"', '\\"') + '"'
    if isinstance(node, str):
        return node
    atoms = [n for n in node if not isinstance(n, list)]
    kids = [n for n in node if isinstance(n, list)]
    first_kid = next((i for i, n in enumerate(node) if isinstance(n, list)), len(node))
    if any(not isinstance(n, list) for n in node[first_kid:]):
        # KiCad forms never put an atom after a child list; refuse rather than reorder.
        raise ValueError(f"atom after child list in {node[:2]!r}")
    head = "(" + " ".join(dumps(a) for a in atoms)
    if not kids:
        return head + ")"
    body = "".join("\n" + "\t" * (indent + 1) + dumps(k, indent + 1) for k in kids)
    return head + body + "\n" + pad + ")"


def find_all(node: list, tag: str) -> list[list]:
    """Direct children lists whose first atom is ``tag``."""
    return [c for c in node if isinstance(c, list) and c and c[0] == tag]


def find(node: list, tag: str) -> list | None:
    hits = find_all(node, tag)
    return hits[0] if hits else None


def extract_symbol(lib_text: str, name: str) -> list:
    """Return the top-level ``(symbol "name" ...)`` form from a .kicad_sym file.

    Scans for the symbol's own text and parses only that slice (parsing a multi-MB library per lookup took ~60 s per run).
    """
    marker = f'\n\t(symbol "{name}"'
    start = lib_text.find(marker)
    if start < 0:
        raise KeyError(f"symbol {name!r} not in library")
    start += 2
    depth, i, in_str = 0, start, False
    while i < len(lib_text):
        ch = lib_text[i]
        if in_str:
            if ch == "\\":
                i += 1
            elif ch == '"':
                in_str = False
        elif ch == '"':
            in_str = True
        elif ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0:
                return parse(lib_text[start:i + 1])
        i += 1
    raise ValueError(f"unbalanced symbol {name!r}")


def extract_symbol_slow(lib_text: str, name: str) -> list:
    """Reference implementation (full parse) kept for the equivalence test."""
    for child in find_all(parse(lib_text), "symbol"):
        if child[1] == name:
            return child
    raise KeyError(f"symbol {name!r} not in library")


if __name__ == "__main__":
    sample = '(a "q\\"z" (b "x y" 1.5) (c))'
    tree = parse(sample)
    assert tree == ["a", 'q"z', ["b", "x y", "1.5"], ["c"]], tree
    try:
        dumps(["a", ["b"], "late"])
        raise SystemExit("expected ValueError for atom after list")
    except ValueError:
        pass
    assert parse(dumps(tree).replace("\n", " ").replace("\t", "")) == tree
    print("sexpr ok:", dumps(tree).replace("\n", " ").replace("\t", ""))
