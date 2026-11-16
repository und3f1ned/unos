<script lang="ts">
	import { Alert } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import { allProfiles, installedHardware, type AppTemplate, type InstalledApp } from '$lib/api/apps';

	/**
	 * The app whose containers' hardware of this host's SoC is named (ADR-0382); nothing is shown
	 * without any.
	 */
	export let app: AppTemplate;
	/**
	 * Its installed containers, if any: the hint names only the hardware that a container got, and
	 * a warning says that it was installed without the rest and how to give it that.
	 */
	export let installed: InstalledApp | null = null;

	// What a hardware profile gives, by its id, and what to choose in an app, by the app's id and
	// the profile's.
	const PROFILE_TEXTS: Record<string, string> = {
		'rk3588-video': 'apps.hardware.profile.rk3588Video',
		'rk3588-npu': 'apps.hardware.profile.rk3588Npu'
	};
	const SETTING_TEXTS: Record<string, string> = {
		'jellyfin:rk3588-video': 'apps.hardware.setting.jellyfin.rk3588Video',
		'immich:rk3588-video': 'apps.hardware.setting.immich.rk3588Video'
	};

	$: hardware = installed ? installedHardware(app, installed) : { got: allProfiles(app), missing: [] };
	// Each profile once, whichever of the app's containers got it.
	$: profiles = [...new Set(hardware.got.map((entry) => entry.profile.id))];
	$: settings = profiles.map((id) => SETTING_TEXTS[`${app.id}:${id}`]).filter((key) => key !== undefined);
</script>

{#if profiles.length > 0}
	<Alert variant="info" class={$$props.class || ''} title={$t('apps.hardware.title')}>
		{#each profiles as id}
			<p>{$t(PROFILE_TEXTS[id] ?? 'apps.hardware.generic', { name: app.name })}</p>
		{/each}
		{#each settings as key}
			<p class="mt-2">{$t(key)}</p>
		{/each}
	</Alert>
{/if}
{#if hardware.missing.length > 0}
	<Alert variant="warning" class={$$props.class || ''} title={$t('apps.hardware.title')}>
		<p>{$t('apps.hardware.missing', { name: app.name })}</p>
	</Alert>
{/if}
