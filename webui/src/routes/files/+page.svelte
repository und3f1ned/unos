<script lang="ts">
  import { onMount } from "svelte";
  import {
    Breadcrumbs,
    DeleteDialog,
    FileActions,
    FileList,
    FilesHeader,
    LeftoversDialog,
    LeftoversNotice,
    NewFolderForm,
    TransferBar,
  } from "$lib/components/files";
  import { FileBrowser, type FileFailure } from "$lib/components/files/browser.svelte";
  import { UploadLeftovers } from "$lib/components/files/leftovers.svelte";
  import { getBreadcrumbs } from "$lib/components/files/paths";
  import { Alert } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  // The page's words for what failed; the server's reason follows them.
  function failed(failure: FileFailure): string {
    switch (failure.action) {
      case "open":
        return $t("files.error.open");
      case "create":
        return $t("files.error.create");
      case "rename":
        return $t("files.error.rename");
      case "copy":
        return $t("files.error.copy");
      case "move":
        return $t("files.error.move");
      case "download":
        return $t("files.error.download", { name: failure.name });
    }
  }

  const browser = new FileBrowser({ failed, uploadFailed: (files) => $t("files.error.upload", { files }) });
  const leftovers = new UploadLeftovers();

  // The parts that take the keyboard focus back when a dialog closes, and the dialogs.
  let fileActions: ReturnType<typeof FileActions> | undefined;
  let leftoversNotice: ReturnType<typeof LeftoversNotice> | undefined;
  let deleteDialog: ReturnType<typeof DeleteDialog> | undefined;
  let leftoversDialog: ReturnType<typeof LeftoversDialog> | undefined;

  onMount(() => {
    browser.load();
    leftovers.load();
  });

  let breadcrumbs = $derived(getBreadcrumbs(browser.currentPath, browser.rootPath, $t("files.pools")));

  // Sizes in the units of the page's language.
  let units = $derived([$t("files.unit.b"), $t("files.unit.kb"), $t("files.unit.mb"), $t("files.unit.gb"), $t("files.unit.tb")]);
</script>

<!-- On a narrow screen the page has no padding of its own beside main's, its buttons wrap, the
     list leaves out the Modified column, and the page grows with the list, so that main scrolls
     it as a whole. From md the list fills the rest of the window and scrolls inside it (ADR-0302). -->
<div class="md:p-6 md:h-full flex flex-col">
  <FilesHeader {browser} />

  {#if browser.error}
    <!-- role="alert": screen readers announce the message when it appears. -->
    <div role="alert">
      <Alert variant="error" dismissible class="mb-4" ondismiss={() => (browser.error = "")}>{browser.error}</Alert>
    </div>
  {/if}

  <LeftoversNotice bind:this={leftoversNotice} {leftovers} {units} onremove={() => leftoversDialog?.ask()} />
  <Breadcrumbs crumbs={breadcrumbs} onnavigate={(path) => browser.navigate(path)} />
  <FileActions bind:this={fileActions} {browser} ondelete={() => deleteDialog?.ask()} />
  <TransferBar {browser} />
  <NewFolderForm {browser} />
  <FileList {browser} {units} />
</div>

<DeleteDialog bind:this={deleteDialog} {browser} onclosed={() => fileActions?.focusDelete()} />
<LeftoversDialog
  bind:this={leftoversDialog}
  {browser}
  {leftovers}
  {units}
  onclosed={() => leftoversNotice?.focusRemove()}
/>
