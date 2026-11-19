<script lang="ts">
  import { tick } from "svelte";
  import { Trash2 } from "lucide-svelte";
  import { formatSize } from "$lib/api/files";
  import { Alert, Button, Modal } from "$lib/components/ui";
  import { intlLocale, t } from "$lib/i18n";
  import { formatDateTime } from "$lib/time";
  import type { FileBrowser } from "./browser.svelte";
  import type { UploadLeftovers } from "./leftovers.svelte";

  type Props = {
    browser: FileBrowser;
    leftovers: UploadLeftovers;
    // Sizes in the units of the page's language.
    units: readonly string[];
    // The dialog closed without removing: the page gives the keyboard focus back to the notice.
    onclosed: () => void;
  };

  let { browser, leftovers, units, onclosed }: Props = $props();

  // The dialog's Cancel, which takes the keyboard focus: a removal cannot be undone.
  let leftoversCancelSlot: HTMLSpanElement | null = null;

  export async function ask() {
    leftovers.ask();
    await tick();
    leftoversCancelSlot?.querySelector("button")?.focus();
  }

  async function closeLeftovers() {
    if (leftovers.sending) return;
    leftovers.removing = null;
    await tick();
    onclosed();
  }

  // A refusal that leaves none of the dialog's files listed closes it: the reason stays above the
  // list.
  async function handleRemoveLeftovers() {
    if (!leftovers.removing || leftovers.sending) return;
    browser.error = "";
    const outcome = await leftovers.remove();
    if (outcome === "signed-out") return;
    if (outcome === "unlisted") browser.error = `${$t("files.leftovers.failed")}: ${leftovers.error}`;
    // The open folder may have listed them.
    await browser.refresh();
  }
</script>

<Modal
  open={leftovers.removing !== null}
  title={$t("files.leftovers.dialogTitle", { n: leftovers.removing?.length ?? 0 })}
  closable={!leftovers.sending}
  onclose={closeLeftovers}
>
  {#if leftovers.removing}
    <div class="space-y-3">
      <p class="text-sm">{$t("files.leftovers.confirm", { n: leftovers.removing.length })}</p>
      <ul class="space-y-2">
        {#each leftovers.removing as file (file.path)}
          <li class="card-nested py-2 text-sm">
            <p class="font-mono break-all">{file.path}</p>
            <p class="text-content-secondary">
              {$t("files.leftovers.file", {
                size: formatSize(file.size, units),
                time: formatDateTime(file.modified, $intlLocale),
              })}
            </p>
          </li>
        {/each}
      </ul>
      {#if leftovers.error}
        <!-- role="alert": screen readers announce the server's refusal. -->
        <div role="alert">
          <Alert variant="error" title={$t("files.leftovers.failed")}>{leftovers.error}</Alert>
        </div>
      {/if}
    </div>
  {/if}

  {#snippet footer()}
    <span bind:this={leftoversCancelSlot} class="contents">
      <Button variant="secondary" class="flex-1" disabled={leftovers.sending} onclick={closeLeftovers}>
        {$t("common.cancel")}
      </Button>
    </span>
    <Button
      variant="danger"
      class="flex-1"
      icon={Trash2}
      loading={leftovers.sending}
      disabled={leftovers.sending}
      onclick={handleRemoveLeftovers}
    >
      {$t("files.leftovers.removeForGood")}
    </Button>
  {/snippet}
</Modal>
