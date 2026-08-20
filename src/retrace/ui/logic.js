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
