<script lang="ts">
  import type { Snippet } from "svelte";
  import { Alert, Button, Modal, type IconComponent } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  // A dialog that asks for a code of the authenticator app: the setup's confirmation, or turning
  // two-factor authentication off.
  type Props = {
    title: string;
    icon: IconComponent;
    // Turning it off: the title in the colour of an error, and a confirmation of danger.
    danger?: boolean;
    inputId: string;
    label: string;
    code: string;
    error: string;
    loading: boolean;
    confirmLabel: string;
    oncancel: () => void;
    onconfirm: () => void;
    // What the dialog says before the field.
    children: Snippet;
  };

  let {
    title,
    icon: Icon,
    danger = false,
    inputId,
    label,
    code = $bindable(),
    error,
    loading,
    confirmLabel,
    oncancel,
    onconfirm,
    children
  }: Props = $props();
</script>

<!-- A Modal (ADR-0432): a danger dialog's icon in a tinted circle, the title in the text's colour. -->
<!-- Not closable while the server answers: the code may still take effect. -->
<Modal open {title} size="sm" tone={danger ? "danger" : null} closable={!loading} onclose={oncancel}>
  {#snippet titleIcon()}
    <Icon class={danger ? "w-4 h-4" : "w-5 h-5 text-content-secondary"} aria-hidden="true" />
  {/snippet}
  <div class="space-y-4">
    {@render children()}

    <div>
      <label for={inputId} class="form-label">
        {label}
      </label>
      <input
        type="text"
        id={inputId}
        bind:value={code}
        class="form-input text-center text-2xl tracking-widest"
        placeholder="000000"
        maxlength="6"
        pattern="[0-9]*"
        inputmode="numeric"
      />
    </div>

    {#if error}
      <Alert variant="error" size="sm">{error}</Alert>
    {/if}
  </div>

  {#snippet footer()}
    <Button variant="secondary" class="flex-1" type="button" disabled={loading} onclick={oncancel}>
      {$t("common.cancel")}
    </Button>
    <Button
      variant={danger ? "danger" : "primary"}
      class="flex-1"
      type="button"
      onclick={onconfirm}
      {loading}
      disabled={code.length !== 6}
    >
      {confirmLabel}
    </Button>
  {/snippet}
</Modal>
