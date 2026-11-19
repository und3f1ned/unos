import { getHostname } from "$lib/api/network";
import { getTimezone, getTimezones } from "$lib/api/system";

/**
 * The name and time zone of the host (ADR-0331): each read and saved on its own. The page creates
 * it and loads it; HostSettingsCard edits and saves it.
 */
export class HostSettings {
  hostname = $state("");
  savedHostname = $state("");
  zone = $state("");
  savedZone = $state("");
  zones = $state<string[]>([]);
  loading = $state(true);
  loadFailed = $state(false);
  hostnameLoaded = $state(false);
  zoneLoaded = $state(false);

  // The host's zone first when the host does not list it, so that the page shows it.
  zoneOptions = $derived([
    ...(this.savedZone && this.savedZone !== "Unknown" && !this.zones.includes(this.savedZone) ? [this.savedZone] : []),
    ...this.zones,
  ].map((name) => ({ value: name, label: name })));

  async load() {
    // Each row from its own answers: one that fails leaves the other usable.
    const [name, current, listed] = await Promise.allSettled([getHostname(), getTimezone(), getTimezones()]);
    if (name.status === "fulfilled") {
      this.hostname = this.savedHostname = name.value.hostname;
      this.hostnameLoaded = true;
    }
    if (current.status === "fulfilled") {
      this.zone = this.savedZone = current.value.timezone;
      this.zoneLoaded = true;
    }
    if (listed.status === "fulfilled") this.zones = listed.value.timezones;
    this.loadFailed = !this.hostnameLoaded && !this.zoneLoaded;
    this.loading = false;
  }
}
