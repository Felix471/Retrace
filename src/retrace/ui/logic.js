// Pure replay transformations. Event input is already ordered by the API.

export function groupByTurn(events) {
  const groups = [];
  for (const event of events) {
    const last = groups[groups.length - 1];
    if (!last || last.turn !== event.turn) {
      groups.push({ turn: event.turn, events: [event] });
    } else {
      last.events.push(event);
    }
  }
  return groups;
}

export function laneFor(agentId, agentIds) {
  if (agentId === null || agentId === undefined) return null;
  const index = agentIds.indexOf(agentId);
  return index === -1 ? agentIds.length : index;
}

export function previewOf(content, maxLen = 120) {
  const flat = String(content ?? "").replace(/\s+/g, " ").trim();
  if (flat.length <= maxLen) return flat;
  return `${flat.slice(0, Math.max(0, maxLen - 3))}...`;
}

function eventOrdinal(eventId) {
  const value = String(eventId ?? "");
  const separator = value.lastIndexOf(":");
  if (separator === -1) return null;
  const suffix = value.slice(separator + 1);
  if (!/^\d+$/.test(suffix)) return null;
  const ordinal = Number(suffix);
  return Number.isSafeInteger(ordinal) ? ordinal : null;
}

export function resolveAnchors(tag, loadedOrdinals, totalEvents) {
  const loaded = new Set(loadedOrdinals);
  const detachedFromApi = [...(tag?.detached_event_ids || [])];
  const detached = new Set(detachedFromApi);
  const anchored = [];
  const needsLoad = [];
  for (const id of tag?.event_ids || []) {
    if (detached.has(id)) continue;
    const ordinal = eventOrdinal(id);
    if (ordinal === null || ordinal < 0 || ordinal >= totalEvents) continue;
    anchored.push(id);
    if (!loaded.has(ordinal) && !needsLoad.includes(ordinal)) needsLoad.push(ordinal);
  }
  return { anchored, detachedFromApi, needsLoad };
}

export function toggleSelection(selectedIds, id) {
  const unique = selectedIds.filter((value, index, values) => values.indexOf(value) === index);
  return unique.includes(id) ? unique.filter(value => value !== id) : [...unique, id];
}

export function tagListWith(tags, newTag) {
  return [...tags, newTag];
}

export function tagListWithout(tags, index) {
  return tags.filter((_tag, tagIndex) => tagIndex !== index);
}

function decoded(value) {
  if (value === null || value === "") return null;
  try { return decodeURIComponent(value); } catch { return null; }
}

function present(params, key) {
  const value = params.get(key);
  return value === null || value === "" ? null : value;
}

