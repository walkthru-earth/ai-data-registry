import { LitElement, css, html, nothing } from 'lit'
import { customElement, state } from 'lit/decorators.js'
import { repeat } from 'lit/directives/repeat.js'
import { parse as parseToml } from 'smol-toml'

type RegistryManifest = {
  description?: string
  schedule?: string
  timeout?: number
  tags?: string[]
  schema?: string
  table?: string
  tables?: string[]
  mode?: 'append' | 'replace' | 'upsert'
  storage?: string | string[]
  runner?: { backend?: string; flavor?: string; image?: string }
  license?: {
    code?: string
    data?: string
    data_source?: string
    mixed?: boolean
  }
}

type Workspace = {
  name: string
  manifest: RegistryManifest
}

type WorkflowRun = {
  id: number
  name: string
  display_title: string
  status: string
  conclusion: string | null
  html_url: string
  run_started_at: string
  updated_at: string
  event: string
  head_branch: string
  path: string
}

const EXTRACT_WORKFLOW_PATHS = new Set([
  '.github/workflows/extract-github.yml',
  '.github/workflows/extract-hetzner.yml',
  '.github/workflows/extract-huggingface.yml',
])

const SYSTEM_WORKFLOWS: Record<string, string> = {
  '.github/workflows/merge-catalog.yml': 'Catalog merge',
  '.github/workflows/maintenance.yml': 'Maintenance',
  '.github/workflows/scheduler.yml': 'Scheduler',
}

const REFRESH_INTERVAL_MS = 70_000
const RELATIVE = new Intl.RelativeTimeFormat(undefined, { numeric: 'auto' })

function relativeTime(iso: string): string {
  const diffMs = Date.now() - new Date(iso).getTime()
  const diffSec = Math.round(diffMs / 1000)
  const ranges: [number, Intl.RelativeTimeFormatUnit][] = [
    [60, 'second'],
    [3600, 'minute'],
    [86400, 'hour'],
    [86400 * 7, 'day'],
    [86400 * 30, 'week'],
    [86400 * 365, 'month'],
    [Infinity, 'year'],
  ]
  const divs: Record<string, number> = {
    second: 1,
    minute: 60,
    hour: 3600,
    day: 86400,
    week: 86400 * 7,
    month: 86400 * 30,
    year: 86400 * 365,
  }
  for (const [limit, unit] of ranges) {
    if (Math.abs(diffSec) < limit) {
      return RELATIVE.format(-Math.round(diffSec / divs[unit]), unit)
    }
  }
  return new Date(iso).toLocaleString()
}

const DEFAULT_OWNER = 'walkthru-earth'
const DEFAULT_REPO = 'ai-data-registry'
const DEFAULT_BRANCH = 'main'

function detectRepoFromLocation(): {
  owner: string
  repo: string
} | null {
  const host = location.hostname
  const segs = location.pathname.split('/').filter(Boolean)

  // user.github.io/repo/... → owner = user, repo = first path segment
  // org.github.io/repo/...  → owner = org,  repo = first path segment
  // user.github.io          → owner = user, repo = user.github.io (user site)
  const ghPages = host.match(/^([^.]+)\.github\.io$/)
  if (ghPages) {
    const owner = ghPages[1]
    const repo = segs[0] || `${owner}.github.io`
    return { owner, repo }
  }

  // Custom domain: look for a <meta name="registry:repo" content="owner/repo">
  const meta = document.querySelector<HTMLMetaElement>(
    'meta[name="registry:repo"]',
  )
  if (meta?.content?.includes('/')) {
    const [owner, repo] = meta.content.split('/')
    if (owner && repo) return { owner, repo }
  }

  return null
}

