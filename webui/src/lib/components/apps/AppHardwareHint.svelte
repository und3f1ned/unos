<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { missingHardware, type AppTemplate, type InstalledApp } from '$lib/api/apps';

	/** The app whose hardware of this host's SoC is named (ADR-0377); nothing is shown without any. */
	export let app: AppTemplate;
	/**
	 * Its installed container, if any: the hint names only the hardware the container got, and a
	 * warning says that it was installed without the rest and how to give it that.
	 */
	export let installed: InstalledApp | null = null;

	// What a hardware profile gives, by its id, and what to choose in an app, by the app's id.
	const PROFILE_TEXTS: Record<string, string> = { 'rk3588-video': 'apps.hardware.profile.rk3588Video' };
	const SETTING_TEXTS: Record<string, string> = { jellyfin: 'apps.hardware.setting.jellyfin' };

	$: missing = installed ? missingHardware(app, installed) : [];
	$: got = app.hardware.filter((profile) => !missing.includes(profile));
</script>

{#if got.length > 0}
	<Alert variant="info" class={$$props.class || ''} title={$t('apps.hardware.title')}>
		{#each got as profile}
			<p>{$t(PROFILE_TEXTS[profile.id] ?? 'apps.hardware.generic', { name: app.name })}</p>
		{/each}
		{#if SETTING_TEXTS[app.id]}
			<p class="mt-2">{$t(SETTING_TEXTS[app.id])}</p>
		{/if}
	</Alert>
{/if}
{#if missing.length > 0}
	<Alert variant="warning" class={$$props.class || ''} title={$t('apps.hardware.title')}>
		<p>{$t('apps.hardware.missing', { name: app.name })}</p>
	</Alert>
{/if}
