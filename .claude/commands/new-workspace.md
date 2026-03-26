---
description: Create a new pixi sub-workspace with its own isolated environment
argument-hint: <name> <language: python|go|node|rust>
disable-model-invocation: true
allowed-tools: Bash(pixi:*), Bash(mkdir:*), Bash(git:*), Read, Write, Edit
---
Create a new sub-workspace named `$0` with language `$1` in this mono-repo.

Follow the workspace rules in `.claude/rules/workspaces.md`.

## Steps

1. **Parse arguments** — extract workspace name and language from `$ARGUMENTS`

2. **Create and initialize the workspace** (from project root)
```bash
mkdir $0
cd $0
pixi init . --channel conda-forge --platform osx-arm64 --platform linux-64 --platform win-64
cd ..
```

3. **Register in root workspace**
```bash
pixi workspace register --name $0 --path $0
```

4. **Add the language runtime** (using -w flag from root)
   - python: `pixi add -w $0 python`
   - go: `pixi add -w $0 go`
   - node: `pixi add -w $0 nodejs`
   - rust: `pixi add -w $0 rust`

5. **Add workspace-specific dependencies** (ask the user what they need)
```bash
pixi add -w $0 <dep1> <dep2>
pixi add -w $0 --pypi <pypi-dep>
```

Always prefer conda-forge packages. Use `--pypi` only when not available on conda-forge.

6. **Set up basic tasks in the workspace pixi.toml**

For Python workspaces:
```toml
[tasks]
dev = "python main.py"
test = { cmd = "pytest", cwd = "tests/" }
lint = "ruff check ."
```

For Go workspaces:
```toml
[tasks]
build = "go build -o bin/app ."
run = { cmd = "./bin/app", depends-on = ["build"] }
test = "go test ./..."
```

For Node workspaces:
```toml
[tasks]
dev = "pnpm run dev"
build = "pnpm run build"
test = "pnpm run test"
```

7. **Add platform-specific deps if needed**
```toml
[target.unix.dependencies]
# Unix-only deps here

[target.win-64.dependencies]
# Windows-only deps here
```

8. **Add .gitignore for pixi environments** (if not inherited from root)
```
# pixi environments
.pixi/*
!.pixi/config.toml
```

9. **Show the final pixi.toml for review**

## Notes
- Shared tools (DuckDB, GDAL, gpio, pnpm) are available from the root — no need to add them per workspace
- Run workspace tasks from root: `pixi run -w $0 <task>`
- Use `depends-on` to chain tasks within the workspace
- Use `[target.<platform>.dependencies]` for platform-specific deps
- Single `pixi.lock` at root covers all workspaces — do NOT expect per-workspace lock files
