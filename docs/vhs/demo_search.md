# Demo: Semantic search

Find packages by topic across all cached changelog entries using vector similarity
search (fastembed ONNX + pgvector HNSW). The query is embedded and compared against
all 384-dim entry vectors -- no keyword matching required.

**Prompt:** *"find network related packages whose changelog entries mention new command line flags in the last 2 months"*

## Session output

<!-- demo-output:demo_search -->
<!-- /demo-output:demo_search -->

![Semantic search](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_search.gif)
