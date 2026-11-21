<script lang="ts">
	// The card of each install, update or rollback of an app (ADR-0384, ADR-0408): its state and
	// step, while it downloads one line of its whole download (how many images are on the NAS, and
	// a bar in which each image is an equal share), each image and each container, and its result.
	// No layers: Docker's word for a part of an image says nothing to a person (webui.ux-apps).
	import { Ban, X } from 'lucide-svelte';
	import { Alert, Badge, Button, Card, ProgressBar } from '$lib/components/ui';
	import { intlLocale, t } from '$lib/i18n';
	import { formatSize as formatSizeIn } from './size';
	import { missingHardware, type AppInstallJob, type AppTemplate, type ImagePull } from '$lib/api/apps';

	type Props = {
		/** The installs that /ws/v1/events sends (ADR-0384): the running one and the last of each app. */
		jobs?: AppInstallJob[];
		/** The catalog, for each app's icon and what its install's notice adds. */
		catalog?: AppTemplate[];
		/** The ids of the running jobs whose cancel was asked: they say so until they end. */
		cancelling?: ReadonlySet<string>;
		/** The ended job's card was dismissed. */
		ondismiss?: (job: AppInstallJob) => void;
		/** The running job's cancel was asked. */
		oncancel?: (job: AppInstallJob) => void;
	};

	let { jobs = [], catalog = [], cancelling = new Set(), ondismiss, oncancel }: Props = $props();

	// The apps whose first account to register becomes their administrator: the notice of their
	// install asks to create it at once, before anyone else on the network can (ADR-0415).
	const FIRST_ADMIN: ReadonlySet<string> = new Set(['immich', 'paperless-ngx', 'linkwarden']);
	// The apps that come with an account of a known e-mail and password, which the notice of their
	// install asks to change at once: BookStack's admin@admin.com (ADR-0415).
	const DEFAULT_LOGIN: ReadonlySet<string> = new Set(['bookstack']);

	// The words of the job's states and steps, of an image's pull and of a container (ADR-0384).
	const STATE: Record<AppInstallJob['state'], { key: string; variant: 'info' | 'success' | 'error' | 'warning' }> = {
		running: { key: 'apps.job.state.running', variant: 'info' },
		succeeded: { key: 'apps.job.state.succeeded', variant: 'success' },
		failed: { key: 'apps.job.state.failed', variant: 'error' },
		cancelled: { key: 'apps.job.state.cancelled', variant: 'warning' }
	};
	const STEP: Record<AppInstallJob['step'], string> = {
		checking: 'apps.job.step.checking',
		pulling: 'apps.job.step.pulling',
		stopping: 'appUpdates.step.stopping',
		copying: 'appUpdates.step.copying',
		creating: 'apps.job.step.creating',
		starting: 'apps.job.step.starting',
		cleaning: 'apps.job.step.cleaning',
		restoring: 'appUpdates.step.restoring'
	};
	// What an update and a rollback (ADR-0408) say instead of an install's words.
	const LABEL: Record<AppInstallJob['kind'], string> = {
		install: 'apps.job.label',
		update: 'appUpdates.job.update',
		rollback: 'appUpdates.job.rollback'
	};
	const RUNNING: Record<AppInstallJob['kind'], string> = {
		install: 'apps.job.state.running',
		update: 'appUpdates.state.update',
		rollback: 'appUpdates.state.rollback'
	};
	const SUCCEEDED: Record<AppInstallJob['kind'], string> = {
		install: 'apps.job.state.succeeded',
		update: 'appUpdates.state.updated',
		rollback: 'appUpdates.state.rolledBack'
	};
	const FAILED: Record<AppInstallJob['kind'], string> = {
		install: 'apps.detail.installFailed',
		update: 'appUpdates.updateFailed',
		rollback: 'appUpdates.rollbackFailedTitle'
	};

	/** The word of the job's state, by its kind. */
	function stateKey(job: AppInstallJob): string {
		if (job.state === 'running') return RUNNING[job.kind];
		if (job.state === 'succeeded') return SUCCEEDED[job.kind];
		return STATE[job.state].key;
	}

	/** The step's words: an update checks other things than an install. */
	function stepKey(job: AppInstallJob): string {
		return job.kind !== 'install' && job.step === 'checking' ? 'appUpdates.step.checking' : STEP[job.step];
	}
	const IMAGE: Record<ImagePull['state'], string> = {
		waiting: 'apps.job.image.waiting',
		present: 'apps.job.image.present',
		pulling: 'apps.job.image.pulling',
		pulled: 'apps.job.image.pulled',
		failed: 'apps.job.image.failed'
	};
	const CONTAINER: Record<string, { key: string; variant: 'neutral' | 'info' | 'success' | 'error' }> = {
		waiting: { key: 'apps.job.container.waiting', variant: 'neutral' },
		created: { key: 'apps.job.container.created', variant: 'neutral' },
		starting: { key: 'apps.job.container.starting', variant: 'info' },
		running: { key: 'apps.job.container.running', variant: 'success' },
		failed: { key: 'apps.job.container.failed', variant: 'error' },
		removed: { key: 'apps.job.container.removed', variant: 'neutral' }
	};

	// A size with one decimal in the language of the page (formatSize of ./size).
	function formatSize(bytes: number): string {
		return formatSizeIn(bytes, $intlLocale, $t, 1);
	}

	function templateOf(job: AppInstallJob): AppTemplate | undefined {
		return catalog.find((app) => app.id === job.template_id);
	}

	/**
	 * The whole download of a job: the images on the NAS, and the percent of the download, in which
	 * each image is an equal share: one on the NAS is whole, one that downloads its part done of the
	 * layers Docker reported so far, any other none. Bytes are not added up across the images: an
	 * image's size is known only as Docker reports its layers (ADR-0384), so a sum of them would near
	 * 100% and drop as each next image starts.
	 */
	function download(job: AppInstallJob): { percent: number; ready: number } {
		let shares = 0;
		let ready = 0;
		for (const image of job.images) {
			if (image.state === 'pulled' || image.state === 'present') {
				ready++;
				shares += 1;
			} else if (image.state === 'pulling' && image.bytes_total > 0) {
				shares += Math.min(1, image.bytes_done / image.bytes_total);
			}
		}
		return { percent: Math.floor((shares / job.images.length) * 100), ready };
	}

	/** The image's name without its digest: the tag tells the version; the digest is in the details. */
	function shortName(image: string): string {
		return image.split('@')[0];
	}
