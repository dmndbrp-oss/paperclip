---
name: paperclip-create-plugin
description: >
  Create and develop external Paperclip plugins with the CLI-first workflow,
  and adopt external Claude Code plugins/skills into Sage Surfaces' catalog via
  the three-route intake matrix (registry install, fork-and-vendor, or
  reference-only). Use when scaffolding a new plugin, working on a local plugin
  against a running Paperclip instance, updating plugin authoring docs, or
  bringing a sweep-vetted external skill into the company catalog. Covers
  `paperclipai plugin init`, the local install loop via `paperclipai plugin
  install <path>`, worker/UI rebuild and reload semantics, the required success
  checklist, and the SAG-683 §1 vetting gates for each adoption route.
---

# Create and develop a Paperclip plugin

Use this skill when the task is to create, scaffold, or iterate on a Paperclip plugin against a local Paperclip instance, **or** to adopt an existing external Claude Code plugin/skill into the company catalog.

## 0. Two purposes: authoring vs adoption

This skill covers two distinct workflows:

1. **Authoring** a new Paperclip plugin from scratch (sections 1–9 below — the existing content).
2. **Adopting** an external Claude Code plugin or skill into Sage Surfaces' catalog (the three-route matrix in the next section).

If the task is to scaffold or iterate on a brand-new Paperclip plugin, jump to section 1. If the task is to bring an *existing* external plugin/skill into the company's catalog (post-sweep, post-vetting), use the three-route matrix first.

## 0.1 Three-route adoption matrix

