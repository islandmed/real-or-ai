// Today's standings for a list of players (friends board, group boards): each
// player's first result that day (any board) and latest non-empty name. Scores
// stay hidden until the requester has a result that day (locked).
export async function standings(env, pid, ids, d) {
  if (!ids.length) return { locked: true, rows: [] };
  const marks = ids.map(() => "?").join(",");
  const { results: played } = await env.DB.prepare(
    `SELECT pid, score, time, won, grid, MIN(created) AS c FROM scores WHERE date = ? AND pid IN (${marks}) GROUP BY pid`
  ).bind(d, ...ids).all();
  const { results: names } = await env.DB.prepare(
    `SELECT pid, name, MAX(created) AS c FROM scores WHERE pid IN (${marks}) AND name != '' GROUP BY pid`
  ).bind(...ids).all();
  const byPid = Object.fromEntries(played.map(r => [r.pid, r]));
  const nameOf = Object.fromEntries(names.map(r => [r.pid, r.name]));
  const locked = !byPid[pid];
  const rows = ids.map(id => {
    const r = byPid[id];
    const row = { pid: id, name: nameOf[id] || "", played: !!r };
    if (r && (!locked || id === pid)) Object.assign(row, { score: r.score, time: r.time, won: r.won, grid: r.grid });
    return row;
  });
  return { locked, rows };
}