</script>

{#each jobs as job (job.id)}
	{@const template = templateOf(job)}
	{@const state = STATE[job.state]}
	<!-- A region named after the app, which the E2E specs find it by. -->
	<section aria-label={$t(LABEL[job.kind], { name: job.name })}>
		<Card>
			<div class="flex flex-wrap items-center gap-3">
				<div
					class="w-10 h-10 rounded-lg bg-surface-muted flex items-center justify-center text-2xl flex-shrink-0"
				>
					{template?.icon || '📦'}
				</div>
				<div class="flex-1 min-w-0">
					<div class="flex flex-wrap items-center gap-2">
						<h2 class="font-semibold break-words">{job.name}</h2>
						<Badge variant={state.variant}>{$t(stateKey(job))}</Badge>
					</div>
					{#if job.state === 'running'}
						<p class="text-sm text-secondary">{$t(stepKey(job))}</p>
					{/if}
				</div>
				{#if job.state !== 'running'}
					<Button variant="ghost" size="sm" icon={X} onclick={() => ondismiss?.(job)}>
						{$t('apps.job.dismiss')}
					</Button>
				{:else if cancelling.has(job.id)}
					<!-- Its cancel was asked: the button waits until the job ends. -->
					<Button variant="secondary" size="sm" icon={Ban} loading>
						{$t(job.kind === 'update' ? 'appUpdates.cancelUpdate' : 'apps.job.cancel')}
					</Button>
				{:else if job.kind === 'install'}
					<Button variant="secondary" size="sm" icon={Ban} onclick={() => oncancel?.(job)}>
						{$t('apps.job.cancel')}
					</Button>
				{:else if job.kind === 'update'}
					<!-- A rollback cannot be cancelled: it ends on its own (ADR-0408). -->
					<Button variant="secondary" size="sm" icon={Ban} onclick={() => oncancel?.(job)}>
						{$t('appUpdates.cancelUpdate')}
					</Button>
				{/if}
			</div>

			<!-- The end of a job stays on its card, no toast (ADR-0432): the card is the job's record,
			     kept by the server across reloads and tabs until dismissed. -->
			{#if job.state === 'succeeded' && job.kind !== 'install'}
				<!-- design-ignore: an update's or a rollback's end, on the job's card that the server keeps until dismissed -->
				<Alert variant="success" class="mt-4">
					<p>
						{$t(job.kind === 'update' ? 'appUpdates.updatedNotice' : 'appUpdates.rolledBackNotice', {
							name: job.name
						})}
					</p>
				</Alert>
			{:else if job.state === 'succeeded' && job.result}
				{@const missing = template ? missingHardware(template, job.result).length > 0 : false}
				<!-- design-ignore: an install's end says how to sign in to the app, to read before going on -->
				<Alert variant={missing ? 'warning' : 'success'} class="mt-4">
					<p>
						{job.result.pool
							? $t('apps.installedNoticePool', { name: job.name, pool: job.result.pool })
							: $t('apps.installedNotice', { name: job.name })}
					</p>
					{#if FIRST_ADMIN.has(job.template_id)}
						<p class="mt-1">{$t('apps.install.firstAdmin', { name: job.name })}</p>
					{/if}
					{#if DEFAULT_LOGIN.has(job.template_id)}
						<p class="mt-1">{$t('apps.install.defaultLogin', { name: job.name })}</p>
					{/if}
					{#if missing}
						<p class="mt-1">{$t('apps.hardware.missing', { name: job.name })}</p>
					{/if}
				</Alert>
			{:else if job.state === 'failed'}
				<Alert variant="error" title={$t(FAILED[job.kind])} class="mt-4">
					<p class="break-words">{job.error || $t(FAILED[job.kind])}</p>
				</Alert>
			{:else if job.state === 'cancelled'}
				{@const cancelled = job.kind === 'update' ? 'appUpdates.cancelledTitle' : 'apps.job.cancelledTitle'}
				<Alert variant="warning" title={$t(cancelled)} class="mt-4">
					<p class="break-words">{job.error || $t(cancelled)}</p>
				</Alert>
			{/if}

			{#if job.state === 'running' && job.step === 'pulling' && job.images.length > 0}
				{@const whole = download(job)}
				<div class="mt-4">
					<p class="text-sm">{$t('apps.job.imagesReady', { done: whole.ready, n: job.images.length })}</p>
					<ProgressBar
						class="mt-1.5"
						variant="info"
						value={whole.percent}
						max={100}
						label={$t('apps.job.downloadAll', { name: job.name })}
					/>
				</div>
			{/if}

			{#if job.images.length > 0}
				<h3 class="text-sm font-medium mt-4 mb-2">{$t('apps.job.images')}</h3>
				<ul class="space-y-3">
					{#each job.images as image (image.image)}
						<li>
							<div class="flex flex-wrap items-baseline justify-between gap-x-3 gap-y-1">
								<code class="text-xs mono break-all">{shortName(image.image)}</code>
								<span class="text-xs text-secondary">{$t(IMAGE[image.state])}</span>
							</div>
							{#if image.state === 'pulling' || (image.state === 'failed' && image.layers > 0)}
								<ProgressBar
									class="mt-1"
									size="sm"
									variant={image.state === 'failed' ? 'error' : 'info'}
									value={image.bytes_done}
									max={image.bytes_total}
									label={$t('apps.job.download', { image: shortName(image.image) })}
								/>
								{#if image.bytes_total > 0}
									<p class="text-xs text-content-muted mt-1 tabular">
										{$t('apps.job.bytes', {
											done: formatSize(image.bytes_done),
											total: formatSize(image.bytes_total)
										})}
									</p>
								{/if}
							{/if}
						</li>
					{/each}
				</ul>
			{/if}

			<h3 class="text-sm font-medium mt-4 mb-2">{$t('apps.job.containers')}</h3>
			<ul class="flex flex-wrap gap-2">
				{#each job.containers as container (container.name)}
					{@const look = CONTAINER[container.state] ?? CONTAINER.waiting}
					<li class="flex items-center gap-2 rounded-md bg-surface-muted px-2 py-1">
						<span class="text-xs mono break-all">{container.name}</span>
						<Badge variant={look.variant}>{$t(look.key)}</Badge>
					</li>
				{/each}
			</ul>
		</Card>
	</section>
{/each}
