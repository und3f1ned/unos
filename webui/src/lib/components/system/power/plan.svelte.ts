import { messageOf } from '$lib/errors';
import { getPowerConfig, getPowerSchedule, type PowerConfig, type PowerSchedule } from '$lib/api/power';

function clockTime(hour: number, minute: number): string {
	return `${String(hour).padStart(2, '0')}:${String(minute).padStart(2, '0')}`;
}

/** The form of the stored settings. */
function formOf(settings: PowerConfig) {
	return {
		autoShutdown: settings.auto_shutdown,
		shutdownTime: clockTime(settings.shutdown_hour, settings.shutdown_minute),
		autoWakeup: settings.auto_wakeup,
		wakeTime: clockTime(settings.wakeup_hour, settings.wakeup_minute),
		idleShutdown: settings.idle_shutdown,
		idleMinutes: settings.idle_timeout_minutes
	};
}

/**
 * The scheduled shutdown, the wake by the RTC and the idle shutdown of the power settings
 * (ADR-0407): their times are on the host's clock, and the server acts on them. The settings, their
 * form and the plan that the server made of them; the page creates it and loads it.
 */
export class PowerPlan {
	config = $state<PowerConfig | null>(null);
	configError = $state('');
	schedule = $state<PowerSchedule | null>(null);
	scheduleError = $state('');
	form = $state({
		autoShutdown: false,
		shutdownTime: '23:00',
		autoWakeup: false,
		wakeTime: '07:00',
		idleShutdown: false,
		idleMinutes: 30
	});
	saving = $state(false);
	saveError = $state('');

	// The form differs from the stored settings: only then is there something to save. A field
	// emptied or typed back to its value counts as what it reads.
	changed = $derived.by(() => {
		if (!this.config) return false;
		const stored = formOf(this.config);
		const form = this.form;
		return (
			form.autoShutdown !== stored.autoShutdown ||
			form.shutdownTime !== stored.shutdownTime ||
			form.autoWakeup !== stored.autoWakeup ||
			form.wakeTime !== stored.wakeTime ||
			form.idleShutdown !== stored.idleShutdown ||
			Number(form.idleMinutes) !== stored.idleMinutes ||
			String(form.idleMinutes ?? '') === ''
		);
	});

	fillForm(settings: PowerConfig) {
		this.form = formOf(settings);
	}

	/** The settings and the plan, again after a read failed. */
	async reload() {
		await Promise.all([this.loadConfig(), this.loadSchedule()]);
	}

	async loadConfig() {
		try {
			this.config = await getPowerConfig();
			this.fillForm(this.config);
			this.configError = '';
		} catch (e) {
			this.configError = messageOf(e);
		}
	}

	async loadSchedule() {
		try {
			this.schedule = await getPowerSchedule();
			this.scheduleError = '';
		} catch (e) {
			this.scheduleError = messageOf(e);
		}
	}
}
