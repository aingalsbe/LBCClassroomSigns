# Project Guidelines

## Git Branching & Workflow
- **Never commit or push directly to `main`.** `main` holds the clean baseline (tagged at `v0.1-poc` for the working POC snapshot and each subsequent semantic release tag).
- **Do all production work on feature or milestone branches** (e.g., `prod/foundations`, `prod/<milestone>`, `feat/<feature>`, or `docs/<topic>`).
- Before making code edits or committing changes, check the active branch; if on `main`, create or switch to an appropriate feature/milestone branch first.
- Only merge into `main` (or via a PR) when explicitly requested by the user.

## Release Notes & Changelog
- **Record every release in `CHANGELOG.md`.** Whenever preparing a release (`vX.Y.Z`), add a dated/tagged entry at the top of `CHANGELOG.md` containing the release summary, highlights, upgrade notes (if any), and the GitHub Release URL (`https://github.com/aingalsbe/LBCClassroomSigns/releases/tag/vX.Y.Z`).
- Bump `BUILD_ID` in `app/config.py` to match the new release tag so deployed wall iPads auto-refresh cleanly.

