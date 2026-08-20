import { Component, h, render } from "/ui/vendor/preact.module.js";
import htm from "/ui/vendor/htm.module.js";
import { badgeClassFor, groupByTurn, laneFor, previewOf, repairedFields } from "/ui/logic.js";

const html = htm.bind(h);
const PAGE_SIZE = 500;

function selectedFromHash() {
  const match = location.hash.match(/^#\/run\/(.*)$/);
  if (!match) return null;
  try { return decodeURIComponent(match[1]); } catch { return null; }
}

async function json(url) {
  const response = await fetch(url);
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function Value({ value }) {
  if (typeof value === "string") return value;
  return JSON.stringify(value, null, 2);
}

function Metadata({ metadata }) {
  const provenance = metadata?._retrace;
  const entries = Object.entries(metadata || {}).filter(([key]) => key !== "_retrace");
  const repaired = repairedFields(metadata);
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

class EventRow extends Component {
  state = { open: false };
  render({ event, agentIds }, { open }) {
  const lane = laneFor(event.agent_id, agentIds);
  const repaired = repairedFields(event.metadata).length > 0;
  const style = lane === null ? {} : { "--lane": lane, "--lanes": Math.max(agentIds.length, lane + 1) };
  return html`<article class=${`event ${lane === null ? "neutral" : `color-${lane % 8}`}`} style=${style}>
    <button class="event-summary" onClick=${() => this.setState({ open: !open })} aria-expanded=${open}>
      <span class="agent">${event.agent_id ?? "system"}</span>
      ${event.role && html`<span class="role">${event.role}</span>`}
      <span class=${`badge badge-${badgeClassFor(event.type)}`}>${event.type}</span>
      ${repaired && html`<span class="badge repaired">repaired</span>`}
      <span class="preview">${previewOf(event.content, 120)}</span>
    </button>
    ${open && html`<div class="event-detail">
      <section><h4>Content</h4><pre>${event.content}</pre></section>
      ${event.structured != null && html`<section><h4>Structured</h4><pre>${JSON.stringify(event.structured, null, 2)}</pre></section>`}
      <${Metadata} metadata=${event.metadata} />
    </div>`}
  </article>`;
  }
}

class Replay extends Component {
  state = { run: null, events: [], total: 0, busy: false, error: "" };
  componentDidMount() { this.reset(this.props.runId); }
  componentDidUpdate(previous) {
    if (previous.runId !== this.props.runId) this.reset(this.props.runId);
  }
  reset(runId) {
    this.setState({ run: null, events: [], total: 0, error: "" });
    json(`/api/runs/${encodeURIComponent(runId)}`).then(run => this.setState({ run })).catch(reason => this.setState({ error: String(reason) }));
    this.load(0, false, runId);
  }
  async load(offset, all = false, runId = this.props.runId) {
    this.setState({ busy: true });
    try {
      let loaded = [];
      let next = offset;
      let knownTotal = this.state.total;
      do {
        const page = await json(`/api/runs/${encodeURIComponent(runId)}/events?offset=${next}&limit=${PAGE_SIZE}`);
        loaded = loaded.concat(page.events);
        knownTotal = page.total;
        next += page.events.length;
      } while (all && next < knownTotal);
      this.setState(current => ({ events: offset === 0 ? loaded : current.events.concat(loaded), total: knownTotal }));
    } catch (reason) { this.setState({ error: String(reason) }); } finally { this.setState({ busy: false }); }
  }
  render(_, { run, events, total, busy, error }) {
  if (error) return html`<p class="error">${error}</p>`;
  if (!run) return html`<p>Loading run...</p>`;
  return html`<section class="replay">
    <header><h2>${run.id}</h2><span>${run.outcome ?? "No outcome"}</span></header>
    ${run.ingest_warnings > 0 && html`<div class="banner warning">${run.ingest_warnings} ingest warnings in this run</div>`}
    ${run.n_repaired > 0 && html`<div class="banner repair">${run.n_repaired} records repaired in this run</div>`}
    <div class="timeline">${groupByTurn(events).map(group => html`<section class="turn">
      <h3>Turn ${group.turn}</h3>
      ${group.events.map(event => html`<${EventRow} key=${event.id} event=${event} agentIds=${run.agent_ids} />`)}
    </section>`)}</div>
    <footer class="paging"><span>${events.length} / ${total} loaded</span>
      ${events.length < total && html`<button disabled=${busy} onClick=${() => this.load(events.length)}>Load more</button>`}
      ${events.length < total && html`<button disabled=${busy} onClick=${() => this.load(events.length, true)}>Load all</button>`}
    </footer>
  </section>`;
  }
}

class App extends Component {
  state = { runs: [], selected: selectedFromHash(), error: "" };
  changed = () => this.setState({ selected: selectedFromHash() });
  componentDidMount() {
    json("/api/runs").then(runs => this.setState({ runs })).catch(reason => this.setState({ error: String(reason) }));
    addEventListener("hashchange", this.changed);
  }
  componentWillUnmount() { removeEventListener("hashchange", this.changed); }
  render(_, { runs, selected, error }) {
  return html`<div class="layout"><aside><h1>Retrace</h1>${error && html`<p class="error">${error}</p>`}
    <nav>${runs.map(run => html`<a class=${selected === run.id ? "selected" : ""} href=${`#/run/${encodeURIComponent(run.id)}`}>
      <b>${run.id}</b><span>${run.outcome ?? "No outcome"}</span><small>${run.n_events} events; ${run.ingest_warnings} warnings</small>
    </a>`)}</nav>
  </aside><main>${selected ? html`<${Replay} runId=${selected} />` : html`<div class="empty"><h2>Select a run</h2><p>Choose a run from the list to replay its events.</p></div>`}</main></div>`;
  }
}

render(html`<${App} />`, document.getElementById("app"));
