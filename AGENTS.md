# Project Guidelines

## Git Branching & Workflow
- **Never commit or push directly to `main`.** `main` holds the clean baseline (tagged at `v0.1-poc` for the working POC snapshot).
- **Do all production work on feature or milestone branches** (e.g., `prod/foundations`, `prod/<milestone>`, or `feat/<feature>`).
- Before making code edits or committing changes, check the active branch; if on `main`, create or switch to an appropriate feature/milestone branch first.
- Only merge into `main` (or via a PR) when explicitly requested by the user.
