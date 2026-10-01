# IR-OWC Lab Terminal

Desktop Application for Infrared Optical Wireless Communication (IR-OWC) built with PySide6, Python 3.11+, and uv.

## Setup & Run

```bash
# Install dependencies
uv sync --extra dev

# Run application
uv run python -m ir_terminal

# Run tests
uv run pytest tests/ -v

# Code quality
uv run ruff check .
uv run mypy src/ tests/
```