| Route | When to use | Mechanism | Trust posture |
|---|---|---|---|
| **(a) Registry install** | Candidate is published in `anthropics/claude-plugins-official` (or a future board-approved registry). Verify against the registry inventory enumerated in [SAG-2401](/SAG/issues/SAG-2401#comment-cff608a8). | `/plugin install <name>@claude-plugins-official` inside the v1.3 §5 sandbox at `sandbox/plugin-pilot/` ([SAG-2404](/SAG/issues/SAG-2404) harness). | First-install runs full SAG-683 §1 + §1.5 vetting (license, dep surface, maintainer rep) — registry membership does NOT skip vetting. Lockfile pin (version or commit SHA) recorded under `companies/.../config/`. Snyk + Socket baseline scan captured. Author-diff hook on update (null-author guard required for plugins with empty `author: {}` — see SAG-2401 task 3 finding). |
| **(b) Fork-and-vendor** | Candidate is not on the registry, OR requires patches (denylist strips, security backports), OR the registry install fails the v1.3 §5 sandbox probe. | Clone the repo, vet under SAG-683, import via `paperclipai plugin install <fork-path>` (this skill's sections 4 onward). Catalog entry lives under `__catalog__/<org>-<repo>--sag<ticket>/`. | Full SAG-683 §1 + §1.5 vetting. Pin to a specific commit SHA in the catalog SKILL.md frontmatter. Re-vet on each upstream sync. |
| **(c) Reference-only** | Candidate is content (notebooks, docs, prompt patterns) rather than installable code, OR has a license that blocks install (non-commercial, CC BY-NC, AGPL with no compatible carve-out), OR is too unstable for a runtime dependency. | Extract patterns into `docs/sources/<topic>.md`. No executable code lands in the runtime. | License/stability gates only. No SAG-683 install vetting needed since nothing executes; still record source URL + license + commit SHA. |

**Default flow for a new candidate post-sweep ADOPT verdict:**

1. Check registry inventory ([SAG-2401](/SAG/issues/SAG-2401#comment-cff608a8) or `~/.claude/plugins/marketplaces/claude-plugins-official/{plugins,external_plugins}/`). On registry → route (a).
2. If not on registry but installable + permissively licensed → route (b).
3. If content-only or license-blocked → route (c).

The Monday/Thursday/Sunday repo sweep routine (routine `c5c5f40d` post-[SAG-2400](/SAG/issues/SAG-2400)) already names the route in its verdict tables. Use that as the input.

## 1. Default: build the plugin OUTSIDE Paperclip core

Plugins are their own packages. Unless the task **explicitly** asks for a bundled in-repo example, do not add plugin source under `packages/plugins/` in this repo.

- Scaffold the plugin into a directory outside the Paperclip checkout (e.g. `~/dev/paperclip-plugins/<name>`).
- Install it into the running Paperclip instance by local absolute path.
- Edit code in the external package; let Paperclip pick up rebuilt output.

Only edit Paperclip core itself when the user asks to surface a plugin as a bundled example (`server/src/routes/plugins.ts`, in-repo example lists, docs).

## 2. Ground rules

Reference docs when you need detail:

1. `doc/plugins/PLUGIN_AUTHORING_GUIDE.md`
2. `packages/plugins/sdk/README.md`
3. `doc/plugins/PLUGIN_SPEC.md` — future-looking context only

Current runtime assumptions:

- plugin workers are trusted code
- plugin UI is trusted same-origin host code
- worker APIs are capability-gated
- plugin UI is not sandboxed by manifest capabilities
- no host-provided shared plugin UI component kit yet
- `ctx.assets` is not supported in the current runtime

## 3. CLI-first scaffold workflow

Use `paperclipai plugin init`. Do not invoke the scaffold package node entrypoint by hand unless the CLI command is unavailable in the environment.

```bash
paperclipai plugin init @acme/my-plugin --output ~/dev/paperclip-plugins
```

Useful flags (all optional):

- `--output <dir>` — parent directory; the command creates `<dir>/<unscoped-name>/`. Defaults to the current directory.
- `--template <default|connector|workspace|environment>` — starter template.
- `--category <connector|workspace|automation|ui|environment>` — manifest category.
- `--display-name <name>`, `--description <text>`, `--author <name>` — manifest metadata.
- `--sdk-path <path>` — snapshot the local SDK from a Paperclip checkout into `.paperclip-sdk/` (useful when developing against an unreleased SDK).

On success the command prints the exact next commands (`cd`, `pnpm install`, `pnpm dev`, `paperclipai plugin install <abs-path>`). Run them in order.

If `paperclipai` is not on PATH in your environment, fall back to:

```bash
pnpm --filter @paperclipai/create-paperclip-plugin build
node packages/plugins/create-paperclip-plugin/dist/index.js @acme/my-plugin \
  --output /absolute/path \
  --sdk-path /absolute/path/to/paperclip/packages/plugins/sdk
```

## 4. Local install + rebuild loop

In the scaffolded plugin folder:

```bash
pnpm install
pnpm dev            # esbuild --watch: rebuilds dist/manifest.js, dist/worker.js, dist/ui/
paperclipai plugin install /absolute/path/to/my-plugin
```

Notes:

- `paperclipai plugin install` auto-detects local paths (absolute, `./`, `../`, `~`, or an existing relative folder) and forwards `isLocalPath: true` to the server. Pass `--local` to force local mode if the heuristic is ambiguous.
- Paths are resolved to absolute paths before being sent to the server.
- The server watches built outputs (`dist/`) for local-path plugins and restarts the plugin worker on rebuild — you do not need to reinstall after every edit.
- UI hot reload via the SDK dev server (`pnpm dev:ui`, port `4177`) is optional and template-dependent; only mention it if the template wires `devUiUrl` and you verified it works end to end.
- `--version` only applies to npm package installs. Combining it with a local path is an error.

After install, inspect with:

```bash
paperclipai plugin list
paperclipai plugin inspect <plugin-key>
```

## 5. After scaffolding, sanity-check the package

Open and confirm:

- `src/manifest.ts` — declared capabilities and slots
- `src/worker.ts` — worker entry
- `src/ui/index.tsx` — UI entry (if applicable)
- `tests/plugin.spec.ts` — placeholder test
- `package.json` — `paperclipPlugin` block points at `dist/manifest.js`, `dist/worker.js`, `dist/ui/`

Make sure the plugin:

- declares only supported capabilities
- does not use `ctx.assets`
- does not import host UI component stubs
- keeps UI self-contained
- uses `routePath` only on `page` slots

## 6. Verification (run before declaring success)

From the plugin folder:

```bash
pnpm typecheck
pnpm test
pnpm build
```

If the plugin is already running under `pnpm dev`, you can keep the watcher up and run `pnpm typecheck` and `pnpm test` in a separate shell.

If you changed Paperclip SDK/host/plugin runtime code in addition to the plugin, also run the relevant Paperclip workspace checks.

## 6.1 Route-(a) install verification

When route (a) is in play, the install runs under the [SAG-2404](/SAG/issues/SAG-2404) sandbox at `sandbox/plugin-pilot/` (commit `c0e7475` on `feature/SAG-2145-pilot-artifacts`). Verification commands:

```bash
cd sandbox/plugin-pilot
python3 plugin_sandbox.py --plugin <plugin-name>
# Review audit/*.json for: denylist precheck (must be 'clean'),
#   post-install behaviour check (must be 'ok'),
#   files-written sha256 + size diff,
#   network egress log,
#   sandbox boundary check (HOME override unviolated).
```

Do not promote a route-(a) install out of the sandbox until two clean smoke runs are recorded (per SAG-2404 reset procedure).

## 7. Success checklist (report this back)

When you finish a local plugin task, report:

- **Scaffold path** — absolute path of the created plugin folder.
- **Commands run** — the exact `paperclipai plugin init`, `pnpm install`, `pnpm dev`, `paperclipai plugin install <path>` invocations (and any verification commands).
- **Install status** — output of `paperclipai plugin list` / `plugin inspect` (plugin key, version, status). Note if `status` is anything other than `ready` and include `lastError`.
- **Tests / build result** — `pnpm typecheck`, `pnpm test`, `pnpm build` pass/fail with the failing output if any.
- **Reload limitations** — call out anything that did not hot-reload (e.g. manifest changes required a reinstall, UI dev server was not wired, etc.).

If any item is missing, mark it as such — do not silently skip.

## 8. When NOT to edit Paperclip core

Do not add the plugin under `packages/plugins/` or update bundled-example wiring unless the user explicitly asks for a bundled example. Local-path installs are the supported development model; npm packages are the production deployment path.

If the user does ask for a bundled example, also update:

- `server/src/routes/plugins.ts` example list
- any docs that enumerate in-repo example plugins

## 9. Documentation expectations

When authoring or updating plugin docs:

- distinguish current implementation from future spec ideas
- be explicit about the trusted-code model
- do not promise host UI components or asset APIs
- prefer local-path development + npm-package deployment guidance over repo-local workflows
