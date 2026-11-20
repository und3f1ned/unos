<script lang="ts">
  import { Alert, Button, Modal } from "$lib/components/ui";
  import { t } from "$lib/i18n";
  import type { ActionTexts } from "./actions";

  // The confirmation of a reboot, a shutdown or a suspend; the host's answer when it refused.
  type Props = {
    // The texts of the action asked; null while the dialog is closed.
    confirmation: ActionTexts | null;
    busy: boolean;
    error: string;
    onclose: () => void;
    onconfirm: () => void;
  };

  let { confirmation, busy, error, onclose, onconfirm }: Props = $props();
</script>

<Modal
  open={confirmation !== null}
  title={confirmation ? $t(confirmation.titleKey) : ""}
  size="md"
  danger
  closable={!busy}
  {onclose}
>
  <p>{confirmation ? $t(confirmation.textKey) : ""}</p>
  {#if error && confirmation}
    <div role="alert" class="mt-4">
      <Alert variant="error" title={$t(confirmation.failedKey)}>{error}</Alert>
    </div>
  {/if}

  {#snippet footer()}
    <Button variant="secondary" class="flex-1" disabled={busy} onclick={onclose}>{$t("common.cancel")}</Button>
    <Button variant={confirmation?.variant ?? "danger"} class="flex-1" loading={busy} onclick={onconfirm}>
      {confirmation ? $t(confirmation.confirmKey) : ""}
    </Button>
  {/snippet}
</Modal>