@customElement('workspaces-status')
export class WorkspacesStatus extends LitElement {
  @state() private owner = DEFAULT_OWNER
  @state() private repo = DEFAULT_REPO
  @state() private branch = DEFAULT_BRANCH
  @state() private source = 'default'
  @state() private workspaces: Workspace[] = []
  @state() private runsByWorkspace = new Map<string, WorkflowRun[]>()
  @state() private systemRuns = new Map<string, WorkflowRun[]>()
  @state() private loading = false
  @state() private error = ''
  @state() private lastFetched: Date | null = null
  @state() private autoRefresh = true
  @state() private nextTickMs = REFRESH_INTERVAL_MS
  private _timer?: number
  private _tick?: number

  connectedCallback() {
    super.connectedCallback()
    const params = new URLSearchParams(location.search)
    const detected = detectRepoFromLocation()
    if (detected) {
      this.owner = detected.owner
      this.repo = detected.repo
      this.source = 'auto-detected'
    }
    if (params.get('owner')) {
      this.owner = params.get('owner')!
      this.source = 'query-param'
    }
    if (params.get('repo')) {
      this.repo = params.get('repo')!
      this.source = 'query-param'
    }
    if (params.get('branch')) this.branch = params.get('branch')!
    this.bootstrap()
  }

  private async bootstrap() {
    await this.loadManifests()
    await this.refresh()
    this.startPolling()
  }

  disconnectedCallback() {
    super.disconnectedCallback()
    this.stopPolling()
  }

  private startPolling() {
    this.stopPolling()
    if (!this.autoRefresh) return
    this._timer = window.setInterval(() => this.refresh(), REFRESH_INTERVAL_MS)
    this._tick = window.setInterval(() => {
      if (!this.lastFetched) return
      const elapsed = Date.now() - this.lastFetched.getTime()
      this.nextTickMs = Math.max(0, REFRESH_INTERVAL_MS - elapsed)
    }, 1000)
  }

  private stopPolling() {
    if (this._timer) window.clearInterval(this._timer)
    if (this._tick) window.clearInterval(this._tick)
    this._timer = undefined
    this._tick = undefined
  }

  private toggleAuto() {
    this.autoRefresh = !this.autoRefresh
    if (this.autoRefresh) this.startPolling()
    else this.stopPolling()
  }

