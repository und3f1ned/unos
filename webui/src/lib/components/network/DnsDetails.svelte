<script lang="ts">
  // The host's name servers and search domains (ADR-0430: their one place in System > Network).
  import type { DNSConfig } from "$lib/api/network";
  import { t } from "$lib/i18n";
  import { Server } from "lucide-svelte";

  type Props = { dnsConfig: DNSConfig };

  let { dnsConfig }: Props = $props();
</script>

<section class="card" aria-labelledby="network-dns-title">
  <h3 id="network-dns-title" class="card-title mb-3">
    <Server class="w-4 h-4 text-content-secondary" />
    {$t("network.dnsConfig.title")}
  </h3>
  <div class="grid grid-cols-1 md:grid-cols-2 gap-4">
    <div>
      <p class="text-xs text-content-muted mb-2">{$t("network.dnsConfig.servers")}</p>
      {#each dnsConfig.nameservers ?? [] as ns}
        <p class="text-sm font-mono break-all">{ns}</p>
      {:else}
        <p class="text-sm text-content-muted">{$t("network.dnsConfig.none")}</p>
      {/each}
    </div>
    {#if dnsConfig.search_domains?.length}
      <div>
        <p class="text-xs text-content-muted mb-2">{$t("network.dnsConfig.domains")}</p>
        {#each dnsConfig.search_domains as domain}
          <p class="text-sm font-mono">{domain}</p>
        {/each}
      </div>
    {/if}
  </div>
</section>
