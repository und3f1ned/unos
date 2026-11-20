<script lang="ts">
  import { Copy, Download, FolderInput, Pencil, Trash2 } from "lucide-svelte";
  import { Button } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { FileBrowser } from "./browser.svelte";
  import SelectionBar from "./SelectionBar.svelte";

  type Props = {
    browser: FileBrowser;
    // Delete was chosen: the page asks in its dialog.
    ondelete: () => void;
  };

  let { browser, ondelete }: Props = $props();

  // The Delete button, which takes the keyboard focus back when the Delete dialog closes.
  let deleteButtonSlot: HTMLSpanElement | null = null;

  export function focusDelete() {
    deleteButtonSlot?.querySelector("button")?.focus();
  }
</script>

<!-- Actions on the selected items, shown in the selection's bar while one is selected; Delete
     moves to the trash, so it is no solid red button (ADR-0432). -->
<SelectionBar count={browser.selected.size} hint={$t("files.selectHint")}>
  <Button
    variant="secondary"
    size="sm"
    icon={Download}
    loading={browser.downloading}
    disabled={browser.working || !browser.selectedFile}
    onclick={() => browser.selectedFile && browser.download(browser.selectedFile)}
  >
    {$t("files.download")}
  </Button>
  <Button
    variant="secondary"
    size="sm"
    icon={Copy}
    disabled={browser.working || browser.selected.size === 0}
    onclick={() => browser.startTransfer("copy")}
  >
    {$t("files.copy")}
  </Button>
  <Button
    variant="secondary"
    size="sm"
    icon={FolderInput}
    disabled={browser.working || browser.selected.size === 0}
    onclick={() => browser.startTransfer("move")}
  >
    {$t("files.move")}
  </Button>
  <Button
    variant="secondary"
    size="sm"
    icon={Pencil}
    disabled={browser.working || !browser.selectedItem}
    onclick={() => browser.selectedItem && browser.startRename(browser.selectedItem)}
  >
    {$t("files.rename")}
  </Button>
  <span bind:this={deleteButtonSlot} class="contents">
    <Button
      variant="ghost-danger"
      size="sm"
      icon={Trash2}
      disabled={browser.working || browser.selected.size === 0}
      onclick={() => ondelete()}
    >
      {$t("files.delete")}
    </Button>
  </span>
</SelectionBar>