  private async ghJson<T>(url: string): Promise<T> {
    const res = await fetch(url, {
      headers: {
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
    })
    if (!res.ok) {
      throw new Error(`${res.status} ${res.statusText} on ${url}`)
    }
    return res.json() as Promise<T>
  }

  private async fetchRaw(path: string): Promise<string> {
    const url = `https://raw.githubusercontent.com/${this.owner}/${this.repo}/${this.branch}/${path}`
    const res = await fetch(url)
    if (!res.ok) throw new Error(`${res.status} on ${path}`)
    return res.text()
  }

  private async loadManifests() {
    this.loading = true
    this.error = ''
    try {
      const entries = await this.ghJson<Array<{ name: string; type: string }>>(
        `https://api.github.com/repos/${this.owner}/${this.repo}/contents/workspaces?ref=${this.branch}`,
      )
      const dirs = entries.filter((e) => e.type === 'dir').map((e) => e.name)

      const manifests = await Promise.all(
        dirs.map(async (name): Promise<Workspace | null> => {
          try {
            const text = await this.fetchRaw(`workspaces/${name}/pixi.toml`)
            const parsed = parseToml(text) as {
              tool?: { registry?: RegistryManifest }
            }
            const manifest = parsed.tool?.registry
            if (!manifest) return null
            return { name, manifest }
          } catch {
            return null
          }
        }),
      )
      this.workspaces = manifests.filter((w): w is Workspace => w !== null)
    } catch (e) {
      this.error = (e as Error).message
    } finally {
      this.loading = false
    }
  }

  private async refresh() {
    this.loading = true
    this.error = ''
    try {
      const runs = await this.ghJson<{ workflow_runs: WorkflowRun[] }>(
        `https://api.github.com/repos/${this.owner}/${this.repo}/actions/runs?per_page=100`,
      )
      const map = new Map<string, WorkflowRun[]>()
      const sys = new Map<string, WorkflowRun[]>()
      for (const r of runs.workflow_runs) {
        if (EXTRACT_WORKFLOW_PATHS.has(r.path)) {
          const match = this.workspaces.find((w) =>
            r.display_title?.includes(w.name),
          )
          if (!match) continue
          const list = map.get(match.name) ?? []
          list.push(r)
          map.set(match.name, list)
        } else if (r.path in SYSTEM_WORKFLOWS) {
          const list = sys.get(r.path) ?? []
          list.push(r)
          sys.set(r.path, list)
        }
      }
      const sortRuns = (a: WorkflowRun, b: WorkflowRun) =>
        new Date(b.run_started_at).getTime() -
        new Date(a.run_started_at).getTime()
      for (const list of map.values()) list.sort(sortRuns)
      for (const list of sys.values()) list.sort(sortRuns)
      this.runsByWorkspace = map
      this.systemRuns = sys
      this.lastFetched = new Date()
      this.nextTickMs = REFRESH_INTERVAL_MS
    } catch (e) {
      this.error = (e as Error).message
    } finally {
      this.loading = false
    }
  }

  private renderTables(m: RegistryManifest) {
    const list = m.tables ?? (m.table ? [m.table] : [])
    return list.map(
      (t) => html`<code class="pill">${m.schema}.${t}</code>`,
    )
  }

  private renderStatusBadge(run: WorkflowRun | undefined) {
    if (!run) return html`<span class="badge badge-none">no runs</span>`
    const state =
      run.status === 'completed' ? run.conclusion ?? 'unknown' : run.status
    return html`
      <a
        class="badge badge-${state}"
        href=${run.html_url}
        target="_blank"
        rel="noopener"
        title=${`${run.name} · ${new Date(run.run_started_at).toLocaleString()}`}
      >
        ${state} ↗
      </a>
    `
  }

  private renderHistory(runs: WorkflowRun[] | undefined) {
    if (!runs || runs.length === 0) return nothing
    return html`<div class="history">
      ${repeat(
        runs.slice(0, 8),
        (r) => r.id,
        (r) => {
          const state =
            r.status === 'completed' ? r.conclusion ?? 'unknown' : r.status
          return html`<a
            class="dot dot-${state}"
            href=${r.html_url}
            target="_blank"
            rel="noopener"
            title=${`${state} · ${new Date(r.run_started_at).toLocaleString()}`}
          ></a>`
        },
      )}
    </div>`
  }

  private renderWorkspace(w: Workspace) {
    const m = w.manifest
    const runs = this.runsByWorkspace.get(w.name)
    const latest = runs?.[0]
    return html`
      <article class="card">
        <header>
          <h2>${w.name}</h2>
          ${this.renderStatusBadge(latest)}
        </header>
        <p class="desc">${m.description ?? ''}</p>

        <dl>
          <dt>Tables</dt>
          <dd class="pills">${this.renderTables(m)}</dd>

          <dt>Mode</dt>
          <dd><code class="pill mode-${m.mode}">${m.mode ?? '—'}</code></dd>

          <dt>Schedule</dt>
          <dd><code>${m.schedule ?? '—'}</code></dd>

          <dt>Backend</dt>
          <dd>
            ${m.runner?.backend ?? '—'}
            ${m.runner?.flavor
              ? html` · <code>${m.runner.flavor}</code>`
              : nothing}
          </dd>

          <dt>License</dt>
          <dd>
            code <code>${m.license?.code ?? '—'}</code> · data
            <code>${m.license?.data ?? '—'}</code>
            ${m.license?.data_source
              ? html`<br /><span class="muted"
                    >source: ${m.license.data_source}</span
                  >`
              : nothing}
          </dd>

          ${m.tags?.length
            ? html`<dt>Tags</dt>
                <dd class="pills">
                  ${m.tags.map((t) => html`<span class="pill">${t}</span>`)}
                </dd>`
            : nothing}
        </dl>

        <footer>
          <span class="muted">Recent runs</span>
          ${this.renderHistory(runs)}
          ${latest
            ? html`<a
                class="link"
                href=${`https://github.com/${this.owner}/${this.repo}/actions?query=${encodeURIComponent(
                  `"${w.name}"`,
                )}`}
                target="_blank"
                rel="noopener"
                >all runs ↗</a
              >`
            : nothing}
        </footer>
      </article>
    `
  }

  private summary() {
    let success = 0
    let failure = 0
    let running = 0
    let noRuns = 0
    for (const w of this.workspaces) {
      const latest = this.runsByWorkspace.get(w.name)?.[0]
      if (!latest) {
        noRuns++
        continue
      }
      if (latest.status !== 'completed') running++
      else if (latest.conclusion === 'success') success++
      else failure++
    }
    return { success, failure, running, noRuns, total: this.workspaces.length }
  }

  private renderSystemCard(path: string, label: string) {
    const runs = this.systemRuns.get(path)
    const latest = runs?.[0]
    const state = latest
      ? latest.status === 'completed'
        ? latest.conclusion ?? 'unknown'
        : latest.status
      : 'none'
    return html`
      <article class="sys-card">
        <div class="sys-head">
          <span class="sys-label">${label}</span>
          <span class="badge badge-${state}">${state}</span>
        </div>
        ${latest
          ? html`
              <a
                class="sys-link"
                href=${latest.html_url}
                target="_blank"
                rel="noopener"
              >
                ${relativeTime(latest.run_started_at)} ↗
              </a>
            `
          : html`<span class="muted">no runs yet</span>`}
        ${this.renderHistory(runs)}
      </article>
    `
  }

  render() {
    const s = this.summary()
    const countdown = Math.ceil(this.nextTickMs / 1000)
    return html`
      <header class="hero">
        <div class="title">
          <span class="live-dot" aria-hidden="true"></span>
          <h1>Registry Watch Center</h1>
        </div>
        <div class="meta">
          <a
            class="repo"
            href=${`https://github.com/${this.owner}/${this.repo}`}
            target="_blank"
            rel="noopener"
            ><code>${this.owner}/${this.repo}</code></a
          >
          <code class="branch">@${this.branch}</code>
          <span class="muted source" title=${`repo source: ${this.source}`}
            >${this.source}</span
          >
          ${this.lastFetched
            ? html`<span class="muted"
                >updated ${relativeTime(this.lastFetched.toISOString())}${this
                  .autoRefresh
                  ? html` · next in ${countdown}s`
                  : nothing}</span
              >`
            : nothing}
        </div>
        <div class="controls">
          <button
            class=${this.autoRefresh ? 'on' : ''}
            @click=${() => this.toggleAuto()}
            title="Toggle auto-refresh"
          >
            ${this.autoRefresh ? 'live ●' : 'paused'}
          </button>
          <button @click=${() => this.refresh()} ?disabled=${this.loading}>
            ${this.loading ? '…' : 'refresh'}
          </button>
        </div>
      </header>

      <section class="stats">
        <div class="stat">
          <span class="stat-val">${s.total}</span>
          <span class="stat-lbl">workspaces</span>
        </div>
        <div class="stat stat-success">
          <span class="stat-val">${s.success}</span>
          <span class="stat-lbl">passing</span>
        </div>
        <div class="stat stat-failure">
          <span class="stat-val">${s.failure}</span>
          <span class="stat-lbl">failing</span>
        </div>
        <div class="stat stat-running">
          <span class="stat-val">${s.running}</span>
          <span class="stat-lbl">in progress</span>
        </div>
        <div class="stat stat-muted">
          <span class="stat-val">${s.noRuns}</span>
          <span class="stat-lbl">no runs yet</span>
        </div>
      </section>

      <section class="sys-row">
        ${Object.entries(SYSTEM_WORKFLOWS).map(([path, label]) =>
          this.renderSystemCard(path, label),
        )}
      </section>

      ${this.error ? html`<p class="error">${this.error}</p>` : nothing}
      ${this.loading && this.workspaces.length === 0
        ? html`<p class="muted center">Loading workspaces…</p>`
        : nothing}

      <h2 class="section-title">Workspaces</h2>
      <section class="grid">
        ${repeat(
          this.workspaces,
          (w) => w.name,
          (w) => this.renderWorkspace(w),
        )}
      </section>
    `
  }

  static styles = css`
    :host {
      --bg: #fff;
      --fg: #0b0d12;
      --muted: #6b7280;
      --border: #e5e7eb;
      --card: #fff;
      --accent: #7c3aed;
      --success: #15803d;
      --failure: #b91c1c;
      --running: #b45309;
      --cancelled: #6b7280;
      --success-bg: #dcfce7;
      --failure-bg: #fee2e2;
      --running-bg: #fef3c7;
      --cancelled-bg: #f3f4f6;
      display: block;
      max-width: 1200px;
      margin: 0 auto;
      padding: 24px;
      color: var(--fg);
      font:
        14px/1.5 system-ui,
        -apple-system,
        sans-serif;
    }

    @media (prefers-color-scheme: dark) {
      :host {
        --bg: #0b0d12;
        --fg: #e5e7eb;
        --muted: #9ca3af;
        --border: #1f2937;
        --card: #11141b;
        --success: #22c55e;
        --failure: #ef4444;
        --running: #f59e0b;
        --success-bg: rgba(34, 197, 94, 0.15);
        --failure-bg: rgba(239, 68, 68, 0.15);
        --running-bg: rgba(245, 158, 11, 0.15);
        --cancelled-bg: rgba(107, 114, 128, 0.15);
      }
    }

    h1 {
      font-size: 24px;
      margin: 0;
    }

    h2 {
      font-size: 16px;
      margin: 0;
    }

    .hero {
      display: grid;
      grid-template-columns: auto 1fr auto;
      gap: 16px;
      align-items: center;
      padding-bottom: 16px;
      border-bottom: 1px solid var(--border);
      margin-bottom: 20px;
    }

    .title {
      display: flex;
      align-items: center;
      gap: 10px;
    }

    .live-dot {
      width: 10px;
      height: 10px;
      border-radius: 50%;
      background: var(--failure);
      box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.7);
      animation: pulse 1.8s infinite;
    }

    @keyframes pulse {
      0% {
        box-shadow: 0 0 0 0 rgba(239, 68, 68, 0.6);
      }
      70% {
        box-shadow: 0 0 0 10px rgba(239, 68, 68, 0);
      }
      100% {
        box-shadow: 0 0 0 0 rgba(239, 68, 68, 0);
      }
    }

    .repo,
    .branch {
      text-decoration: none;
      color: inherit;
    }

    button.on {
      border-color: var(--failure);
      color: var(--failure);
    }

    .stats {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
      gap: 12px;
      margin-bottom: 20px;
    }

    .stat {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 4px;
    }

    .stat-val {
      font-size: 28px;
      font-weight: 600;
      line-height: 1;
    }

    .stat-lbl {
      color: var(--muted);
      font-size: 12px;
      text-transform: uppercase;
      letter-spacing: 0.05em;
    }

    .stat-success .stat-val {
      color: var(--success);
    }
    .stat-failure .stat-val {
      color: var(--failure);
    }
    .stat-running .stat-val {
      color: var(--running);
    }
    .stat-muted .stat-val {
      color: var(--muted);
    }

    .sys-row {
      display: grid;
      grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
      gap: 12px;
      margin-bottom: 24px;
    }

    .sys-card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 12px 14px;
      display: flex;
      flex-direction: column;
      gap: 6px;
    }

