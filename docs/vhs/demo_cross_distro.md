# Demo: Cross-distro dependency blast radius

When a core library is updated, which packages depend on it -- and did their changelogs
acknowledge the change? Then compare the library version across distributions in one shot.
Demonstrates `get_reverse_dependencies`, `get_dependency_changes`, and `compare_versions`
working together without naming any tool in the prompt.

**Prompt:** *"openssl was updated last week. Which packages in my system depend on it, and did their changelogs mention that update? Give me a cross-distro status comparison between OpenSUSE, Ubuntu and Fedora. Summarise all findings."*

## Session output

<!-- demo-output:demo_cross_distro -->
<!-- /demo-output:demo_cross_distro -->

![Cross-distro blast radius](https://raw.githubusercontent.com/ilmanzo/changelog-poc/main/docs/vhs/demo_cross_distro.gif)
