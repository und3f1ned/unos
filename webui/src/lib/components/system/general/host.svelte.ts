import { getHostname, type HostnameConfig } from '$lib/api/network';
import { getTimezone, getTimezones } from '$lib/api/system';
import { settle, type Settled } from '$lib/pages/read';

/**
 * The name and time zone of the host (ADR-0331): each read and saved on its own. The page creates
 * it and gives it what its load read (apply); HostSettingsCard edits and saves it, and reads it
 * again on Retry.
 */
export class HostSettings {
	hostname = $state('');
	savedHostname = $state('');
	zone = $state('');
	savedZone = $state('');
	zones = $state<string[]>([]);
	loadFailed = $state(false);
	hostnameLoaded = $state(false);
	zoneLoaded = $state(false);

	// The host's zone first when the host does not list it, so that the page shows it.
	zoneOptions = $derived(
		[
			...(this.savedZone && this.savedZone !== 'Unknown' && !this.zones.includes(this.savedZone)
				? [this.savedZone]
				: []),
			...this.zones
		].map((name) => ({ value: name, label: name }))
	);

	async load() {
		const [name, current, listed] = await Promise.all([
			settle(getHostname()),
			settle(getTimezone()),
			settle(getTimezones())
		]);
		this.apply(name, current, listed);
	}

	/**
	 * Each row from its own answers: one that fails leaves the other usable. A field changed and not
	 * saved keeps what was typed.
	 */
	apply(
		name: Settled<HostnameConfig>,
		current: Settled<{ timezone: string }>,
		listed: Settled<{ timezones: string[] }>
	) {
		if (name.ok) {
			if (this.hostname === this.savedHostname) this.hostname = name.value.hostname;
			this.savedHostname = name.value.hostname;
			this.hostnameLoaded = true;
		}
		if (current.ok) {
			if (this.zone === this.savedZone) this.zone = current.value.timezone;
			this.savedZone = current.value.timezone;
			this.zoneLoaded = true;
		}
		if (listed.ok) this.zones = listed.value.timezones;
		this.loadFailed = !this.hostnameLoaded && !this.zoneLoaded;
	}
}