    .sys-head {
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 8px;
    }

    .sys-label {
      font-weight: 500;
    }

    .sys-link {
      font-size: 12px;
      color: var(--accent);
      text-decoration: none;
    }

    .sys-link:hover {
      text-decoration: underline;
    }

    .section-title {
      font-size: 14px;
      text-transform: uppercase;
      letter-spacing: 0.08em;
      color: var(--muted);
      margin: 0 0 12px;
    }

    .meta {
      display: flex;
      gap: 12px;
      align-items: center;
      flex-wrap: wrap;
    }

    .controls {
      display: flex;
      gap: 8px;
    }

    input,
    button {
      font: inherit;
      padding: 6px 10px;
      border-radius: 6px;
      border: 1px solid var(--border);
      background: var(--card);
      color: var(--fg);
    }

    input {
      min-width: 260px;
    }

    button {
      cursor: pointer;
    }

    button:hover:not(:disabled) {
      border-color: var(--accent);
    }

    code {
      font: 12px/1.4 ui-monospace, Menlo, monospace;
      background: rgba(127, 127, 127, 0.12);
      padding: 1px 6px;
      border-radius: 4px;
    }

    .muted {
      color: var(--muted);
      font-size: 12px;
    }

    .center {
      text-align: center;
      padding: 40px 0;
    }

