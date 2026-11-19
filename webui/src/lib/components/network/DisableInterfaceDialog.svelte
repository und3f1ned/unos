<script lang="ts">
  // Disable asks first: the connection to UNOS may go through the interface, and the host turns
  // it back on by itself unless the disable is kept in time (ADR-0244).
  import { Button, Modal } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  type Props = {
    // The interface the dialog asks about; null while it is closed.
    confirming: string | null;
    revertSeconds: number;
    // The interface being changed.
    busy: string | null;
    oncancel: () => void;
    onconfirm: () => void;
  };

  let { confirming, revertSeconds, busy, oncancel, onconfirm }: Props = $props();
</script>

<Modal
  open={confirming !== null}
  title={$t("network.disable.title", { name: confirming ?? "" })}
  size="md"
  danger
  closable={busy === null}
  onclose={oncancel}
>
  {#if confirming}
    <p>
      {$t("network.disable.dropBefore")} <strong class="text-content">{confirming}</strong>
      {$t("network.disable.dropAfter")}
    </p>
    <p class="mt-3">{$t("network.disable.revert", { n: revertSeconds, name: confirming })}</p>
  {/if}

  {#snippet footer()}
    <Button variant="secondary" class="flex-1" disabled={busy !== null} onclick={oncancel}>
      {$t("common.cancel")}
    </Button>
    <Button variant="danger" class="flex-1" loading={busy !== null} onclick={onconfirm}>
      {$t("network.disable.confirm", { name: confirming ?? "" })}
    </Button>
  {/snippet}
</Modal>
