<script lang="ts">
  import type { Snippet } from "svelte";
  import { Alert, Button, type IconComponent } from "$lib/components/ui";
  import { t } from "$lib/i18n";

  // A dialog that asks for a code of the authenticator app: the setup's confirmation, or turning
  // two-factor authentication off. Its own overlay with Modal's classes (ADR-0302): one taller
  // than the window scrolls with it, so that its title and buttons stay reachable; my-auto
  // centers it while it fits.
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
    // The confirmation's text while the server answers.
    busyLabel: string;
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
    busyLabel,
    oncancel,
    onconfirm,
    children
  }: Props = $props();
</script>

<!-- Modal's look (ADR-0428) on its own markup, whose title and parts the specs walk: a danger
     dialog's icon in a tinted circle, the title in the text's colour. -->
<div class="modal-overlay items-start overflow-y-auto p-4">
  <div class="modal max-w-md my-auto">
    <h3 class="modal-title mb-4 flex items-center gap-3">
      {#if danger}
        <span class="modal-danger-icon" aria-hidden="true"><Icon class="w-4 h-4" /></span>
      {:else}
        <Icon class="w-5 h-5 text-content-secondary" aria-hidden="true" />
      {/if}
      {title}
    </h3>

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

      <div class="modal-footer">
        <Button variant="secondary" class="flex-1" type="button" onclick={oncancel}>
          {$t("common.cancel")}
        </Button>
        <Button
          variant={danger ? "danger" : "primary"}
          class="flex-1"
          type="button"
          onclick={onconfirm}
          disabled={loading || code.length !== 6}
        >
          {loading ? busyLabel : confirmLabel}
        </Button>
      </div>
    </div>
  </div>
</div>
