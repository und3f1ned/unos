// The web UI is a single-page app (the static adapter, ADR-0012): nothing is rendered on a server,
// in the build or under `vite dev`, and the pages' loads ($lib/pages/load) run in the browser only,
// where the tab's session key is (ADR-0397).
export const ssr = false;
