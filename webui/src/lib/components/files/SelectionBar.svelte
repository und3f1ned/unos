<script lang="ts">
  import type { Snippet } from "svelte";
  import { t } from "$lib/i18n";

  type Props = {
    // How many items are selected.
    count: number;
    // What selecting does here, shown while nothing is selected.
    hint: string;
    // The actions on them: buttons, shown only while something is selected.
    children?: Snippet;
  };

  let { count, hint, children }: Props = $props();
</script>

<!-- The toolbar of the Files page and of its trash (ADR-0432): the count of the selected items,
     and their actions only while one is selected; until then a hint takes their place. The actions
     and the hint share one cell, so that the bar is as tall either way: actions that appeared on
     the first click of a double-click would otherwise move the rows under the pointer, as a row of
     buttons that wraps on a narrow screen does. The BulkBar of the design system takes it over
     when it comes. -->
<div class="mb-4 flex items-start gap-2" role="toolbar" aria-label={$t("files.selectedItems")}>
  <span class="shrink-0 mr-2 text-sm leading-8 {count > 0 ? 'text-content font-medium' : 'text-content-secondary'}">{$t("files.selected", { n: count })}</span>
  <div class="grid flex-1 min-w-0">
    <div class="col-start-1 row-start-1 flex flex-wrap items-center gap-2 {count > 0 ? '' : 'invisible'}" inert={count === 0}>
      {@render children?.()}
    </div>
    {#if count === 0}
      <span class="col-start-1 row-start-1 py-2 text-xs leading-4 text-content-muted">{hint}</span>
    {/if}
  </div>
</div>
