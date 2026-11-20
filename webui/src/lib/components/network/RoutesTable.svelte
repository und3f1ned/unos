<script lang="ts">
  // The host's routing table, as ip prints it (ADR-0295); a default route is named so.
  import type { Route } from "$lib/api/network";
  import { t } from "$lib/i18n";
  import { isDefault } from "./network";

  type Props = { routes: Route[] };

  let { routes }: Props = $props();
</script>

<div class="card table-frame">
  <table class="table table-compact">
    <thead class="table-header">
      <tr>
        <th class="pr-4">{$t("network.routes.destination")}</th>
        <th class="pr-4">{$t("network.gateway")}</th>
        <th class="pr-4">{$t("network.routes.interface")}</th>
        <th>{$t("network.protocol")}</th>
      </tr>
    </thead>
    <tbody>
      {#each routes as route}
        <tr class="table-row">
          <td class="pr-4 font-mono break-all">{isDefault(route) ? $t("network.routes.default") : route.dst}</td>
          <td class="pr-4 font-mono break-all">{route.gateway || "-"}</td>
          <td class="pr-4">{route.dev}</td>
          <td class="text-content-secondary">{route.protocol || "-"}</td>
        </tr>
      {:else}
        <tr>
          <td colspan="4" class="py-8 text-center text-content-secondary">{$t("network.routes.none")}</td>
        </tr>
      {/each}
    </tbody>
  </table>
</div>
