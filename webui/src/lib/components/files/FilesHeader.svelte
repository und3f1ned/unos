<script lang="ts">
  import { FolderOpen, Plus, RefreshCw, Trash, Upload } from "lucide-svelte";
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

<div class="flex flex-wrap items-center justify-between gap-2 mb-4">
  <h1 class="text-2xl font-bold flex items-center gap-2">
    <FolderOpen class="w-7 h-7" />
    {$t("files.title")}
  </h1>
  <div class="flex flex-wrap items-center gap-2">
    <input bind:this={uploadInput} type="file" multiple class="hidden" onchange={handleUpload} />
    <!-- The span shows the tooltip: Button does not pass a title on. -->
    <span class="flex" title={$t("files.upload")}>
      <Button variant="secondary" size="icon" onclick={() => uploadInput?.click()}>
        <Upload class="w-5 h-5" />
        <span class="sr-only">{$t("files.upload")}</span>
      </Button>
    </span>
    <Button variant="secondary" size="icon"
      onclick={() => (browser.showNewFolder = true)}
      title={$t("files.newFolder")}
    >
      <Plus class="w-5 h-5" />
    </Button>
    <Button variant="secondary" size="icon"
      onclick={() => browser.refresh()}
      title={$t("common.refresh")}
      disabled={browser.loading}
    >
      <RefreshCw class="w-5 h-5 {browser.loading ? 'animate-spin' : ''}" />
    </Button>
    <Button variant="secondary" icon={Trash} href="/files/trash">{$t("files.trash.title")}</Button>
  </div>
</div>
