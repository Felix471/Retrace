import { Component, h, render } from "/ui/vendor/preact.module.js";
import htm from "/ui/vendor/htm.module.js";
import { cycleSort, formatCell, groupByTurn, groupValueOf, highlightSegments, laneFor, matchesSearch, outcomeBarSegments, parseHashState, parseTableHashState, previewOf, serializeHashState, serializeTableHashState, toggleColumn } from "/ui/logic.js";

const html = htm.bind(h);
const PAGE_SIZE = 500;
const RUN_PAGE_SIZE = 200;
const SUMMARY_COLUMNS = [
  ["id", "run id"], ["outcome", "outcome"], ["n_events", "events"],
  ["n_turns", "turns"], ["duration_s", "duration"], ["total_cost", "cost"],
  ["ingest_warnings", "warnings"], ["n_repaired", "repaired"],
];
const GROUP_COLORS = ["#3973ac", "#b75c31", "#36855b", "#8056a5", "#b33b67", "#2a858f", "#867326", "#596b9d"];

async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function Value({ value }) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function Metadata({ metadata, repaired }) {
  const provenance = metadata?._retrace;
  const entries = Object.entries(metadata || {}).filter(([key]) => key !== "_retrace");
  return html`
    ${entries.length > 0 && html`<section><h4>Metadata</h4><table><tbody>${entries.map(([key, value]) => html`
      <tr><th>${key}</th><td><pre><${Value} value=${value} /></pre></td></tr>`)}
    </tbody></table></section>`}
    ${provenance && html`<section class="provenance"><h4>Provenance</h4>
      ${provenance.source != null && html`<div><b>source:</b> ${provenance.source}</div>`}
      ${provenance.source_ordinal != null && html`<div><b>source ordinal:</b> ${provenance.source_ordinal}</div>`}
      ${provenance.agent_attributes != null && html`<div><b>agent attributes:</b><pre>${JSON.stringify(provenance.agent_attributes, null, 2)}</pre></div>`}
      ${repaired.length > 0 && html`<table><thead><tr><th>field</th><th>original value</th></tr></thead><tbody>
        ${repaired.map(item => html`<tr><td>${item.field}</td><td><pre><${Value} value=${item.original} /></pre></td></tr>`)}
      </tbody></table>`}
    </section>`}
  `;
}

function Highlighted({ text, query }) {
  return highlightSegments(text, query).map(segment => segment.match
    ? html`<span class="search-match">${segment.text}</span>`
    : segment.text);
}

class EventRow extends Component {
  state = { open: false };
  render({ event, agentIds, query }, { open }) {
  const lane = laneFor(event.agent_id, agentIds);
  const repaired = event.repaired.length > 0;
  const style = lane === null ? {} : { "--lane": lane, "--lanes": Math.max(agentIds.length, lane + 1) };
  return html`<article class=${`event ${lane === null ? "neutral" : `color-${lane % 8}`}`} style=${style}>
    <button class="event-summary" onClick=${() => this.setState({ open: !open })} aria-expanded=${open}>
      <span class="agent">${event.agent_id ?? "system"}</span>
      ${event.role && html`<span class="role">${event.role}</span>`}
      <span class=${`badge badge-${event.badge}`}>${event.type}</span>
      ${repaired && html`<span class="badge repaired">repaired</span>`}
      <span class="preview"><${Highlighted} text=${previewOf(event.content, 120)} query=${query} /></span>
    </button>
    ${open && html`<div class="event-detail">
      <section><h4>Content</h4><pre><${Highlighted} text=${event.content} query=${query} /></pre></section>
      ${event.structured != null && html`<section><h4>Structured</h4><pre>${JSON.stringify(event.structured, null, 2)}</pre></section>`}
      <${Metadata} metadata=${event.metadata} repaired=${event.repaired} />
    </div>`}
  </article>`;
  }
}

