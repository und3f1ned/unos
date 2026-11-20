<script lang="ts">
  import RemoteAccessCard from '$lib/components/remote/RemoteAccessCard.svelte';
  import { t } from '$lib/i18n';

  // System > Remote access (ADR-0420): the WireGuard tunnel. The apps' local names, which reach the
  // LAN only, are in System > Network (ADR-0431).
</script>

<div class="space-y-6">
  <h2 class="text-xl font-semibold">{$t('remote.tab')}</h2>
  <RemoteAccessCard />
</div>
