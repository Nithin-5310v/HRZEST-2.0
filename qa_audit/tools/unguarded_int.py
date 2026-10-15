"""List int()/float()/date.fromisoformat() calls on request-supplied values
(request.args/form/json, `data.get(...)`) that are NOT inside a try block
whose handlers catch ValueError (or a broader Exception) -- each one turns
bad input into an unhandled 500 (traceback logged + error alert) instead of
a 400.
Usage: python qa_audit/tools/unguarded_int.py blueprints
"""
import ast
import pathlib
import sys

CATCHES = {"ValueError", "Exception", "BaseException", "TypeError"}
CONVERTERS = {"int", "float", "fromisoformat", "strptime"}


def is_request_value(node):
    src = ast.unparse(node)
    return any(t in src for t in ("request.args", "request.form", "request.values", "request.get_json",
                                  "data.get(", "payload.get(", "body.get("))


def handler_catches(try_node):
    for h in try_node.handlers:
        if h.type is None:
            return True
        names = [h.type] if not isinstance(h.type, ast.Tuple) else list(h.type.elts)
        for n in names:
            if isinstance(n, ast.Name) and n.id in CATCHES:
                return True
            if isinstance(n, ast.Attribute) and n.attr in CATCHES:
                return True
    return False


def main(root):
    total = 0
    for path in sorted(pathlib.Path(root).rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        parents = {}
        for node in ast.walk(tree):
            for child in ast.iter_child_nodes(node):
                parents[child] = node
        hits = []
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            fname = node.func.id if isinstance(node.func, ast.Name) else (
                node.func.attr if isinstance(node.func, ast.Attribute) else "")
            if fname not in CONVERTERS or not node.args or not is_request_value(node.args[0]):
                continue
            guarded, p = False, parents.get(node)
            while p is not None:
                if isinstance(p, ast.Try) and handler_catches(p):
                    guarded = True
                    break
                if isinstance(p, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    break
                p = parents.get(p)
            if not guarded:
                hits.append(node.lineno)
        if hits:
            total += len(hits)
            print(f"{path}: {len(hits)} -> lines {','.join(map(str, sorted(set(hits))))}")
    print(f"TOTAL unguarded: {total}")


if __name__ == "__main__":
    main(sys.argv[1])
