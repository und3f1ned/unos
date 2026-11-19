<script lang="ts">
  import { deleteConfig, type NotificationConfig } from "$lib/api/notifications";
  import { Alert, Button, Modal } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import { nameOf } from "./channels";

  // The confirmation of deleting a channel; why the server refused it, shown in the dialog until
  // it closes.
  type Props = {
    // The channel to delete; null while the dialog is closed.
    channel: NotificationConfig | null;
    onclose: () => void;
    // The channel of `id` is deleted: the dialog is to close.
    ondeleted: (id: string) => Promise<void>;
  };

  let { channel, onclose, ondeleted }: Props = $props();

  let busy = $state(false);
  let error = $state("");

  function close() {
    if (busy) return;
    error = "";
    onclose();
  }

  async function confirmDelete() {
    const id = channel?.id;
    if (!id || busy) return;
    busy = true;
    try {
      await deleteConfig(id);
      busy = false;
      error = "";
      await ondeleted(id);
    } catch (e) {
      error = e instanceof Error ? e.message : String(e);
    } finally {
      busy = false;
    }
  }
</script>

<Modal
  open={channel !== null}
  title={$t("notifications.delete.title")}
  size="sm"
  danger
  closable={!busy}
  onclose={close}
>
  {#if channel}
    <p>{$t("notifications.delete.text", { name: nameOf($t, channel) })}</p>
  {/if}
  {#if error}
    <div role="alert" class="mt-4">
      <Alert variant="error" title={$t("notifications.delete.error")}>{error}</Alert>
    </div>
  {/if}

  {#snippet footer()}
    <Button variant="secondary" class="flex-1" disabled={busy} onclick={close}>
      {$t("common.cancel")}
    </Button>
    <Button variant="danger" class="flex-1" loading={busy} onclick={confirmDelete}>
      {$t("notifications.delete.confirm")}
    </Button>
  {/snippet}
</Modal>
