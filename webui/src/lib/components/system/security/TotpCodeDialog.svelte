<script lang="ts">
  import type { Snippet } from "svelte";
  import { Button, type IconComponent } from "$lib/components/ui";
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

<div class="modal-overlay items-start overflow-y-auto p-4">
  <div class="bg-surface-elevated rounded-lg p-6 max-w-md w-full my-auto">
    <h3 class={["text-lg font-semibold mb-4 flex items-center gap-2", danger && "text-status-error"]}>
      <Icon class="w-5 h-5" />
      {title}
    </h3>

    <div class="space-y-4">
      {@render children()}

      <div>
        <label for={inputId} class="block text-sm font-medium text-content-secondary mb-1">
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
        <div class="p-3 bg-status-error/20 border border-status-error text-status-error rounded-lg text-sm">
          {error}
        </div>
      {/if}

      <div class="flex gap-3">
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
