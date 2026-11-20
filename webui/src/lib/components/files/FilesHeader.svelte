<script lang="ts">
  import { FolderPlus, RefreshCw, Upload } from "lucide-svelte";
  import { Button } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { FileBrowser } from "./browser.svelte";

  type Props = {
    browser: FileBrowser;
  };

  let { browser }: Props = $props();

  // The file chooser of Upload. It is hidden: the Upload button opens it, also
  // from the keyboard.
  let uploadInput: HTMLInputElement | null = null;

  function handleUpload(e: Event & { currentTarget: HTMLInputElement }) {
    const input = e.currentTarget;
    const files = Array.from(input.files ?? []);
    input.value = "";
    void browser.upload(files);
  }
</script>

<!-- The page header (ADR-0428, ADR-0432): the title, then Refresh, New folder and the page's one
     primary action, Upload, in words. The trash is in the sidebar, under Files. -->
<div class="page-header mb-4">
  <h1 class="page-title">{$t("files.title")}</h1>
  <div class="page-actions">
    <input bind:this={uploadInput} type="file" multiple class="hidden" onchange={handleUpload} />
    <Button variant="secondary" size="icon"
      onclick={() => browser.refresh()}
      title={$t("common.refresh")}
      disabled={browser.loading}
    >
      <RefreshCw class="w-4 h-4 {browser.loading ? 'animate-spin' : ''}" aria-hidden="true" />
      <span class="sr-only">{$t("common.refresh")}</span>
    </Button>
    <Button variant="secondary" icon={FolderPlus} onclick={() => (browser.showNewFolder = true)}>
      {$t("files.newFolder")}
    </Button>
    <Button icon={Upload} onclick={() => uploadInput?.click()}>{$t("files.upload")}</Button>
  </div>
</div>
