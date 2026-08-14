"""Check a source file against Python 3.11 grammar + f-string restrictions.

SiS pins Python 3.11. PEP 701 (3.12) legalised newlines, reused quotes and
backslashes inside f-string expressions; ast.feature_version does not reject
them, so they are checked by hand here.
"""
import ast
import io
import sys
import tokenize
from pathlib import Path

BACKSLASH = chr(92)


def check(path: Path) -> int:
    src = path.read_text(encoding="utf-8")
    try:
        ast.parse(src, feature_version=(3, 11))
        print(f"OK   {path.name}: parses under Python 3.11 grammar")
    except SyntaxError as e:
        print(f"FAIL {path.name}: 3.11 parse, line {e.lineno}: {e.msg}")
        return 1

    bad = []
    for tok in tokenize.generate_tokens(io.StringIO(src).readline):
        if tok.type != tokenize.STRING:
            continue
        s = tok.string
        n = 0
        while n < len(s) and s[n] not in "\"'":
            n += 1
        prefix = s[:n].lower()
        if "f" not in prefix:
            continue
        rest = s[n:]
        if rest.startswith('"""') or rest.startswith("'''"):
            q = rest[:3]
        else:
            q = rest[0]
        body = rest[len(q):-len(q)]

        depth, expr, i = 0, "", 0
        while i < len(body):
            c = body[i]
            if c == "{":
                if i + 1 < len(body) and body[i + 1] == "{":
                    i += 2
                    continue
                depth += 1
                expr = ""
                i += 1
                continue
            if c == "}" and depth:
                depth -= 1
                if "\n" in expr:
                    bad.append((tok.start[0], "newline inside f-string expression", expr))
                if len(q) == 1 and q in expr:
                    bad.append((tok.start[0], f"delimiter {q} reused inside expression", expr))
                if BACKSLASH in expr:
                    bad.append((tok.start[0], "backslash inside f-string expression", expr))
                i += 1
                continue
            if depth:
                expr += c
            i += 1

    if bad:
        print(f"FAIL {path.name}: {len(bad)} Python 3.11 f-string violation(s)")
        for line, why, snippet in bad:
            print(f"  line {line}: {why}")
            print(f"      {snippet[:70]!r}")
        return 1
    print(f"OK   {path.name}: no PEP 701-only f-string syntax")
    return 0


if __name__ == "__main__":
    rc = 0
    for arg in sys.argv[1:]:
        rc |= check(Path(arg))
    sys.exit(rc)
