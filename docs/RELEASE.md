# Release and Tag Rules

This project uses a lightweight release process.

## Tag format

Use semantic version tags:

```text
vMAJOR.MINOR.PATCH
```

Examples:
- `v0.1.0`
- `v0.2.0`
- `v0.2.1`
- `v1.0.0`

## Versioning rules

### PATCH
Use PATCH for:
- bug fixes
- small path/config corrections
- non-breaking rule adjustments
- README or docs improvements that accompany small fixes

Example:
- `v0.2.0` -> `v0.2.1`

### MINOR
Use MINOR for:
- new pipeline steps
- collector capability expansion
- new analysis outputs
- architecture upgrades that do not break the main run commands

Example:
- `v0.2.1` -> `v0.3.0`

### MAJOR
Use MAJOR for:
- breaking runtime changes
- incompatible config changes
- entrypoint changes that require users to change how they run the project
- major storage or output layout changes that require migration

Example:
- `v0.9.0` -> `v1.0.0`

## Release checklist

Before creating a release:
1. Ensure `python hourly_hot_collector.py` still works.
2. Ensure `python hot_topic_pipeline.py` still works.
3. Ensure key docs are updated when behavior changes.
4. Ensure `.env` or config changes are reflected in example files if needed.
5. Ensure output paths and database expectations are clear.

## Git workflow

Recommended steps:

```bash
git checkout main
git pull
git tag v0.1.0
git push origin main
git push origin v0.1.0
```

## GitHub Release notes

For each tagged milestone, create a GitHub Release with:
- summary of collector changes
- summary of pipeline changes
- config or path changes
- migration notes if any

## Scope guidance

Create a release when:
- a milestone is runnable end-to-end
- behavior changed in a way worth documenting
- downstream users need a stable checkpoint

Do not create a new release for every tiny local experiment.
