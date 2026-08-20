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

export function repairedFields(metadata) {
  const repaired = metadata?._retrace?.repaired;
  if (!repaired || typeof repaired !== "object" || Array.isArray(repaired)) return [];
  return Object.entries(repaired).map(([field, original]) => ({ field, original }));
}

const BADGES = new Set(["message", "tool_call", "tool_result", "system", "other"]);

export function badgeClassFor(type) {
  return BADGES.has(type) ? type : "other";
}