export function parseHashState(hash) {
  const match = String(hash ?? "").match(/^#\/run\/([^?]*)(?:\?(.*))?$/);
  if (!match) return { runId: null, agent: null, phase: null, type: null, q: null };
  const params = new URLSearchParams(match[2] || "");
  return {
    runId: decoded(match[1]),
    agent: present(params, "agent"),
    phase: present(params, "phase"),
    type: present(params, "type"),
    q: present(params, "q"),
  };
}

export function serializeHashState(state) {
  if (state?.runId === null || state?.runId === undefined || state.runId === "") return "";
  const params = new URLSearchParams();
  for (const key of ["agent", "phase", "type", "q"]) {
    if (state[key] !== null && state[key] !== undefined && state[key] !== "") {
      params.set(key, String(state[key]));
    }
  }
  const query = params.toString();
  return `#/run/${encodeURIComponent(String(state.runId))}${query ? `?${query}` : ""}`;
}

export function compareStateParse(hash) {
  const match = String(hash ?? "").match(/^#\/compare(?:\?(.*))?$/);
  const params = new URLSearchParams(match?.[1] || "");
  const offset = Number.parseInt(params.get("offset") || "0", 10);
  return {
    a: match ? present(params, "a") : null,
    b: match ? present(params, "b") : null,
    comparator: match ? (present(params, "comparator") || "normalized") : "normalized",
    offset: Number.isInteger(offset) && offset >= 0 ? offset : 0,
  };
}

export function compareStateSerialize(state) {
  const params = new URLSearchParams();
  for (const key of ["a", "b"]) if (state?.[key]) params.set(key, String(state[key]));
  if (state?.comparator && state.comparator !== "normalized") params.set("comparator", String(state.comparator));
  const offset = Number(state?.offset);
  if (Number.isInteger(offset) && offset > 0) params.set("offset", String(offset));
  const query = params.toString();
  return `#/compare${query ? `?${query}` : ""}`;
}

export function pagesNeededFor(pairIndex, pageSize, loadedPages) {
  const target = Math.floor(Number(pairIndex) / Number(pageSize));
  if (!Number.isInteger(target) || target < 0 || !(Number(pageSize) > 0)) return [];
  const loaded = new Set(loadedPages || []);
  return Array.from({ length: target + 1 }, (_, index) => index * Number(pageSize))
    .filter(offset => !loaded.has(offset));
}

export function gutterMarkFor(status) {
  return ({ match: "gutter-match", "content-diff": "gutter-content-diff", "only-a": "gutter-only-a", "only-b": "gutter-only-b" })[status] || "gutter-unknown";
}

export function selectionToCompareState(selectedRunIds) {
  return selectedRunIds?.length === 2 ? { a: selectedRunIds[0], b: selectedRunIds[1] } : null;
}

const TABLE_DEFAULTS = {
  sort: "id", order: "asc", outcome: null, metadataKey: null,
  metadataValue: null, groupBy: null, columns: [], offset: 0,
};

export function parseTableHashState(hash) {
  const match = String(hash ?? "").match(/^#\/(?:\?(.*))?$/);
  if (!match) return { ...TABLE_DEFAULTS, columns: [] };
  const params = new URLSearchParams(match[1] || "");
  const offset = Number.parseInt(params.get("offset") || "0", 10);
  return {
    sort: present(params, "sort") || TABLE_DEFAULTS.sort,
    order: params.get("order") === "desc" ? "desc" : "asc",
    outcome: present(params, "outcome"),
    metadataKey: present(params, "key"),
    metadataValue: present(params, "value"),
    groupBy: present(params, "group_by"),
    columns: params.getAll("column").filter((key, index, values) => key && values.indexOf(key) === index),
    offset: Number.isFinite(offset) && offset >= 0 ? offset : 0,
  };
}

export function serializeTableHashState(state) {
  const params = new URLSearchParams();
  if (state?.sort && state.sort !== TABLE_DEFAULTS.sort) params.set("sort", String(state.sort));
  if (state?.order === "desc") params.set("order", "desc");
  if (state?.outcome != null && state.outcome !== "") params.set("outcome", String(state.outcome));
  if (state?.metadataKey != null && state.metadataKey !== "") params.set("key", String(state.metadataKey));
  if (state?.metadataValue != null && state.metadataValue !== "") params.set("value", String(state.metadataValue));
  if (state?.groupBy != null && state.groupBy !== "") params.set("group_by", String(state.groupBy));
  for (const key of state?.columns || []) if (key !== "") params.append("column", String(key));
  const offset = Number(state?.offset);
  if (Number.isInteger(offset) && offset > 0) params.set("offset", String(offset));
  const query = params.toString();
  return `#/${query ? `?${query}` : ""}`;
}

export function groupValueOf(run, groupKey) {
  if (!groupKey || !run?.metadata || !Object.prototype.hasOwnProperty.call(run.metadata, groupKey)) return null;
  return run.metadata[groupKey] ?? null;
}

export function outcomeBarSegments(distribution, totalWidth) {
  const entries = Object.entries(distribution || {}).filter(([, count]) => Number(count) > 0);
  const total = entries.reduce((sum, [, count]) => sum + Number(count), 0);
  if (total === 0 || !(Number(totalWidth) >= 0)) return [];
  let x = 0;
  return entries.map(([label, count], index) => {
    const width = index === entries.length - 1 ? Number(totalWidth) - x : Number(totalWidth) * Number(count) / total;
    const segment = { label, count: Number(count), x, width, colorIndex: index };
    x += width;
    return segment;
  });
}

export function distributionBars(modes, maxWidth) {
  const peak = Math.max(0, ...(modes || []).map(mode => Number(mode.runs_with_tag) || 0));
  const available = Number(maxWidth) >= 0 ? Number(maxWidth) : 0;
  return (modes || []).map(mode => ({
    id: mode.id,
    width: peak === 0 ? 0 : available * (Number(mode.runs_with_tag) || 0) / peak,
    label: `${Number(mode.runs_with_tag) || 0} runs, ${Number(mode.total_tags) || 0} tags`,
  }));
}

export function groupByCategory(modes) {
  const groups = [];
  for (const mode of [...(modes || [])].sort((left, right) =>
    String(left.id).localeCompare(String(right.id), undefined, { numeric: true }))) {
    let group = groups.find(item => item.category === mode.category);
    if (!group) {
      group = { category: mode.category, modes: [] };
      groups.push(group);
    }
    group.modes.push(mode);
  }
  return groups;
}

export function toggleColumn(selected, key) {
  const columns = [...new Set(selected || [])];
  const index = columns.indexOf(key);
  if (index === -1) columns.push(key);
  else columns.splice(index, 1);
  return columns;
}

export function cycleSort(current, field) {
  if (current?.sort !== field) return { sort: field, order: "asc" };
  return { sort: field, order: current.order === "asc" ? "desc" : "asc" };
}

export function formatCell(value, field) {
  if (value === null || value === undefined) return "";
  if (field === "duration_s") return Number(value).toFixed(2);
  if (field === "total_cost") return Number(value).toFixed(4);
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export function matchesSearch(event, query) {
  const needle = String(query ?? "").toLocaleLowerCase();
  if (!needle) return true;
  return [event?.content, event?.agent_id, event?.role, event?.phase]
    .some(value => String(value ?? "").toLocaleLowerCase().includes(needle));
}

export function highlightSegments(text, query) {
  const source = String(text ?? "");
  const needle = String(query ?? "");
  if (!needle) return [{ text: source, match: false }];
  const folded = source.toLocaleLowerCase();
  const target = needle.toLocaleLowerCase();
  const segments = [];
  let start = 0;
  let index = folded.indexOf(target);
  if (index === -1) return [{ text: source, match: false }];
  while (index !== -1) {
    if (index > start) segments.push({ text: source.slice(start, index), match: false });
    const end = index + needle.length;
    segments.push({ text: source.slice(index, end), match: true });
    start = end;
    index = folded.indexOf(target, start);
  }
  if (start < source.length) segments.push({ text: source.slice(start), match: false });
  return segments;
}
