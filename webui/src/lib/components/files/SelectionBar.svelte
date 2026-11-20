<script lang="ts">
  import type { Snippet } from "svelte";
  import { t } from "$lib/i18n";

  type Props = {
    // How many items are selected.
    count: number;
    // The actions on them: buttons.
    children?: Snippet;
  };

  let { count, children }: Props = $props();
</script>

<!-- The toolbar of the Files page and of its trash: the count of the selected items, then the
     actions on them. -->
<div class="mb-4 flex flex-wrap items-center gap-2" role="toolbar" aria-label={$t("files.selectedItems")}>
  <span class="text-sm text-content-secondary mr-2">{$t("files.selected", { n: count })}</span>
  {@render children?.()}
</div>
