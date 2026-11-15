<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { AppTemplate } from '$lib/api/apps';

	/** The app whose hardware of this host's SoC is named (ADR-0377); nothing is shown without any. */
	export let app: AppTemplate;

	// What a hardware profile gives, by its id, and what to choose in an app, by the app's id.
	const PROFILE_TEXTS: Record<string, string> = { 'rk3588-video': 'apps.hardware.profile.rk3588Video' };
	const SETTING_TEXTS: Record<string, string> = { jellyfin: 'apps.hardware.setting.jellyfin' };
</script>

{#if app.hardware.length > 0}
	<Alert variant="info" class={$$props.class || ''} title={$t('apps.hardware.title')}>
		{#each app.hardware as profile}
			<p>{$t(PROFILE_TEXTS[profile.id] ?? 'apps.hardware.generic', { name: app.name })}</p>
		{/each}
		{#if SETTING_TEXTS[app.id]}
			<p class="mt-2">{$t(SETTING_TEXTS[app.id])}</p>
		{/if}
	</Alert>
{/if}
