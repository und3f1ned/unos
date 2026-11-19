<script lang="ts">
	import { onMount } from 'svelte';
	import { Monitor, Moon, Palette, Sun } from 'lucide-svelte';
	import { Alert, Card } from '$lib/components/ui';
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
	<fieldset aria-describedby="theme-hint">
		<legend class="form-label">{$t('theme.label')}</legend>
		<div class="flex flex-wrap gap-2">
			{#each THEME_CHOICES as choice (choice)}
				{@const Icon = icons[choice]}
				<label
					class="flex items-center gap-2 px-3 py-2 rounded-lg border cursor-pointer text-sm transition-colors
						{$themeChoice === choice
						? 'border-brand-ring bg-surface-overlay text-content'
						: 'border-border-strong text-content-soft hover:bg-surface-overlay'}"
				>
					<input
						type="radio"
						name="theme"
						value={choice}
						checked={$themeChoice === choice}
						onchange={() => choose(choice)}
					/>
					<Icon class="w-4 h-4" aria-hidden="true" />
					{$t(names[choice])}
				</label>
			{/each}
		</div>
	</fieldset>
	<p id="theme-hint" class="form-hint">{$t('theme.hint')}</p>
	{#if $themeLoadFailed}
		<Alert variant="warning" class="mt-3">{$t('theme.loadFailed')}</Alert>
	{/if}
	{#if error}
		<Alert variant="error" class="mt-3">{error}</Alert>
	{/if}
</Card>
