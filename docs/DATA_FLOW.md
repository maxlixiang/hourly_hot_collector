# Data Flow

1. Collectors fetch NewsNow and RSS data.
2. Collectors write markdown, raw JSON, and SQLite records.
3. Hot topic pipeline reads SQLite and writes hot cluster JSON.
4. Future agent layer will consume hot cluster JSON and related context.
