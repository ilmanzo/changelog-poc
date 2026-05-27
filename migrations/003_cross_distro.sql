-- Cross-distro support: composite index on (name, distro) for efficient
-- per-distro lookups now that Fedora and Ubuntu sources are active.

CREATE INDEX IF NOT EXISTS packages_name_distro_idx ON packages (name, distro);
