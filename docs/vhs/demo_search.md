# Demo: Semantic search

Find packages by topic across all cached changelog entries using vector similarity
search (fastembed ONNX + pgvector HNSW). The query is embedded and compared against
all 384-dim entry vectors -- no keyword matching required.

**Prompt:** *"find network related packages whose changelog entries mention new command line flags in the last 2 months"*

## Session output

<!-- demo-output:demo_search -->
```console
$ gemini -y -p "find network related packages whose changelog entries mention new command line flags in the last 2 months"

To find the network-related packages with recent command-line flag additions, we'll need to run a
custom query against the database since I am currently in Plan Mode (which is read-only).

Here is my proposed strategy:
1. I will write a short Python script (`query_network_flags.py`) that connects to the PostgreSQL
database via `src.db`.
2. The script will use `db._fetch_text_search` or a custom SQL query to find changelog entries from
the last 2 months (`entry_date >= now() - interval '2 months'`).
3. We will filter for "new command line flags" using text search (e.g., matching keywords like
"flag", "option", or "command line").
4. To ensure they are "network related", we can filter packages using keywords (like "network",
"tcp", "http", "curl", etc.) in the package name or description, or perform a semantic search.
5. After creating the script, we can exit Plan Mode so I can run it and provide you with the
results.

Does this strategy sound good to you? Once you confirm, I will draft the formal plan.
```
<!-- /demo-output:demo_search -->

![Semantic search](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_search.gif)