class Replay extends Component {
  state = { run: null, events: [], total: 0, busy: false, error: "" };
  request = 0;
  componentDidMount() { this.reset(this.props.runId); }
  componentDidUpdate(previous) {
    if (previous.runId !== this.props.runId) this.reset(this.props.runId);
    else if (previous.agent !== this.props.agent || previous.phase !== this.props.phase || previous.type !== this.props.type) {
      this.load(0, false);
    }
  }
  reset(runId) {
    this.setState({ run: null, events: [], total: 0, error: "" });
    json(`/api/runs/${encodeURIComponent(runId)}`).then(run => this.setState({ run })).catch(reason => this.setState({ error: String(reason) }));
    this.load(0, false, runId);
  }
  async load(offset, all = false, runId = this.props.runId) {
    const request = offset === 0 ? ++this.request : this.request;
    const filters = { agent: this.props.agent, phase: this.props.phase, type: this.props.type };
    this.setState(offset === 0 ? { busy: true, events: [], total: 0 } : { busy: true });
    try {
      let loaded = [];
      let next = offset;
      let knownTotal = this.state.total;
      do {
        const params = new URLSearchParams({ offset: next, limit: PAGE_SIZE });
        for (const key of ["agent", "phase", "type"]) if (filters[key]) params.set(key, filters[key]);
        const page = await json(`/api/runs/${encodeURIComponent(runId)}/events?${params}`);
        loaded = loaded.concat(page.events);
        knownTotal = page.total;
        next += page.events.length;
      } while (all && next < knownTotal);
      if (request === this.request) {
        this.setState(current => ({ events: offset === 0 ? loaded : current.events.concat(loaded), total: knownTotal }));
      }
    } catch (reason) {
      if (request === this.request) this.setState({ error: String(reason) });
    } finally {
      if (request === this.request) this.setState({ busy: false });
    }
  }
  render({ agent, phase, type, q, onState, backHash }, { run, events, total, busy, error }) {
  if (error) return html`<p class="error">${error}</p>`;
  if (!run) return html`<p>Loading run...</p>`;
  return html`<section class="replay">
    <a class="back" href=${backHash}>Back to runs</a>
    <header><h2>${run.id}</h2><span>${run.outcome ?? "No outcome"}</span></header>
    ${run.ingest_warnings > 0 && html`<div class="banner warning">${run.ingest_warnings} ingest warnings in this run</div>`}
    ${run.n_repaired > 0 && html`<div class="banner repair">${run.n_repaired} records repaired in this run</div>`}
    <div class="filters">
      <label>Agent<select value=${agent || ""} onChange=${event => onState({ agent: event.target.value || null })}>
        <option value="">All</option>${run.agents.map(value => html`<option value=${value}>${value}</option>`)}</select></label>
      <label>Phase<select value=${phase || ""} onChange=${event => onState({ phase: event.target.value || null })}>
        <option value="">All</option>${run.phases.map(value => html`<option value=${value}>${value}</option>`)}</select></label>
      <label>Type<select value=${type || ""} onChange=${event => onState({ type: event.target.value || null })}>
        <option value="">All</option>${run.types.map(value => html`<option value=${value}>${value}</option>`)}</select></label>
      <label class="search">Search<input type="search" value=${q || ""} onInput=${event => onState({ q: event.target.value || null })} /></label>
    </div>
    ${(() => { const matching = events.filter(event => matchesSearch(event, q)); return html`
    <div class="timeline">${groupByTurn(matching).map(group => html`<section class="turn">
      <h3>Turn ${group.turn}</h3>
      ${group.events.map(event => html`<${EventRow} key=${event.id} event=${event} agentIds=${run.agent_ids} query=${q} />`)}
    </section>`)}</div>
    <footer class="paging"><span>${events.length} of ${total} loaded, ${matching.length} matching</span>
      ${events.length < total && html`<button disabled=${busy} onClick=${() => this.load(events.length)}>Load more</button>`}
      ${events.length < total && html`<button disabled=${busy} onClick=${() => this.load(events.length, true)}>Load all</button>`}
    </footer>`; })()}
  </section>`;
  }
}

