<script lang="ts">
	import { FileKey } from 'lucide-svelte';
	import { generateSelfSignedCert } from '$lib/api/security';
	import { Alert, Button, Modal, toast } from '$lib/components/ui';
	import { t } from '$lib/i18n';
	import type { Certificates } from './certificates.svelte';

	// The form of a new self-signed certificate. What was typed stays for the next opening until a
	// certificate is generated.
	type Props = {
		certificates: Certificates;
	};

	let { certificates }: Props = $props();

	let newCert = $state({
		domain: '',
		validity_days: 365,
		organization: '',
		country: ''
	});

	async function handleGenerateCert() {
		if (!newCert.domain) {
			certificates.generateError = $t('account.certs.domainRequired');
			return;
		}

		certificates.loading = true;
		certificates.generateError = '';
		try {
			await generateSelfSignedCert({
				domain: newCert.domain,
				validity_days: newCert.validity_days,
				organization: newCert.organization || undefined,
				country: newCert.country || undefined
			});
			toast.success($t('account.certs.generated'));
			certificates.generateOpen = false;
			newCert = { domain: '', validity_days: 365, organization: '', country: '' };
			await certificates.load();
		} catch (e) {
			certificates.generateError = (e as Error).message || $t('account.certs.generateFailed');
		} finally {
			certificates.loading = false;
		}
	}
</script>

<!-- Generate a self-signed certificate: a Modal (ADR-0428), whose form keeps the browser's checks of
     its fields; its buttons are the form's, in the look of Modal's footer. -->
<Modal
	open={certificates.generateOpen}
	title={$t('account.certs.generateTitle')}
	closable={!certificates.loading}
	onclose={() => (certificates.generateOpen = false)}
>
	{#snippet titleIcon()}
		<FileKey class="w-5 h-5 text-content-secondary" aria-hidden="true" />
	{/snippet}

	<form
		onsubmit={(e) => {
			e.preventDefault();
			handleGenerateCert();
		}}
		class="space-y-4"
	>
		<!-- In the dialog: behind its overlay a refusal could not be read. -->
		{#if certificates.generateError}
			<Alert variant="error">{certificates.generateError}</Alert>
		{/if}

		<div>
			<label for="cert-domain" class="form-label">{$t('account.certs.domain')}</label>
			<input
				id="cert-domain"
				type="text"
				bind:value={newCert.domain}
				class="form-input"
				placeholder={$t('account.certs.domainPlaceholder')}
				required
			/>
		</div>

		<div>
			<label for="cert-validity" class="form-label">{$t('account.certs.validity')}</label>
			<input
				id="cert-validity"
				type="number"
				bind:value={newCert.validity_days}
				min="1"
				max="3650"
				class="form-input"
			/>
		</div>

		<div>
			<label for="cert-organization" class="form-label">{$t('account.certs.organization')}</label>
			<input
				id="cert-organization"
				type="text"
				bind:value={newCert.organization}
				maxlength="64"
				class="form-input"
				placeholder={$t('account.certs.organizationPlaceholder')}
			/>
		</div>

		<div>
			<label for="cert-country" class="form-label">{$t('account.certs.country')}</label>
			<input
				id="cert-country"
				type="text"
				bind:value={newCert.country}
				minlength="2"
				maxlength="2"
				pattern="[A-Za-z][A-Za-z]"
				title={$t('account.certs.countryHint')}
				class="form-input"
				placeholder={$t('account.certs.countryPlaceholder')}
			/>
		</div>

		<div class="modal-footer">
			<Button
				variant="secondary"
				type="button"
				disabled={certificates.loading}
				onclick={() => (certificates.generateOpen = false)}
			>
				{$t('common.cancel')}
			</Button>
			<Button variant="primary" type="submit" loading={certificates.loading}>
				{$t('account.certs.generate')}
			</Button>
		</div>
	</form>
</Modal>
