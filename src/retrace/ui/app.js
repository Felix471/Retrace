import { render } from "/ui/vendor/preact.module.js";
import htm from "/ui/vendor/htm.module.js";
import { h } from "/ui/vendor/preact.module.js";

const html = htm.bind(h);

async function start() {
  const response = await fetch("/api/experiment");
  const data = await response.json();
  render(html`<h1>${data.run_count} runs loaded</h1>`, document.getElementById("app"));
}

start();
