<script lang="ts">
	import { smbUrl, windowsAddress } from '$lib/components/shares/texts';
	import { t } from '$lib/i18n';

	// How to open a share from Windows, macOS and Linux, with the address of this NAS as this
	// browser reaches it.

	type Props = {
		/** The NAS's address as this browser reaches it (an IP address or a name). */
		host: string;
		/** The share the examples open. */
		share: string;
		/** The SMB name the account signs in with; null for a guest share. */
		username: string | null;
	};

	let { host, share, username }: Props = $props();

	// i18n-ignore: an option of the command below, the same in every language
	const mountOption = $derived(username !== null ? `username=${username}` : 'guest');
	// i18n-ignore: a command, the same in every language
	const mountCommand = $derived(`sudo mount -t cifs //${host}/${share} /mnt/${share} -o ${mountOption}`);
</script>

<div class="space-y-2">
	<h4 class="card-title">{$t('account.smb.connect')}</h4>
	<ul class="space-y-1 text-sm">
		<li>
			<span class="text-secondary">{$t('account.smb.windows')}</span>
			<span class="mono">{windowsAddress(host, share)}</span>
		</li>
		<li>
			<span class="text-secondary">{$t('account.smb.macos')}</span>
			{$t('account.smb.finder')}
			<span class="mono">{smbUrl(host, share)}</span>
		</li>
		<li>
			<span class="text-secondary">{$t('account.smb.linux')}</span>
			<span class="mono">{smbUrl(host, share)}</span>
			{$t('account.smb.or')}
			<span class="mono break-all">{mountCommand}</span>
		</li>
	</ul>
	<p class="text-sm text-muted">{$t('account.smb.noTwoFactor')}</p>
	<p class="text-sm text-muted">{$t('account.smb.localAddress')}</p>
</div>
