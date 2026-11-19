<script lang="ts">
  import { ChevronRight } from "lucide-svelte";
  import { Button } from "$lib/components/ui";
  import type { Crumb } from "./paths";

  type Props = {
    // From the pools directory down to the folder that is open, the last one.
    crumbs: Crumb[];
    // A crumb was chosen: open its folder.
    onnavigate: (path: string) => void;
  };

  let { crumbs, onnavigate }: Props = $props();
</script>

<!-- Breadcrumbs -->
<div class="flex items-center gap-1 mb-4 text-sm overflow-x-auto">
  {#each crumbs as crumb, i}
    {#if i > 0}
      <ChevronRight class="w-4 h-4 text-content-muted flex-shrink-0" />
    {/if}
    <Button variant="plain"
      onclick={() => onnavigate(crumb.path)}
      class="px-2 py-1 rounded hover:bg-surface-overlay truncate {i === crumbs.length - 1 ? 'text-status-info' : 'text-content-soft'}"
    >
      {crumb.name}
    </Button>
  {/each}
</div>
