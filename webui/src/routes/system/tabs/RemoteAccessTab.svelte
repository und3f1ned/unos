<script lang="ts">
  import RemoteAccessCard from '$lib/components/remote/RemoteAccessCard.svelte';
  import AppNamesCard from '$lib/components/remote/AppNamesCard.svelte';
  import { t } from '$lib/i18n';

  // System > Remote access (ADR-0420): the WireGuard tunnel and the local names of apps.
</script>

<div class="space-y-6">
  <h2 class="text-xl font-bold">{$t('remote.tab')}</h2>
  <RemoteAccessCard />
  <AppNamesCard />
</div>