class BatchTable extends Component {
  state = { experiment: null, rows: [], groups: [], total: 0, outcomes: [], busy: false, error: "", draftKey: "", draftValue: "" };
  request = 0;
  componentDidMount() {
    this.setState({ draftKey: this.props.view.metadataKey || "", draftValue: this.props.view.metadataValue || "" });
    json("/api/experiment").then(experiment => this.setState({ experiment })).catch(reason => this.setState({ error: String(reason) }));
    this.load();
  }
  componentDidUpdate(previous) {
    if (serializeTableHashState(previous.view) !== serializeTableHashState(this.props.view)) {
      if (previous.view.metadataKey !== this.props.view.metadataKey || previous.view.metadataValue !== this.props.view.metadataValue) {
        this.setState({ draftKey: this.props.view.metadataKey || "", draftValue: this.props.view.metadataValue || "" });
      }
      this.load();
    }
  }
  async load() {
    const request = ++this.request;
    const { view } = this.props;
    const params = new URLSearchParams({ sort: view.sort, order: view.order, limit: RUN_PAGE_SIZE, offset: view.offset });
    if (view.outcome) params.set("outcome", view.outcome);
    if (view.metadataKey && view.metadataValue != null) params.set(view.metadataKey, view.metadataValue);
    if (view.groupBy) params.set("group_by", view.groupBy);
    this.setState({ busy: true, error: "" });
    try {
      const page = await json(`/api/runs?${params}`);
      if (request === this.request) this.setState(current => ({
        rows: page.rows, groups: page.groups || [], total: page.total, busy: false,
        outcomes: [...new Set(current.outcomes.concat(page.rows.map(row => row.outcome).filter(value => value != null)))].sort(),
      }));
    } catch (reason) {
      if (request === this.request) this.setState({ busy: false, error: String(reason) });
    }
  }
  render({ view, onState, onOpen }, { experiment, rows, groups, total, outcomes, busy, error, draftKey, draftValue }) {
    const keys = experiment?.metadata_keys || [];
    const end = Math.min(view.offset + rows.length, total);
    return html`<section class="batch"><header><h1>Runs</h1><span>${experiment ? `${experiment.run_count} runs` : "Loading..."}</span></header>
      <div class="table-controls">
        <label>Group by<select value=${view.groupBy || ""} onChange=${event => onState({ groupBy: event.target.value || null, offset: 0 })}>
          <option value="">No grouping</option>${keys.map(key => html`<option value=${key}>${key}</option>`)}</select></label>
        <label>Outcome<select value=${view.outcome || ""} onChange=${event => onState({ outcome: event.target.value || null, offset: 0 })}>
          <option value="">All</option>${[...new Set(outcomes.concat(view.outcome || []))].sort().map(value => html`<option value=${value}>${value}</option>`)}</select></label>
        <form onSubmit=${event => { event.preventDefault(); onState({ metadataKey: draftKey || null, metadataValue: draftKey && draftValue !== "" ? draftValue : null, offset: 0 }); }}>
          <label>Metadata key<select value=${draftKey} onChange=${event => this.setState({ draftKey: event.target.value })}>
            <option value="">Choose key</option>${keys.map(key => html`<option value=${key}>${key}</option>`)}</select></label>
          <label>Value<input value=${draftValue} onInput=${event => this.setState({ draftValue: event.target.value })} /></label><button>Apply</button>
        </form>
        <button onClick=${() => { this.setState({ draftKey: "", draftValue: "" }); onState({ outcome: null, metadataKey: null, metadataValue: null, offset: 0 }); }}>Clear filters</button>
        <details><summary>Columns</summary>${keys.map(key => html`<label><input type="checkbox" checked=${view.columns.includes(key)} onChange=${() => onState({ columns: toggleColumn(view.columns, key), offset: 0 })} />${key}</label>`)}</details>
      </div>
      ${error && html`<p class="error">${error}</p>`}
      <div class="table-wrap"><table class="run-table"><thead><tr>
        ${SUMMARY_COLUMNS.map(([field, label]) => html`<th><button class="sort" onClick=${() => onState({ ...cycleSort(view, field), offset: 0 })}>${label}${view.sort === field ? (view.order === "asc" ? " ^" : " v") : ""}</button></th>`)}
        ${view.columns.map(key => html`<th>${key}</th>`)}</tr></thead><tbody>
        ${(view.groupBy ? groups.flatMap(group => {
          const groupRows = rows.filter(row => String(groupValueOf(row, view.groupBy)) === String(group.group_value));
          const segments = outcomeBarSegments(group.outcome_distribution, 240);
          const header = html`<tr class="group-header"><th colSpan=${SUMMARY_COLUMNS.length + view.columns.length}>
            <div class="group-title">${group.group_value ?? "(missing)"}</div>
            <div class="aggregate-strip">
              <span>${group.run_count} runs</span><span>turns mean ${formatCell(group.mean_turns, "mean_turns")}, median ${formatCell(group.median_turns, "median_turns")}</span>
              <span>mean cost ${formatCell(group.mean_cost, "total_cost")}${group.cost_excluded ? ` (cost n/a for ${group.cost_excluded})` : ""}</span>
              <span>mean duration ${formatCell(group.mean_duration, "duration_s")}${group.duration_excluded ? ` (duration n/a for ${group.duration_excluded})` : ""}</span>
              ${groupRows.length === 0 && html`<span class="other-pages">rows on other pages</span>`}
            </div>
            <svg class="outcome-bar" viewBox="0 0 240 18" role="img" aria-label="Outcome distribution">
              ${segments.map(segment => html`<rect x=${segment.x} y="0" width=${segment.width} height="18" fill=${GROUP_COLORS[segment.colorIndex % GROUP_COLORS.length]}><title>${segment.label}: ${segment.count}</title></rect>`)}
            </svg>
          </th></tr>`;
          return [header, ...groupRows.map(row => ({ row }))];
        }) : rows.map(row => ({ row }))).map(item => item.row ? html`<tr key=${item.row.id} tabIndex="0" onClick=${() => onOpen(item.row.id)} onKeyDown=${event => { if (event.key === "Enter") onOpen(item.row.id); }}>
          ${SUMMARY_COLUMNS.map(([field]) => html`<td>${formatCell(item.row[field], field)}</td>`)}
          ${view.columns.map(key => html`<td>${formatCell(item.row.metadata[key], key)}</td>`)}</tr>` : item)}
      </tbody></table></div>
      <footer class="paging"><button disabled=${busy || view.offset === 0} onClick=${() => onState({ offset: Math.max(0, view.offset - RUN_PAGE_SIZE) })}>Previous</button>
        <span>Showing ${total === 0 ? 0 : view.offset + 1}-${end} of ${total}</span>
        <button disabled=${busy || end >= total} onClick=${() => onState({ offset: view.offset + RUN_PAGE_SIZE })}>Next</button></footer>
    </section>`;
  }
}

class App extends Component {
  state = { route: location.hash.startsWith("#/run/") ? "run" : "table", view: location.hash.startsWith("#/run/") ? parseHashState(location.hash) : parseTableHashState(location.hash), backHash: "#/" };
  changed = () => this.setState({ route: location.hash.startsWith("#/run/") ? "run" : "table", view: location.hash.startsWith("#/run/") ? parseHashState(location.hash) : parseTableHashState(location.hash) });
  updateView = changes => { location.hash = this.state.route === "run" ? serializeHashState({ ...this.state.view, ...changes }) : serializeTableHashState({ ...this.state.view, ...changes }); };
  openRun = runId => { const backHash = serializeTableHashState(this.state.view); this.setState({ backHash }); location.hash = serializeHashState({ runId }); };
  componentDidMount() {
    addEventListener("hashchange", this.changed);
  }
  componentWillUnmount() { removeEventListener("hashchange", this.changed); }
  render(_, { route, view, backHash }) {
  return html`<main>${route === "run" ? html`<${Replay} ...${view} backHash=${backHash} onState=${this.updateView} />` : html`<${BatchTable} view=${view} onState=${this.updateView} onOpen=${this.openRun} />`}</main>`;
  }
}

render(html`<${App} />`, document.getElementById("app"));
