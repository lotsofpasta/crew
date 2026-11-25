// The visualizer's data layer. Keep this file; replace the drawing in visualizer.html (or write your own page).
//
//   const viz = VizData.start({ every: 2000 });
//   viz.on("update", (state) => ...)        // after every poll: state.nodes, state.edges, state.approvals
//   viz.on("pulse",  (pulse, state) => ...) // once per new signal, in order: animate it from pulse.from to pulse.to
//
// state.nodes  Map id -> {id, type: "agent"|"line", label, role, parent, alive, busy, doing, heartbeat, attention, ...}
//              (any extra field you put on an agent in agents.yaml - colour, icon, x, y - shows up here)
// state.edges  [{from, to, type: "reports_to"}]
// state.pulses the last 500 pulses: {id, t, from, to, kind, label, chat, level, ref, meta}
// The shape is documented in DESIGN.md and served by GET /api/viz.
(function () {
  function start(opts = {}) {
    const every = opts.every || 2000;
    const handlers = { update: [], pulse: [], error: [] };
    const state = { brand: "", nodes: new Map(), edges: [], pulses: [], approvals: 0, cursor: 0, t: 0 };
    const emit = (name, ...a) => handlers[name].forEach((fn) => { try { fn(...a); } catch (e) { console.error(e); } });

    async function poll() {
      let d;
      try { d = await Crew.api(`/api/viz?after=${state.cursor}&limit=300`); }
      catch (e) { emit("error", e); return; }
      state.brand = d.brand; state.t = d.t; state.edges = d.edges; state.approvals = d.approvals;
      const seen = new Set();
      for (const n of d.nodes) { seen.add(n.id); state.nodes.set(n.id, { ...(state.nodes.get(n.id) || {}), ...n }); }
      for (const id of [...state.nodes.keys()]) if (!seen.has(id)) state.nodes.delete(id);
      const first = state.cursor === 0;
      state.cursor = d.cursor;
      state.pulses.push(...d.pulses);
      if (state.pulses.length > 500) state.pulses.splice(0, state.pulses.length - 500);
      emit("update", state);
      if (!first || opts.replayHistory) for (const p of d.pulses) emit("pulse", p, state);
    }
    Crew.poll(poll, every);
    return { state, on(name, fn) { handlers[name].push(fn); return this; } };
  }
  window.VizData = { start };
})();
