import { Component, h, render } from "/ui/vendor/preact.module.js";
import htm from "/ui/vendor/htm.module.js";
import { compareBannerState, compareStateParse, compareStateSerialize, cycleSort, distributionBars, formatCell, groupByCategory, groupByTurn, groupLabel, groupValueOf, gutterMarkFor, highlightSegments, laneFor, matchesSearch, outcomeBarSegments, pagesNeededFor, parseHashState, parseTableHashState, previewOf, resolveAnchors, selectionToCompareState, serializeHashState, serializeTableHashState, tagListWithout, tagsWithModes, toggleColumn, toggleSelection } from "/ui/logic.js";

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

async function putJson(url, payload) {
  const response = await fetch(url, {
    method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
  });
  if (!response.ok) throw new Error(`${response.status} ${response.statusText}`);
  return response.json();
}

function ordinalOf(id) {
  return Number.parseInt(String(id).slice(String(id).lastIndexOf(":") + 1), 10);
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
  render({ event, agentIds = [], query = "", selected = false, onSelect = null, markers = [] }, { open }) {
  const lane = laneFor(event.agent_id, agentIds);
  const repaired = event.repaired.length > 0;
  const style = lane === null ? {} : { "--lane": lane, "--lanes": Math.max(agentIds.length, lane + 1) };
  return html`<article id=${`event-${event.ordinal}`} class=${`event ${lane === null ? "neutral" : `color-${lane % 8}`}`} style=${style}>
    ${onSelect && html`<label class="event-select" title="Select event for tag anchor"><input type="checkbox" checked=${selected} onChange=${() => onSelect(event.id)} /> select</label>`}
    <button class="event-summary" onClick=${() => this.setState({ open: !open })} aria-expanded=${open}>
      <span class="agent">${event.agent_id ?? "system"}</span>
      ${event.role && html`<span class="role">${event.role}</span>`}
      <span class=${`badge badge-${event.badge}`}>${event.type}</span>
      ${repaired && html`<span class="badge repaired">repaired</span>`}
      ${markers.map(marker => html`<span class="tag-glyph" title=${`${marker.mode} ${marker.name}`}>tag</span>`)}
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

function RunSummary({ run }) {
  return html`<section class="compare-summary"><h2>${run.id}</h2><span>${run.outcome ?? "No outcome"}</span><span>${run.n_events} events</span><span>${run.ingest_warnings} warnings</span></section>`;
}

class Compare extends Component {
  state = { runs: [], pairs: [], total: 0, baseOffset: 0, summary: null, busy: false, error: "" };
  request = 0;
  componentDidMount() { this.loadRuns(); this.reset(); }
  componentDidUpdate(previous) {
    if (previous.a !== this.props.a || previous.b !== this.props.b || previous.comparator !== this.props.comparator || previous.offset !== this.props.offset) this.reset();
  }
  async loadRuns() {
    try { const page = await json(`/api/runs?limit=1000`); this.setState({ runs: page.rows }); }
    catch (reason) { this.setState({ error: String(reason) }); }
  }
  reset() {
    const { a, b, offset } = this.props;
    this.setState({ pairs: [], total: 0, baseOffset: offset || 0, summary: null, error: "" });
    if (a && b && a !== b) this.load(offset || 0, false, true);
  }
  async fetchPage(offset) {
    const params = new URLSearchParams({ a: this.props.a, b: this.props.b, comparator: this.props.comparator, offset, limit: PAGE_SIZE });
    return json(`/api/compare?${params}`);
  }
  async load(offset, all = false, replace = false) {
    const request = replace ? ++this.request : this.request;
    this.setState({ busy: true, error: "" });
    try {
      let page = await this.fetchPage(offset), loaded = page.pairs, next = offset + page.pairs.length;
      while (all && next < page.total) { const more = await this.fetchPage(next); loaded = loaded.concat(more.pairs); next += more.pairs.length; if (!more.pairs.length) break; }
      if (request === this.request) this.setState(current => ({ summary: page, total: page.total, baseOffset: replace ? offset : current.baseOffset, pairs: replace ? loaded : current.pairs.concat(loaded), busy: false }));
    } catch (reason) { if (request === this.request) this.setState({ busy: false, error: String(reason).replace(/^Error: 404.*/, "Run not found. Choose two available runs.") }); }
  }
  async jumpTo(index) {
    if (!this.state.pairs[index - this.state.baseOffset]) {
      const loadedPages = [];
      for (let offset = 0; offset <= index; offset += PAGE_SIZE) {
        const pageEnd = Math.min(offset + PAGE_SIZE, this.state.total);
        if (this.state.baseOffset <= offset && this.state.baseOffset + this.state.pairs.length >= pageEnd) loadedPages.push(offset);
      }
      const offsets = pagesNeededFor(index, PAGE_SIZE, loadedPages);
      const byIndex = new Map(this.state.pairs.map((pair, local) => [this.state.baseOffset + local, pair]));
      let summary = this.state.summary;
      this.setState({ busy: true });
      try { for (const offset of offsets) { summary = await this.fetchPage(offset); summary.pairs.forEach((pair, local) => byIndex.set(offset + local, pair)); } const pairs = Array.from({ length: index + 1 }, (_, pairIndex) => byIndex.get(pairIndex)); this.setState({ pairs, baseOffset: 0, total: summary.total, summary, busy: false }); }
      catch (reason) { this.setState({ busy: false, error: String(reason) }); return; }
    }
    requestAnimationFrame(() => requestAnimationFrame(() => { const row = document.getElementById(`pair-${index}`); if (row) { row.scrollIntoView({ behavior: "smooth", block: "center" }); row.classList.add("pair-highlight"); setTimeout(() => row.classList.remove("pair-highlight"), 1400); } }));
  }
  render({ a, b, comparator, onState, backHash }, { runs, pairs, total, baseOffset, summary, busy, error }) {
    const pick = (side, value) => html`<label>Run ${side}<select value=${value || ""} onChange=${event => onState({ [side]: event.target.value || null, offset: 0 })}><option value="">Choose run</option>${runs.map(run => html`<option value=${run.id}>${run.id}</option>`)}</select></label>`;
    if (a && b && a === b) return html`<section class="compare"><a class="back" href=${backHash}>Back to runs</a><div class="compare-pickers">${pick("a", a)}${pick("b", b)}</div><p class="empty">Choose two different runs to compare.</p></section>`;
    const banner = compareBannerState(summary);
    return html`<section class="compare"><a class="back" href=${backHash}>Back to runs</a><div class="compare-pickers">${pick("a", a)}${pick("b", b)}<label>Comparator<select value=${comparator} onChange=${event => onState({ comparator: event.target.value, offset: 0 })}><option value="normalized">normalized</option><option value="exact">exact</option></select></label></div>
      ${error && html`<p class="error">${error}</p>`}${summary && html`<header><${RunSummary} run=${summary.run_a} /><${RunSummary} run=${summary.run_b} /></header>
      <div class="banner divergence"><div><div>${banner.headline}</div>${banner.secondary && html`<div class="divergence-secondary">${banner.secondary} <button class="jump-link" onClick=${() => this.jumpTo(banner.secondaryIndex)}>Jump</button></div>`}</div>${banner.headlineIndex !== null && html`<button onClick=${() => this.jumpTo(banner.headlineIndex)}>Jump</button>`}</div>
      <div class="pair-list">${pairs.map((pair, local) => { const index = baseOffset + local; const divergent = pair.status !== "match"; return html`<div id=${`pair-${index}`} class=${`pair-row status-${pair.status}`}>
        <div class="event-cell">${pair.event_a ? html`<${EventRow} event=${pair.event_a} agentIds=${summary.run_a.agent_ids} />` : html`<div class="event-placeholder" aria-label="No event in run a"></div>`}</div>
        <button class=${`alignment-gutter ${gutterMarkFor(pair.status)}`} title=${`${pair.status} at pair ${index}`} onClick=${divergent ? () => this.jumpTo(index) : null}>${pair.status === "match" ? "=" : pair.status === "content-diff" ? "!" : "-"}</button>
        <div class="event-cell">${pair.event_b ? html`<${EventRow} event=${pair.event_b} agentIds=${summary.run_b.agent_ids} />` : html`<div class="event-placeholder" aria-label="No event in run b"></div>`}</div>
      </div>`; })}</div><footer class="paging"><span>${pairs.length} of ${total} pairs loaded</span>${baseOffset + pairs.length < total && html`<button disabled=${busy} onClick=${() => this.load(baseOffset + pairs.length)}>Load more</button>`}${baseOffset + pairs.length < total && html`<button disabled=${busy} onClick=${() => this.load(baseOffset + pairs.length, true)}>Load all</button>`}</footer>`}
      ${!summary && !error && html`<p>${a && b ? "Loading comparison..." : "Choose two runs to compare."}</p>`}
    </section>`;
  }
}

class Replay extends Component {
  state = { run: null, events: [], total: 0, busy: false, error: "", tags: [], runNote: "", vocabulary: [], selectedIds: [], modes: [], note: "", anchor: true, tagBusy: false };
  request = 0;
  componentDidMount() { this.reset(this.props.runId); }
  componentDidUpdate(previous) {
    if (previous.runId !== this.props.runId) this.reset(this.props.runId);
    else if (previous.agent !== this.props.agent || previous.phase !== this.props.phase || previous.type !== this.props.type) {
      this.load(0, false);
    }
  }
  reset(runId) {
    this.setState({ run: null, events: [], total: 0, error: "", tags: [], selectedIds: [], modes: [], note: "" });
    json(`/api/runs/${encodeURIComponent(runId)}`).then(run => this.setState({ run })).catch(reason => this.setState({ error: String(reason) }));
    json("/api/tags/vocabulary").then(value => this.setState({ vocabulary: value.categories })).catch(reason => this.setState({ error: String(reason) }));
    json(`/api/runs/${encodeURIComponent(runId)}/tags`).then(value => this.setState({ tags: value.tags, runNote: value.run_note })).catch(reason => this.setState({ error: String(reason) }));
    this.load(0, false, runId);
  }
  async replaceTags(tags) {
    this.setState({ tagBusy: true });
    try {
      const value = await putJson(`/api/runs/${encodeURIComponent(this.props.runId)}/tags`, { tags, run_note: this.state.runNote });
      this.setState({ tags: value.tags, runNote: value.run_note, tagBusy: false });
      return true;
    } catch (reason) {
      this.setState({ error: String(reason), tagBusy: false });
      return false;
    }
  }
  async addTag(event) {
    event.preventDefault();
    const tags = tagsWithModes(this.state.tags, this.state.modes, this.state.note, this.state.anchor ? this.state.selectedIds : []);
    if (await this.replaceTags(tags)) this.setState({ note: "", selectedIds: [], modes: [] });
  }
  async jumpTo(eventId) {
    const ordinal = ordinalOf(eventId);
    if (!this.state.events.some(event => event.ordinal === ordinal) && this.state.events.length < this.state.total) {
      await this.load(this.state.events.length, true);
    }
    requestAnimationFrame(() => {
      const row = document.getElementById(`event-${ordinal}`);
      if (!row) return;
      row.scrollIntoView({ behavior: "smooth", block: "center" });
      row.classList.add("anchor-highlight");
      setTimeout(() => row.classList.remove("anchor-highlight"), 1400);
    });
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
  render({ agent, phase, type, q, onState, backHash }, { run, events, total, busy, error, tags, vocabulary, selectedIds, modes, note, anchor, tagBusy }) {
  if (error) return html`<p class="error">${error}</p>`;
  if (!run) return html`<p>Loading run...</p>`;
  return html`<section class="replay">
    <a class="back" href=${backHash}>Back to runs</a>
    <header><h2>${run.id}</h2><span>${run.outcome ?? "No outcome"}</span></header>
    ${run.ingest_warnings > 0 && html`<div class="banner warning">${run.ingest_warnings} ingest warnings in this run</div>`}
    ${run.n_repaired > 0 && html`<div class="banner repair">${run.n_repaired} records repaired in this run</div>`}
    <details class="tag-panel" open><summary>Tags (${tags.length})</summary>
      <div class="tag-list">${tags.length === 0 && html`<p>No tags.</p>`}${tags.map((tag, index) => {
        const modeInfo = vocabulary.flatMap(category => category.modes).find(item => item.id === tag.mode);
        const resolution = resolveAnchors(tag, events.map(item => item.ordinal), run.n_events);
        const available = new Set(resolution.anchored);
        return html`<details class="tag-item" key=${`${tag.created_at}-${index}`}><summary>
          <b>${tag.mode} ${modeInfo?.name || ""}</b> <span>${tag.event_ids.length} anchors</span>
          ${resolution.detachedFromApi.length > 0 && html`<span class="badge detached">${resolution.detachedFromApi.length} detached</span>`}
        </summary><div class="tag-body">
          ${tag.note && html`<p>${tag.note}</p>`}<small>Created ${tag.created_at}</small>
          ${tag.event_ids.length > 0 && html`<ul>${tag.event_ids.map(id => html`<li>${available.has(id)
            ? html`<button class="anchor-link" onClick=${() => this.jumpTo(id)}>${id}</button>`
            : resolution.detachedFromApi.includes(id) ? html`<span>${id} (detached)</span>` : html`<button class="anchor-link" onClick=${() => this.jumpTo(id)}>${id}</button>`}</li>`)}</ul>`}
          <button disabled=${tagBusy} onClick=${() => this.replaceTags(tagListWithout(tags, index))}>Delete tag</button>
        </div></details>`;
      })}</div>
      <form class="tag-form" onSubmit=${event => this.addTag(event)}>
        <label>Failure mode<select multiple size="8" required value=${modes} onChange=${event => this.setState({ modes: Array.from(event.target.selectedOptions, option => option.value) })}>
          ${vocabulary.map(category => html`<optgroup label=${category.category}>${category.modes.map(item => html`<option value=${item.id} title=${item.description}>${item.id} ${item.name}</option>`)}</optgroup>`)}
        </select></label>
        <label>Note (optional)<textarea value=${note} onInput=${event => this.setState({ note: event.target.value })}></textarea></label>
        <label class="anchor-toggle"><input type="checkbox" checked=${anchor} onChange=${event => this.setState({ anchor: event.target.checked })} /> Anchor to selected events (${selectedIds.length})</label>
        <button disabled=${tagBusy || modes.length === 0}>Add tag</button>
      </form>
    </details>
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
      ${group.events.map(event => html`<${EventRow} key=${event.id} event=${event} agentIds=${run.agent_ids} query=${q} selected=${selectedIds.includes(event.id)} onSelect=${id => this.setState({ selectedIds: toggleSelection(this.state.selectedIds, id) })} markers=${tags.filter(tag => tag.event_ids.includes(event.id)).map(tag => ({ mode: tag.mode, name: vocabulary.flatMap(category => category.modes).find(item => item.id === tag.mode)?.name || "" }))} />`)}
    </section>`)}</div>
    <footer class="paging"><span>${events.length} of ${total} loaded, ${matching.length} matching</span>
      ${events.length < total && html`<button disabled=${busy} onClick=${() => this.load(events.length)}>Load more</button>`}
      ${events.length < total && html`<button disabled=${busy} onClick=${() => this.load(events.length, true)}>Load all</button>`}
    </footer>`; })()}
  </section>`;
  }
}

class BatchTable extends Component {
  state = { experiment: null, rows: [], groups: [], total: 0, outcomes: [], distribution: null, vocabulary: [], busy: false, error: "", draftKey: "", draftValue: "", selectedRunIds: [] };
  request = 0;
  componentDidMount() {
    this.setState({ draftKey: this.props.view.metadataKey || "", draftValue: this.props.view.metadataValue || "" });
    json("/api/experiment").then(experiment => this.setState({ experiment })).catch(reason => this.setState({ error: String(reason) }));
    json("/api/tags/vocabulary").then(value => this.setState({ vocabulary: value.categories.flatMap(category => category.modes) })).catch(reason => this.setState({ error: String(reason) }));
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
      const distributionParams = new URLSearchParams();
      if (view.groupBy) distributionParams.set("group_by", view.groupBy);
      const [page, distribution] = await Promise.all([
        json(`/api/runs?${params}`),
        json(`/api/tags/distribution?${distributionParams}`),
      ]);
      if (request === this.request) this.setState(current => ({
        rows: page.rows, groups: page.groups || [], total: page.total, distribution, busy: false,
        outcomes: [...new Set(current.outcomes.concat(page.rows.map(row => row.outcome).filter(value => value != null)))].sort(),
      }));
    } catch (reason) {
      if (request === this.request) this.setState({ busy: false, error: String(reason) });
    }
  }
  render({ view, onState, onOpen, onCompare }, { experiment, rows, groups, total, outcomes, distribution, vocabulary, busy, error, draftKey, draftValue, selectedRunIds }) {
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
        ${selectionToCompareState(selectedRunIds) && html`<button class="compare-button" onClick=${() => onCompare(selectedRunIds)}>Compare</button>`}
      </div>
      ${error && html`<p class="error">${error}</p>`}
      <div class="table-wrap"><table class="run-table"><thead><tr><th>Select</th>
        ${SUMMARY_COLUMNS.map(([field, label]) => html`<th><button class="sort" onClick=${() => onState({ ...cycleSort(view, field), offset: 0 })}>${label}${view.sort === field ? (view.order === "asc" ? " ^" : " v") : ""}</button></th>`)}
        ${view.columns.map(key => html`<th>${key}</th>`)}</tr></thead><tbody>
        ${(view.groupBy ? groups.flatMap(group => {
          const groupRows = rows.filter(row => String(groupValueOf(row, view.groupBy)) === String(group.group_value));
          const segments = outcomeBarSegments(group.outcome_distribution, 240);
          const header = html`<tr class="group-header"><th colSpan=${SUMMARY_COLUMNS.length + view.columns.length + 1}>
            <div class="group-title">${groupLabel(group.group_value, "(missing)")}</div>
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
        }) : rows.map(row => ({ row }))).map(item => item.row ? html`<tr key=${item.row.id} tabIndex="0" onClick=${() => onOpen(item.row.id)} onKeyDown=${event => { if (event.key === "Enter") onOpen(item.row.id); }}><td><input aria-label=${`Select ${item.row.id}`} type="checkbox" checked=${selectedRunIds.includes(item.row.id)} onClick=${event => event.stopPropagation()} onChange=${() => this.setState({ selectedRunIds: toggleSelection(selectedRunIds, item.row.id) })} /></td>
          ${SUMMARY_COLUMNS.map(([field]) => html`<td>${formatCell(item.row[field], field)}</td>`)}
          ${view.columns.map(key => html`<td>${formatCell(item.row.metadata[key], key)}</td>`)}</tr>` : item)}
      </tbody></table></div>
      <footer class="paging"><button disabled=${busy || view.offset === 0} onClick=${() => onState({ offset: Math.max(0, view.offset - RUN_PAGE_SIZE) })}>Previous</button>
        <span>Showing ${total === 0 ? 0 : view.offset + 1}-${end} of ${total}</span>
        <button disabled=${busy || end >= total} onClick=${() => onState({ offset: view.offset + RUN_PAGE_SIZE })}>Next</button></footer>
      <section class="failure-modes"><h2>Failure modes</h2>
        ${distribution?.warnings?.map(warning => html`<p class="banner warning">${warning}</p>`)}
        ${distribution && distribution.total_tags === 0
          ? html`<p class="tag-distribution-empty">No tags yet - open a run and add a failure-mode tag in the replay view</p>`
          : distribution && (distribution.groups || [{ group_value: null, modes: distribution.modes }]).map((set, setIndex) => {
            const descriptions = new Map(vocabulary.map(mode => [mode.id, mode.description]));
            const geometry = new Map(distributionBars(set.modes, 260).map(bar => [bar.id, bar]));
            return html`<section class="tag-distribution-set">
              ${distribution.groups && html`<h3>${groupLabel(set.group_value, "(none)")}</h3>`}
              ${groupByCategory(set.modes).map(category => html`<div class="tag-category"><h4>${category.category}</h4>
                ${category.modes.map(mode => { const bar = geometry.get(mode.id); return html`<div class="tag-bar-row">
                  <span class="tag-bar-name">${mode.id} ${mode.name}</span>
                  <svg viewBox="0 0 260 20" role="img" aria-label=${`${mode.id} ${mode.name}: ${bar.label}`} preserveAspectRatio="none">
                    <title>${mode.id} ${mode.name}: ${descriptions.get(mode.id) || ""}</title>
                    <rect width=${bar.width} height="20" fill=${GROUP_COLORS[setIndex % GROUP_COLORS.length]}></rect>
                  </svg><span>${bar.label}</span>
                </div>`; })}
              </div>`)}
            </section>`;
          })}
      </section>
    </section>`;
  }
}

class App extends Component {
  routeFor = hash => hash.startsWith("#/run/") ? "run" : hash.startsWith("#/compare") ? "compare" : "table";
  viewFor = hash => this.routeFor(hash) === "run" ? parseHashState(hash) : this.routeFor(hash) === "compare" ? compareStateParse(hash) : parseTableHashState(hash);
  state = { route: this.routeFor(location.hash), view: this.viewFor(location.hash), backHash: "#/" };
  changed = () => this.setState({ route: this.routeFor(location.hash), view: this.viewFor(location.hash) });
  updateView = changes => { location.hash = this.state.route === "run" ? serializeHashState({ ...this.state.view, ...changes }) : this.state.route === "compare" ? compareStateSerialize({ ...this.state.view, ...changes }) : serializeTableHashState({ ...this.state.view, ...changes }); };
  openRun = runId => { const backHash = serializeTableHashState(this.state.view); this.setState({ backHash }); location.hash = serializeHashState({ runId }); };
  openCompare = selected => { const state = selectionToCompareState(selected); if (state) { const backHash = serializeTableHashState(this.state.view); this.setState({ backHash }); location.hash = compareStateSerialize(state); } };
  componentDidMount() {
    addEventListener("hashchange", this.changed);
  }
  componentWillUnmount() { removeEventListener("hashchange", this.changed); }
  render(_, { route, view, backHash }) {
  return html`<main>${route === "run" ? html`<${Replay} ...${view} backHash=${backHash} onState=${this.updateView} />` : route === "compare" ? html`<${Compare} ...${view} backHash=${backHash} onState=${this.updateView} />` : html`<${BatchTable} view=${view} onState=${this.updateView} onOpen=${this.openRun} onCompare=${this.openCompare} />`}</main>`;
  }
}

render(html`<${App} />`, document.getElementById("app"));
