"""Rewrite the unguarded int() calls on request input that
qa_audit/tools/unguarded_int.py reports into utils.helpers.int_param(), which
answers 400 instead of raising a 500. Other converters are only reported.

Usage: python qa_audit/tools/guard_request_numbers.py FILE [--apply]
(dry run by default: prints each call and its replacement)."""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from unguarded_int import CONVERTERS, handler_catches, is_request_value  # noqa: E402

REPLACEMENT = {"int": "int_param"}


def unguarded_calls(tree):
    parents = {c: n for n in ast.walk(tree) for c in ast.iter_child_nodes(n)}
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fname = node.func.id if isinstance(node.func, ast.Name) else (
            node.func.attr if isinstance(node.func, ast.Attribute) else "")
        if fname not in CONVERTERS or not node.args or not is_request_value(node.args[0]):
            continue
        p, guarded = parents.get(node), False
        while p is not None and not isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if isinstance(p, ast.Try) and handler_catches(p):
                guarded = True
                break
            p = parents.get(p)
        if not guarded:
            yield node, fname


def rewrite(path, apply):
    src = pathlib.Path(path).read_text(encoding="utf-8")
    lines = src.splitlines(keepends=True)
    edits = []
    for node, fname in unguarded_calls(ast.parse(src)):
        func = node.func
        old = ast.get_source_segment(src, func)
        if fname in REPLACEMENT:
            new = REPLACEMENT[fname]
        else:
            print(f"  SKIP {path}:{node.lineno}: {ast.get_source_segment(src, node)[:90]}")
            continue
        edits.append((func.lineno, func.col_offset, func.end_lineno, func.end_col_offset, old, new))
        print(f"  {path}:{node.lineno}: {old}(...) -> {new}(...)")
    if apply:
        for l1, c1, l2, c2, old, new in sorted(edits, reverse=True):
            assert l1 == l2
            line = lines[l1 - 1]
            # col offsets are in UTF-8 bytes
            b = line.encode("utf-8")
            assert b[c1:c2].decode("utf-8") == old, (path, l1, old)
            lines[l1 - 1] = (b[:c1] + new.encode("utf-8") + b[c2:]).decode("utf-8")
        pathlib.Path(path).write_text("".join(lines), encoding="utf-8")
    return len(edits)


if __name__ == "__main__":
    n = rewrite(sys.argv[1], "--apply" in sys.argv)
    print(f"{n} call(s) {'rewritten' if '--apply' in sys.argv else 'to rewrite'}")
