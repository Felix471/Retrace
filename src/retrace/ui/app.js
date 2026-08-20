import { Component, h, render } from "/ui/vendor/preact.module.js";
import htm from "/ui/vendor/htm.module.js";
import { groupByTurn, highlightSegments, laneFor, matchesSearch, parseHashState, previewOf, serializeHashState } from "/ui/logic.js";

const html = htm.bind(h);
const PAGE_SIZE = 500;

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
  render({ agent, phase, type, q, onState }, { run, events, total, busy, error }) {
  if (error) return html`<p class="error">${error}</p>`;
  if (!run) return html`<p>Loading run...</p>`;
  return html`<section class="replay">
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

class App extends Component {
  state = { runs: [], view: parseHashState(location.hash), error: "" };
  changed = () => this.setState({ view: parseHashState(location.hash) });
  updateView = changes => { location.hash = serializeHashState({ ...this.state.view, ...changes }); };
  componentDidMount() {
    json("/api/runs").then(runs => this.setState({ runs })).catch(reason => this.setState({ error: String(reason) }));
    addEventListener("hashchange", this.changed);
  }
  componentWillUnmount() { removeEventListener("hashchange", this.changed); }
  render(_, { runs, view, error }) {
  return html`<div class="layout"><aside><h1>Retrace</h1>${error && html`<p class="error">${error}</p>`}
    <nav>${runs.map(run => html`<a class=${view.runId === run.id ? "selected" : ""} href=${serializeHashState({ runId: run.id })}>
      <b>${run.id}</b><span>${run.outcome ?? "No outcome"}</span><small>${run.n_events} events; ${run.ingest_warnings} warnings</small>
    </a>`)}</nav>
  </aside><main>${view.runId ? html`<${Replay} ...${view} onState=${this.updateView} />` : html`<div class="empty"><h2>Select a run</h2><p>Choose a run from the list to replay its events.</p></div>`}</main></div>`;
  }
}

render(html`<${App} />`, document.getElementById("app"));
