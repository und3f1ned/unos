import { getWolStatus, type WolInterface } from "$lib/api/power";

/** The host's network interfaces and their Wake-on-LAN. The page creates it and loads it. */
export class WakeOnLan {
  interfaces = $state<WolInterface[]>([]);
  loading = $state(true);
  error = $state("");
  // The interface whose Wake-on-LAN is being changed.
  busy = $state<string | null>(null);

  async load() {
    try {
      this.interfaces = (await getWolStatus()).interfaces;
      this.error = "";
    } catch (e) {
      this.error = e instanceof Error ? e.message : String(e);
    } finally {
      this.loading = false;
    }
  }
}
