<script lang="ts">
	import { onMount } from 'svelte';
	import { Monitor, Moon, Palette, Sun } from 'lucide-svelte';
	import { Alert, Card, RadioGroup } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import {
		chooseTheme,
		loadAccountTheme,
		themeChoice,
		themeLoadFailed,
		THEME_CHOICES,
		type ThemeChoice
	} from '$lib/stores/theme';

	// The theme of the web UI (ADR-0405): it applies at once and the server keeps it for the
	// account, so that it stays across sign-ins and follows the user to another browser. System
	// follows the device's light or dark setting as it changes. The choices stay enabled while one is
	// sent, so that the arrow keys go on through them: the last one is kept.

	const icons = { dark: Moon, light: Sun, system: Monitor };
	const names = { dark: 'theme.dark', light: 'theme.light', system: 'theme.system' };

	let error = $state('');

	// The user's theme as the server keeps it: asked again when the first read failed.
	onMount(() => void loadAccountTheme());

	async function choose(next: ThemeChoice) {
		if (next === $themeChoice) return;
		error = '';
		try {
			await chooseTheme(next);
		} catch (e) {
			error = e instanceof Error && e.message ? e.message : $t('theme.saveFailed');
		}
	}
</script>

<Card>
	<h3 class="card-title mb-4">
		<Palette class="w-5 h-5 text-content-secondary" />
		{$t('theme.title')}
	</h3>
	<!-- The choices stay enabled while one is sent (ADR-0405); a RadioGroup of the theme (ADR-0432). -->
	<RadioGroup
		name="theme"
		legend={$t('theme.label')}
		description={$t('theme.hint')}
		inline
		value={$themeChoice}
		options={THEME_CHOICES.map((choice) => ({ value: choice, label: $t(names[choice]), icon: icons[choice] }))}
		onchange={(value) => choose(value as ThemeChoice)}
	/>
	{#if $themeLoadFailed}
		<Alert variant="warning" class="mt-3">{$t('theme.loadFailed')}</Alert>
	{/if}
	{#if error}
		<Alert variant="error" class="mt-3">{error}</Alert>
	{/if}
</Card>
