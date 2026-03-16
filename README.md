# Football Highlights Generator

Automatic football highlights generator that analyzes commentator speech to detect exciting moments and produce a short highlights clip from a full match video.

## Setup

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
pre-commit install
```

## Usage

```bash
python main.py
```

## Testing

```bash
pytest
```

## TDD

The EDR scoring/merging logic (`pipeline/edr.py`) and event filtering (`pipeline/filtering.py`) were developed using Test-Driven Development. Tests in `tests/test_edr.py` and `tests/test_filtering.py` were written first.

## Static Analysis

Runs automatically on every commit via pre-commit hooks (ruff, mypy, bandit). To run manually:

```bash
ruff check .
mypy .
bandit -r . -c pyproject.toml
```
