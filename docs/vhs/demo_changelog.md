# Demo: Vim changelog query

Compare two versions of a package and surface the most relevant changes between them.
The MCP server queries cached OBS and Gitea changelogs, filters by version range, and
returns a ranked summary.

**Prompt:** *"What are the 5 most relevant changes in vim between version 9.0 and 9.2?"*

## Session output

<!-- demo-output:demo_changelog -->
```console
$ gemini -y -p "What are the 5 most relevant changes in vim between version 9.0 and 9.2?"

Based on the changelog diff between versions **9.0** and **9.2**, here are the 5 most relevant
changes and improvements in Vim:

1.  **Vim9 Classes and Objects**: This is the most significant architectural addition in the 9.x
era. Vim matured its **Vim9 script** by fully implementing object-oriented programming features,
including `class`, `interface`, `extends`, and `implements`. This allows for more structured and
performant plugin development.
2.  **GTK4 GUI Support**: Vim added a new backend for the **GTK4** toolkit. This modernizes the GUI
version (`gvim`) for Linux users, addressing high-DPI scaling issues ("blurry text"), improving
rendering, and ensuring compatibility with the latest desktop environments.
3.  **Introduction of the `:defer` Command**: A new control flow command was added that allows
developers to schedule a function call to run just before the current function returns. This is
similar to the `defer` keyword in languages like Go and significantly simplifies resource management
(e.g., closing temporary buffers or files).
4.  **Enhanced Virtual Text & Smooth Scrolling**: Significant work was done on the `virtualtext`
feature (used for inline documentation, LSP diagnostics, and type hints) and the `smoothscroll`
option. Improvements include better handling of wrapped lines, virtual text placement (`above`,
`below`, `padding`), and scroll-position stability.
5.  **Critical Security Hardening**: The transition from 9.0 to 9.2 included a massive wave of
security fixes (addressing dozens of CVEs). These primarily targeted memory safety issues in the
**spellchecker** (`spellfile.c`) and prevented command injection vulnerabilities in core plugins
like **netrw**, **tar**, and **cucumber**.

**Bonus:** Version 9.2 also saw the addition of support for dozens of new filetypes (including
`ghostty`, `Beancount`, `Typst`, and `Nix`) and refinements to the **Wayland** focus-handling and
clipboard integration.
```
<!-- /demo-output:demo_changelog -->

![Vim changelog query](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_changelog.gif)
