<script lang="ts">
  import { tick } from "svelte";
  import { Trash, Trash2 } from "lucide-svelte";
  import { ApiError } from "$lib/api/client";
  import { deleteItems } from "$lib/api/files";
  import { Alert, Button, Modal } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { FileBrowser } from "./browser.svelte";
  import { messageOf, signedOut } from "./errors";

  type Props = {
    browser: FileBrowser;
    // The dialog closed without deleting: the page gives the keyboard focus back to Delete.
    onclosed: () => void;
  };

  let { browser, onclosed }: Props = $props();

  // The Delete dialog (ADR-0259): the items it is about while it is open, which
  // of its choices is on its way, and the server's refusal. Delete for good is
  // on offer only once the server refused to move the items to the trash
  // because they have none there, or it cannot be used (409), or their pool
  // has no space left (507): Delete in Files is never one click away from
  // deleting for good.
  let deleting = $state.raw<string[] | null>(null);
  let deleteSending: "trash" | "permanent" | null = $state(null);
  let deleteError = $state("");
  let deleteErrorTitle = $state("");
  let offerForGood = $state(false);
  // The dialog's Cancel and Move to trash, for the keyboard focus.
  let deleteCancelSlot: HTMLSpanElement | null = null;
  let moveToTrashSlot: HTMLSpanElement | null = null;

  // Asks about the selected items. Move to trash takes the keyboard focus, as
  // the browser's own confirmation did: Enter moves the items to the trash,
  // Escape keeps them.
  export async function ask() {
    if (browser.selected.size === 0) return;
    deleting = Array.from(browser.selected);
    deleteError = "";
    offerForGood = false;
    await tick();
    moveToTrashSlot?.querySelector("button")?.focus();
  }

  async function closeDelete() {
    if (deleteSending) return;
    deleting = null;
    await tick();
    onclosed();
  }

  // Into the trash, or with `permanent` deleted for good at once. A refusal
  // keeps the dialog open with the server's reason, about the items that are
  // still in the folder: the server may have moved or deleted some before it
  // refused one, and says how many. The keyboard focus goes to Cancel.
  async function handleDelete(permanent: boolean) {
    if (!deleting || deleteSending || (permanent && !offerForGood)) return;
    browser.error = "";
    deleteError = "";
    browser.busy = true;
    deleteSending = permanent ? "permanent" : "trash";
    let refusal: unknown = null;
    try {
      await deleteItems(deleting, permanent);
      deleting = null;
      browser.selected = new Set();
    } catch (e) {
      refusal = e;
    } finally {
      browser.busy = false;
      deleteSending = null;
    }
    await browser.refresh();
    if (refusal === null || deleting === null) return;
    if (signedOut(refusal)) return;
    const title = permanent ? $t("files.error.forGood") : $t("files.error.toTrash");
    const message = messageOf(refusal);
    const listed = new Set(browser.listing?.items.map((item) => item.path) ?? []);
    const left = deleting.filter((path) => listed.has(path));
    browser.selected = new Set(left);
    if (left.length === 0) {
      deleting = null;
      browser.error = `${title}: ${message}`;
      return;
    }
    deleting = left;
    deleteErrorTitle = title;
    deleteError = message;
    if (refusal instanceof ApiError && (refusal.status === 409 || refusal.status === 507)) offerForGood = true;
    await tick();
    deleteCancelSlot?.querySelector("button")?.focus();
  }
</script>

<Modal
  open={deleting !== null}
  title={$t("files.delete.title", { n: deleting?.length ?? 0 })}
  closable={deleteSending === null}
  onclose={closeDelete}
>
  {#if deleting}
    <div class="space-y-3">
      {#if deleting.length === 1}
        <p class="text-sm">{$t("files.delete.one")}</p>
      {:else}
        <p class="text-sm">{$t("files.delete.many", { n: deleting.length })}</p>
      {/if}
      {#if deleteError}
        <!-- role="alert": screen readers announce the server's refusal. -->
        <div role="alert">
          <Alert variant="error" title={deleteErrorTitle}>{deleteError}</Alert>
        </div>
      {/if}
      {#if offerForGood}
        <p class="text-sm text-secondary">
          {#if deleting.length === 1}
            {$t("files.delete.forGoodOne")}
          {:else}
            {$t("files.delete.forGoodMany")}
          {/if}
        </p>
      {/if}
    </div>
  {/if}

  {#snippet footer()}
    <span bind:this={deleteCancelSlot} class="contents">
      <Button variant="secondary" class="flex-1" disabled={deleteSending !== null} onclick={closeDelete}>
        {$t("common.cancel")}
      </Button>
    </span>
    {#if offerForGood}
      <Button
        variant="danger"
        class="flex-1"
        icon={Trash2}
        loading={deleteSending === "permanent"}
        disabled={deleteSending !== null}
        onclick={() => handleDelete(true)}
      >
        {$t("files.deleteForGood")}
      </Button>
    {/if}
    <span bind:this={moveToTrashSlot} class="contents">
      <Button
        class="flex-1"
        icon={Trash}
        loading={deleteSending === "trash"}
        disabled={deleteSending !== null}
        onclick={() => handleDelete(false)}
      >
        {$t("files.moveToTrash")}
      </Button>
    </span>
  {/snippet}
</Modal>
