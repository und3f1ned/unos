<script lang="ts">
	import { restartSmb, type SmbInterfaces } from '$lib/api/shares';
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import DangerDialog from './DangerDialog.svelte';
	import { messageOf } from './errors';

	// Restart SMB, which closes every open SMB connection: the dialog says so first. Always
	// mounted: the card asks with open().
	type Props = {
		/** The interfaces as the server reports them, with the open sessions; null before they are read. */
		current: SmbInterfaces | null;
		/** smbd restarted: the server's answer. */
		onrestarted: (answer: SmbInterfaces) => void;
		/** The restart failed: the card reads the interfaces again, and the dialog waits for it. */
		onfailed: () => Promise<void>;
	};

	let { current, onrestarted, onfailed }: Props = $props();

	let isOpen = $state(false);
	let restarting = $state(false);
	let error = $state('');

	/** Asks, without the error of an earlier try. */
	export function open() {
		error = '';
		isOpen = true;
	}

	function close() {
		if (!restarting) isOpen = false;
	}

	function sessionsText(state: SmbInterfaces): string {
		if (state.sessions === null) return $t('shares.interfaces.restartSessionsAny');
		if (state.sessions === 0) return $t('shares.interfaces.restartSessionsNone');
		return $t('shares.interfaces.restartSessions', { n: state.sessions });
	}

	async function restart() {
		restarting = true;
		error = '';
		try {
			const answer = await restartSmb();
			isOpen = false;
			onrestarted(answer);
		} catch (e) {
			error = messageOf(e);
			await onfailed();
		} finally {
			restarting = false;
		}
	}
</script>

<DangerDialog
	open={isOpen}
	title={$t('shares.interfaces.restartTitle')}
	busy={restarting}
	confirmLabel={$t('shares.interfaces.restartNow')}
	onclose={close}
	onconfirm={restart}
>
	{#if current}
		<p>{$t('shares.interfaces.restartText')}</p>
		<p class="mt-2">{sessionsText(current)}</p>
	{/if}
	{#if error}
		<Alert variant="error" title={$t('shares.interfaces.notRestarted')}>{error}</Alert>
	{/if}
</DangerDialog>
