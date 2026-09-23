# Contributing

1. Use Python 3.11 or newer.
2. Create a virtual environment and install `.[dev]`.
3. Run `ruff check .` and `pytest -q`.
4. Keep the server local-only over `stdio`.
5. Do not add hard-coded user paths, credentials, or permission bypasses.

Pull requests should explain the user-visible behavior and include a focused test
for any changed discovery, validation, or command behavior.
