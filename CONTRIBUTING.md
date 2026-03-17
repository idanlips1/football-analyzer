# Contributing

## Type annotations

We use type hints on all functions and methods. mypy runs on every commit and will fail if you add untyped code.

### Basics

```python
def greet(name: str) -> str:
    return f"Hello, {name}"

def process(items: list[int]) -> None:
    for x in items:
        print(x)
```

### Common patterns

| Situation | Example |
|-----------|---------|
| No return value | `-> None` |
| Optional argument | `x: int \| None = None` |
| List of dicts | `items: list[dict[str, Any]]` |
| Callable (function as arg) | `callback: Callable[[float], None]` — import from `collections.abc` |
| Path | `path: Path` — from `pathlib` |
| Pytest fixture return | `def fake_thing(monkeypatch: pytest.MonkeyPatch) -> Callable[[float], None]:` |

### Imports

- Use `list`, `dict`, etc. directly (Python 3.9+)
- For `Callable`, `Sequence`, etc. use `from collections.abc import ...` (not `typing`)
- For `Any` use `from typing import Any`

### Quick check before committing

```bash
mypy pipeline config utils scripts tests
```

If mypy reports "Function is missing a return type annotation" or "missing type annotation for one or more arguments", add the types and it will pass.
