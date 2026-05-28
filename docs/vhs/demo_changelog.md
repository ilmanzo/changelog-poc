# Demo: Vim changelog query

Compare two versions of a package and surface the most relevant changes between them.
The MCP server queries cached OBS and Gitea changelogs, filters by version range, and
returns a ranked summary.

**Prompt:** *"What are the 5 most relevant changes in vim between version 9.0 and 9.2?"*

## Session output

<!-- demo-output:demo_changelog -->
```console
$ gemini -y -p "What are the 5 most relevant changes in vim between version 9.0 and 9.2?"

Between version 9.0 and 9.2, Vim has undergone significant architectural and feature improvements, primarily focused on modernizing the Vim9 script language and enhancing the user interface.

Based on the changelog data, here are the 5 most relevant changes:

1.  **Vim9 Classes and Objects**: This is the most substantial addition to Vim9 script. It introduces a complete object-oriented programming (OOP) model, including support for classes, objects, interfaces, and inheritance (using keywords like `:class`, `:interface`, `implements`, and `extends`). This allows for more structured and maintainable plugin development.
2.  **Smooth Scrolling (`'smoothscroll'`)**: A highly requested feature that allows for line-by-line scrolling even when a single line wraps and occupies multiple screen lines. Previously, Vim would "jump" the entire logical line; `'smoothscroll'` enables a more fluid experience by scrolling through individual display lines of a wrapped line.
3.  **Enhanced Virtual Text**: The virtual text system has been significantly expanded. It now supports positioning text above or below lines (using `text_align` and `text_line` properties), better padding, and improved handling of truncation, allowing plugins to display rich metadata and diagnostics more effectively.
4.  **Kitty Keyboard Protocol Support**: Vim now supports the Kitty keyboard protocol, which allows for much better handling of complex key combinations and modifiers (like `Alt`, `Shift`, and `Ctrl` in various combinations) that were traditionally difficult to map reliably in terminal environments.
5.  **Introduction of the `:defer` Command**: Similar to the `defer` keyword in Go, this command allows developers to schedule a function call or command to be executed automatically when the current function exits. This simplifies resource management (e.g., closing temporary buffers or files) and makes error handling more robust in scripts.

Other notable mentions include the ongoing work on **GTK4 support** for the GUI version and the inclusion of **built-in EditorConfig support**, both of which further modernize the Vim ecosystem.
```
<!-- /demo-output:demo_changelog -->

![Vim changelog query](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_changelog.gif)