    .error {
      padding: 12px;
      border-radius: 6px;
      background: var(--failure-bg);
      color: var(--failure);
    }

    .grid {
      display: grid;
      grid-template-columns: repeat(auto-fill, minmax(360px, 1fr));
      gap: 16px;
    }

    .card {
      background: var(--card);
      border: 1px solid var(--border);
      border-radius: 10px;
      padding: 16px;
      display: flex;
      flex-direction: column;
      gap: 12px;
    }

    .card header {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 8px;
    }

    .desc {
      color: var(--muted);
      margin: 0;
      font-size: 13px;
    }

    dl {
      display: grid;
      grid-template-columns: 88px 1fr;
      gap: 6px 12px;
      margin: 0;
      font-size: 13px;
    }

    dt {
      color: var(--muted);
    }

    dd {
      margin: 0;
    }

    .pills {
      display: flex;
      flex-wrap: wrap;
      gap: 4px;
    }

    .pill {
      display: inline-flex;
      padding: 1px 8px;
      border-radius: 999px;
      background: rgba(127, 127, 127, 0.14);
      font-size: 12px;
    }

    .mode-append {
      background: rgba(124, 58, 237, 0.15);
      color: var(--accent);
    }

    .mode-replace {
      background: rgba(245, 158, 11, 0.2);
      color: var(--running);
    }

