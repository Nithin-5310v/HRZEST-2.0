"""Structural scan of first-party Python:
  - files over 800 lines
  - functions over 50 lines (and whether they have a docstring)
  - DB calls (cursor.execute / cur.execute / *.execute) lexically inside a
    for/while loop -> N+1 candidates (function, loop line, call count)
  - functions that borrow get_db_connection()/get_master_db()/get_tenant_db()
    without a `with` block or try/finally and that contain a `return`
    before the first .close() call -> possible connection leak on that path
Usage: python qa_audit/tools/structure_scan.py > qa_audit/baseline/structure.txt
"""
import ast
import pathlib

ROOTS = ["app.py", "extensions.py", "database.py", "wsgi.py", "qr_generator.py", "blueprints", "utils", "migrations"]
BORROW = {"get_db_connection", "get_master_db", "get_tenant_db"}


def py_files():
    for r in ROOTS:
        p = pathlib.Path(r)
        if p.is_file():
            yield p
        else:
            yield from sorted(x for x in p.rglob("*.py") if "__pycache__" not in x.parts)


def walk_own(node):
    """ast.walk, but without descending into nested function/class bodies."""
    stack = list(ast.iter_child_nodes(node))
    while stack:
        n = stack.pop()
        yield n
        if not isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.Lambda, ast.ClassDef)):
            stack.extend(ast.iter_child_nodes(n))


def call_name(node):
    f = node.func
    if isinstance(f, ast.Attribute):
        return f.attr
    if isinstance(f, ast.Name):
        return f.id
    return ""


def main():
    big_files, long_funcs, loop_queries, leak_candidates = [], [], [], []
    for path in py_files():
        src = path.read_text(encoding="utf-8")
        lines = src.count("\n") + 1
        if lines > 800:
            big_files.append((lines, str(path)))
        tree = ast.parse(src)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            length = (fn.end_lineno or fn.lineno) - fn.lineno + 1
            if length > 50:
                long_funcs.append((length, f"{path}:{fn.lineno}", fn.name, bool(ast.get_docstring(fn))))
            # N+1: execute() inside a loop body (not nested function defs)
            for loop in ast.walk(fn):
                if isinstance(loop, (ast.For, ast.While, ast.comprehension)):
                    body_nodes = loop.body if hasattr(loop, "body") else []
                    calls = [n for b in body_nodes for n in ast.walk(b)
                             if isinstance(n, ast.Call) and call_name(n) in ("execute", "executemany")]
                    if calls:
                        loop_queries.append((len(calls), f"{path}:{loop.lineno}", fn.name))
            # connection-leak heuristic
            borrows = [n for n in walk_own(fn) if isinstance(n, ast.Call) and call_name(n) in BORROW]
            if not borrows:
                continue
            has_with_or_finally = any(isinstance(n, (ast.With, ast.Try)) and (
                isinstance(n, ast.With) or n.finalbody) for n in walk_own(fn))
            if has_with_or_finally:
                continue
            first_borrow = min(n.lineno for n in borrows)
            closes = sorted(n.lineno for n in walk_own(fn)
                            if isinstance(n, ast.Call) and call_name(n) == "close" and n.lineno > first_borrow)
            first_close = closes[0] if closes else 10 ** 9
            early_returns = [n.lineno for n in walk_own(fn)
                             if isinstance(n, ast.Return) and first_borrow < n.lineno < first_close]
            if early_returns:
                leak_candidates.append((f"{path}:{fn.lineno}", fn.name, early_returns[:5]))

    print(f"== Files over 800 lines ({len(big_files)})")
    for n, p in sorted(big_files, reverse=True):
        print(f"{n:6d}  {p}")
    print(f"\n== Functions over 50 lines ({len(long_funcs)}); 'nodoc' = no docstring")
    for n, loc, name, doc in sorted(long_funcs, reverse=True):
        print(f"{n:5d}  {loc}  {name}{'' if doc else '  nodoc'}")
    print(f"\n== DB execute() inside loops ({len(loop_queries)})")
    for n, loc, name in sorted(loop_queries, key=lambda x: x[1]):
        print(f"{loc}  in {name}  ({n} execute call(s) in loop body)")
    print(f"\n== Possible connection leak: early return before first close(), no with/finally ({len(leak_candidates)})")
    for loc, name, rets in leak_candidates:
        print(f"{loc}  {name}  returns at {rets}")


if __name__ == "__main__":
    main()
