# Agent instructions

This repo ships the `agent-keybank` PyPI package. The CLI command is `keybank`.

## Version bumps

Bump the version when the change is material to the PyPI package. That means the CLI, the bundled skill, or package metadata that users get from `pipx install agent-keybank`.

Do not bump for docs-only or repo-only changes (README, this file, `install.sh`, GitHub workflows).

Keep these in sync:

- `pyproject.toml` (`project.version`)
- `keybank/cli.py` (`__version__`)
- `keybank/skill/SKILL.md` (`metadata.version`) when the bundled skill changed

## Release

1. Bump the version as above.
2. Push to `origin/main`.
3. Create a GitHub Release whose tag matches the version (`v0.1.1` for `0.1.1`).

`.github/workflows/publish.yml` publishes that tag to PyPI. Do not upload with twine unless Trusted Publishing is broken.
