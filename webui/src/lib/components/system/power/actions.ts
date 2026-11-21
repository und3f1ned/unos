import { rebootSystem, shutdownSystem, suspendSystem, type PowerAnswer } from '$lib/api/power';
import type { PaletteDialog } from '$lib/stores/palette';

// Reboot, shutdown, suspend and Wake-on-LAN act on the host (ADR-0233):
// reboot and shutdown one minute after the confirmation, so that they can
// still be cancelled here; suspend at once.

export type Action = 'reboot' | 'shutdown' | 'suspend';

export type ActionTexts = {
	titleKey: string;
	textKey: string;
	confirmKey: string;
	failedKey: string;
	variant: 'warning' | 'danger';
	run: () => Promise<PowerAnswer>;
};

// The confirmation of each action: the keys of its texts in the catalog.
export const ACTIONS: Record<Action, ActionTexts> = {
	reboot: {
		titleKey: 'power.reboot.title',
		textKey: 'power.reboot.text',
		confirmKey: 'power.reboot.confirm',
		failedKey: 'power.reboot.failed',
		variant: 'warning',
		run: rebootSystem
	},
	shutdown: {
		titleKey: 'power.shutdown.title',
		textKey: 'power.shutdown.text',
		confirmKey: 'power.shutdown.confirm',
		failedKey: 'power.shutdown.failed',
		variant: 'danger',
		run: shutdownSystem
	},
	suspend: {
		titleKey: 'power.suspend.title',
		textKey: 'power.suspend.text',
		confirmKey: 'power.suspend.confirm',
		failedKey: 'power.suspend.failed',
		variant: 'danger',
		run: suspendSystem
	}
};

// The command palette's Reboot, Shut down and Suspend open their confirmation on this tab (ADR-0393).
export const REQUESTED: Partial<Record<PaletteDialog, Action>> = {
	reboot: 'reboot',
	shutdown: 'shutdown',
	suspend: 'suspend'
};
