# File Operations Protocol

> Agent skill for safe file system interaction.
> GRIM never touches files — this protocol is for agents only.

## When This Applies

Any time an agent needs to read, write, move, or delete files.

## Protected Paths

The following paths must NEVER be modified without explicit user permission:
- `.env*` — secrets and environment variables
- `*.key`, `*.pem` — cryptographic material
- `.github/workflows/` — CI/CD pipelines
- `.gitlab-ci.yml` — CI/CD config
- `*lock.json`, `*lock.yaml` — dependency locks
- `.git/` — git internals

## Operations

### Read
1. Verify file exists
2. Use appropriate encoding (UTF-8 default)
3. For large files, read only the needed range
4. Return content or structured extract

### Write
1. Verify target directory exists (create if needed)
2. Check file isn't in protected paths
3. Write with correct encoding
4. Verify write succeeded

### Move/Rename
1. Verify source exists
2. Verify destination doesn't exist (or confirm overwrite)
3. Update any references to the old path
4. Verify source is gone and destination exists

### Delete
1. Verify file exists
2. Check file isn't in protected paths
3. For non-temporary files, require confirmation
4. Verify deletion

### Create Directory
1. Use recursive creation (mkdir -p equivalent)
2. Verify creation succeeded

### List
1. List contents of directory
2. Include file/directory indicator
3. Respect .gitignore for display purposes

## Safety Rules

1. **Scope enforcement** — only touch files within the declared task scope
2. **No silent overwrites** — check before writing to existing files
3. **Backup consideration** — for destructive operations on important files
4. **Encoding awareness** — detect and preserve file encoding
5. **Path normalization** — handle Windows/Unix path differences

## Currency Check

After completing this skill, verify the protocol is still accurate:
- [ ] Commands in this protocol match the actual codebase
- [ ] File paths referenced still exist
- [ ] Test counts and quality gates match current reality
- [ ] If anything is stale, update this protocol before finishing
