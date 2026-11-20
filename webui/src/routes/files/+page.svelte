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
  import { Alert, Skeleton } from "$lib/components/ui";
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
  // State: they come with the page's first draw (ADR-0430).
  let fileActions = $state.raw<ReturnType<typeof FileActions>>();
  let leftoversNotice = $state.raw<ReturnType<typeof LeftoversNotice>>();
  let deleteDialog: ReturnType<typeof DeleteDialog> | undefined;
  let leftoversDialog: ReturnType<typeof LeftoversDialog> | undefined;

  onMount(() => {
    browser.load();
    leftovers.load();
  });

  // The first folder and the uploads' leftovers load at once; until both have answered the page is
  // their skeleton, so that the notice above the list pushes nothing down (ADR-0430). A refresh
  // keeps what is shown.
  let pending = $derived(!leftovers.loaded || (browser.loading && !browser.listing));

  let breadcrumbs = $derived(getBreadcrumbs(browser.currentPath, browser.rootPath, $t("files.pools")));

  // Sizes in the units of the page's language.
  let units = $derived([$t("files.unit.b"), $t("files.unit.kb"), $t("files.unit.mb"), $t("files.unit.gb"), $t("files.unit.tb")]);
</script>

<!-- The page has no padding of its own beside main's (ADR-0428). On a narrow screen its buttons
     wrap, the list leaves out the Modified column, and the page grows with the list, so that main
     scrolls it as a whole. From md the list fills the rest of the window and scrolls inside it
     (ADR-0302). -->
<div class="md:h-full flex flex-col mx-auto w-full max-w-[1440px]">
  <FilesHeader {browser} />

  {#if browser.error}
    <!-- role="alert": screen readers announce the message when it appears. -->
    <div role="alert">
      <Alert variant="error" dismissible class="mb-4" ondismiss={() => (browser.error = "")}>{browser.error}</Alert>
    </div>
  {/if}

  {#if pending}
    <!-- The breadcrumbs, the selection's buttons and rows of the list, in their places. -->
    <div class="md:flex-1 md:min-h-0 flex flex-col" aria-busy="true">
      <div class="flex items-center mb-4 px-1.5 py-1 rounded-lg border border-border bg-surface-elevated">
        <span class="flex items-center h-7 px-2"><Skeleton class="w-24" /></span>
      </div>
      <div class="mb-4 flex flex-wrap items-center gap-2">
        <Skeleton class="w-20 mr-2" />
        {#each [0, 1, 2, 3, 4] as action (action)}
          <Skeleton shape="block" class="h-8 w-24" />
        {/each}
      </div>
      <div class="md:flex-1 md:min-h-0 bg-surface-elevated rounded-card border border-border shadow-raised overflow-hidden">
        <Skeleton shape="row" columns={4} count={8} class="px-4" />
      </div>
    </div>
  {:else}
    <LeftoversNotice bind:this={leftoversNotice} {leftovers} {units} onremove={() => leftoversDialog?.ask()} />
    <Breadcrumbs crumbs={breadcrumbs} onnavigate={(path) => browser.navigate(path)} />
    <FileActions bind:this={fileActions} {browser} ondelete={() => deleteDialog?.ask()} />
    <TransferBar {browser} />
    <NewFolderForm {browser} />
    <FileList {browser} {units} />
  {/if}
</div>

<DeleteDialog bind:this={deleteDialog} {browser} onclosed={() => fileActions?.focusDelete()} />
<LeftoversDialog
  bind:this={leftoversDialog}
  {browser}
  {leftovers}
  {units}
  onclosed={() => leftoversNotice?.focusRemove()}
/>