    .badge {
      display: inline-flex;
      align-items: center;
      gap: 4px;
      padding: 2px 10px;
      border-radius: 999px;
      font-size: 12px;
      font-weight: 500;
      text-decoration: none;
      white-space: nowrap;
    }

    .badge-success {
      color: var(--success);
      background: var(--success-bg);
    }
    .badge-failure,
    .badge-timed_out,
    .badge-startup_failure {
      color: var(--failure);
      background: var(--failure-bg);
    }
    .badge-in_progress,
    .badge-queued,
    .badge-waiting,
    .badge-pending,
    .badge-requested {
      color: var(--running);
      background: var(--running-bg);
    }
    .badge-cancelled,
    .badge-skipped,
    .badge-neutral,
    .badge-none,
    .badge-unknown {
      color: var(--cancelled);
      background: var(--cancelled-bg);
    }

    footer {
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 12px;
      border-top: 1px solid var(--border);
      padding-top: 10px;
      margin-top: auto;
    }

    .history {
      display: flex;
      gap: 3px;
    }

    .dot {
      width: 10px;
      height: 10px;
      border-radius: 2px;
      background: var(--cancelled-bg);
      display: inline-block;
    }
    .dot-success {
      background: var(--success);
    }
    .dot-failure,
    .dot-timed_out,
    .dot-startup_failure {
      background: var(--failure);
    }
    .dot-in_progress,
    .dot-queued,
    .dot-waiting {
      background: var(--running);
    }

    .link {
      font-size: 12px;
      color: var(--accent);
      text-decoration: none;
    }

    .link:hover {
      text-decoration: underline;
    }

    @media (max-width: 720px) {
      .hero {
        grid-template-columns: 1fr;
      }
      input {
        min-width: 0;
        width: 100%;
      }
    }
  `
}

declare global {
  interface HTMLElementTagNameMap {
    'workspaces-status': WorkspacesStatus
  }
}
